"""After the KG is built: the document's sections as a numbered tree, from which the user picks what the deck covers:
ticked in a checklist (`scope_picker`), or, where the terminal cannot draw one, typed.

The tree is the KB's structural index (TOC or detected headings), only the sections a lecture may present, down to
SCOPE_MENU_DEPTH levels. An answer like "2, 5-7, 12.3, 1.2-1.4" picks sections (a section whose parent is picked is
part of it), "-5.1" or "-5.1-5.3" leaves sections out of what is picked, "all" is the whole document ("all, -3" or just
"-3": all but that). An empty answer picks nothing and is asked again, so a stray Enter never starts a run. The answer
becomes a `root_heading` of paths ("Document > Chapter", left out: "!Document > Chapter"), so a title several documents
share still names the right section.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from src.rag.idea_candidates import scope_sentences
from src.rag.retrieve_content import load_structural_index, same_title
from src.utils.config import Config
from src.workflow import scope_picker
from src.workflow.scope import EXCLUDE_MARK, TITLE_TEXT, ScopeOrchestrator

PATH_SEP = " > "
ALL = "all"             # the answer for the whole document
EACH = "each"           # in an answer: one deck per top section
LEAVE_OUT = "-"         # opens a section number to leave out
TITLE_ROW = "0"         # the number of the row for the text under the document's title
TITLE_ROW_NOTE = "(text before the first heading)"


def with_text(kb_dir: Path, index: dict) -> set:
    """The headings that hold sentences a slide can draw on (a heading with nothing under it is not worth choosing)."""
    every = {int(h) for h in index.get("headings") or {}}
    return {int(h) for h, row in scope_sentences(Path(kb_dir), index, every).items() if row.get("sentences")}


def entries(index: dict, depth: int = 0, keep: Optional[set] = None) -> List[dict]:
    """[{num: "3.2", hid, title, pages, level}] in document order, top level first, `depth` levels deep; `keep`: only
    sections with one of these headings in them, and a first row for the text under the title when it is one of them."""
    depth = depth or Config.SCOPE_MENU_DEPTH
    scope = ScopeOrchestrator(index, Path("."))
    headings = scope.headings
    out: List[dict] = []

    def shown(ids: List[int]) -> List[int]:
        """The sections listed: an alias of its parent's title is replaced by its children."""
        out_ids: List[int] = []
        for hid in ids:
            if headings[str(hid)].get("alias") is not None:
                out_ids += shown(scope.children.get(hid, []))
            else:
                out_ids.append(hid)
        return out_ids

    def walk(ids: List[int], prefix: str, level: int) -> None:
        n = 0
        for hid in shown(ids):
            if not scope.visible(hid) or (keep is not None and not keep & _subtree(scope, hid)):
                continue
            n += 1
            num = f"{prefix}{n}"
            out.append({"num": num, "hid": hid, "title": scope.display(hid), "level": level,
                        "pages": len(headings[str(hid)].get("pages") or [])})
            if level < depth:
                walk(scope.children.get(hid, []), f"{num}.", level + 1)

    if keep is not None and scope.title_id in keep:
        out.append({"num": TITLE_ROW, "hid": scope.title_id, "title": f"{scope.display(scope.title_id)} {TITLE_ROW_NOTE}",
                    "level": 1, "pages": None})
    walk(scope.tops(), "", 1)
    return out


def render(items: List[dict]) -> str:
    """The numbered tree, one section a line, its page count on the right."""
    width = max((len(e["num"]) + 2 * (e["level"] - 1) for e in items), default=0)
    return "\n".join(f"  {'  ' * (e['level'] - 1)}{e['num']:<{width - 2 * (e['level'] - 1)}}  {e['title']}"
                     f"{scope_picker.pages_note(e)}" for e in items)


def parse(answer: str, items: List[dict], index: dict) -> Tuple[Optional[List[int]], List[int]]:
    """(heading ids picked, heading ids left out) by an answer ("2, 5-7, 12.3, -5.1"), in document order; a section
    inside another picked one is part of it. Picked None: the whole document ("all", or only sections to leave out).
    ValueError says what cannot be read: an empty answer picks nothing."""
    tokens = [t for t in re.split(r"[,;\s]+", answer.strip()) if t]
    if not tokens:
        raise ValueError("no section chosen")
    at = {e["num"]: i for i, e in enumerate(items)}

    def sections(token: str) -> List[int]:
        first, _, last = token.partition("-")
        if first not in at or (last and last not in at):
            raise ValueError(f"no section numbered '{token}'")
        a, b = at[first], at[last] if last else at[first]
        if b < a:
            raise ValueError(f"'{token}' runs backwards")
        return [items[i]["hid"] for i in range(a, b + 1) if not last or items[i]["level"] == items[a]["level"]]

    picked: List[int] = []
    left: List[int] = []
    whole = False
    for token in tokens:
        if token.casefold() == ALL:
            whole = True
        elif token.startswith(LEAVE_OUT):
            left += sections(token[len(LEAVE_OUT):])
        else:
            picked += sections(token)
    scope = ScopeOrchestrator(index, Path("."))
    order = {e["hid"]: i for i, e in enumerate(items)}
    left = sorted(dict.fromkeys(left), key=order.get)
    if whole or not picked:
        if {e["hid"] for e in items if e["level"] == 1} <= set(left):
            raise ValueError("every section is left out")
        return None, left
    picked = list(dict.fromkeys(picked))
    picked = [h for h in picked if not any(h != p and h in _subtree(scope, p) for p in picked)]
    outside = [h for h in left if not any(h in _subtree(scope, p) for p in picked)]
    if outside:
        raise ValueError(f"'{next(e['num'] for e in items if e['hid'] == outside[0])}' is not in a chosen section")
    kept = [h for h in picked if h not in left]
    if not kept:
        raise ValueError("every chosen section is left out")
    return sorted(kept, key=order.get), [h for h in left if h not in picked]


