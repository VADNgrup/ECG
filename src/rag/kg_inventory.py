"""The KG's inventory of one chapter: every node and stated relation the chapter's text expresses, each tied to the book
sentences that state it.

The inventory is the checklist the agents must place, so nothing the section holds is forgotten; the sentences are the
evidence, so nothing is made up. Node types are not trusted (the extractor types `Linear Regression` as `code`), so a node
is kept by its content: named in a prose sentence, or its description anchored to one. Variable names, digits and
one-letter symbols are extraction noise; a name that only appears inside listings travels with its code block. A
relation the extractor synthesized is kept only when a sentence of the chapter supports it.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from src.rag.embedder import embed_transient
from src.rag.kg_schema import clean_type, infer_node_kind, name_patterns
from src.rag.structural import HEAD_MARK

_SEP = "<SEP>"
_NOT_STATED = ("mentions", "contains", "illustrates", "has_asset")
_VARIABLE = re.compile(r"[A-Za-z]_\w*")                                # X_train, y_train_5, n_correct: program variables
_NUMBER = re.compile(r"[\d.,\s×x]+(\s*\w+)?")                            # "5", "60,000 images", "28×28 pixels"
# Cosine floors, calibrated on HOML Ch.3-4 (text-embedding-3-small): a description anchored below them mostly lands on
# a neighbouring sentence about something else ("grid" -> "Each image is labeled with the digit it represents").
NODE_ANCHOR_MIN = 0.6              # a node never named in prose, anchored by its description
PARAGRAPH_ANCHOR_MIN = 0.5         # a relation inside a paragraph that names both ends
RELATION_ANCHOR_MIN = 0.62         # a relation anchored by its description alone


def is_noise(name: str) -> bool:
    """Extraction noise: a symbol, a quantity or a program variable (never a teaching idea by itself). A function or
    class name (cross_val_score, SGDClassifier) is not noise: whether prose teaches it decides."""
    core = re.sub(r"\W", "", name)
    return len(core) <= 2 or bool(_NUMBER.fullmatch(name.strip())) or bool(_VARIABLE.fullmatch(name.strip()))


def _salience(kb_dir) -> Dict[str, float]:
    """Global PageRank of the KG nodes (salience.json, written at build time); empty when absent."""
    path = Path(kb_dir) / "salience.json" if kb_dir else None
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}
    except (OSError, ValueError):
        return {}


def _heading_nodes(chapter, index: dict) -> List[str]:
    headings = index.get("headings") or {}
    return [HEAD_MARK + str(headings[str(h)].get("display", "")) for h in chapter.heading_ids if str(h) in headings]


def _home_index(chapter, index: dict):
    """A function node name -> the node's home: None when this chapter's headings mention it at least as often as any
    other section's (at the chapter's own level), else {"section": title, "side": "earlier"|"later"} of the section
    that mentions it most (the earliest on a tie)."""
    heads = index.get("headings") or {}
    root = heads.get(str(chapter.id)) or {}
    level, order = root.get("level"), root.get("order", 0)
    by_display = {}
    for hid, h in heads.items():
        by_display.setdefault(HEAD_MARK + str(h.get("display", "")), hid)
    own = {str(h) for h in chapter.heading_ids}

    def section_of(hid):
        while hid in heads and heads[hid].get("level") != level and heads[hid].get("parent") is not None:
            hid = str(heads[hid]["parent"])
        return hid if heads.get(hid, {}).get("level") == level else None

    def find(graph, name: str) -> Optional[dict]:
        here, counts = 0, Counter()
        for nb in graph.neighbors(name):
            hid = by_display.get(nb)
            if hid is None:
                continue
            section = str(chapter.id) if hid in own else section_of(hid)
            if section == str(chapter.id):
                here += 1
            elif section is not None:
                counts[section] += 1
        if not counts:
            return None
        top = max(counts, key=lambda s: (counts[s], -heads[s].get("order", 0)))
        if counts[top] <= here:
            return None
        return {"section": heads[top]["title"], "side": "later" if heads[top].get("order", 0) > order else "earlier"}

    return find


def _chunks(data: dict) -> List[str]:
    return [c for c in str(data.get("source_id") or "").split(_SEP) if c]


class _Anchor:
    """Ranks a chapter's sentences against a KG text (description), one batched embedding request per inventory."""

    def __init__(self, chapter, store):
        self.chapter, self.store = chapter, store
        self.row = {s["id"]: i for i, s in enumerate(store.sentences) if s["id"] in chapter.sentences}
        self.vec: Dict[str, np.ndarray] = {}

    def prepare(self, texts: List[str]) -> None:
        todo = [t for t in dict.fromkeys(texts) if t and t not in self.vec]
        if todo:
            self.vec.update(zip(todo, embed_transient(self.store.embedder, todo)))

    def rank(self, text: str, sids: List[str]) -> List[tuple]:
        """[(sid, cosine)] of `sids` closest to `text` first."""
        rows = [self.row[s] for s in sids if s in self.row]
        if not text or not rows or text not in self.vec:
            return []
        sims = self.store._vectors(rows) @ self.vec[text]
        ids = [self.store.sentences[r]["id"] for r in rows]
        return sorted(zip(ids, sims.tolist()), key=lambda p: -p[1])

    def in_chunks(self, chunks: List[str]) -> List[str]:
        return [self.store.sentences[i]["id"] for c in chunks for i in self.store.by_chunk.get(c, ())
                if self.store.sentences[i]["id"] in self.chapter.sentences
                and self.chapter.sentences[self.store.sentences[i]["id"]]["kind"] == "text"]

    def best(self, text: str, sids: List[str], floor: float) -> List[str]:
        return [s for s, c in self.rank(text, sids)[:2] if c >= floor]


