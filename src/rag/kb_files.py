"""Readers of a KB's JSON files, shared by the structural pass and the retrieval side."""
import json
from pathlib import Path


def load_structural_index(kb_dir: Path) -> dict:
    return json.loads((Path(kb_dir) / "structural_index.json").read_text(encoding="utf-8"))


def load_text_chunks(kb_dir: Path) -> dict:
    return json.loads((Path(kb_dir) / "kv_store_text_chunks.json").read_text(encoding="utf-8"))


def kb_file(kb_dir: Path, stored: str) -> str:
    """A file path a KB stored, resolved against where the KB lives now: as stored when it exists, else the shortest
    tail of it (after the KB's own folder name when present) that exists under `kb_dir`."""
    if not stored or Path(stored).exists():
        return stored
    parts = Path(stored).parts
    name = Path(kb_dir).name
    tails = [parts[parts.index(name) + 1:]] if name in parts else []
    tails += [parts[n:] for n in range(len(parts) - 1, 0, -1)]
    for tail in tails:
        candidate = Path(kb_dir).joinpath(*tail) if tail else None
        if candidate is not None and candidate.exists():
            return candidate.as_posix()
    return stored


def load_asset_rows(kb_dir: Path) -> list:
    """`asset_headings.json` with each `img_path` resolved by `kb_file`; [] when the KB has none."""
    p = Path(kb_dir) / "asset_headings.json"
    if not p.exists():
        return []
    rows = json.loads(p.read_text(encoding="utf-8"))
    for row in rows:
        if row.get("img_path"):
            row["img_path"] = kb_file(kb_dir, row["img_path"])
    return rows