def _subtree(scope: ScopeOrchestrator, hid: int) -> set:
    if hid == scope.title_id:                 # its sections are rows of their own
        return {hid}
    out, stack = {hid}, [hid]
    while stack:
        for child in scope.children.get(stack.pop(), []):
            if child not in out:
                out.add(child)
                stack.append(child)
    return out


def root_heading(index: dict, ids: List[int], left: Sequence[int] = ()) -> str:
    """The picked sections as a `root_heading`: each one's path of titles from its top-level section, `;`-separated,
    then the sections left out, each marked EXCLUDE_MARK."""
    scope = ScopeOrchestrator(index, Path("."))
    paths = []
    for hid in [*ids, *left]:
        if hid == scope.title_id:
            paths.append((EXCLUDE_MARK if hid in left else "") + TITLE_TEXT)
            continue
        chain, cur = [], hid
        while cur is not None and cur != scope.title_id and str(cur) in scope.headings:
            parent = scope.headings[str(cur)].get("parent")
            parent = int(parent) if parent is not None else None
            among = _subtree(scope, parent) - {parent} if parent is not None and parent != scope.title_id else None
            same = same_title(index, scope.title(cur), among)     # a repeated title: which one of them
            nth = same.index(cur) + 1 if cur in same else 1
            chain.append(scope.title(cur) + (f" #{nth}" if nth > 1 else ""))
            cur = parent
        paths.append((EXCLUDE_MARK if hid in left else "") + PATH_SEP.join(reversed(chain)))
    return ";".join(paths)


def decks(index: dict, items: List[dict], ids: Optional[List[int]], left: List[int], each: bool) -> List[Optional[str]]:
    """The `root_heading` of each deck: one for all that is chosen, or (`each`) one per top section with a choice in it."""
    if not each:
        return [root_heading(index, ids or [], left) or None]
    scope = ScopeOrchestrator(index, Path("."))
    tops = [e["hid"] for e in items if e["level"] == 1]
    out = []
    for top in tops:
        inside = _subtree(scope, top)
        picks = [top] if ids is None else [h for h in ids if h in inside]
        if picks and top not in left:
            out.append(root_heading(index, picks, [h for h in left if h in inside]))
    return out


def ask(kb_dir: Path, read: Optional[Callable[[str], str]] = None, show: Callable[[str], None] = print,
        collection: bool = False) -> List[Optional[str]]:
    """Show the KB's sections and read which the decks cover: one `root_heading` per deck (None: the whole document).
    The checklist (`scope_picker`) where the terminal can draw it, else a typed answer. `collection`: the top sections
    are separate documents, so one deck per document is what the checklist starts with."""
    index = load_structural_index(Path(kb_dir))
    items = entries(index, keep=with_text(kb_dir, index))
    if all(e["num"] == TITLE_ROW for e in items):             # nothing to choose between
        return [None]
    titled = lambda ids: "; ".join(f"{e['num']} {e['title']}" for h in ids for e in items if e["hid"] == h)
    if read is None and scope_picker.available():
        label = "One deck per document" if collection else "One deck per top section"
        ids, left, each = scope_picker.pick(items, collection, label)
    else:
        ids, left, each = _typed(items, index, read or input, show)
    show("  Chosen: " + ("the whole document" if ids is None else titled(ids)))
    if left:
        show("  Left out: " + titled(left))
    chosen = decks(index, items, ids, left, each)
    show(f"  Decks: {len(chosen)}")
    return chosen


def _typed(items: List[dict], index: dict, read: Callable[[str], str], show: Callable[[str], None]):
    """(picked, left, one deck per top section) from an answer typed at a prompt, asked again until it can be read."""
    show(f"\nSections of the document ({len(items)} shown):\n{render(items)}\n")
    while True:
        try:
            answer = read(f"Sections for the deck (e.g. 1, 2.1-2.3, -2.2; {ALL}; {ALL}, -3; add {EACH} for one deck per "
                          f"top section): ")
            words = [t for t in re.split(r"[,;\s]+", answer.strip()) if t]
            each = EACH in [w.casefold() for w in words]
            return (*parse(" ".join(w for w in words if w.casefold() != EACH), items, index), each)
        except EOFError:
            raise SystemExit("No section chosen.") from None
        except ValueError as exc:
            show(f"  {exc}; try again.")
