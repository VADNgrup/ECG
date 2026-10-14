"""Text embeddings for retrieval and support/coverage scores. Backends (`Config.EMBED_BACKEND`):

- `openai` (the pipeline): OpenAI-compatible embeddings endpoint, multilingual (`EMBED_MODEL`, e.g. text-embedding-3-small).
- `hash`: deterministic offline vectors from character n-grams (no network, no cost); the tests use it.

Vectors are L2-normalised float32. Source texts (sentences, captions) are cached on disk per model: a base
`embeddings_<model>.npz` plus small append-only `.part-*.npz` files, merged back into the base once there are many.
Transient texts (queries, written bullets) live in memory for the run only, so the disk cache does not grow with
every run's wording.
"""
import hashlib
import json
import os
import tempfile
import threading
import uuid
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

import numpy as np
from openai import OpenAI

from src.utils.config import Config

HASH_DIM = 512
MAX_PARTS = 24


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]


def _hash_vector(text: str) -> np.ndarray:
    """Character 2-4-gram hashing trick: script-agnostic, similar strings get similar vectors."""
    vec = np.zeros(HASH_DIM, dtype=np.float32)
    s = f" {' '.join(text.casefold().split())} "
    for n in (2, 3, 4):
        for i in range(len(s) - n + 1):
            vec[zlib.crc32(s[i:i + n].encode("utf-8")) % HASH_DIM] += 1.0
    return vec


