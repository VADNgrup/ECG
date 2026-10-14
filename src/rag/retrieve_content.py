from __future__ import annotations

import json
import re
from pathlib import Path

import networkx as nx

from src.rag.asset_validation import validate_formula, validate_table
from src.rag.kb_files import load_asset_rows, load_structural_index, load_text_chunks
from src.rag.kg_schema import is_semantic_node, normalize_asset_records, normalize_graph_schema, validate_graph_schema
from src.rag.structural import _clean_chars, _toc_match
from src.utils import lang
from src.utils.content_cache import digest


def _blocks(text: str) -> list[tuple[int, str]]:
    """(offset, block) for each blank-line-separated block; a fenced code listing stays one block across its own blank
    lines."""
    out: list[tuple[int, str]] = []
    for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", text):
        if out and out[-1][1].lstrip().startswith("```") and out[-1][1].count("```") % 2:
            start = out[-1][0]
            out[-1] = (start, text[start:match.end()])
        else:
            out.append((match.start(), match.group()))
    return out


def heading_evidence(kb_dir: Path, heading_id: int) -> dict:
    """Read exact, heading-owned KB spans. Generated descriptions are navigation only."""
    view = _kb_view(kb_dir)
    heading = view["index"]["headings"][str(heading_id)]
    units, excluded = [], []
    chunks = view["chunks"]
    for seg in get_subtree_content(kb_dir, heading_id):
        if seg.get("node") is None:
            raise ValueError("KB lacks heading offsets; rebuild before using strict KB mode")
        if seg["node"] != heading_id:
            continue
        if chunks[seg["chunk_id"]]["content"][seg["start"]:seg["end"]] != seg["text"]:
            raise ValueError("KB source offsets do not match stored text")
        for offset, raw in _blocks(seg["text"]):
            start = seg["start"] + offset
            derived = bool(re.match(r"\[(?:Derived\s+)?Figure\]|\[Table Summary\]", raw, re.I))
            if raw.startswith("[Table]"):
                table = re.search(r"(?m)^\s*\|", raw)
                if table:
                    excluded.append({"text": raw[:table.start()], "reason": "generated table summary"})
                    start += table.start()
                    raw = raw[table.start():]
                else:
                    derived = True
            clean = re.sub(r"\s+", " ", raw).strip()
            noise = (not re.search(r"\w", clean) or bool(re.fullmatch(r"\d+[\W_]*", clean))
                     or clean.casefold().lstrip("# ") == heading["title"].casefold())
            if derived or noise:
                excluded.append({"text": raw, "reason": "derived metadata" if derived else "structural/OCR marker"})
                continue
            ref = {"chunk_id": seg["chunk_id"], "heading_id": heading_id,
                   "page": seg["page"], "start": start, "end": start + len(raw), "quote": raw}
            uid = digest(ref)[:20]
            head = raw.lstrip()
            kind = ("table" if head.startswith("|") else "code" if head.startswith("```")
                    else "formula" if head.startswith("$$") else "text")
            units.append({"id": uid, "text": raw, "kind": kind, "source_ref": ref})
    chunk_ids = {u["source_ref"]["chunk_id"] for u in units}
    concepts = []
    for name, sources in view["concepts"]:
        if chunk_ids & sources and any(name.casefold() in u["text"].casefold() for u in units):
            concepts.append(name)
    return {"heading_id": heading_id, "heading": heading["title"], "units": units,
            "concepts": concepts, "excluded": excluded}


_SENT_BOUNDARY = re.compile(r"[.!?…؟।։۔]+[\"')\]”]*\s+|[。！？]+[」』”’)）]*\s*|\n(?=\s*(?:[-*•]|\d+[.)])\s)")
_ABBREVIATION = re.compile(r"(?:\b(?:e\.g|i\.e|vs|etc|fig|eq|al|dr|mr|mrs|prof|no)\.|\b[A-Z]\.)\s*$", re.I)


_BULLET_GLYPH = re.compile(r"^\s*(?:[�•▪●‣·*-]\s+)+")


def clean_display_text(text: str) -> str:
    """Presentation text of a source span: drops OCR bullet glyphs and maps stray U+FFFD quote glyphs to quotes."""
    text = _BULLET_GLYPH.sub("", text)
    text = re.sub(r"(?<=\w)�(?=\w)", "’", text)
    text = re.sub(r"�(?=\w)", "“", text)
    return re.sub(r"�", "”", text).strip()


