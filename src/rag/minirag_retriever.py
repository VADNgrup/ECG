"""Retrieval through the KB's own MiniRAG stores (chunk / entity vector stores and the entity graph), no LLM involved.

The agents look things up here (evidence for a concept, prerequisites, support of a bullet); what comes back is chunk ids
and entity names, which `EvidenceStore` maps to source sentences with IDs and offsets.
"""
import asyncio
import json
import threading
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.rag import build_kg as B
from src.utils.config import Config
from src.utils.content_cache import cached_json

_SEP = "<SEP>"


_LOOP: Optional[asyncio.AbstractEventLoop] = None
_LOOP_LOCK = threading.Lock()


def _run(coro):
    """Run a coroutine from sync code on one shared background event loop, so MiniRAG's async HTTP clients stay bound to a
    loop that lives as long as the process (also works when the caller already runs inside an event loop)."""
    global _LOOP
    with _LOOP_LOCK:
        if _LOOP is None:
            _LOOP = asyncio.new_event_loop()
            threading.Thread(target=_LOOP.run_forever, daemon=True, name="minirag-loop").start()
    return asyncio.run_coroutine_threadsafe(coro, _LOOP).result()


class MiniRAGRetriever:
    def __init__(self, kb_dir: Path):
        self.kb_dir = Path(kb_dir)
        self.stats: Counter = Counter()
        self._rag = None
        self._chunks: Optional[dict] = None
        self._rag_lock = threading.Lock()
        self.error: Optional[str] = None
        self._embedding: Optional[dict] = None
        self._warned: set = set()

    def embedding_info(self) -> dict:
        """How the KB's vector stores were embedded (backend, model, dim), from its run report."""
        if self._embedding is None:
            try:
                report = json.loads((self.kb_dir / "run_report.json").read_text(encoding="utf-8"))
                self._embedding = dict(report.get("embeddings") or {})
            except (OSError, ValueError):
                self._embedding = {}
        return self._embedding

    def backend(self) -> str:
        return str(self.embedding_info().get("backend") or "local")

    def vector_usable(self, vector, model: Optional[str]) -> bool:
        """A caller's query vector may stand in for MiniRAG's own only when both come from the same model and dim."""
        if vector is None:
            return False
        info = self.embedding_info()
        same = (str(info.get("model") or "") == str(model or "")
                and int(info.get("dim") or 0) == len(vector))
        if not same:
            self.stats["vector_mismatch"] += 1
            self.warn("vector_mismatch", f"query vectors come from {model!r} but the KB's stores were embedded with "
                                         f"{info.get('model')!r} (dim {info.get('dim')}); MiniRAG embeds its own queries")
        return same

    def warn(self, key: str, message: str) -> None:
        """Print a MiniRAG problem once per kind; every occurrence is still counted in `stats`."""
        if key not in self._warned:
            self._warned.add(key)
            print(f"[MiniRAG] {message}")

    @property
    def rag(self):
        if self._rag is None and self.error is None:
            # Adaptive planner shards start together.  Initialise the fairly
            # heavy MiniRAG backend once, not once per racing worker.
            with self._rag_lock:
                if self._rag is None and self.error is None:
                    try:
                        self._rag = B._make_rag(self.kb_dir, self.backend())
                    except Exception as exc:              # no KB stores / embedding backend unavailable
                        self.error = f"{type(exc).__name__}: {exc}"
        return self._rag

    @property
    def available(self) -> bool:
        return self.rag is not None

    def chunk_hits(self, query: str, k: int = 8) -> List[Tuple[str, float]]:
        """(chunk id, cosine) of the k chunks closest to `query`, best first."""
        rag = self.rag
        if rag is None or not query.strip():
            return []
        self.stats["chunk_queries"] += 1
        rows = _run(rag.chunks_vdb.query(query, top_k=k))
        return [(str(r["id"]), float(r.get("distance", 0.0))) for r in rows]

    def entity_hits(self, query: str, k: int = 5) -> List[Tuple[str, float]]:
        """(entity name, cosine) of the k KG entities closest to `query` (descriptions and names)."""
        rag = self.rag
        if rag is None or not query.strip():
            return []
        self.stats["entity_queries"] += 1
        best: Dict[str, float] = {}
        for store in (rag.entities_vdb, rag.entity_name_vdb):
            for row in _run(store.query(query, top_k=k)):
                name = str(row.get("entity_name") or "")
                if name:
                    best[name] = max(best.get(name, 0.0), float(row.get("distance", 0.0)))
        return sorted(best.items(), key=lambda kv: -kv[1])[:k]

    def hybrid_hits(self, query: str, chunk_k: int = 8, entity_k: int = 5, vector=None, vector_model=None) \
            -> Tuple[List[Tuple[str, float]], List[Tuple[str, float]]]:
        """Query chunk, entity-description and entity-name indexes with one embedding.

        MiniRAG's public ``store.query(text)`` embeds the identical text once
        per vector store.  Retrieval needs all three stores, so doing that
        literally triples HTTP round-trips.  NanoVectorDB exposes its local
        client; compute the query vector once and fan it out locally.
        """
        rag = self.rag
        if rag is None or not query.strip():
            return [], []
        if not self.vector_usable(vector, vector_model):
            vector = None

        async def query_all():
            query_vector = vector if vector is not None else (await rag.embedding_func([query]))[0]

            def rows(store, top_k):
                client = getattr(store, "_client", None)
                if client is None:
                    return None
                raw = client.query(
                    query=query_vector,
                    top_k=top_k,
                    better_than_threshold=store.cosine_better_than_threshold,
                )
                return [{**row, "id": row["__id__"],
                         "distance": row["__metrics__"]} for row in raw]

            return (rows(rag.chunks_vdb, chunk_k),
                    rows(rag.entities_vdb, entity_k),
                    rows(rag.entity_name_vdb, entity_k))

        chunk_rows, entity_rows, name_rows = _run(query_all())
        # Unknown/custom vector backends retain the public-API fallback.
        if chunk_rows is None or entity_rows is None or name_rows is None:
            return self.chunk_hits(query, chunk_k), self.entity_hits(query, entity_k)
        self.stats["chunk_queries"] += 1
        self.stats["entity_queries"] += 1
        chunks = [(str(row["id"]), float(row.get("distance", 0.0))) for row in chunk_rows]
        best: Dict[str, float] = {}
        for row in [*entity_rows, *name_rows]:
            name = str(row.get("entity_name") or "")
            if name:
                best[name] = max(best.get(name, 0.0), float(row.get("distance", 0.0)))
        return chunks, sorted(best.items(), key=lambda item: -item[1])[:entity_k]

    def entity_chunks(self, name: str) -> List[str]:
        """Chunk ids where the KG says the entity occurs."""
        rag = self.rag
        if rag is None:
            return []
        node = _run(rag.chunk_entity_relation_graph.get_node(name))
        return [c for c in str((node or {}).get("source_id", "")).split(_SEP) if c]

    def neighbors(self, name: str) -> List[str]:
        """Entities linked to `name` in the KG."""
        rag = self.rag
        if rag is None:
            return []
        edges = _run(rag.chunk_entity_relation_graph.get_node_edges(name)) or []
        return [b if a == name else a for a, b in edges]

    def mini_context(self, query: str, allowed_chunks=None) -> str:
        """A small cached context assembled directly from MiniRAG's indexes.

        ``rag.aquery(mode="mini")`` first asks an LLM to expand a query into
        many keywords and then embeds every keyword.  That is useful for open
        question answering, but planner shards already contain their exact
        heading/concept names.  Query the three persisted vector indexes once
        instead, then read the matching entity descriptions and source chunks
        locally.  ``allowed_chunks`` is a hard scope boundary, not just a
        ranking hint.
        """
        rag = self.rag
        if rag is None or not query.strip():
            return ""
        allowed = sorted({str(c) for c in (allowed_chunks or []) if c})
        payload = {"query": query, "allowed_chunks": allowed, "chunk_k": 8, "entity_k": 10}

        def invoke():
            self.stats["mini_queries"] += 1
            chunk_hits, entity_hits = self.hybrid_hits(query, chunk_k=8, entity_k=10)
            allowed_set = set(allowed)
            chunk_hits = [(cid, score) for cid, score in chunk_hits
                          if not allowed_set or cid in allowed_set]
            if self._chunks is None:
                try:
                    self._chunks = json.loads((self.kb_dir / "kv_store_text_chunks.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    self._chunks = {}

            entities = []
            for name, _ in entity_hits:
                try:
                    node = _run(rag.chunk_entity_relation_graph.get_node(name)) or {}
                except Exception:
                    node = {}
                sources = {c for c in str(node.get("source_id", "")).split(_SEP) if c}
                if allowed_set and sources and not (sources & allowed_set):
                    continue
                description = " ".join(str(node.get("description") or "").split())[:280]
                entities.append(f"- {name}: {description}" if description else f"- {name}")
                if len(entities) >= 8:
                    break

            excerpts = []
            for chunk_id, _ in chunk_hits[:5]:
                text = " ".join(str((self._chunks or {}).get(chunk_id, {}).get("content") or "").split())
                if text:
                    excerpts.append(f"- {text[:620]}")
            parts = []
            if entities:
                parts.append("Relevant KG entities:\n" + "\n".join(entities))
            if excerpts:
                parts.append("Relevant source chunks:\n" + "\n".join(excerpts))
            return "\n\n".join(parts)[:4000]

        return cached_json("minirag_planner_context_v1", self.backend(), payload, invoke) or ""
