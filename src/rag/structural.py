from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import networkx as nx
from rapidfuzz import fuzz

from src.rag.kb_files import load_structural_index, load_text_chunks
from src.rag.kg_schema import asset_node_fields, clean_type, is_asset_node, is_semantic_node, name_patterns

TITLE_ID = -1
HEAD_MARK = "⁣"
STRUCT_MARK = "[[p{page}|n{node}]] "
STRUCT_MARK_RE = re.compile(r"\[\[p(\d+)\|n(-?\d+)\]\]")

_NAV_HEADING = re.compile(
    r"^\s*(cover|table of contents|contents|index|list of (figures|tables)|copyright|dedication)\s*$",
    re.IGNORECASE,
)

_NUM_PREFIX = re.compile(
    r"^(chapter|part|section|appendix|chap|sec|ch|pt)\s+([0-9]+|[ivxlcdm]{1,6}|[a-z])\s+",
    re.IGNORECASE,
)
_NUM_PREFIX_RAW = re.compile(
    r"^\s*(chapter|part|section|appendix|chap|sec|ch|pt)\s+([0-9]+|[ivxlcdm]{1,6}|[a-z])\b[\s:.\-]*",
    re.IGNORECASE,
)


def _clean_chars(s: str) -> str:
    s = (s or "").casefold()
    s = "".join(ch if (ch == " " or unicodedata.category(ch)[0] in ("L", "N")) else " " for ch in s)
    return re.sub(r"\s+", " ", s).strip()


def norm_title(s: str) -> str:
    s = _clean_chars(s)
    return _NUM_PREFIX.sub("", s).strip() or s


def _display_variant(title: str) -> str:
    return _NUM_PREFIX_RAW.sub("", title).strip() or title


def _is_noise_heading(nt: str) -> bool:
    if len(nt) <= 1:
        return True
    toks = nt.split()
    return bool(toks) and all(len(x) == 1 for x in toks)


_BULLET_RE = re.compile(r"^\s*[•·▪◦‣•‣◦⁃∙\-\*]\s+")
_CAPTION_SERIAL_RE = re.compile(r"^\S+\s+\d+[.\-]\d+")
_CAPTION_DOT_RE = re.compile(r"^[A-Za-z]\S*\s+\d+\.\s+\S")
_RUNNING_HEADER_RE = re.compile(r"^\d+\s*\|\s*\S")


def _looks_bullet(t: str) -> bool:
    return bool(_BULLET_RE.match(t))


def _looks_caption(t: str) -> bool:
    return bool(_CAPTION_SERIAL_RE.match(t) or _CAPTION_DOT_RE.match(t))


def _looks_running_header(t: str) -> bool:
    """A page-number-plus-separator prefix (e.g. "4 | Chapter 1: The Machine Learning Landscape") is a
    running page header/footer repeated on every page of a chapter, not a heading -- each occurrence has
    a different leading number so the exact-text "running" dedup in extract_headings never catches it,
    and its title portion is real enough to slip past every other _looks_* check and even fuzzy-match an
    unrelated TOC entry once the real one has already been consumed."""
    return bool(_RUNNING_HEADER_RE.match(t.strip()))


def _looks_code(t: str) -> bool:
    if re.search(r"[{};=]", t):
        return True
    if re.search(r"\w\(", t):
        return True
    if re.search(r">\s|>>>|<\w+>", t):
        return True
    if re.search(r"[a-z][A-Z]", t) and ("," in t or "(" in t):
        return True
    return False


def _looks_titlecase(t: str) -> bool:
    words = [w for w in re.findall(r"[^\W\d_]+", t, re.UNICODE) if len(w) > 2]
    if len(words) < 3:
        return False
    cap = sum(1 for w in words if w[0].isupper())
    return cap / len(words) >= 0.6


def _looks_prose(t: str) -> bool:
    s = t.rstrip()
    w = s.split()
    if s.endswith(("!", "?")) and len(w) > 4:
        return True
    if s.endswith((".", ":", ";")) and len(w) > 6:
        return True
    return len(w) > 10 and not _looks_titlecase(s)


def _first_cased(t: str) -> str | None:
    for ch in t:
        if ch.isalpha() and (ch.islower() or ch.isupper()):
            return ch
    return None


def _looks_fragment(t: str) -> bool:
    c = _first_cased(t)
    if c is None or not c.islower():
        return False
    s = t.rstrip()
    if s.endswith((".", "!", "?", "…")):
        return True
    return len(s.split()) <= 3 and not any(ch.isupper() for ch in s)


def _is_typographic_noise(title: str) -> bool:
    return (_looks_bullet(title) or _looks_caption(title) or _looks_code(title)
            or _looks_prose(title) or _looks_fragment(title) or _looks_running_header(title))


def _neighbor_looks_code(items: list[dict], i: int) -> bool:
    """True if the text item immediately AFTER item `i` is plain body text (no text_level, i.e. not
    itself a detected heading) that looks like source code -- a short imperative phrase directly
    introducing a code line (e.g. a "# Load the data" style comment the parser bolded) is a code
    comment, not a heading, even though the phrase itself carries none of _is_typographic_noise's usual
    signals. Deliberately NEXT-only, not prev-or-next: a genuine section heading commonly follows a code
    block with no intro sentence (prev-is-code is a weak signal, would false-positive on those), but a
    real heading is essentially never immediately followed by raw code with zero introductory prose."""
    j = i + 1
    if j >= len(items):
        return False
    nb = items[j]
    return bool(nb.get("type") == "text" and not nb.get("text_level")
                and _looks_code(nb.get("text") or ""))


_ROMAN_RE = re.compile(r"^(?=[ivxlcdm])m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")


def _is_enum(tok: str) -> bool:
    return tok.isdigit() or bool(_ROMAN_RE.match(tok))


def _family_key(title: str) -> tuple[tuple[str, ...], bool]:
    # Uses _clean_chars, not norm_title: norm_title strips a leading "chapter/part/section N" marker,
    # deleting the numeral this function needs for word-prefixed titles like "Part I. ...".
    lead: list[str] = []
    has_num = False
    for t in _clean_chars(title).split():
        if _is_enum(t):
            has_num = True
            break
        lead.append(t)
    return tuple(lead), has_num