def sentence_units(units: list[dict]) -> list[dict]:
    """Split text units into sentences with stable IDs and exact source refs; code/table units stay whole."""
    out = []
    for u in units:
        text = u["text"]
        validation = None
        resolved_kind = u["kind"]
        display_text = text
        if u["kind"] == "formula":
            validation = validate_formula(text)
            if not validation["valid"]:
                # Failed code-as-equation OCR is retained as source material but
                # can no longer become a formula visual.
                resolved_kind = "code" if validation["resolved_type"] == "code_block" else "invalid_formula"
            else:
                display_text = validation["normalized_content"]
        elif u["kind"] == "table":
            validation = validate_table(text)
            if validation["valid"]:
                display_text = validation["normalized_content"]
            else:
                resolved_kind = "invalid_table"
        spans, start = [], 0
        if resolved_kind == "text":
            for m in _SENT_BOUNDARY.finditer(text):
                nxt = text[m.end():m.end() + 1]
                if _ABBREVIATION.search(text[start:m.end()]) or (nxt and nxt.islower()):
                    continue
                if nxt.isdigit() and re.search(r"\d\s?$", text[:m.start()]):   # decimal point in spaced OCR math
                    continue
                spans.append((start, m.end()))
                start = m.end()
        spans.append((start, len(text)))
        k = 0
        for s, e in spans:
            piece = text[s:e]
            s += len(piece) - len(piece.lstrip())
            e -= len(piece) - len(piece.rstrip())
            if e <= s:
                continue
            k += 1
            base = u["source_ref"]["start"]
            row = {"id": f"{u['id']}.s{k}", "unit_id": u["id"], "kind": resolved_kind, "text": text[s:e],
                        "list_item": bool(_BULLET_GLYPH.match(text)),
                        "clean": clean_display_text(display_text if resolved_kind != "text" else text[s:e]),
                        "ref": {**u["source_ref"], "quote": text[s:e], "start": base + s, "end": base + e}}
            if validation is not None:
                row["asset_validation"] = {key: value for key, value in validation.items()
                                           if key != "structured_table"}
                if validation.get("structured_table"):
                    row["structured_table"] = validation["structured_table"]
                row["normalized_content"] = validation.get("normalized_content", display_text)
            out.append(row)
    return out


def load_asset_headings(kb_dir: Path) -> list[dict] | None:
    """Page-anchored asset->heading map written by build_kg._build_asset_headings, or None for
    KBs built before it existed (caller then falls back to concept-mediated assets)."""
    if not (Path(kb_dir) / "asset_headings.json").exists():
        return None
    try:
        normalized, _ = normalize_asset_records(load_asset_rows(kb_dir), strict_images=True)
        return normalized
    except Exception:
        return None


def find_heading_id(structural_index: dict, title: str) -> int | None:
    headings = structural_index.get("headings") or {}
    # On duplicate titles, the lowest-document-order occurrence wins (the real chapter, not a later recap).
    keyed: dict[str, str] = {}
    for hid, h in sorted(headings.items(), key=lambda kv: kv[1].get("order", 0)):
        k = _clean_chars(h["title"])
        if k and k not in keyed:  # first (lowest order) wins
            keyed[k] = hid
    nt = _clean_chars(title)
    if nt in keyed:
        return int(keyed[nt])
    key = _toc_match(title, dict.fromkeys(keyed, 0))
    return int(keyed[key]) if key is not None else None


def subtree_ids(structural_index: dict, root_id: int) -> set[int]:
    headings = structural_index.get("headings") or {}
    children: dict[int, list[int]] = {}
    for hid, h in headings.items():
        parent = h.get("parent")
        if parent is not None:
            children.setdefault(int(parent), []).append(int(hid))
    out = {root_id}
    stack = [root_id]
    while stack:
        cur = stack.pop()
        for c in children.get(cur, []):
            if c not in out:
                out.add(c)
                stack.append(c)
    return out


def scope_ids(structural_index: dict, root_ids: list[int]) -> set[int]:
    """Union of the subtrees below every selected root heading (the deck's scope)."""
    out: set[int] = set()
    for root in root_ids:
        out |= subtree_ids(structural_index, int(root))
    return out


_HEADING_EVIDENCE_CACHE: dict = {}


def cached_heading_evidence(kb_dir: Path, heading_id: int) -> dict:
    """heading_evidence memoised per (kb, heading): planner and packet builder read the same spans."""
    key = (str(kb_dir), int(heading_id))
    if key not in _HEADING_EVIDENCE_CACHE:
        _HEADING_EVIDENCE_CACHE[key] = heading_evidence(kb_dir, int(heading_id))
    return _HEADING_EVIDENCE_CACHE[key]


