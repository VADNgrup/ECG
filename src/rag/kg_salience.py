"""Deterministic KG enrichment for Config.KG_CONTENT_MODE content selection.

Two artifacts, both computed from the FINISHED graph (no LLM, no re-parse), so they can be
produced during `build_kg.build()` and also back-filled onto existing KBs via the CLI:

  * ``salience.json`` -- global node importance (PageRank), read by
    ``retrieve_content.select_content_for_heading`` instead of recomputing per slide.
  * prerequisite direction -- annotated ONTO existing semantic-relation edges via a
    ``prereq_of`` attribute (the name of the dependent endpoint), NON-destructively (we never
    add/remove edges, so the original relation's keywords/description are preserved and the
    pass is fully idempotent). Direction is inferred from document order (a concept introduced
    on an earlier page is a prerequisite of a related concept introduced later); only pairs
    that ALREADY share a semantic relation are considered, so we never invent connectivity.

Run standalone:  python -m src.rag.kg_salience --kb data/cache/kb/<stem>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import networkx as nx

from src.rag.kg_schema import is_semantic_node, normalize_graph_schema, validate_graph_schema
from src.rag.structural import HEAD_MARK

GRAPHML = "graph_chunk_entity_relation.graphml"


def compute_salience(graph) -> dict:
    try:
        return nx.pagerank(graph) if graph.number_of_nodes() else {}
    except Exception:
        return {n: float(graph.degree(n) or 0) for n in graph.nodes}


def save_salience(kb_dir: Path, salience: dict) -> Path:
    out = Path(kb_dir) / "salience.json"
    out.write_text(json.dumps(salience, ensure_ascii=False), encoding="utf-8")
    return out


def load_salience(kb_dir: Path) -> dict | None:
    p = Path(kb_dir) / "salience.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _heading_min_page(structural_index: dict) -> dict:
    """graph heading node name (HEAD_MARK + display) -> earliest page."""
    out: dict[str, int] = {}
    for h in (structural_index.get("headings") or {}).values():
        pages = h.get("pages") or []
        if not pages:
            continue
        name = HEAD_MARK + str(h.get("display", ""))
        pg = min(int(p) for p in pages)
        out[name] = min(pg, out.get(name, pg))
    return out


def _concept_first_page(graph, head_page: dict) -> dict:
    """concept node -> earliest page of any heading that mentions it (document intro order)."""
    first: dict[str, int] = {}
    for u, v, ed in graph.edges(data=True):
        if "mentions" not in (ed.get("keywords") or ""):
            continue
        # a mentions edge is heading<->concept; find which endpoint is the heading
        for head, concept in ((u, v), (v, u)):
            if head in head_page:
                if not is_semantic_node(graph.nodes[concept]):
                    continue
                pg = head_page[head]
                if concept not in first or pg < first[concept]:
                    first[concept] = pg
    return first


def infer_prerequisites(graph, structural_index: dict, *, max_per_concept: int = 5,
                        salience: dict | None = None) -> list[tuple[str, str, str]]:
    """(u, v, dependent) triples where (u, v) is an EXISTING semantic edge and `dependent` is
    whichever endpoint is introduced LATER (so the other endpoint is its prerequisite)."""
    head_page = _heading_min_page(structural_index)
    first = _concept_first_page(graph, head_page)
    if salience is None:
        salience = compute_salience(graph)

    # rank by dependent so each dependent keeps only its strongest prerequisites
    by_dependent: dict[str, list[tuple[float, str, str]]] = {}
    for u, v, ed in graph.edges(data=True):
        kw = ed.get("keywords") or ""
        if "mentions" in kw or "structure" in kw:
            continue  # only reuse genuine semantic relations as the "relatedness" filter
        if u not in first or v not in first or first[u] == first[v]:
            continue
        prereq, dependent = (u, v) if first[u] < first[v] else (v, u)
        by_dependent.setdefault(dependent, []).append((salience.get(prereq, 0.0), prereq, dependent))

    triples: list[tuple[str, str, str]] = []
    for dependent, cands in by_dependent.items():
        cands.sort(reverse=True)
        for _sal, prereq, dep in cands[:max_per_concept]:
            triples.append((prereq, dep, dep))
    return triples


def enrich_kb(kb_dir: Path, *, verbose: bool = True) -> dict:
    """Compute + persist salience.json and annotate prerequisite direction onto existing
    semantic edges (via a `prereq_of` attribute). Non-destructive and idempotent: existing
    `prereq_of` annotations are cleared and recomputed; no edges are added or removed."""
    kb_dir = Path(kb_dir)
    gpath = kb_dir / GRAPHML
    sipath = kb_dir / "structural_index.json"
    if not gpath.exists():
        raise FileNotFoundError(f"no graphml at {gpath}")
    graph = nx.read_graphml(gpath)
    normalize_graph_schema(graph)
    validate_graph_schema(graph)

    salience = compute_salience(graph)
    save_salience(kb_dir, salience)

    annotated = 0
    if sipath.exists():
        structural_index = json.loads(sipath.read_text(encoding="utf-8"))
        # idempotent reset: clear any prior prereq_of annotation (never touches edge existence)
        for _u, _v, ed in graph.edges(data=True):
            ed.pop("prereq_of", None)
        for prereq, dep_endpoint, dependent in infer_prerequisites(
                graph, structural_index, salience=salience):
            if graph.has_edge(prereq, dep_endpoint):
                graph[prereq][dep_endpoint]["prereq_of"] = dependent
                annotated += 1
    elif verbose:
        print(f"[kg_salience] {kb_dir.name}: no structural_index.json -> salience only, "
              f"no prerequisite annotation")
    nx.write_graphml(graph, gpath)

    if verbose:
        print(f"[kg_salience] {kb_dir.name}: salience for {len(salience)} nodes, "
              f"{annotated} prereq_of annotations")
    return {"nodes": len(salience), "prerequisite_annotations": annotated}


def main() -> None:
    ap = argparse.ArgumentParser(description="Enrich a KG KB with salience + prerequisite edges")
    ap.add_argument("--kb", required=True, help="path to data/cache/kb/<stem>")
    args = ap.parse_args()
    enrich_kb(Path(args.kb))


if __name__ == "__main__":
    main()
