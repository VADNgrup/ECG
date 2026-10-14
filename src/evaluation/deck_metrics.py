"""Offline quality metrics of a generated deck (no LLM): coverage of the source checklist, bullet support/completeness,
bullet length against the source, scope leaks, dangling numbered references, and token use from the run log.

    python -m src.evaluation.deck_metrics --lecture <lecture folder name> [--embed openai|hash]
"""
import argparse
import json
from pathlib import Path
from typing import Dict, List

import numpy as np

from src.rag.embedder import Embedder
from src.rag.evidence_store import _informative, EvidenceStore
from src.rag.retrieve_content import find_heading_id, load_structural_index
from src.utils import lang
from src.utils.bullet_text import has_crossref, support_ratio
from src.utils.config import Config
from src.workflow.coverage import build_checklist, coverage_rate, presence, used_sentence_ids
from src.workflow.scope import clean_display_title, find_kb_dir


def _quantiles(values: List[float]) -> Dict[str, float]:
    if not values:
        return {}
    a = np.array(values)
    return {"mean": round(float(a.mean()), 3), "p10": round(float(np.percentile(a, 10)), 3),
            "p50": round(float(np.percentile(a, 50)), 3), "p90": round(float(np.percentile(a, 90)), 3)}


def _is_prose(text: str) -> bool:
    return not text.lstrip().startswith(("```", "$$", "|")) and lang.length_units(text) >= 3


def run_tokens(document_id: str) -> Dict:
    """Largest completed run of this document in logs/llm_runs.jsonl (tokens per phase / api type)."""
    path = Config.LOGS_DIR / "llm_runs.jsonl" if hasattr(Config, "LOGS_DIR") else Path("logs/llm_runs.jsonl")
    best = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("document_id") == document_id and row.get("status") == "completed" \
                    and row.get("total_tokens", 0) >= best.get("total_tokens", 0):
                best = row
    return {k: best.get(k) for k in ("run_id", "total_calls", "prompt_tokens", "completion_tokens", "total_tokens", "by_phase")
            if k in best}


def measure(lecture_dir: Path, embed_backend: str = None) -> Dict:
    stem = lecture_dir.name
    lecture = json.loads((lecture_dir / f"{stem}.json").read_text(encoding="utf-8"))
    meta = lecture["metadata"]
    kb_dir = find_kb_dir(meta["source_document_id"])
    index = load_structural_index(kb_dir)
    roots = [find_heading_id(index, t.strip()) for t in str(meta.get("root_heading") or "").split(";") if t.strip()]
    store = EvidenceStore(kb_dir, [r for r in roots if r is not None], Embedder(backend=embed_backend, cache_dir=kb_dir))
    slides = lecture["slides"]

    bullets, supports, completes, lexical, ratios, leaks, crossrefs = [], [], [], [], [], 0, 0
    for slide in slides:
        facts = {f["fact_id"]: f for f in slide["slide"].get("grounding", {}).get("facts", [])}
        for fact in facts.values():
            leaks += sum(1 for ref in fact.get("source_refs") or [] if int(ref.get("heading_id", -1)) not in store.scope)
        for text, fids in zip(slide.get("content") or [], slide.get("fact_ids") or []):
            if not isinstance(text, str) or not _is_prose(text):
                continue
            source = " ".join(facts[f]["text"] for f in fids if f in facts)
            if not source:
                continue
            bullets.append((text, source))
            lexical.append(support_ratio(text, source))
            ratios.append(lang.length_units(text) / max(lang.length_units(source), 1))
            crossrefs += has_crossref(text) and not has_crossref(source)
    vecs = store.embedder.embed([t for t, _ in bullets] + [s for _, s in bullets]) if bullets else []
    n = len(bullets)
    for i, (text, source) in enumerate(bullets):
        supports.append(float(vecs[i] @ vecs[n + i]))
        terms = lang.salient_terms(source, store.idf, 6)
        have = set(lang.terms(text))
        completes.append(sum(t in have for t in terms) / len(terms) if terms else 1.0)

    rows = presence(store, slides, build_checklist(store, limit=150))
    informative = {r["id"] for r in store.sentences if r["tier"] == "scope" and _informative(r["text"])}
    used = used_sentence_ids(store, slides) & informative
    result = {
        "lecture": stem, "embed": store.embedder.model, "slides": len(slides), "bullets_measured": n,
        "scope_sentences": sum(1 for s in store.sentences if s["tier"] == "scope"),
        "coverage": {"rate": round(coverage_rate(rows), 3), "items": len(rows),
                     "missing": [r["concept"] for r in rows if not r["covered_by"]],
                     "by": {k: sum(1 for r in rows if r["covered_by"] == k) for k in ("used", "named", "similar")}},
        "sentences_used": {"used": len(used), "informative": len(informative), "rate": round(len(used) / max(len(informative), 1), 3)},
        "support_embed": _quantiles(supports), "support_lexical": _quantiles(lexical), "completeness": _quantiles(completes),
        "length_vs_source": _quantiles(ratios),
        "low_support": sum(s < Config.SUPPORT_MIN for s in supports),
        "low_completeness": sum(c < Config.KEYTERM_MIN for c in completes),
        "scope_leaks": leaks, "new_crossrefs": int(crossrefs),
        "prereq_gaps": len(store.prereq_gaps(50)),
        "tokens": run_tokens(meta["source_document_id"] + "__" + stem.split("__", 1)[-1]) or run_tokens(stem),
    }
    store.embedder.save()
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lecture", required=True)
    ap.add_argument("--embed", default=None, choices=[None, "openai", "hash"])
    args = ap.parse_args()
    folder = Config.LECTURES_DIR / args.lecture
    out = measure(folder, args.embed)
    (folder / f"{args.lecture}_deck_metrics.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "tokens"}, ensure_ascii=False, indent=2))
    print("tokens:", json.dumps({k: v for k, v in out["tokens"].items() if k != "by_phase"}), )
    print("by_phase:", json.dumps((out["tokens"] or {}).get("by_phase", {}))[:600])
