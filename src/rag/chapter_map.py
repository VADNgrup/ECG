"""One chapter of a KB as the lecture pipeline sees it: its paragraphs and sentences.

A chapter is a top-level section of the requested scope. Its sub-headings only bound which sentences belong to it; the
content inside is organised by the agents, not by the book's headings. Only true noise is removed here (the extraction's
own `filter_sentences`).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from src.rag.idea_candidates import scope_sentences
from src.utils import lang
from src.workflow.scope import ScopeOrchestrator


class Chapter:
    """Paragraphs (source units) of one chapter in reading order, with short IDs `P1..`."""

    def __init__(self, root_id: int, title: str, heading_ids: List[int], by_heading: dict):
        self.id, self.title = root_id, title
        self.heading_ids = list(heading_ids)
        self.paragraphs: List[dict] = []
        self.sentences: Dict[str, dict] = {}
        self.pid_of: Dict[str, str] = {}
        for hid in heading_ids:
            units: Dict[str, List[dict]] = {}
            for s in (by_heading.get(hid) or {}).get("sentences", []):
                units.setdefault(s["unit_id"], []).append(s)
            for unit_id, rows in units.items():
                kind = rows[0]["kind"]
                items = all(r.get("list_item") for r in rows)
                prev = self.paragraphs[-1] if self.paragraphs else None
                if items and prev is not None and prev["heading"] == hid and prev["kind"] == "text":
                    prev["sentences"] += [r["id"] for r in rows]           # a list belongs to its lead-in sentence
                    prev["list_items"] += len(rows)
                    self._index(rows, prev["pid"], hid)
                    continue
                pid = f"P{len(self.paragraphs) + 1}"
                pages = [(r.get("ref") or {}).get("page") for r in rows]
                self.paragraphs.append({"pid": pid, "unit_id": unit_id, "heading": hid, "kind": kind,
                                        "sentences": [r["id"] for r in rows], "list_items": 0,
                                        "page": next((p for p in pages if p is not None), None),
                                        "chunk": (rows[0].get("ref") or {}).get("chunk_id")})
                self._index(rows, pid, hid)
        self._drop_truncated_copies()
        self.by_pid = {p["pid"]: p for p in self.paragraphs}

    def _drop_truncated_copies(self) -> None:
        """OCR sometimes emits a paragraph twice, the first copy cut short (a code block may sit between the two, and the
        copy may carry a word or two the complete one lacks): keep the complete one. A code listing, formula or table that
        overlapping chunks repeat (the earlier copy cut where its chunk ends) keeps only its complete copy too."""
        norm = lambda p: " ".join(self.sentences[s]["text"] for s in p["sentences"]).casefold().rstrip(" .…")

        def truncated(k: int) -> bool:
            p = self.paragraphs[k]
            if p["kind"] != "text":
                mine = " ".join(norm(p).split())
                later = [" ".join(norm(q).split()) for q in self.paragraphs[k + 1:k + 6] if q["kind"] == p["kind"]][:2]
                return len(mine) >= 20 and any(q.startswith(mine) for q in later)
            words = norm(p).split()
            heads = [" ".join(words[n:]) for n in range(3)]
            later = [q for q in self.paragraphs[k + 1:k + 4] if q["kind"] == "text"][:2]
            return any(len(h) >= 40 and norm(q).startswith(h)
                       for q in later for h in heads)

        kept = []
        for k, p in enumerate(self.paragraphs):
            if truncated(k):
                for s in p["sentences"]:
                    self.sentences.pop(s, None)
                    self.pid_of.pop(s, None)
                continue
            kept.append(p)
        for n, p in enumerate(kept, 1):                  # renumber so PIDs stay dense and in reading order
            p["pid"] = f"P{n}"
            for s in p["sentences"]:
                self.sentences[s]["pid"] = p["pid"]
                self.pid_of[s] = p["pid"]
        self.paragraphs = kept

    def _index(self, rows: List[dict], pid: str, hid: int) -> None:
        for r in rows:
            self.sentences[r["id"]] = {**r, "pid": pid, "heading": hid, "text": r["clean"],
                                       "page": (r.get("ref") or {}).get("page")}
            self.pid_of[r["id"]] = pid

    def teachable(self, paragraph: dict) -> bool:
        """A paragraph that can carry teaching content: prose with some words, a valid formula or table."""
        if paragraph["kind"] in ("formula", "table"):
            return True
        return paragraph["kind"] == "text" and any(
            lang.length_units(self.sentences[s]["text"]) >= 3 for s in paragraph["sentences"])

    def text_of(self, pid: str, limit: Optional[int] = None) -> str:
        text = " ".join(self.sentences[s]["text"] for s in self.by_pid[pid]["sentences"])
        return text if limit is None or len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"

    def following_assets(self, pid: str) -> List[str]:
        """Formula / table / code paragraphs right after a prose paragraph: they usually illustrate it."""
        out, k = [], int(pid[1:])
        while k < len(self.paragraphs) and self.paragraphs[k]["kind"] != "text":
            out.append(self.paragraphs[k]["pid"])
            k += 1
        return out


def load_chapters(kb_dir, index: dict, roots: List[int], visible) -> List[Chapter]:
    orchestrator = ScopeOrchestrator(index, kb_dir)
    out = []
    for root in roots:
        ordered = orchestrator.ordered_scope([root], visible)
        by_heading = scope_sentences(kb_dir, index, set(ordered))
        out.append(Chapter(root, orchestrator.title(root), ordered, by_heading))
    return out