_NORM_TEXT = re.compile(r"\W+")


def filter_sentences(sentences: list[dict], heading: str) -> tuple[list[dict], list[dict]]:
    """Drop OCR list markers, running heads and chunk-overlap duplicates; each dropped ID is reported."""
    norm = lambda t: _NORM_TEXT.sub(" ", t.casefold()).strip()
    title, seen, kept, dropped = norm(heading), set(), [], []
    for s in sentences:
        n, reason = norm(s["clean"]), None
        if s["kind"] == "text":
            if not re.search(r"[^\W\d_]", s["clean"]):
                reason = "OCR list marker"
            elif n in title and lang.length_units(s["clean"]) <= 12:
                reason = "running head"
            elif lang.caption_pattern().match(s["clean"]) and lang.length_units(s["clean"]) <= 30:
                reason = "figure caption"
            elif n in seen:
                reason = "duplicate sentence"
            seen.add(n)
        if reason:
            dropped.append({"source_id": s["id"], "reason": reason})
        else:
            kept.append(s)
    return kept, dropped


def get_subtree_content(kb_dir: Path, root_id: int) -> list[dict]:
    """Text segments belonging to root_id and its descendants, split at heading boundaries via
    __struct__ offsets. Old-format chunks (pre offset-preserving change) fall back to whole-chunk
    inclusion -- rebuild (--force) for exact boundaries."""
    kb_dir = Path(kb_dir)
    view = _kb_view(kb_dir)
    subtree = subtree_ids(view["index"], root_id)
    ordered = view["ordered"]

    out: list[dict] = []
    warned_old_format = False
    for chunk_id, ch in ordered:
        struct = ch.get("__struct__") or []
        content = ch.get("content") or ""
        if not struct:
            continue
        if len(struct[0]) == 3:
            for i, (off, page, node) in enumerate(struct):
                node = int(node)
                if node not in subtree:
                    continue
                end = struct[i + 1][0] if i + 1 < len(struct) else len(content)
                seg = content[off:end].strip()
                if seg:
                    leading = len(content[off:end]) - len(content[off:end].lstrip())
                    out.append({"chunk_id": chunk_id, "node": node, "page": int(page), "text": seg,
                                "start": off + leading, "end": off + leading + len(seg)})
        else:
            if not warned_old_format:
                print(f"[retrieve_content] {kb_dir.name}: __struct__ is the old (page,node)-only "
                      f"format -- falling back to whole-chunk inclusion; rebuild with --force to "
                      f"get exact per-heading boundaries")
                warned_old_format = True
            nodes_here = {int(n) for _, n in struct}
            if nodes_here & subtree:
                out.append({"chunk_id": chunk_id, "node": None, "page": None, "text": content})
    return out


_GRAPHML_NAME = "graph_chunk_entity_relation.graphml"


_KB_VIEW: dict = {}


def _kb_view(kb_dir: Path) -> dict:
    """The KB files heading_evidence reads, loaded once per version of the files (not once per heading)."""
    kb = Path(kb_dir)
    stamp = tuple((kb / n).stat().st_mtime_ns if (kb / n).exists() else 0
                  for n in ("structural_index.json", "kv_store_text_chunks.json", _GRAPHML_NAME))
    hit = _KB_VIEW.get(str(kb))
    if hit and hit[0] == stamp:
        return hit[1]
    chunks = load_text_chunks(kb)
    graph = load_kg_graph(kb)
    view = {"index": load_structural_index(kb), "chunks": chunks,
            "ordered": sorted(chunks.items(), key=lambda kv: kv[1].get("chunk_order_index", 0)),
            "concepts": [(str(name), set(str(data.get("source_id", "")).split("<SEP>")))
                         for name, data in graph.nodes(data=True) if is_semantic_node(data)]}
    _KB_VIEW[str(kb)] = (stamp, view)
    return view


def load_kg_graph(kb_dir: Path):
    graph = nx.read_graphml(Path(kb_dir) / _GRAPHML_NAME)
    normalize_graph_schema(graph)
    validate_graph_schema(graph)
    return graph


def compute_salience(graph) -> dict:
    """Global node importance -- PageRank, degree fallback."""
    try:
        return nx.pagerank(graph) if graph.number_of_nodes() else {}
    except Exception:
        return {n: float(graph.degree(n) or 0) for n in graph.nodes}