def _numeric_families(admitted: list[dict], min_members: int = 3) -> dict[tuple[str, ...], int]:
    count: dict[tuple[str, ...], int] = defaultdict(int)
    levels: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for h in admitted:
        lead, has_num = _family_key(h["title"])
        if has_num and lead:
            count[lead] += 1
            levels[lead].append(h["level"])
    out: dict[tuple[str, ...], int] = {}
    for lead, c in count.items():
        if c >= min_members:
            lv = defaultdict(int)
            for x in levels[lead]:
                lv[x] += 1
            out[lead] = max(lv.items(), key=lambda kv: kv[1])[0]
    return out


def _families_nesting(
    members_by_family: dict[tuple[str, ...], list[float]],
) -> dict[tuple[str, ...], int]:
    """Rank detected numbered families by nesting depth using order-containment, not titles/keywords:
    a family with FEWER members whose span (first-to-last position) covers another family's span is the
    coarser/outer one (e.g. 2 "Part"s covering 16 "Chapter"s). A family with NO containment relation to
    any other is OMITTED from the returned dict (not defaulted to depth 0) -- callers must treat a
    missing key as "no evidence", distinct from a family whose depth-0 status was actually established
    by containment, and fall back to another signal for it instead of assuming it's the top level."""
    keys = list(members_by_family)
    edges: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for a in keys:
        pos_a = members_by_family[a]
        for b in keys:
            if a == b:
                continue
            pos_b = members_by_family[b]
            if len(pos_a) < len(pos_b) and min(pos_a) <= min(pos_b) and max(pos_a) <= max(pos_b):
                edges.append((a, b))
    touched = {n for e in edges for n in e}
    depth = {k: 0 for k in touched}
    changed = True
    while changed:
        changed = False
        for a, b in edges:
            if depth[b] < depth[a] + 1:
                depth[b] = depth[a] + 1
                changed = True
    return depth


def _complete_siblings(items: list[dict], admitted: list[dict]) -> list[dict]:
    families = _numeric_families(admitted)
    if not families:
        return admitted
    have = {h["item_idx"] for h in admitted}
    for i, it in enumerate(items):
        if i in have or it.get("type") != "text" or it.get("text_level"):
            continue
        t = re.sub(r"\s+", " ", it.get("text") or "").strip()
        if not t or len(t.split()) > 8:
            continue
        lead, has_num = _family_key(t)
        if has_num and lead in families:
            pg = it.get("page_idx", 0)
            admitted.append({"item_idx": i, "page": int(pg) if str(pg).isdigit() else 0,
                             "level": families[lead], "title": t})
    admitted.sort(key=lambda x: x["item_idx"])
    return admitted


