"""Idea candidates for a deck: concepts ranked and their depth of treatment measured INSIDE the deck's scope.

Scope = the chunks owned by the selected headings. A concept only counts when the KG says (via its chunk ids) that it
occurs in scope and a scoped sentence mentions it; the KG's own descriptions are never used as content because they
merge every chunk of the book, including chapters outside the scope.
"""
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

from src.rag.kg_salience import load_salience
from src.rag.kg_schema import is_semantic_node
from src.rag.retrieve_content import (cached_heading_evidence, filter_sentences, load_kg_graph, load_structural_index,
                                      sentence_units)
from src.utils import lang
from src.utils.config import Config

_ZERO_WIDTH = re.compile(r"[​-‏⁠-⁯﻿]")
_NON_IDEA_SEMANTIC_TYPES = {"code", "person", "organization"}
TREATMENTS = ("intro", "standard", "deep")


def normalize_name(name: str) -> str:
    """Comparable concept name: no zero-width marks, quotes, case or spacing differences."""
    text = _ZERO_WIDTH.sub("", str(name)).strip().strip('"').strip("'")
    return re.sub(r"\s+", " ", text).casefold()


def display_name(name: str) -> str:
    return re.sub(r"\s+", " ", _ZERO_WIDTH.sub("", str(name)).strip().strip('"').strip("'"))


def scope_sentences(kb_dir: Path, index: dict, scope: Set[int]) -> Dict[int, dict]:
    """heading id -> {title, order, sentences (noise-filtered), chunks} for every in-scope heading with own text."""
    headings = index.get("headings") or {}
    out: Dict[int, dict] = {}
    lang.use_labels(lang.labels_for_kb(kb_dir))
    for hid in sorted(scope, key=lambda i: headings[str(i)].get("order", 0)):
        try:
            evidence = cached_heading_evidence(kb_dir, hid)
        except (ValueError, KeyError):
            continue
        title = headings[str(hid)].get("title", "")
        kept, _ = filter_sentences(sentence_units(evidence["units"]), title)
        out[hid] = {"title": title, "order": headings[str(hid)].get("order", 0), "sentences": kept,
                    "chunks": {u["source_ref"]["chunk_id"] for u in evidence["units"]}}
    return out


def sentences_mentioning(pool: Dict[str, dict], name: str) -> List[str]:
    """Sentence ids of `pool` (any {id: {"clean": text, ...}} mapping) that mention `name`, in `pool`'s order.
    Same matching as `build_candidates` (normalized, word-boundary, plural s/es) but for ONE given set of
    sentences -- lets a caller ask "does THIS heading's own text mention this concept", not just "who owns it
    globally"."""
    pattern = re.compile(rf"(?<!\w){re.escape(normalize_name(name))}(?:s|es)?(?!\w)")
    return [sid for sid, s in pool.items() if pattern.search(_ZERO_WIDTH.sub("", s["clean"]).casefold())]


def _minmax(values: Dict[str, float]) -> Dict[str, float]:
    lo, hi = min(values.values(), default=0.0), max(values.values(), default=0.0)
    return {k: (v - lo) / (hi - lo) if hi > lo else 0.0 for k, v in values.items()}


