"""A KB's knowledge graph as one self-contained HTML page (kg_view.html): laid out by the document's structure,
drawn only when something changes, with filters (sections, types, relation kinds, degree, search) and a sortable
node list."""
import argparse
import json
import re
from pathlib import Path

import networkx as nx

from src.rag.kg_schema import infer_node_kind, normalize_graph_schema, validate_graph_schema
from src.rag.structural import HEAD_MARK
from src.rag.view_kg_layout import layout

BASE_DIR = Path(__file__).resolve().parents[2]
KB_DIR = BASE_DIR / "data" / "cache" / "kb"

_PALETTE = [
    "#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#b07aa1",
    "#76b7b2", "#edc948", "#ff9da7", "#9c755f", "#bab0ac",
    "#8cd17d", "#b6992d", "#86bcb6", "#d37295", "#fabfd2",
]
# Heading hierarchy ramp: level 0 (document title) darkest -> deeper headings lighter, so headings read
# as a hierarchy AND stand apart from concept colors (which use _PALETTE).
_HEADING_RAMP = ["#08306b", "#2171b5", "#4292c6", "#6baed6", "#9ecae1", "#c6dbef", "#deebf7"]
EDGE_KINDS = ("structure", "mentions", "asset", "relation")


def _load_graph(graphml: Path) -> nx.Graph:
    graph = nx.read_graphml(graphml)
    normalize_graph_schema(graph)
    validate_graph_schema(graph)
    return graph


def _edge_kind(keywords: str) -> int:
    """Index into EDGE_KINDS: the heading tree, a section naming an entity, an asset link, or a relation."""
    kw = keywords.replace(" ", "").lower()
    if kw.startswith("structure,contains"):
        return 0
    if kw.startswith("mentions"):
        return 1
    if kw.startswith("structure,has_asset") or kw.startswith("asset,"):
        return 2
    return 3


def _first_page(data: dict) -> int | None:
    for part in str(data.get("page") or "").split(","):
        if part.strip().isdigit():
            return int(part)
    return None


def _to_payload(g: nx.Graph, index: dict) -> dict:
    """Nodes with their precomputed place, section, part and reading-order rank; edges by node position and kind;
    the section tree with node counts; the parts (cells) to frame."""
    placed = layout(g, index)
    st, pos, sec, part = placed["structure"], placed["pos"], placed["sec"], placed["part"]
    types: dict[str, int] = {}
    deg = dict(g.degree())
    rows = []
    for nid, data in g.nodes(data=True):
        etype = (data.get("entity_type") or "concept").strip().strip('"') or "concept"
        types.setdefault(etype, len(types))
        h = sec.get(nid)
        is_head = etype == "heading" and h is not None and st.name(h) == nid
        page = (st.heads[h].get("pages") or [None])[0] if is_head else _first_page(data)
        rows.append((nid, data, etype, h, is_head, page))
    big = len(st.ids) + 1
    ranked = sorted(range(len(rows)), key=lambda i: (
        part.get(rows[i][0], -1) if part.get(rows[i][0], -1) >= 0 else big,
        st.rank.get(rows[i][3], big), not rows[i][4],
        rows[i][5] if rows[i][5] is not None else big, -deg.get(rows[i][0], 0), str(rows[i][0])))
    order = {i: r for r, i in enumerate(ranked)}
    nodes, at = [], {}
    for i, (nid, data, etype, h, is_head, page) in enumerate(rows):
        x, y = pos.get(nid, (0.0, 0.0))
        node = {"id": str(nid), "l": str(nid).strip('"').replace(HEAD_MARK, ""), "t": etype,
                "k": infer_node_kind(data), "d": (data.get("description") or "").strip().strip('"'),
                "g": deg.get(nid, 0), "x": x, "y": y, "s": h, "c": part.get(nid, -1), "p": page, "o": order[i]}
        if is_head:  # headings coloured by level: they read as a hierarchy apart from the concept colours
            node["lv"] = st.level(h)
            node["col"] = _HEADING_RAMP[min(st.level(h), len(_HEADING_RAMP) - 1)]
        at[nid] = len(nodes)
        nodes.append(node)
    edges = []
    for u, v, data in g.edges(data=True):
        kw = (data.get("keywords") or "").strip().strip('"')
        kind = _edge_kind(kw)
        edge = {"a": at[u], "b": at[v], "k": kind, "r": kw or (data.get("description") or "").strip().strip('"')}
        if kind == 3 and kw:
            edge["d"] = (data.get("description") or "").strip().strip('"')
        edges.append(edge)
    counts: dict = {}
    for nid, h in sec.items():
        while h is not None:
            counts[h] = counts.get(h, 0) + 1
            h = st.parent.get(h)
    tree = [{"h": h, "t": str(st.heads[h].get("display", "")), "p": st.parent[h], "n": counts.get(h, 0),
             "c": st.part_of.get(h, -1)} for h in st.ids]
    color_by_type = {t: _PALETTE[i % len(_PALETTE)] for t, i in types.items()}
    if "heading" in color_by_type:  # legend swatch = a representative mid-ramp blue
        color_by_type["heading"] = _HEADING_RAMP[2]
    return {"nodes": nodes, "edges": edges, "colors": color_by_type, "kinds": list(EDGE_KINDS),
            "tree": tree, "cells": placed["cells"], "palette": _PALETTE}


