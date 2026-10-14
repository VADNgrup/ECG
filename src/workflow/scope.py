"""The deck's scope, resolved deterministically from the structural KG (no LLM): which KB, which chapters (roots) and
which headings of theirs the lecture may draw on, as an explicit manifest."""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from src.rag.idea_candidates import scope_sentences
from src.rag.retrieve_content import find_heading_id, find_heading_path, load_asset_headings, root_ids_of
from src.utils.config import Config

KB_DIR = Config.KB_CACHE_DIR
EXCLUDE_MARK = "!"      # a `root_heading` term naming a section left out of the deck ("Document;!Document > Methods")
TITLE_TEXT = "="        # a `root_heading` term naming the text under the document's title, without its sections


def find_kb_dir(document_id: str) -> Optional[Path]:
    """The KB of a document: extract.py names it {document_id}; build_kg.py {document_id}__{mode}{suffix}."""
    if not KB_DIR.exists():
        return None
    for kb_dir in [KB_DIR / document_id, *KB_DIR.glob(f"{document_id}__*")]:
        if (kb_dir / "structural_index.json").exists():
            return kb_dir
    return None


def clean_display_title(title: str) -> str:
    """Strip trailing dot-leader + page-number junk from TOC-harvest (e.g. "Preface ... xiii" -> "Preface")."""
    return re.sub(r'\.{3,}\s*(?:[ivxlcdm]+|\d+)\s*$', '', title.strip(), flags=re.IGNORECASE).strip()