def build_candidates(kb_dir: Path, scope: Set[int], index: dict | None = None,
                     by_heading: Dict[int, dict] | None = None) -> List[dict]:
    """Ranked idea candidates for the scope, each with owner heading, treatment depth and evidence sentence IDs."""
    index = index or load_structural_index(kb_dir)
    by_heading = by_heading or scope_sentences(kb_dir, index, scope)
    chunks = set().union(*(h["chunks"] for h in by_heading.values())) if by_heading else set()
    graph = load_kg_graph(kb_dir)
    concepts: Dict[str, str] = {}
    for key, data in graph.nodes(data=True):
        if not is_semantic_node(data) or str(data.get("entity_type", "")).lower() in _NON_IDEA_SEMANTIC_TYPES:
            continue
        if not set(str(data.get("source_id", "")).split("<SEP>")) & chunks:
            continue
        name = normalize_name(key)
        if len(name) >= 2:
            concepts.setdefault(name, key)
    patterns = {n: re.compile(rf"(?<!\w){re.escape(n)}(?:s|es)?(?!\w)") for n in concepts}
    position: Dict[str, int] = {}
    mentions: Dict[str, List[str]] = defaultdict(list)
    per_heading: Dict[str, Dict[int, List[str]]] = defaultdict(lambda: defaultdict(list))
    for hid, h in by_heading.items():
        for s in h["sentences"]:
            position[s["id"]] = len(position)
            low = _ZERO_WIDTH.sub("", s["clean"]).casefold()
            for name, pattern in patterns.items():
                if pattern.search(low):
                    mentions[name].append(s["id"])
                    per_heading[name][hid].append(s["id"])
    names = [n for n in concepts if mentions.get(n)]
    node_of = {concepts[n]: n for n in names}
    neighbors: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for u, v, data in graph.edges(data=True):
        if u in node_of and v in node_of and u != v and set(str(data.get("source_id", "")).split("<SEP>")) & chunks:
            weight = float(data.get("weight", 1) or 1)
            neighbors[node_of[u]][node_of[v]] += weight
            neighbors[node_of[v]][node_of[u]] += weight
    total = max(len(position), 1)
    owner: Dict[str, int] = {}
    for n in names:
        owner[n] = max(per_heading[n], key=lambda h: (n in normalize_name(by_heading[h]["title"]),
                                                     len(per_heading[n][h]), -by_heading[h]["order"]))
    freq = _minmax({n: float(len(mentions[n])) for n in names})
    degree = _minmax({n: float(len(neighbors[n])) for n in names})
    # heading-local relation centrality: weight of the edges to OTHER concepts owned by the SAME heading (not the
    # whole scope) -- how central n is to what its own heading actually discusses, not how connected it is overall.
    heading_members: Dict[int, List[str]] = defaultdict(list)
    for n in names:
        heading_members[owner[n]].append(n)
    local_centrality = {n: sum(neighbors[n].get(m, 0.0) for m in heading_members[owner[n]] if m != n) for n in names}
    local_norm = _minmax(local_centrality)
    salience_raw = load_salience(kb_dir) or {}
    salience_norm = _minmax({n: salience_raw.get(concepts[n], 0.0) for n in names})
    w_freq, w_focus, w_degree, w_title, w_early = Config.KG_IDEA_WEIGHTS
    scored = []
    for n in names:
        own = len(per_heading[n][owner[n]])
        titled = n in normalize_name(by_heading[owner[n]]["title"])
        first = min(position[s] for s in mentions[n]) / total
        score = (w_freq * freq[n] + w_focus * own / len(mentions[n]) + w_degree * degree[n]
                 + w_title * float(titled) + w_early * (1 - first)
                 + Config.KG_IDEA_LOCAL_WEIGHT * local_norm[n] + Config.KG_IDEA_SALIENCE_WEIGHT * salience_norm[n])
        generic = len(mentions[n]) / total > Config.KG_GENERIC_SPREAD
        if generic:
            score *= 0.3
        treatment = ("intro" if own <= Config.KG_TREAT_INTRO_MAX and not titled
                     else "deep" if own >= Config.KG_TREAT_DEEP_MIN else "standard")
        ordered = per_heading[n][owner[n]] + [s for s in mentions[n] if s not in per_heading[n][owner[n]]]
        scored.append({"concept": display_name(concepts[n]), "key": n, "score": round(score, 4), "generic": generic,
                       "owner": owner[n], "treatment": treatment, "mentions": len(mentions[n]),
                       "headings": sorted(per_heading[n]), "evidence": ordered[:8],
                       "related": [display_name(concepts[m]) for m, _ in sorted(
                           neighbors[n].items(), key=lambda kv: -kv[1])[:4]]})
    scored.sort(key=lambda c: -c["score"])
    return scored