def _despace(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _toc_match(candidate_title: str, toc_keys: dict[str, int]) -> str | None:
    """Despace-equality -> despace-substring -> rapidfuzz partial_ratio>=80, in that order (HiPS
    arXiv:2509.00909 App. C.3, minus its word-overlap fallback: HiPS bounds that tier to one expected
    page, we scan the whole document, so a single shared short word gives false positives). None if
    nothing clears the bar."""
    if not toc_keys:
        return None
    cand = _clean_chars(candidate_title)
    cand_ds = _despace(cand)
    if len(cand_ds) < 3:
        return None
    for k in toc_keys:
        if _despace(k) == cand_ds:
            return k
    for k in sorted(toc_keys, key=len, reverse=True):
        k_ds = _despace(k)
        if k_ds and (k_ds in cand_ds or cand_ds in k_ds):
            return k
    best_key, best_score = None, 0.0
    for k in toc_keys:
        score = fuzz.partial_ratio(cand, k)
        if score > best_score:
            best_key, best_score = k, score
    return best_key if best_key is not None and best_score >= 80 else None


def _complete_from_toc(items: list[dict], admitted: list[dict],
                       toc_levels: dict[str, int] | None) -> list[dict]:
    if not toc_levels:
        return admitted
    have = {h["item_idx"] for h in admitted}
    remaining = dict(toc_levels)
    for i, it in enumerate(items):
        if i in have or it.get("type") != "text" or it.get("text_level"):
            continue
        t = re.sub(r"\s+", " ", it.get("text") or "").strip()
        if not t or len(t.split()) > 12:
            continue
        nt = _clean_chars(t)
        if nt in remaining:
            key = nt
        elif _is_typographic_noise(t):
            key = None
        else:
            key = _toc_match(t, remaining)
        if key is None:
            continue
        lv = remaining.pop(key)
        if lv:
            pg = it.get("page_idx", 0)
            admitted.append({"item_idx": i, "page": int(pg) if str(pg).isdigit() else 0,
                             "level": int(lv), "title": t})
            have.add(i)
    admitted.sort(key=lambda x: x["item_idx"])
    if remaining:
        print(f"[structural] {len(remaining)} TOC entries have no matching body heading "
              f"(kept only in toc_levels, not synthesized -- see PROGRESS.md)")
    return admitted


def extract_headings(items: list[dict], toc_levels: dict[str, int] | None = None) -> list[dict]:
    tocset = set(toc_levels or ())
    hs: list[dict] = []
    for i, it in enumerate(items):
        if it.get("type") == "text" and it.get("text_level"):
            title = re.sub(r"\s+", " ", it.get("text") or "").strip()
            if not title:
                continue
            if _NAV_HEADING.match(title):
                continue
            nt = _clean_chars(title)
            if _is_noise_heading(nt):
                continue
            pg = it.get("page_idx", 0)
            pg = int(pg) if str(pg).isdigit() else 0
            if pg <= 1 and nt not in tocset:
                continue
            if nt not in tocset and _is_typographic_noise(title):
                continue
            if nt not in tocset and _neighbor_looks_code(items, i):
                continue
            # Prefer the TOC's authoritative level when this title is in the TOC; else the parser's text_level.
            level = toc_levels[nt] if (toc_levels and nt in tocset) else int(it["text_level"])
            hs.append({"item_idx": i, "page": pg, "level": level, "title": title})
    pages_by: dict[str, set] = defaultdict(set)
    for h in hs:
        pages_by[_clean_chars(h["title"])].add(h["page"])
    running = {nt for nt, pgs in pages_by.items()
              if len(pgs) >= 4 and (max(pgs) - min(pgs) + 1) / len(pgs) < 7.0}
    out: list[dict] = []
    seen_toc_running: set[str] = set()
    for h in sorted(hs, key=lambda x: x["item_idx"]):
        nt = _clean_chars(h["title"])
        if nt in running:
            if nt in tocset and nt not in seen_toc_running:
                seen_toc_running.add(nt)
                out.append(h)
            continue
        out.append(h)
    out = _complete_from_toc(items, out, toc_levels)
    return _complete_siblings(items, out)


_MIN_OWN_WORDS = 15
_MIN_UNMATCHED_WORDS = 60
_MIN_RELIABLE_TOC_ENTRIES = 20


def _own_words(nodes: list[dict], per_item: list[dict], items: list[dict]) -> dict[int, int]:
    own_words: dict[int, int] = defaultdict(int)
    for i, it in enumerate(items):
        if it.get("type") == "text":
            own_words[per_item[i]["node"]] += len((it.get("text") or "").split())
    return own_words


def _reattach_pruned(nodes: list[dict], per_item: list[dict],
                     to_prune: set[int]) -> tuple[list[dict], list[dict]]:
    """Remove nodes in `to_prune`, reattaching their children AND their own per_item content up to the
    nearest kept ancestor, then recompute level from real tree depth. Shared by _prune_contentless
    (drops an empty divider parent) and _prune_unmatched_leaves (drops a thin leaf heading that doesn't
    match a reliable TOC) -- same reattachment mechanics, different criteria for `to_prune`."""
    if not to_prune:
        return nodes, per_item
    by_id = {n["id"]: n for n in nodes}

    def _resolve(pid: int) -> int:
        while pid in to_prune:
            pid = by_id[pid]["parent"]
        return pid

    kept = [n for n in nodes if n["id"] not in to_prune]
    for n in kept:
        n["parent"] = _resolve(n["parent"])
    for entry in per_item:
        entry["node"] = _resolve(entry["node"])

    depth_cache: dict[int, int] = {}

    def _depth(nid: int) -> int:
        if nid == TITLE_ID:
            return 0
        if nid not in depth_cache:
            depth_cache[nid] = 1 + _depth(by_id[nid]["parent"])
        return depth_cache[nid]

    for n in kept:
        n["level"] = _depth(n["id"])
    return kept, per_item


def _prune_contentless(nodes: list[dict], per_item: list[dict],
                       items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Drop a node if it has children but ~0 words of its own text (a pure divider page, e.g. "Part I"),
    reattaching its children to its own parent and recomputing level from real tree depth."""
    own_words = _own_words(nodes, per_item, items)
    has_child = {n["parent"] for n in nodes if n["parent"] != TITLE_ID}
    to_prune = {n["id"] for n in nodes
               if n["id"] in has_child and own_words.get(n["id"], 0) < _MIN_OWN_WORDS}
    return _reattach_pruned(nodes, per_item, to_prune)


def _prune_unmatched_leaves(nodes: list[dict], per_item: list[dict], items: list[dict],
                            toc_levels: dict[str, int] | None) -> tuple[list[dict], list[dict]]:
    """Drop a LEAF node (no children) if it has thin own content AND doesn't match any entry in a
    reliable/thorough TOC (toc_levels) -- a real, well-indexed TOC not mentioning it at all, combined
    with barely any content of its own, is strong evidence it's a false-positive heading (e.g. a short
    standalone bold/italic line the parser mistagged, like a typographic-convention example label
    "Constant width" in an O'Reilly book, or a code comment mistagged via its distinct font).

    Only acts when toc_levels is large enough to trust omissions as real evidence (see
    _MIN_RELIABLE_TOC_ENTRIES) -- a sparse/LLM-guessed TOC omitting something is much weaker evidence
    than a thorough one (verified: naive matching without a reliable-TOC guard, or without the fuzzy
    _toc_match below, risks catching real headings like "PART II" whose body text doesn't literally
    equal its TOC entry).

    Deliberately narrower than _prune_contentless: leaf-only (a node WITH children is much more likely a
    genuine section the TOC-author simply didn't index), reuses _toc_match (the same fuzzy matcher TOC-
    based level assignment already relies on) instead of exact matching, and skips titles already caught
    by _is_typographic_noise (that's a different, already-handled signal)."""
    if not toc_levels or len(toc_levels) < _MIN_RELIABLE_TOC_ENTRIES:
        return nodes, per_item
    own_words = _own_words(nodes, per_item, items)
    has_child = {n["parent"] for n in nodes if n["parent"] != TITLE_ID}
    to_prune = {
        n["id"] for n in nodes
        if n["id"] not in has_child
        and own_words.get(n["id"], 0) < _MIN_UNMATCHED_WORDS
        and not _is_typographic_noise(n["title"])
        and _toc_match(n["title"], toc_levels) is None
    }
    return _reattach_pruned(nodes, per_item, to_prune)


def build_heading_tree(items: list[dict],
                       toc_levels: dict[str, int] | None = None) -> tuple[list[dict], list[dict]]:
    headings = extract_headings(items, toc_levels)
    hd_by_item = {h["item_idx"]: h for h in headings}

    nodes: list[dict] = []
    stack: list[tuple[int, int]] = []
    per_item: list[dict] = []
    for i, it in enumerate(items):
        if i in hd_by_item:
            h = hd_by_item[i]
            lv = h["level"]
            while stack and stack[-1][0] >= lv:
                stack.pop()
            parent = stack[-1][1] if stack else TITLE_ID
            nid = len(nodes)
            nodes.append({"id": nid, "level": lv, "title": h["title"][:140], "parent": parent,
                          "order": nid, "start_page": h["page"], "pages": set()})
            stack.append((lv, nid))
        cur = stack[-1][1] if stack else TITLE_ID
        pg = it.get("page_idx", 0)
        pg = int(pg) if str(pg).isdigit() else 0
        if cur >= 0:
            nodes[cur]["pages"].add(pg)
        per_item.append({"node": cur, "page": pg})
    nodes, per_item = _prune_contentless(nodes, per_item, items)
    return _prune_unmatched_leaves(nodes, per_item, items, toc_levels)


def mark(per_item: list[dict], i: int) -> str:
    if not per_item:
        return ""
    s = per_item[i]
    return STRUCT_MARK.format(page=s["page"], node=s["node"])


def _leading_number(title: str) -> int | None:
    for t in norm_title(title).split():
        if t.isdigit():
            return int(t)
        if _ROMAN_RE.match(t):
            return None
    return None


def _group_boundaries(members: list[dict]) -> list[int]:
    """Indices into `members` (sorted by order) where a numbering reset starts a new group."""
    starts = [0]
    prev = _leading_number(members[0]["title"])
    for i in range(1, len(members)):
        num = _leading_number(members[i]["title"])
        if num is not None and prev is not None and num <= prev:
            starts.append(i)
        if num is not None:
            prev = num
    return starts


def _nearby_title_candidate(items: list[dict], start_page: int, lead: tuple[str, ...]) -> str | None:
    """Look for a real title just above the group's first member, on its own page: scan `items` in
    document order and stop as soon as a same-family match is hit (the chapter heading itself), so
    this can never wander past it or pick the chapter heading up as its own title. Among what's left
    on that exact page (untagged, short, non-prose), keep the one closest to the chapter -- matches
    the layout confirmed by rendering the actual PDF page: the untagged title line sits immediately
    above 'Chapter 1' on the same page."""
    best = None
    for it in items:
        if it.get("type") != "text":
            continue
        pg = it.get("page_idx", 0)
        pg = int(pg) if str(pg).isdigit() else 0
        if pg != start_page:
            continue
        t = re.sub(r"\s+", " ", it.get("text") or "").strip()
        if not t:
            continue
        tlead, has_num = _family_key(t)
        if has_num and tlead == lead:
            break
        if it.get("text_level"):
            continue
        if len(t.split()) > 20 or _is_typographic_noise(t):
            continue
        best = t
    return best


def _synthesize_group_parents(nodes: list[dict], items: list[dict],
                              fams: dict[tuple[str, ...], int]) -> list[dict]:
    """For each numbered family whose members have no existing higher-level heading covering a
    numbering-reset boundary (e.g. a 'Part'/story title never tagged in the source), synthesize a
    parent node so the family still nests correctly instead of flattening under the document title.
    Prefers a real title recovered via `_nearby_title_candidate`; falls back to a generic,
    page-anchored placeholder when no safe candidate is found on that page."""
    if not fams:
        return nodes
    next_id = max((n["id"] for n in nodes), default=-1) + 1
    synth: list[dict] = []
    for lead, family_level in fams.items():
        members = sorted(
            [n for n in nodes if _family_key(n["title"]) == (lead, True)],
            key=lambda n: n["order"])
        if len(members) < 2:
            continue
        starts = _group_boundaries(members)
        if len(starts) <= 1:
            # No numbering reset -> this family (e.g. "CHAPTER N") is the top layer.
            for m in members:
                m["level"] = 1
            # A heading sandwiched between two members of the top family is pushed to >= level 2
            # (max, never lowering an already-deeper heading).
            member_ids = {id(m) for m in members}
            lo, hi = members[0]["order"], members[-1]["order"]
            for n in nodes:
                if id(n) not in member_ids and lo <= n["order"] <= hi:
                    n["level"] = max(n["level"], 2)
            continue
        for m in members:
            m["level"] = family_level
        parent_level = family_level - 1
        for gi, mi in enumerate(starts):
            group_start = members[mi]
            lo_order = members[mi - 1]["order"] if gi > 0 else -1
            hi_order = group_start["order"]
            has_parent = any(n["level"] < family_level and lo_order < n["order"] < hi_order
                             for n in nodes)
            if has_parent:
                continue
            end = starts[gi + 1] if gi + 1 < len(starts) else len(members)
            pages: set = set()
            for gm in members[mi:end]:
                pages |= gm.get("pages") or set()
            pages.add(group_start["start_page"])
            title = (_nearby_title_candidate(items, group_start["start_page"], lead)
                    or f"(chưa rõ tiêu đề, tr.{group_start['start_page']})")
            synth.append({"id": next_id, "level": parent_level, "title": title,
                         "parent": TITLE_ID, "order": hi_order - 0.5,
                         "start_page": group_start["start_page"], "pages": pages})
            next_id += 1
    return nodes + synth


def _assign_parents(order_sorted: list[dict]) -> None:
    stack: list[tuple[int, int]] = []
    for n in order_sorted:
        lv = n["level"]
        while stack and stack[-1][0] >= lv:
            stack.pop()
        n["parent"] = stack[-1][1] if stack else TITLE_ID
        stack.append((lv, n["id"]))


def build_structural_index(items: list[dict], title: str,
                           toc_levels: dict[str, int] | None = None) -> dict[str, Any]:
    nodes, _ = build_heading_tree(items, toc_levels)

    if toc_levels:
        order_sorted = sorted(nodes, key=lambda x: x["order"])
        # each TOC entry matches at most one node (popped from `remaining`)
        remaining = dict(toc_levels)
        matched_key: dict[int, str] = {}
        for n in order_sorted:
            nt = _clean_chars(n["title"])
            if nt in remaining:
                key = nt
            elif _is_typographic_noise(n["title"]):
                key = None
            else:
                key = _toc_match(n["title"], remaining)
            if key is not None:
                matched_key[n["id"]] = key
                n["level"] = int(remaining.pop(key))
        top1 = {t for t, lv in toc_levels.items() if int(lv) == 1}
        tops = [pos for pos, n in enumerate(order_sorted) if matched_key.get(n["id"]) in top1]
        first_top = tops[0] if tops else None
        for pos, n in enumerate(order_sorted):
            if n["id"] not in matched_key and first_top is not None and pos >= first_top:
                n["level"] = max(2, int(n["level"]))
        _assign_parents(order_sorted)
    else:
        fams = _numeric_families(nodes)
        nodes = _synthesize_group_parents(nodes, items, fams)
        _assign_parents(sorted(nodes, key=lambda x: x["order"]))

    return _nodes_to_index(nodes, title)


def _nodes_to_index(nodes: list[dict], title: str) -> dict[str, Any]:
    """Shared tail: turn ready nodes (id/title/level/parent/order/pages/start_page) into the
    structural_index dict, with display-variant dedup, multi-parent disambiguation, and
    title-fold. Used by both build_structural_index and build_structural_index_from_toc."""
    all_pages = sorted({p for n in nodes for p in n["pages"]})
    title_norm = norm_title(title)
    node_by_id = {n["id"]: n for n in nodes}
    headings: dict[int, dict] = {
        TITLE_ID: {"title": title, "display": title[:120], "level": 0, "parent": None,
                   "order": -1, "pages": all_pages},
    }

    variants: dict[str, list[str]] = defaultdict(list)
    parents_of_norm: dict[str, set] = defaultdict(set)
    for n in nodes:
        nn = norm_title(n["title"])
        variants[nn].append(_display_variant(n["title"]))
        p = n["parent"]
        parents_of_norm[nn].add(norm_title(node_by_id[p]["title"]) if p in node_by_id else "")

    def _best(ts: list[str]) -> str:
        return sorted(set(ts), key=lambda t: (t.isupper(), len(t), t))[0]

    disp_base = {nn: _best(ts) for nn, ts in variants.items()}
    multiparent = {nn for nn, ps in parents_of_norm.items() if len(ps) > 1}

    for n in sorted(nodes, key=lambda x: x["order"]):
        nn = norm_title(n["title"])
        parent = n["parent"]
        pg0 = min(n["pages"]) if n["pages"] else n["start_page"]

        if parent in node_by_id and nn == norm_title(node_by_id[parent]["title"]):
            par = headings[parent]
            headings[n["id"]] = {"title": par["title"], "display": par["display"],
                                 "level": par["level"], "parent": par["parent"],
                                 "order": n["order"], "pages": sorted(n["pages"])}
            continue

        if title_norm and pg0 <= 2 and (nn == title_norm or nn in title_norm or title_norm in nn):
            headings[n["id"]] = {"title": title, "display": title[:120], "level": 0,
                                 "parent": None, "order": n["order"], "pages": sorted(n["pages"])}
            continue

        disp = disp_base[nn]
        if nn in multiparent and parent in node_by_id:
            parent_disp = disp_base.get(norm_title(node_by_id[parent]["title"]),
                                        node_by_id[parent]["title"])
            disp = f"{disp} ({parent_disp})"
        headings[n["id"]] = {
            "title": n["title"],
            "display": disp[:120],
            "level": max(1, int(n["level"])),
            "parent": n["parent"],
            "order": n["order"],
            "pages": sorted(n["pages"]),
        }
    return {"title": title, "title_id": TITLE_ID, "headings": headings}


# ---------------------------------------------------------------------------
# TOC-backbone path: the embedded PDF outline defines heading set/levels/pages authoritatively; each
# content item belongs to the nearest preceding heading start.
# ---------------------------------------------------------------------------

def _locate_toc_headings(items: list[dict], toc_entries: list[tuple[int, str, int]],
                         page_offset: int = 0) -> list[int]:
    """Return a boundary item-index for each TOC entry (same length/order as toc_entries).

    Titles are matched STRICTLY on the entry's OWN TOC page (`page_i - 1 - page_offset`), never a
    cross-page window, so a duplicate title elsewhere (HOML's "The Machine Learning Landscape" at p24
    vs its exercise-solution at p498) can't steal the boundary.

    Headings that SHARE a page are resolved as a GROUP so a title-collision or a title-miss can't
    collapse one onto the next: each entry's best match is scored by tier (exact > substring > fuzzy),
    same-item collisions are won by the stronger tier, and any un-anchored entry is INTERPOLATED into
    the page's item span between its anchored neighbours — so each same-page heading gets a distinct,
    non-empty slice (fixes "What Is Machine Learning?" fuzzy-matching the "Why Use Machine Learning?"
    item and ending up empty). Monotonic non-decreasing; assumes items are in document order."""
    n_items = len(items)
    item_page: list[int] = []
    item_norm: list[str] = []
    for it in items:
        pg = it.get("page_idx", 0)
        item_page.append(int(pg) if str(pg).isdigit() else 0)
        txt = it.get("text") if it.get("type") == "text" else ""
        item_norm.append(_clean_chars(txt or ""))

    def best_match(raw_title: str, target: int, lo: int) -> tuple[int | None, int]:
        """(pos, tier) of the strongest title match on page `target` at index >= lo.
        tier 3=exact, 2=substring, 1=fuzzy, 0=none."""
        nt = _clean_chars(raw_title)
        if len(nt) < 3:
            return (None, 0)
        best_pos, best_tier = None, 0
        for j in range(lo, n_items):
            if item_page[j] > target:
                break
            if item_page[j] != target or not item_norm[j]:
                continue
            cj = item_norm[j]
            tier = 3 if nt == cj else 2 if nt in cj else (
                1 if len(cj) >= 3 and _toc_match(raw_title, {cj: 0}) is not None else 0)
            if tier > best_tier:
                best_pos, best_tier = j, tier
                if tier == 3:
                    break
        return (best_pos, best_tier)

    def page_span(target: int, lo: int) -> tuple[int | None, int]:
        A: int | None = None
        B = n_items
        for j in range(lo, n_items):
            if item_page[j] == target and A is None:
                A = j
            if item_page[j] > target:
                B = j
                break
        return A, B

    boundaries = [0] * len(toc_entries)
    prev = 0
    i = 0
    while i < len(toc_entries):
        target = max(0, int(toc_entries[i][2]) - 1 - page_offset)
        g = i
        while g < len(toc_entries) and max(0, int(toc_entries[g][2]) - 1 - page_offset) == target:
            g += 1
        group = list(range(i, g))  # entry indices sharing this TOC page
        A, B = page_span(target, prev)

        matched = {gi: best_match(toc_entries[gi][1], target, prev) for gi in group}
        matched = {gi: mt for gi, mt in matched.items() if mt[0] is not None and mt[0] >= prev}
        # resolve same-item collisions: strongest tier (then earliest entry) wins the item
        by_pos: dict[int, tuple[int, int]] = {}
        for gi in group:
            if gi in matched:
                p, t = matched[gi]
                if p not in by_pos or t > by_pos[p][1]:
                    by_pos[p] = (gi, t)
        winners = {gi for gi, _t in by_pos.values()}
        # keep anchors strictly increasing in entry order
        anchors: dict[int, int] = {}
        last = prev - 1
        for gi in group:
            if gi in winners and matched[gi][0] > last:
                anchors[gi] = matched[gi][0]
                last = matched[gi][0]

        cursor = prev
        gp = 0
        while gp < len(group):
            gi = group[gp]
            if gi in anchors:
                boundaries[gi] = max(anchors[gi], cursor)
                cursor = boundaries[gi]
                gp += 1
            else:
                # interpolate the run of un-anchored entries evenly across (page-start .. next anchor)
                run_end = gp
                while run_end < len(group) and group[run_end] not in anchors:
                    run_end += 1
                hi = anchors[group[run_end]] if run_end < len(group) else B
                lo_b = max(cursor, A if A is not None else cursor)
                m = run_end - gp
                span = max(0, hi - lo_b)
                for r in range(m):
                    ridx = group[gp + r]
                    b = lo_b + (span * r) // m if span > 0 else cursor
                    boundaries[ridx] = max(b, cursor)
                    cursor = boundaries[ridx]
                gp = run_end
        prev = cursor
        i = g
    return boundaries


def build_heading_tree_from_toc(items: list[dict], toc_entries: list[tuple[int, str, int]],
                                page_offset: int = 0) -> tuple[list[dict], list[dict]]:
    """Nodes + per_item straight from the embedded outline, VERBATIM (level + order + page taken
    exactly from get_toc), minus pure-navigation entries. Same return shape as build_heading_tree so
    downstream (marker prepend, structural_index, retrieval) is unchanged."""
    # drop pure-navigation entries (cover / TOC / index / copyright / …) — no slide-worthy content
    toc_entries = [e for e in toc_entries if not _NAV_HEADING.match((e[1] or "").strip())]
    boundaries = _locate_toc_headings(items, toc_entries, page_offset)
    nodes: list[dict] = []
    for i, (level, raw_title, page1) in enumerate(toc_entries):
        ti = max(0, int(page1) - 1 - page_offset)  # authoritative start page = the TOC's own page
        nodes.append({"id": i, "level": max(1, int(level)), "title": (raw_title or "")[:140],
                      "parent": TITLE_ID, "order": i, "start_page": ti, "pages": {ti}})
    _assign_parents(nodes)  # levels come straight from get_toc; parents from the level stack

    # assign every item to the heading whose [boundary_i, boundary_{i+1}) contains it
    per_item: list[dict] = []
    for j, it in enumerate(items):
        pg = it.get("page_idx", 0)
        pg = int(pg) if str(pg).isdigit() else 0
        node = TITLE_ID
        for i in range(len(boundaries) - 1, -1, -1):
            if j >= boundaries[i]:
                node = i
                break
        if node >= 0:
            nodes[node]["pages"].add(pg)
        per_item.append({"node": node, "page": pg})
    return nodes, per_item


def build_structural_index_from_toc(items: list[dict], title: str,
                                    toc_entries: list[tuple[int, str, int]],
                                    page_offset: int = 0) -> dict[str, Any]:
    nodes, _ = build_heading_tree_from_toc(items, toc_entries, page_offset)
    return _nodes_to_index(nodes, title)


def _augment_with_structure(
    maybe_nodes: dict,
    maybe_edges: dict,
    markers: list,
    chunk_key: str,
    file_path: str,
    timestamp: int,
    struct: dict,
) -> None:
    headings = struct.get("headings") or {}
    if not headings:
        return
    title_id = struct.get("title_id", -1)

    def _key(i):
        if i in headings:
            return i
        if str(i) in headings:
            return str(i)
        return None

    leaf_ids = set()
    for mk in markers or []:
        try:
            nid = int(mk[2])
        except (TypeError, IndexError, ValueError):
            continue
        k = _key(nid)
        leaf_ids.add(k if k is not None else _key(title_id))
    leaf_ids.discard(None)
    if not leaf_ids:
        return

    concept_names = list(maybe_nodes.keys())

    added: dict = {}

    def _add_heading(hkey) -> str:
        h = headings[hkey]
        name = HEAD_MARK + h["display"]
        pages = h.get("pages") or []
        pg = f"{pages[0]}-{pages[-1]}" if pages else "?"
        desc = (f'{h["title"]} — document-structure heading (level {h["level"]}). '
                f'[meta] level={h["level"]} | pages={pg}')
        maybe_nodes.setdefault(name, []).append(dict(
            entity_name=name, entity_type="heading", description=desc,
            source_id=chunk_key, file_path=file_path, timestamp=timestamp,
        ))
        return name

    for leaf in leaf_ids:
        chain = []
        cur, seen = leaf, set()
        while cur is not None and cur not in seen and cur in headings:
            seen.add(cur)
            chain.append(cur)
            parent = headings[cur].get("parent")
            cur = _key(parent) if parent is not None else None
        tkey = _key(title_id)
        if tkey is not None and tkey not in chain:
            chain.append(tkey)
        for hkey in chain:
            if hkey not in added:
                added[hkey] = _add_heading(hkey)
        for child, parent in zip(chain, chain[1:]):
            a, b = added[parent], added[child]
            if a != b:
                maybe_edges.setdefault((a, b), []).append(dict(
                    src_id=a, tgt_id=b, weight=2.0,
                    description=f'"{a}" contains "{b}".', keywords="structure,contains",
                    source_id=chunk_key, file_path=file_path, timestamp=timestamp,
                ))

    for leaf in leaf_ids:
        hname = added.get(leaf)
        if not hname:
            continue
        for cname in concept_names:
            if cname != hname:
                maybe_edges.setdefault((hname, cname), []).append(dict(
                    src_id=hname, tgt_id=cname, weight=1.0,
                    description=f'"{hname}" mentions "{cname}".', keywords="mentions,contains",
                    source_id=chunk_key, file_path=file_path, timestamp=timestamp,
                ))


# ── Post-build structural augmentation (MiniRAG) ────────────────────────────
# MiniRAG's operate.extract_entities upserts nodes/edges DIRECTLY into the graph (no per-chunk
# maybe_nodes/maybe_edges hook like LightRAG), so the structural layer can't be woven in via
# _augment_with_structure during extraction. Instead we add it in one deterministic pass on the
# FINISHED graph: heading nodes + `contains` (parent->child) straight from structural_index, and
# `mentions` (heading->concept) reconstructed from each concept's source chunks' __struct__ leaves.
_STRUCT_SEP = "<SEP>"
_TERM_RE = re.compile(r"\w+")


def _chunk_spans(chunk: dict) -> list[tuple[int, str]] | None:
    """(heading id, casefolded text) of each heading span inside one chunk; None for offset-less chunks."""
    st = chunk.get("__struct__")
    if st is None:
        st = chunk.get("struct") or []
    if isinstance(st, str):
        try:
            st = json.loads(st)
        except ValueError:
            st = []
    if any(len(t) != 3 for t in st):
        return None
    content = str(chunk.get("content") or "").replace("\\", "").casefold()
    marks = sorted((int(t[0]), int(t[2])) for t in st)
    spans = []
    for k, (start, hid) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(content)
        spans.append((hid, content[0 if k == 0 else start:end]))
    return spans


def _names_term(text: str, name: str) -> bool:
    """`name` occurs in `text` on word boundaries (plural s/es tolerated)."""
    return re.search(rf"(?<!\w){re.escape(name)}(?:s|es)?(?!\w)", text) is not None


def mention_heads(name: str, source_chunks: list[str], chunk_spans: dict, chunk_heads: dict) -> set[int]:
    """Headings whose own span of a source chunk names the concept; a chunk that never names it
    lends it to its only heading, else to the span sharing most of its words."""
    key = name.strip().strip('"').replace("\\", "").casefold()
    words = set(_TERM_RE.findall(key))
    heads: set[int] = set()
    for cid in source_chunks:
        spans = chunk_spans.get(cid)
        if spans is None:
            heads |= chunk_heads.get(cid, set())
            continue
        named = {hid for hid, text in spans if key and _names_term(text, key)}
        if not named and spans:
            if len({hid for hid, _ in spans}) == 1:
                named = {spans[0][0]}
            else:
                best = max(spans, key=lambda s: len(words & set(_TERM_RE.findall(s[1]))))
                if words & set(_TERM_RE.findall(best[1])):
                    named = {best[0]}
        heads |= named
    return heads


def add_structure_to_graph(graph, structural_index: dict, chunks: dict, *, file_path: str = "",
                           assets: list[dict] | None = None) -> int:
    """Mutate `graph` (a networkx Graph) in place; return #heading nodes added. `chunks` is the
    kv_store_text_chunks mapping {chunk_id: {content, __struct__/struct}} used to attribute concepts
    to headings via the per-chunk __struct__ leaves. `assets` (asset_headings.json) makes each
    image/table/formula/code block a first-class asset node linked to its heading (`has_asset` edge)."""
    headings = structural_index.get("headings") or {}
    if not headings:
        return 0

    # Rebuild parser-owned assets from the current sidecar.  This removes stale assets left by a
    # previous build, including legacy decorative images and legacy `equation` nodes.
    graph.remove_nodes_from([node for node, data in graph.nodes(data=True) if is_asset_node(data)])

    def _name(hid) -> str | None:
        h = headings.get(str(hid)) if str(hid) in headings else headings.get(hid)
        return (HEAD_MARK + str(h.get("display", ""))) if h else None

    heading_names: set[str] = set()
    # 1) heading nodes
    for h in headings.values():
        name = HEAD_MARK + str(h.get("display", ""))
        heading_names.add(name)
        pages = h.get("pages") or []
        pg = f"{pages[0]}-{pages[-1]}" if pages else "?"
        desc = (f'{h.get("title", "")} — document-structure heading (level {h.get("level", 1)}). '
                f'[meta] level={h.get("level", 1)} | pages={pg}')
        if graph.has_node(name):
            graph.nodes[name]["entity_type"] = "heading"
            graph.nodes[name]["node_kind"] = "structure"
            graph.nodes[name].setdefault("description", desc)
        else:
            graph.add_node(name, entity_type="heading", node_kind="structure", description=desc,
                           source_id="", file_path=file_path)

    # 2) contains edges (parent -> child)
    for hid, h in headings.items():
        parent = h.get("parent")
        if parent is None:
            continue
        child = HEAD_MARK + str(h.get("display", ""))
        par = _name(parent)
        if par and child and par != child and graph.has_node(par) and graph.has_node(child) \
                and not graph.has_edge(par, child):
            graph.add_edge(par, child, weight=2.0, keywords="structure,contains",
                           description=f'"{par}" contains "{child}".', source_id="", file_path=file_path)

    add_mention_edges(graph, chunks, heading_names, _name, file_path=file_path)

    # 4) parser-owned assets -> first-class nodes + `has_asset` edge to their heading
    asset_labels: dict = {}
    for a in (assets or []):
        try:
            label, desc, atype = asset_node_fields(a)
        except ValueError:
            continue
        if atype == "image" and a.get("classification") != "informative":
            continue
        if not label.strip("[] "):
            continue
        if not graph.has_node(label):
            graph.add_node(label, entity_type=atype, node_kind="asset", description=desc, source_id="",
                           file_path=file_path, page=str(a.get("page")),
                           classification=a.get("classification", "") if atype == "image" else "")
        hname = _name(a.get("node"))
        if hname and graph.has_node(hname) and not graph.has_edge(hname, label):
            graph.add_edge(hname, label, weight=1.0, keywords="structure,has_asset",
                           description=f'"{hname}" has asset "{label}".', source_id="",
                           file_path=file_path)
        asset_labels[label] = a

    _link_assets_to_concepts(graph, asset_labels, heading_names, file_path=file_path)
    return len(heading_names)


def add_mention_edges(graph, chunks: dict, heading_names: set, name_of, *,
                      file_path: str = "") -> int:
    """`mentions` edges (heading -> concept) from the heading span of each source chunk that names the concept."""
    chunk_heads: dict[str, set[int]] = {}
    chunk_spans: dict[str, list | None] = {}
    for cid, ch in (chunks or {}).items():
        st = ch.get("__struct__")
        if st is None:
            st = ch.get("struct") or []
        hids: set[int] = set()
        for triple in st:
            try:
                hids.add(int(triple[2] if len(triple) == 3 else triple[1]))
            except (TypeError, ValueError, IndexError):
                continue
        chunk_heads[cid] = hids

    for cid, ch in (chunks or {}).items():
        chunk_spans[cid] = _chunk_spans(ch)

    added = 0
    for node, data in list(graph.nodes(data=True)):
        if not is_semantic_node(data) or node in heading_names:
            continue
        sources = [c for c in (data.get("source_id") or "").split(_STRUCT_SEP) if c]
        for hid in mention_heads(str(node), sources, chunk_spans, chunk_heads):
            hname = name_of(hid)
            if hname and hname != node and graph.has_node(hname) and not graph.has_edge(hname, node):
                graph.add_edge(hname, node, weight=1.0, keywords="mentions,contains",
                               description=f'"{hname}" mentions "{node}".',
                               source_id="", file_path=file_path)
                added += 1
    return added


def _asset_evidence(asset: dict, data: dict) -> str:
    """The text of an asset that can name a concept: a listing's body, otherwise its caption."""
    atype = str(data.get("entity_type") or "")
    if atype == "code_block":
        return str(asset.get("code_body") or data.get("description") or "")
    return " ".join(str(x) for x in (asset.get("caption"), data.get("description")) if x)


def _pages_of(data: dict) -> set:
    """The pages a node was seen on; `page` is a comma-separated list on enriched concept nodes."""
    out = set()
    for part in str(data.get("page") or "").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


def _link_assets_to_concepts(graph, asset_labels: dict, heading_names: set, *, file_path: str = "") -> int:
    """Add `illustrates` edges from each asset to the semantic entities it actually shows.

    A heading tells you which assets belong to a section but not which idea inside it each one is
    about, and that is the decision a slide has to make. The asset's own text answers it: a listing
    names the APIs it calls, a caption names the concepts it plots. When the asset carries no usable
    text — a bare formula, an uncaptioned figure — the page it sits on stands in for its subject.
    Both routes match names the graph already holds, so no model and no word list are involved."""

    names = [(n, name_patterns(n)) for n, d in graph.nodes(data=True)
             if is_semantic_node(d) and n not in heading_names and len(n) > 2]
    added = 0
    for label, asset in asset_labels.items():
        if not graph.has_node(label):
            continue
        linked = False
        text = _asset_evidence(asset, graph.nodes[label])
        if text:
            for name, patterns in names:
                if name == label or graph.has_edge(label, name):
                    continue
                if any(p.search(text) for p in patterns):
                    graph.add_edge(label, name, weight=2.0, keywords="asset,illustrates",
                                   description=f'"{label}" illustrates "{name}".', source_id="",
                                   file_path=file_path)
                    added += 1
                    linked = True
        added += _link_asset_to_ideas(graph, label, file_path=file_path)
        if linked:
            continue
        added += _link_asset_by_page(graph, label, asset, file_path=file_path)
    return added


_IDEA_TYPES = frozenset({"concept", "metric", "dataset", "work", "product", "event"})
_BRIDGE_KEEP = 3


def _illustrated(graph, label: str) -> list:
    return [n for n in graph.neighbors(label)
            if "illustrates" in str(graph[label][n].get("keywords", ""))]


def _link_asset_to_ideas(graph, label: str, *, file_path: str = "") -> int:
    """Reach an asset's ideas through the API names it already names.

    A listing names `cross_val_predict`, not "Confusion Matrix", so matching text alone leaves a
    third of listings attached to function names and to no idea a slide could be built on. The
    graph already relates those functions to the ideas they serve, so the idea is one hop further
    on. The hop stays inside the asset's own heading and divides by the intermediate's degree, so a
    ubiquitous name like a plotting library cannot drag in every concept it ever sat beside."""

    anchors = _illustrated(graph, label)
    if any(clean_type(graph.nodes[n].get("entity_type")) in _IDEA_TYPES for n in anchors):
        return 0
    heading = next((h for h in graph.neighbors(label)
                    if "has_asset" in str(graph[label][h].get("keywords", ""))), None)
    if heading is None:
        return 0
    scope = {n for n in graph.neighbors(heading)
             if clean_type(graph.nodes[n].get("entity_type")) in _IDEA_TYPES}
    scores: dict = {}
    for api in anchors:
        degree = max(graph.degree(api), 1)
        for idea in graph.neighbors(api):
            if idea in scope:
                scores[idea] = scores.get(idea, 0.0) + 1.0 / degree
    added = 0
    for idea in sorted(scores, key=lambda n: (-scores[n], n))[:_BRIDGE_KEEP]:
        if graph.has_edge(label, idea):
            continue
        graph.add_edge(label, idea, weight=1.0, keywords="asset,illustrates",
                       description=f'"{label}" serves "{idea}".', source_id="",
                       file_path=file_path)
        added += 1
    return added


def _link_asset_by_page(graph, label: str, asset: dict, *, file_path: str = "") -> int:
    """Fall back to the concepts of the asset's own heading that appear on its page."""

    page = asset.get("page")
    if not isinstance(page, int):
        return 0
    heading = next((h for h in graph.neighbors(label)
                    if "has_asset" in str(graph[label][h].get("keywords", ""))), None)
    if heading is None:
        return 0
    added = 0
    for concept in graph.neighbors(heading):
        data = graph.nodes[concept]
        if not is_semantic_node(data) or graph.has_edge(label, concept):
            continue
        if page in _pages_of(data):
            graph.add_edge(label, concept, weight=1.0, keywords="asset,illustrates",
                           description=f'"{label}" appears with "{concept}".', source_id="",
                           file_path=file_path)
            added += 1
    return added


def rescope_mentions(kb_dir) -> tuple[int, int]:
    """Rebuild an existing KB's `mentions` edges at heading-span level (no LLM, no re-parse); (before, after)."""


    kb_dir = Path(kb_dir)
    graphml = kb_dir / "graph_chunk_entity_relation.graphml"
    graph = nx.read_graphml(graphml)
    headings = load_structural_index(kb_dir).get("headings") or {}
    names = {HEAD_MARK + str(h.get("display", "")) for h in headings.values()}
    old = [(u, v) for u, v, d in graph.edges(data=True) if "mentions" in str(d.get("keywords", ""))]
    graph.remove_edges_from(old)
    name_of = lambda hid: (HEAD_MARK + str(headings[str(hid)].get("display", ""))) if str(hid) in headings else None
    added = add_mention_edges(graph, load_text_chunks(kb_dir), names, name_of, file_path=f"{kb_dir.name}.pdf")
    nx.write_graphml(graph, graphml)
    return len(old), added


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild a KB's heading->concept `mentions` edges per heading span")
    parser.add_argument("--kb", required=True)
    args = parser.parse_args()
    before, after = rescope_mentions(args.kb)
    print(f"mentions edges: {before} -> {after}")