class ScopeOrchestrator:
    """Owns scope invariants; no LLM call is made here."""

    def __init__(self, index: dict, kb_dir: Path):
        self.index = index
        self.kb_dir = Path(kb_dir)
        self.headings: Dict[str, dict] = index.get("headings") or {}
        self.title_id = int(index.get("title_id", -1))
        self.excluded: set = set()               # sections the `root_heading` leaves out (resolve_roots)
        self.children: Dict[int, List[int]] = defaultdict(list)
        for sid, heading in self.headings.items():
            parent = heading.get("parent")
            if parent is not None:
                self.children[int(parent)].append(int(sid))
        order = lambda hid: self.headings[str(hid)].get("order", 0)
        for values in self.children.values():
            values.sort(key=order)

    def display(self, hid: int) -> str:
        return clean_display_title(self.title(hid))

    def visible(self, hid: int) -> bool:
        """A heading the deck covers: all of them but those the `root_heading` leaves out."""
        return hid not in self.excluded

    def tops(self) -> List[int]:
        """The document's top sections in order: the headings no section holds."""
        held = lambda p: p is not None and int(p) != self.title_id and str(int(p)) in self.headings
        return sorted((int(k) for k, h in self.headings.items() if int(k) != self.title_id and not held(h.get("parent"))),
                      key=lambda i: self.headings[str(i)].get("order", 0))

    def under(self, root: int) -> List[int]:
        """The sections directly under a deck root: the title's are roots themselves."""
        return [] if root == self.title_id else self.children.get(root, [])

    def _title_has_text(self) -> bool:
        """Whether sentences sit under the document's title, before its first heading."""
        if str(self.title_id) not in self.headings or not (self.kb_dir / "structural_index.json").is_file():
            return False
        return bool((scope_sentences(self.kb_dir, self.index, {self.title_id}).get(self.title_id) or {}).get("sentences"))

    def _whole(self, visible: Callable[[int], bool]) -> List[int]:
        """Every root of the whole document: the text under its title and each top section."""
        own = [self.title_id] if visible(self.title_id) and self._title_has_text() else []
        return own + [i for i in self.tops() if visible(i)]

    def resolve_roots(self, root_heading: str | None, visible: Callable[[int], bool]) -> List[int]:
        """Selected roots in document order, or every root of the whole document (the document's title names that too;
        TITLE_TEXT names only the text under it); a term starting with EXCLUDE_MARK leaves that section (and what is
        under it) out of the scope."""
        terms = [p.strip() for p in (root_heading or "").split(";") if p.strip()]
        self.excluded = {self._named(t[len(EXCLUDE_MARK):].strip()) for t in terms if t.startswith(EXCLUDE_MARK)}
        chosen = [t for t in terms if not t.startswith(EXCLUDE_MARK)]
        if chosen:
            roots = []
            for name in chosen:
                hid = self._named(name)
                if not visible(hid):
                    raise ValueError(f"Requested heading is excluded from lecture content: {name}")
                whole = hid == self.title_id and name != TITLE_TEXT
                roots += [r for r in (self._whole(visible) if whole else [hid]) if r not in roots]
        else:
            roots = self._whole(visible)
        roots.sort(key=lambda i: self.headings[str(i)].get("order", 0))
        for i, root in enumerate(roots):
            descendants = root_ids_of(self.index, root) - {root}
            overlap = next((other for other in roots[i + 1:] if other in descendants), None)
            if overlap is not None:
                raise ValueError(
                    f"Selected roots overlap: '{self.title(root)}' contains '{self.title(overlap)}'. "
                    "Select the parent or the child, not both.")
        return roots

    def _named(self, name: str) -> int:
        """The heading a term of `root_heading` names: a path of titles, or one title."""
        if name == TITLE_TEXT:
            return self.title_id
        hid = find_heading_path(self.index, name)
        if hid is None:
            hid = find_heading_id(self.index, name)
        if hid is None:
            candidates = [self.headings[k].get("title", "") for k in self.headings if int(k) != self.title_id]
            raise ValueError(f"Requested heading not found: {name}. Available headings: {candidates[:30]}")
        return hid

    def title(self, hid: int) -> str:
        return str((self.headings.get(str(hid)) or {}).get("title") or "")

    def root_of(self, hid: int, roots: Sequence[int]) -> int:
        for root in roots:
            if hid in root_ids_of(self.index, root):
                return root
        raise ValueError(f"Heading {hid} lies outside selected roots {list(roots)}")

    def ordered_scope(self, roots: Sequence[int], visible: Callable[[int], bool]) -> List[int]:
        allowed = {hid for root in roots for hid in root_ids_of(self.index, root)}
        root_set = set(roots)

        def path_visible(hid: int) -> bool:
            cur = hid
            while cur is not None and cur in allowed:
                if not visible(cur):
                    return False
                if cur in root_set:
                    return True
                parent = self.headings[str(cur)].get("parent")
                cur = int(parent) if parent is not None else None
            return False

        return sorted((hid for hid in allowed if path_visible(hid)), key=lambda i: self.headings[str(i)].get("order", 0))

    def sections(self, roots: Sequence[int], ordered: Sequence[int], visible: Callable[[int], bool]) -> List[List[int]]:
        """Root introduction then one section per direct child, for any root depth/cardinality."""
        allowed = set(ordered)
        out: List[List[int]] = []
        for root in roots:
            if root in allowed:
                out.append([root])
            for child in self.under(root):
                if not visible(child):
                    continue
                ids = root_ids_of(self.index, child)
                section = [hid for hid in ordered if hid in ids]
                if section:
                    out.append(section)
        return out

    def manifest(self, roots: Sequence[int], ordered: Sequence[int]) -> dict:
        scope = set(ordered)
        assets = []
        for asset in load_asset_headings(self.kb_dir) or []:
            try:
                owner = int(asset.get("node"))
            except (TypeError, ValueError):
                continue
            if owner in scope:
                assets.append({k: asset.get(k) for k in ("id", "node", "type", "page", "img_path", "caption")})
        return {
            "root_ids": list(roots),
            "roots": [{"id": r, "title": self.title(r), "order": self.headings[str(r)].get("order", 0),
                       "heading_ids": [h for h in ordered if h in root_ids_of(self.index, r)]} for r in roots],
            "document_order": list(ordered),
            "scope_boundaries": [
                {"root_id": root, "first_heading_id": members[0], "last_heading_id": members[-1],
                 "start_order": self.headings[str(members[0])].get("order", 0),
                 "end_order": self.headings[str(members[-1])].get("order", 0)}
                for root in roots
                for members in [[h for h in ordered if h in root_ids_of(self.index, root)]] if members],
            "heading_tree": [{"id": h, "title": self.title(h),
                              "parent": self._rebased_parent(h, scope, roots),
                              "level": self.headings[str(h)].get("level"),
                              "rebased_level": self._rebased_level(h, roots)} for h in ordered],
            "asset_inventory": assets,
        }

    def _rebased_level(self, hid: int, roots: Sequence[int]) -> int:
        root = self.root_of(hid, roots)
        base = int(self.headings[str(root)].get("level", 1))
        return int(self.headings[str(hid)].get("level", base)) - base + 1

    def _rebased_parent(self, hid: int, scope: set[int], roots: Sequence[int]) -> int | None:
        if hid in roots:
            return None
        parent = self.headings[str(hid)].get("parent")
        parent = int(parent) if parent is not None else None
        while parent is not None and parent not in scope:
            row = self.headings.get(str(parent)) or {}
            parent = row.get("parent")
            parent = int(parent) if parent is not None else None
        return parent if parent is not None and self.root_of(parent, roots) == self.root_of(hid, roots) else None
