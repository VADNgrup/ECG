"""Coverage checklist: what the source scope says that a deck must not forget, and a presence check of a finished deck.

The checklist is built from the KG and the source sentences (no LLM): the deck's important concepts with the sentences
that introduce them. Presence is decided by code (sentence used by a slide, concept named on a slide, or a bullet close in
meaning to the introducing sentence), never by asking a model "what is missing" (LLM judges are near chance at omissions).
"""
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

import numpy as np

from src.rag.evidence_store import _contains, EvidenceStore, SCOPE
from src.rag.idea_candidates import build_candidates, normalize_name, scope_sentences
from src.utils.config import Config


def build_checklist(store: EvidenceStore, limit: int = 60) -> List[dict]:
    """Important in-scope concepts (not generic hubs), each with the sentence ids that introduce it."""
    by_heading = scope_sentences(store.kb_dir, store.index, set(store.scope))
    items = []
    for c in build_candidates(store.kb_dir, set(store.scope), store.index, by_heading):
        if c["generic"] or c["mentions"] < 2:
            continue
        definition = store.definition_of(c["concept"], SCOPE)
        evidence = [i for i in c["evidence"] if store.tier_of(i) == SCOPE]
        if not evidence:
            continue
        items.append({"concept": c["concept"], "key": c["key"], "score": c["score"], "owner": c["owner"],
                      "evidence": evidence, "definition": definition["id"] if definition else None})
        if len(items) == limit:
            break
    return items


def slide_texts(slide: dict) -> List[str]:
    """Everything a viewer or presenter of the slide gets: title, bullets, group titles."""
    out = [str(slide.get("slide", {}).get("slide_title") or "")]
    out += [str(t) for t in slide.get("content") or [] if isinstance(t, str)]
    out += [str(g.get("title", "")) for g in slide.get("groups") or []]
    return [t for t in out if t]


def used_sentence_ids(store: EvidenceStore, slides: Sequence[dict]) -> set:
    """Sentence ids whose source span is (mostly) covered by a fact of some slide, by chunk offsets."""
    spans: Dict[str, List[tuple]] = {}
    for slide in slides:
        for fact in slide.get("slide", {}).get("grounding", {}).get("facts", []):
            for ref in fact.get("source_refs") or []:
                spans.setdefault(ref["chunk_id"], []).append((ref["start"], ref["end"]))
    used = set()
    for row in store.sentences:
        ref = row.get("ref") or {}
        for start, end in spans.get(ref.get("chunk_id"), ()):
            overlap = min(end, ref["end"]) - max(start, ref["start"])
            if overlap > 0.5 * (ref["end"] - ref["start"]):
                used.add(row["id"])
                break
    return used


def presence(store: EvidenceStore, slides: Sequence[dict], items: Sequence[dict]) -> List[dict]:
    """For each checklist item: how a slide covers it (`used` sentence, `named` concept, `similar` bullet) or None."""
    used = used_sentence_ids(store, slides)
    texts = [t.casefold() for s in slides for t in slide_texts(s)]
    bullets = [t for s in slides for t in slide_texts(s)]
    bullet_vecs = store.embedder.embed(bullets) if bullets else np.zeros((0, 1))
    out = []
    for item in items:
        how = None
        if used & set(item["evidence"]):
            how = "used"
        elif any(_contains(t, item["key"]) for t in texts):
            how = "named"
        elif bullets:
            source = store.embedder.embed([store.by_id[item["evidence"][0]]["text"]])[0]
            if float(np.max(bullet_vecs @ source)) >= Config.COVERAGE_MIN:
                how = "similar"
        out.append({**item, "covered_by": how})
    return out


def coverage_rate(rows: Iterable[dict]) -> float:
    rows = list(rows)
    return sum(1 for r in rows if r["covered_by"]) / len(rows) if rows else 1.0