def _normalise(m: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.maximum(norm, 1e-9)


def _read_npz(path: Path) -> Dict[str, np.ndarray]:
    data = np.load(path, allow_pickle=False)
    return dict(zip(json.loads(str(data["keys"])), data["vecs"]))


def _write_npz(path: Path, rows: Dict[str, np.ndarray], compress: bool) -> None:
    keys = list(rows)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            (np.savez_compressed if compress else np.savez)(
                handle, keys=json.dumps(keys), vecs=np.stack([rows[k] for k in keys]))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def embed_transient(embedder, texts: Sequence[str]) -> np.ndarray:
    """Embed texts not worth keeping on disk (queries, written bullets); plain `embed` for other embedders."""
    method = getattr(embedder, "embed_transient", None)
    return method(texts) if method is not None else embedder.embed(texts)


class Embedder:
    def __init__(self, backend: Optional[str] = None, model: Optional[str] = None, cache_dir: Optional[Path] = None):
        self.backend = (backend or Config.EMBED_BACKEND).lower()
        self.model = model or (Config.EMBED_MODEL if self.backend == "openai" else "hash")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.tokens_estimate = 0
        self.calls = 0
        self._mem: Dict[str, np.ndarray] = {}
        self._saved: Set[str] = set()        # persistent keys already on disk
        self._unsaved: Set[str] = set()      # persistent keys waiting for the next save
        self._inflight: Dict[str, threading.Event] = {}
        self._loaded = False
        self._client = None
        self._lock = threading.RLock()

    @property
    def cache_path(self) -> Optional[Path]:
        if self.cache_dir is None:
            return None
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in self.model)
        return self.cache_dir / f"embeddings_{safe}.npz"

    def _parts(self) -> List[Path]:
        path = self.cache_path
        return sorted(path.parent.glob(f"{path.stem}.part-*.npz")) if path and path.parent.exists() else []

    @property
    def dim(self) -> int:
        if self.backend == "hash":
            return HASH_DIM
        return int(next(iter(self._mem.values())).shape[0]) if self._mem else 0

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = self.cache_path
        for file in ([path] if path and path.exists() else []) + self._parts():
            try:
                rows = _read_npz(file)
            except Exception as exc:              # a damaged cache file is skipped, never fatal
                print(f"[Embedder] skipped unreadable cache {file.name}: {type(exc).__name__}")
                continue
            self._mem.update(rows)
            self._saved.update(rows)

    def save(self) -> None:
        """Write the persistent vectors added since the last save as one small part file."""
        with self._lock:
            path = self.cache_path
            if not path or not self._unsaved:
                return
            rows = {k: self._mem[k] for k in self._unsaved}
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                _write_npz(path.with_name(f"{path.stem}.part-{uuid.uuid4().hex[:12]}.npz"), rows, compress=False)
            except OSError as exc:
                print(f"[Embedder] could not save {len(rows)} vector(s) to {path.parent}: {exc}")
                return
            self._saved.update(rows)
            self._unsaved.clear()
            if len(self._parts()) > MAX_PARTS:
                self._compact()

    def _compact(self) -> None:
        """Merge the base file and every part (as they are on disk now, other writers included) into the base."""
        path = self.cache_path
        parts = self._parts()
        merged: Dict[str, np.ndarray] = {}
        for file in ([path] if path.exists() else []) + parts:
            try:
                merged.update(_read_npz(file))
            except Exception:
                continue
        try:
            _write_npz(path, merged, compress=True)
        except OSError as exc:
            print(f"[Embedder] could not compact {path.name}: {exc}")
            return
        for file in parts:
            file.unlink(missing_ok=True)

    def _remote(self, texts: Sequence[str]) -> List[np.ndarray]:
        if self._client is None:
            self._client = OpenAI(api_key=Config.EMBED_API_KEY, base_url=Config.EMBED_BASE_URL,
                                  timeout=120.0, max_retries=5)
        batch = [t[:8000] or " " for t in texts]
        resp = self._client.embeddings.create(model=self.model, input=batch)
        self.calls += 1
        self.tokens_estimate += getattr(getattr(resp, "usage", None), "total_tokens", 0) or sum(len(t) // 3 for t in batch)
        return [np.asarray(d.embedding, dtype=np.float32) for d in resp.data]

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """(len(texts), dim) normalised matrix; only texts missing from the cache are computed, and kept on disk."""
        return self._embed(texts, persist=True)

    def embed_transient(self, texts: Sequence[str]) -> np.ndarray:
        """Like `embed`, but new vectors are kept for this run only."""
        return self._embed(texts, persist=False)

    def _embed(self, texts: Sequence[str], persist: bool) -> np.ndarray:
        texts = list(texts)
        keys = [_key(t) for t in texts]
        with self._lock:
            self._load()
            todo: Dict[str, str] = {}
            waits = []
            for k, t in zip(keys, texts):
                if k in self._mem or k in todo:
                    continue
                if k in self._inflight:
                    waits.append(self._inflight[k])
                    continue
                todo[k] = t
            done = threading.Event()
            for k in todo:
                self._inflight[k] = done
        try:
            if todo:
                self._compute(list(todo.items()), persist)
        finally:
            with self._lock:
                for k in todo:
                    self._inflight.pop(k, None)
            done.set()
        for event in waits:
            event.wait()
        with self._lock:
            missing = [(k, t) for k, t in zip(keys, texts) if k not in self._mem]
        if missing:                              # another thread's request for these failed: compute them here
            self._compute(list(dict(missing).items()), persist)
        with self._lock:
            if persist and self.cache_dir is not None:
                self._unsaved.update(k for k in keys if k not in self._saved)
            if len(self._unsaved) >= max(1, Config.EMBED_AUTOSAVE_EVERY):
                self.save()
            if not texts:
                return np.zeros((0, self.dim), dtype=np.float32)
            return np.stack([self._mem[k] for k in keys])

    def _compute(self, todo: List[tuple], persist: bool) -> None:
        """Embed outside the lock, one request per batch; each finished batch is cached at once."""
        step = max(1, Config.EMBED_BATCH)
        for start in range(0, len(todo), step):
            chunk = todo[start:start + step]
            vecs = ([_hash_vector(t) for _, t in chunk] if self.backend == "hash"
                    else self._remote([t for _, t in chunk]))
            mat = _normalise(np.stack(vecs))
            with self._lock:
                for (k, _), v in zip(chunk, mat):
                    self._mem[k] = v
                    if persist and self.cache_dir is not None:
                        self._unsaved.add(k)


def cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return a @ b.T
