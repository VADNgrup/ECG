"""Agent 4, Content QA: checks the written deck and settles what it finds, one slide at a time.

Two checks, in one batched LLM call per few slides:
- correctness: is each bullet true to the source sentences it cites (not its depth, completeness or style)? A wrong
  bullet is rewritten by the Writer in one small call (only that bullet, its sentences and the reason); QA checks that
  slide again, at most QA_MAX_ROUNDS times, and a bullet still wrong after that is removed (the Writer then checks the
  slide's layout still fits).
- thin slides (fewer than QA_THIN_BULLETS bullets): the Packet Builder's assembly tool gathers facts of the chapter no
  slide uses yet (the slide's presenter-only paragraphs, uncited sentences next to it or on its topic); QA picks the ones
  that teach the slide's topic, they join its pool and the Writer writes that slide again. A thin slide QA can add
  nothing to is dropped: rewriting it from the same source would give the same few bullets.
A thin slide given new facts is written whole again, so the Writer picks its layout again for its new bullets.
Only the slides it sends back are written and checked again; the rest of the deck is never touched.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from src.models.slide import SlideContent
from src.utils.bullet_text import support_ratio
from src.utils.config import Config
from src.utils.content_cache import cached_json, digest
from src.utils.llm import chat, parse_json_reply
from src.workflow.prompts import load

MAX_WORKERS = 8

_INSTRUCTION = load("content_qa")
_SUPPORTED = 0.8             # a bullet this much made of its cited sentences' words cannot state what they do not say


def bullet_count(content: Optional[SlideContent]) -> int:
    return len(content.content) if content is not None else 0


class ContentQAAgent:
    def __init__(self, model: str):
        self.model = model

    def run(self, pipeline, writer, contents: Dict[str, SlideContent]) -> dict:
        """Check every content slide, send back and re-check only the slides with a problem; `contents` (slide_id ->
        SlideContent) and the pipeline's deck are updated in place. Returns the QA report."""
        report = {"rounds": 0, "calls": 0, "thin": [], "wrong": [], "dropped_slides": [], "dropped_bullets": []}
        enriched: set = set()                    # thin slides already looked at (each only once)
        filled: set = set()                      # thin slides that received facts: settled, never dropped later
        targets = [j["spec"].slide_id for j in pipeline.jobs]
        for round_no in range(1, Config.QA_MAX_ROUNDS + 2):
            if not targets:
                break
            final = round_no > Config.QA_MAX_ROUNDS            # the last check only removes what is still wrong
            report["rounds"] = round_no
            thin, drop = ({}, []) if final else self._thin(pipeline, contents, targets, enriched)
            if drop:
                self._drop(pipeline, contents, drop, "thin slide: no unused fact on its topic", report)
                targets = [t for t in targets if t not in drop]
            verdicts = self._ask(pipeline, contents, targets, thin, report)
            issues: Dict[str, List[str]] = {}
            flagged: Dict[str, List[dict]] = {}           # the wrong bullets of each slide, for a targeted rewrite
            regrown: set = set()                          # thin slides given new facts: written again whole
            empty, unfilled = [], []
            for sid in targets:
                verdict = verdicts.get(sid) or {"issues": [], "add": []}
                wrong = verdict["issues"]
                for row in wrong:
                    report["wrong"].append({"round": round_no, "slide": contents[sid].slide.slide_title,
                                            "bullet": row["text"], "kind": row["kind"], "reason": row["reason"]})
                if final and wrong:
                    if not self._remove_bullets(contents[sid], [row["index"] for row in wrong]):
                        empty.append(sid)
                    else:
                        writer.relayout(self._job(pipeline, sid), contents[sid])
                    report["dropped_bullets"] += [{"slide": contents[sid].slide.slide_title, "bullet": row["text"],
                                                   "reason": row["reason"]} for row in wrong]
                    continue
                if wrong:
                    issues[sid] = [self._issue(row) for row in wrong]
                    flagged[sid] = wrong
                if sid in thin:
                    enriched.add(sid)
                    chosen = [c for c in thin[sid] if c["id"] in verdict["add"]]
                    job = self._job(pipeline, sid)
                    slide = self._planned(pipeline, sid)
                    added = pipeline.assembler(job["chapter"]).add_facts(slide, chosen) if chosen else 0
                    report["thin"].append({"round": round_no, "slide": contents[sid].slide.slide_title,
                                           "bullets": bullet_count(contents[sid]), "candidates": len(thin[sid]),
                                           "added_sentences": added, "action": "enriched" if added else "dropped"})
                    if added:
                        filled.add(sid)
                        regrown.add(sid)
                    if not added:
                        unfilled.append(sid)
                        issues.pop(sid, None)
                        continue
                    pipeline.refresh(sid)
                    issues.setdefault(sid, []).append(
                        f"only {bullet_count(contents[sid])} bullets: the passages now hold new facts; teach every "
                        f"passage.")
            if unfilled:
                self._drop(pipeline, contents, unfilled, "thin slide: no unused fact on its topic", report)
            empty = [s for s in empty if s not in filled]
            if empty:
                self._drop(pipeline, contents, empty, "every bullet was still wrong", report)
            print(f"[ContentQA] round {round_no}: {len(thin) + len(drop)} thin "
                  f"({sum(1 for r in report['thin'] if r['round'] == round_no and r['action'] == 'enriched')} enriched, "
                  f"{sum(1 for r in report['dropped_slides'] if r['round'] == round_no)} dropped), "
                  f"{sum(1 for r in report['wrong'] if r['round'] == round_no)} wrong bullet(s) on "
                  f"{len({r['slide'] for r in report['wrong'] if r['round'] == round_no})} slide(s) of "
                  f"{len(targets)} checked -> rewriting {0 if final else len(issues)} slide(s)")
            if final or not issues:
                break
            whole = {sid: issues[sid] + ["choose the layout again for the bullets the slide now holds."]
                     for sid in issues if sid in regrown}   # new facts: the slide is written again, layout included
            for sid in issues:                   # only wrong bullets: those bullets are rewritten in place
                if sid not in whole:
                    writer.fix_bullets(self._job(pipeline, sid), contents[sid], flagged.get(sid, []))
            jobs = [j for j in pipeline.jobs if j["spec"].slide_id in whole]
            taken = [c.slide.slide_title for s, c in contents.items() if s not in whole]
            for content in (writer.write(jobs, whole, taken) if jobs else []):
                contents[content.slide.slide_id] = content
            targets = [sid for sid in issues if sid in contents]
        dropped = bool(report["dropped_slides"] or report["dropped_bullets"])
        report["status"] = "passed_with_warnings" if dropped else "passed"
        report["issues"] = []
        return report

    # ── thin slides: unused facts the Packet Builder's tool can offer ─────────
    def _thin(self, pipeline, contents, targets, enriched):
        """{slide_id: candidates} for thin slides that have some; ids of thin slides with none (to drop)."""
        cited = {s for c in contents.values() for ids in c.fact_ids for s in ids}
        thin, drop = {}, []
        for sid in targets:
            if sid in enriched or bullet_count(contents.get(sid)) >= Config.QA_THIN_BULLETS:
                continue
            job = self._job(pipeline, sid)
            state = pipeline.state[job["chapter"]]
            owners = {p: s["title"] for s in state["slides"] if s.get("slide_id") != sid for p in s["paragraphs"]}
            slide = {**self._planned(pipeline, sid), "bullets": list(contents[sid].content)}
            found = pipeline.assembler(job["chapter"]).candidates(slide, cited, owners)
            if found:
                thin[sid] = found
            else:
                enriched.add(sid)
                drop.append(sid)
        return thin, drop

    # ── one call per batch of slides ──────────────────────────────────────────
    def _ask(self, pipeline, contents, targets, thin, report) -> Dict[str, dict]:
        size = max(1, Config.QA_BATCH)
        batches = [targets[k:k + size] for k in range(0, len(targets), size)]

        def one(batch):
            slides, rows = [], {}
            for n, sid in enumerate(batch):
                content, job = contents[sid], self._job(pipeline, sid)
                texts = {s: t for row in job["built"]["pool"].values() for s, t in row["sentences"]}
                texts.update({a["sid"]: a["text"] for k in ("formula", "code")         # a cited formula or listing
                              for a in job["built"]["assets"].get(k) or []})
                bullets = []
                for k, text in enumerate(content.content):
                    sources = [texts[s] for s in content.fact_ids[k] if s in texts]
                    rows[(n, f"b{k + 1}")] = (sid, k, text, sources)
                    bullets.append({"id": f"b{k + 1}", "text": text, "sources": sources})
                entry = {"slide": n, "title": content.slide.slide_title, "bullets": bullets}
                formulas = content.slide.latex_formulas or [content.slide.latex_block_formula or ""]
                if any(formulas):
                    entry["formula"] = "\n".join(f for f in formulas if f)[:800]
                if sid in thin:
                    entry["candidates"] = [{"id": c["id"], "text": c["text"], "from": c["source"]} for c in thin[sid]]
                slides.append(entry)
            payload = {"slides": slides, "task": "Review these slides as the output schema says."}
            messages = [{"role": "system", "content": _INSTRUCTION},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
            try:
                answer = cached_json(f"content_qa_v1.{digest(_INSTRUCTION)[:10]}", self.model, payload,
                                     lambda: parse_json_reply(chat(self.model, messages, temperature=0, max_tokens=4000,
                                                                   role="qa", json_mode=True)))
            except (ValueError, RuntimeError) as exc:
                print(f"[ContentQA] unreadable answer for {len(batch)} slide(s): {str(exc)[:120]}")
                if len(batch) > 1:               # a batch that made the model loop: each slide asked on its own
                    return {sid: verdict for s in batch for sid, verdict in one([s]).items()}
                answer = {}
            out = {sid: {"issues": [], "add": []} for sid in batch}
            for row in (answer.get("slides") if isinstance(answer, dict) else None) or []:
                try:
                    n = int(row.get("slide"))
                    sid = batch[n]
                except (TypeError, ValueError, IndexError, AttributeError):
                    continue
                for issue in row.get("issues") or []:
                    if not isinstance(issue, dict) or (n, str(issue.get("bullet"))) not in rows:
                        continue
                    kind = str(issue.get("kind") or "wrong")
                    if kind not in ("wrong", "unsupported"):
                        continue
                    _, index, text, sources = rows[(n, str(issue.get("bullet")))]
                    claim = " ".join(str(issue.get("claim") or "").split())
                    if not claim or claim.casefold() not in " ".join(text.split()).casefold():
                        continue                 # no exact wrong words named: nothing the Writer could fix
                    if kind == "unsupported" and (support_ratio(text, " ".join(sources)) >= _SUPPORTED
                                                  or support_ratio(claim, " ".join(sources)) >= _SUPPORTED):
                        continue                 # the named words are the sources' own
                    if index not in {i["index"] for i in out[sid]["issues"]}:
                        out[sid]["issues"].append({"index": index, "text": text, "kind": kind, "sources": sources, "claim": claim,
                                                   "reason": " ".join(str(issue.get("reason") or kind).split())})
                ids = {c["id"] for c in thin.get(sid, [])}
                out[sid]["add"] = [str(c) for c in row.get("add") or [] if str(c) in ids]
            return out

        verdicts: Dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for result in pool.map(one, batches):
                verdicts.update(result)
        report["calls"] += len(batches)
        return verdicts

    # ── settling ───────────────────────────────────────────────────────────────
    @staticmethod
    def _issue(row: dict) -> str:
        """What the Writer is told about one wrong bullet: the error and the sentences the bullet cites."""
        cited = "; ".join(f"'{s[:120]}'" for s in row["sources"]) or "nothing"
        return (f"bullet '{row['text'][:90]}' is {row['kind']} ('{row.get('claim', '')[:80]}'): {row['reason']} It cites "
                f"{cited}. Correct those words so a cited sentence states them, cite the passage sentence that does, or "
                f"leave the bullet out.")

    @staticmethod
    def _job(pipeline, sid: str) -> dict:
        return next(j for j in pipeline.jobs if j["spec"].slide_id == sid)

    @staticmethod
    def _planned(pipeline, sid: str) -> dict:
        return next(s for state in pipeline.state.values() for s in state["slides"] if s.get("slide_id") == sid)

    @staticmethod
    def _remove_bullets(content: SlideContent, indices: List[int]) -> bool:
        """Remove bullets by index, keeping groups consistent; False when none would be left (the slide is kept then)."""
        gone = set(indices)
        keep = [k for k in range(len(content.content)) if k not in gone]
        if not keep:
            return False
        new_index = {old: new for new, old in enumerate(keep)}
        content.content = [content.content[k] for k in keep]
        content.fact_ids = [content.fact_ids[k] for k in keep]
        content.bullet_sources = [content.bullet_sources[k] for k in keep] if content.bullet_sources else []
        groups = []
        for g in content.groups or []:
            items = [new_index[i] for i in g.get("item_indices") or [] if i in new_index]
            if items:
                groups.append({**g, "item_indices": items})
        content.groups = groups
        return True

    @staticmethod
    def _drop(pipeline, contents, sids, reason, report) -> None:
        for sid in sids:
            if sid in contents:
                report["dropped_slides"].append({"round": report["rounds"], "slide": contents[sid].slide.slide_title,
                                                 "reason": reason})
                contents.pop(sid)
        pipeline.drop(sids, reason)