def build_inventory(chapter, store, graph, index: Optional[dict] = None) -> dict:
    """{nodes: {kid: node}, relations: {rid: relation}, noise: [names], api: [names]} for one chapter.

    node = {name, type, description, sentences, via ("name"|"description"), pids, assets, mentions, span, salience,
    home} (mentions/span/salience/home are signals for the agents, who judge what is key and what is only named in
    passing);
    relation = {a, b, kid_a, kid_b, description, sentences, via ("sentence"|"paragraph"|"description")}.
    Nodes and relations no sentence supports are listed under `ungrounded` for the report, never taught."""
    index = index if index is not None else store.index
    heads = [h for h in _heading_nodes(chapter, index) if graph.has_node(h)]
    names = list(dict.fromkeys(nb for h in heads for nb in graph.neighbors(h)
                               if infer_node_kind(graph.nodes[nb]) == "semantic"))
    prose = {sid: s["text"] for sid, s in chapter.sentences.items() if s["kind"] == "text"}
    code = [s["text"] for s in chapter.sentences.values() if s["kind"] == "code"]
    anchor = _Anchor(chapter, store)
    salience = _salience(getattr(store, "kb_dir", None))
    home = _home_index(chapter, index)
    named: Dict[str, List[str]] = {}
    noise, api, pending = [], [], []
    for name in names:
        if is_noise(name):
            noise.append(name)
            continue
        patterns = name_patterns(name)
        named[name] = [sid for sid, text in prose.items() if any(p.search(text) for p in patterns)]
        if not named[name]:
            pending.append(name)
    anchor.prepare([str(graph.nodes[n].get("description") or "") for n in pending])
    nodes: Dict[str, dict] = {}
    ungrounded: List[str] = []
    for name in names:
        if name in noise:
            continue
        data = graph.nodes[name]
        sentences, via = named[name], "name"
        if not sentences:
            sentences = anchor.best(str(data.get("description") or ""), anchor.in_chunks(_chunks(data)), NODE_ANCHOR_MIN)
            via = "description"
        if not sentences:
            patterns = name_patterns(name)
            (api if any(p.search(t) for t in code for p in patterns) else ungrounded).append(name)
            continue
        assets = [nb for nb in graph.neighbors(name) if infer_node_kind(graph.nodes[nb]) == "asset"]
        pids = sorted({chapter.pid_of[s] for s in sentences if s in chapter.pid_of}, key=lambda p: int(p[1:]))
        nodes[name] = {"name": name, "type": clean_type(data.get("entity_type")), "via": via,
                       "description": " ".join(str(data.get("description") or "").split())[:240],
                       "sentences": sentences, "assets": assets, "pids": pids,
                       "mentions": len(named[name]), "span": len(pids),
                       "salience": round(float(salience.get(name, 0.0)), 5), "home": home(graph, name)}
    relations = _relations(chapter, graph, nodes, anchor, ungrounded)
    kid = {name: f"K{n}" for n, name in enumerate(nodes, 1)}
    return {"nodes": {kid[name]: {**node, "kid": kid[name]} for name, node in nodes.items()},
            "relations": {f"R{n}": {**r, "rid": f"R{n}", "kid_a": kid[r["a"]], "kid_b": kid[r["b"]]}
                          for n, r in enumerate(relations, 1)},
            "noise": noise, "api": api, "ungrounded": ungrounded}