_TEMPLATE = Path(__file__).with_name("kg_view_template.html")


def _structural_index(graphml: Path) -> dict:
    """The structural index beside the graph; without one, the heading tree from the graph's `structure` edges."""
    path = Path(graphml).parent / "structural_index.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    g = _load_graph(graphml)
    meta = {}
    for n, d in g.nodes(data=True):
        if (d.get("entity_type") or "").strip('"') == "heading":
            m = re.search(r"level=(\d+)\D+pages=(\d+)", d.get("description") or "")
            meta[n] = (int(m.group(2)), int(m.group(1))) if m else (0, 0)
    ids = {n: i for i, n in enumerate(sorted(meta, key=lambda n: meta[n]))}
    parent = {}
    for u, v, d in g.edges(data=True):
        if u in ids and v in ids and _edge_kind(d.get("keywords") or "") == 0:
            child, par = (v, u) if meta[u][1] < meta[v][1] else (u, v)
            parent[child] = ids[par]
    return {"headings": {str(i): {"title": n.replace(HEAD_MARK, ""), "display": n.replace(HEAD_MARK, ""),
                                  "level": meta[n][1], "parent": parent.get(n), "order": i, "pages": [meta[n][0]]}
                         for n, i in ids.items()}}


def render(graphml: Path, out_html: Path) -> Path:
    g = _load_graph(graphml)
    payload = _to_payload(g, _structural_index(graphml))
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", r"<\/")  # no "</script>"
    html = _TEMPLATE.read_text(encoding="utf-8").replace("/*DATA*/", data)
    out_html.write_text(html, encoding="utf-8")
    print(f"nodes={len(payload['nodes'])} edges={len(payload['edges'])} types={len(payload['colors'])}")
    print(f"wrote {out_html}")
    return out_html


def main():
    ap = argparse.ArgumentParser(description="Render a LightRAG graphml into a self-contained interactive HTML.")
    ap.add_argument("--doc", help="document_id (with --mode)")
    ap.add_argument("--mode", choices=["mineru", "gpt4o"], help="which KB to view")
    ap.add_argument("--kb-suffix", default="", help="extra suffix on the KB dir, e.g. '__struct_full'")
    ap.add_argument("--graphml", help="explicit path to a graphml file")
    ap.add_argument("--out", help="output html path")
    args = ap.parse_args()

    if args.graphml:
        graphml = Path(args.graphml)
    elif args.doc and args.mode:
        graphml = KB_DIR / f"{args.doc}__{args.mode}{args.kb_suffix}" / "graph_chunk_entity_relation.graphml"
    else:
        raise SystemExit("provide --graphml OR (--doc and --mode [--kb-suffix])")

    if not graphml.exists():
        raise SystemExit(f"graphml not found: {graphml}")

    out = Path(args.out) if args.out else graphml.parent / "kg_view.html"
    render(graphml, out)


if __name__ == "__main__":
    main()