def _relations(chapter, graph, nodes: Dict[str, dict], anchor: _Anchor, ungrounded: List[str]) -> List[dict]:
    """Stated relations between two kept nodes, each anchored: a sentence naming both ends, else the sentence of a
    paragraph naming both closest to the relation's description, else the description anchored in the edge's chunks.
    Mere co-occurrence is not a relation: two names in one sentence are already covered when that sentence is."""
    stated, seen = [], set()
    for a, b, data in graph.edges(data=True):
        if a not in nodes or b not in nodes or a == b:
            continue
        keywords = str(data.get("keywords") or "")
        if any(k in keywords for k in _NOT_STATED):
            continue
        pair = tuple(sorted((a, b)))
        if pair in seen:
            continue
        seen.add(pair)
        stated.append((a, b, " ".join(str(data.get("description") or "").split()), _chunks(data)))
    anchor.prepare([d for _, _, d, _ in stated])
    out = []
    for a, b, description, chunks in stated:
        both = [s for s in nodes[a]["sentences"] if s in set(nodes[b]["sentences"])]
        if both:
            ranked = anchor.rank(description, both)
            sentences, via = [ranked[0][0] if ranked else both[0]], "sentence"
        else:
            pids = set(nodes[a]["pids"]) & set(nodes[b]["pids"])
            pool = [s for p in pids for s in chapter.by_pid[p]["sentences"] if chapter.sentences[s]["kind"] == "text"]
            sentences, via = anchor.best(description, pool, PARAGRAPH_ANCHOR_MIN)[:1], "paragraph"
            if not sentences:
                sentences, via = anchor.best(description, anchor.in_chunks(chunks), RELATION_ANCHOR_MIN)[:1], "description"
        if not sentences:
            ungrounded.append(f"{a} — {b}")
            continue
        out.append({"a": a, "b": b, "description": description[:240], "sentences": sentences, "via": via})
    return out


def summary(inventory: dict) -> dict:
    """Counts for a report: kept nodes by type and grounding, relations by how they were anchored."""
    nodes = inventory["nodes"].values()
    return {"nodes": len(inventory["nodes"]), "by_type": dict(Counter(n["type"] for n in nodes)),
            "node_grounding": dict(Counter(n["via"] for n in nodes)),
            "relations": len(inventory["relations"]),
            "relation_grounding": dict(Counter(r["via"] for r in inventory["relations"].values())),
            "noise": len(inventory["noise"]), "api": len(inventory["api"]), "ungrounded": len(inventory["ungrounded"])}
