"""Agent 4, Content QA: checks the written deck, settles what code can and sends the rest back to the Writer.

The audit runs in rounds, each one pass of the graph's QA node; the QA Router sends the slides it finds wrong back to
the Writer, told what to fix (the paper's localized repair), at most QA_MAX_ROUNDS times. In one batched LLM call per few
slides QA:
- judges each bullet against the source sentences it cites (not its depth, completeness or style) and, for a bullet it
  flags or the code checks flagged (numbers its sentences lack, words they hardly hold), suggests a `fix` from those
  sentences, taken when the code checks accept it; a bullet with no acceptable fix goes back to the
  Writer, who writes only that bullet again from its sentences. An empty fix means the sentences do not support the
  bullet: it is cited again from the slide's sentences its words point to, else removed;
- what a slide draws (a formula, a listing) is not judged here: the Writer draws only what its bullets cite;
- fills a thin slide (fewer than QA_THIN_BULLETS bullets): the Packet Builder's assembly tool gathers facts of the
  chapter no slide uses yet; QA writes a bullet for each one on the slide's topic and the facts join its pool. A thin
  slide QA can add nothing to is dropped; so is one the last check or the repeat removal leaves under the minimum
  and its unused facts cannot fill.
The next round asks only the bullets the Writer or QA wrote again and the bullets QA added (QA_MAX_ROUNDS rounds, then a last
check removes what is still wrong); a bullet QA passed is settled and never asked again, and so is one far from its
sentences' words that QA found no acceptable rewording for.
The Writer's code then checks the slide's layout still fits.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from src.models.slide import SlideContent, SlideType
from src.utils.bullet_text import overlap, support_ratio
from src.utils.config import Config
from src.utils.content_cache import cached_json, digest
from src.utils.llm import chat, parse_json_reply
from src.workflow import bullet_checks as checks
from src.workflow.packet_assembly import clean_ocr_latex, formula_key
from src.workflow.prompts import load

MAX_WORKERS = 8
_REMOVED = ("wrong", "unsupported", "formula")  # flags that remove a bullet still flagged after the last round



def bullet_count(content: Optional[SlideContent]) -> int:
    return len(content.content) if content is not None else 0


class ContentQAAgent:
    def __init__(self, model: str, domain: str = "general"):
        self.model = model
        self.prompt = load("content_qa", domain)

    def start(self, pipeline, writer, contents: Dict[str, SlideContent]) -> dict:
        """The audit's own record, kept in the state's review: the report, the bullets QA passed (never asked again),
        the thin slides looked at and filled, the slides to check next and the round. Bullets that only repeat an
        earlier slide go first."""
        report = {"rounds": 0, "calls": 0, "thin": [], "wrong": [], "fixed": 0, "recited": [], "visual": [], "moved_tables": [],
                  "dropped_slides": [], "dropped_bullets": [], "rewritten": []}
        targets = self._unrepeat(pipeline, writer, contents, [j["spec"].slide_id for j in pipeline.jobs], report)
        return {"report": report, "enriched": set(), "filled": set(), "settled": {}, "targets": targets, "round": 0}

    def audit(self, pipeline, writer, contents: Dict[str, SlideContent], memo: dict) -> Dict[str, List[str]]:
        """One round over the slides to check: the code checks, then the review. What the code settles it settles (a
        bullet resting on no sentence goes, an unsupported one is cited again or goes, one QA cannot reword closer to
        its sentences stays) and thin slides get unused facts with the bullets QA writes; every other flagged bullet goes back to
        the Writer: {slide_id: what to fix}. The round after QA_MAX_ROUNDS only removes what is still wrong.
        `contents` and the pipeline's deck change in place; the slides to check next are kept in `memo`."""
        report, enriched, filled, settled = memo["report"], memo["enriched"], memo["filled"], memo["settled"]
        targets = [t for t in memo["targets"] if t in contents]
        feedback: Dict[str, List[str]] = {}
        if not targets:
            memo["targets"] = []
            return feedback
        memo["round"] += 1
        round_no = memo["round"]
        final = round_no > Config.QA_MAX_ROUNDS            # the last check only removes what is still wrong
        report["rounds"] = round_no
        thin, drop = ({}, []) if final else self._thin(pipeline, contents, targets, enriched, report)
        if drop:
            self._drop(pipeline, contents, drop, "thin slide: no unused fact on its topic", report)
            targets = [t for t in targets if t not in drop]
        verdicts = self._ask(pipeline, contents, targets, thin, report, settled)
        for sid in targets:                               # every bullet asked and not flagged is settled
            verdict = verdicts.get(sid) or {}
            flagged_at = {row["index"] for row in verdict.get("issues", [])} | set(verdict.get("uncited", []))
            settled.setdefault(sid, set()).update(
                self._key(contents[sid], k) for k in range(len(contents[sid].content)) if k not in flagged_at)
        for sid in targets:                               # a bullet resting on no sentence of its slide is removed
            gone = (verdicts.get(sid) or {}).get("uncited") or []
            if gone and sid in contents:
                report["dropped_bullets"] += [{"slide": contents[sid].slide.slide_title,
                                               "bullet": contents[sid].content[k],
                                               "reason": "it cites no sentence of its slide"} for k in gone]
                shift = {k: k - sum(g < k for g in gone) for k in range(len(contents[sid].content)) if k not in gone}
                for row in (verdicts.get(sid) or {}).get("issues", []):
                    row["index"] = shift[row["index"]]
                if self._remove_bullets(contents[sid], gone):
                    writer.relayout(self._job(pipeline, sid), contents[sid])
        changed: set = set()                              # slides QA filled: checked again
        empty, unfilled = [], []
        for sid in targets:
            if sid not in contents:
                continue
            verdict = verdicts.get(sid) or {"issues": [], "add": {}}
            content, job = contents[sid], self._job(pipeline, sid)
            wrong = verdict["issues"]
            for row in wrong:
                report["wrong"].append({"round": round_no, "slide": content.slide.slide_title,
                                        "bullet": row["text"], "kind": row["kind"], "reason": row["reason"]})
            if final:                                   # what is still wrong, or still stated by no sentence, goes
                rows_of = self._rows(job)
                wrong = [row for row in wrong if row["kind"] in _REMOVED
                         or row["kind"] == "unlike" and not checks.anchor(rows_of, row["text"])]
                if not wrong:
                    continue
                if not self._remove_bullets(content, [row["index"] for row in wrong]):
                    empty.append(sid)
                else:
                    writer.relayout(job, content)
                report["dropped_bullets"] += [{"slide": content.slide.slide_title, "bullet": row["text"],
                                               "reason": row["reason"]} for row in wrong]
                continue
            if wrong:
                back, fixed, gone = self._triage(job, content, wrong, report, settled.setdefault(sid, set()))
                if back:
                    feedback[sid] = [{**r, "index": r["index"] - sum(g < r["index"] for g in gone)} for r in back]
                if gone:
                    # no support for these points in the slide's sentences: removed now, not asked again
                    report["dropped_bullets"] += [{"slide": content.slide.slide_title, "bullet": r["text"],
                                                   "reason": f"no source support: {r['reason']}"}
                                                  for r in wrong if r["index"] in gone]
                    if not self._remove_bullets(content, sorted(gone)) and sid not in filled:
                        feedback.pop(sid, None)
                        empty.append(sid)
                        continue
                if fixed:
                    changed.add(sid)
                if fixed or gone:
                    writer.relayout(job, content)
            if sid in thin:
                enriched.add(sid)
                chosen = [c for c in thin[sid] if verdict["add"].get(c["id"])]
                added = pipeline.assembler(job["chapter"]).add_facts(self._planned(pipeline, sid), chosen) if chosen else 0
                kept = not added and self._draws_asset(job, content)
                report["thin"].append({"round": round_no, "slide": content.slide.slide_title,
                                       "bullets": bullet_count(content), "candidates": len(thin[sid]),
                                       "added_sentences": added,
                                       "action": "enriched" if added else "kept" if kept else "dropped"})
                if not added:
                    if not kept:
                        unfilled.append(sid)
                    continue
                filled.add(sid)
                pipeline.refresh(sid)
                self._append(content, job, chosen, verdict["add"])
                writer.relayout(job, content)
                changed.add(sid)
        if unfilled:
            self._drop(pipeline, contents, unfilled, "thin slide: no unused fact on its topic", report)
            for sid in unfilled:
                feedback.pop(sid, None)
        empty = [s for s in empty if s not in filled]
        if empty:
            self._drop(pipeline, contents, empty, "every bullet was still wrong", report)
        print(f"[ContentQA] round {round_no}: {len(thin) + len(drop)} thin "
              f"({sum(1 for r in report['thin'] if r['round'] == round_no and r['action'] == 'enriched')} enriched, "
              f"{sum(1 for r in report['dropped_slides'] if r['round'] == round_no)} dropped), "
              f"{sum(1 for r in report['wrong'] if r['round'] == round_no)} wrong bullet(s) on "
              f"{len({r['slide'] for r in report['wrong'] if r['round'] == round_no})} slide(s) of "
              f"{len(targets)} checked -> {0 if final else len(feedback)} slide(s) back to the Writer")
        if final:
            memo["targets"] = []
            return {}
        report["rewritten"] += [{"round": round_no, "slide": contents[sid].slide.slide_title,
                                 "issues": [f"bullet '{r['text']}' is {r['kind']}: {r['reason']}" for r in rows]}
                                for sid, rows in feedback.items()]
        # next round: the slides the Writer writes again, the ones QA changed, and those with a bullet still flagged
        memo["targets"] = [sid for sid in targets if sid in contents and (sid in feedback or sid in changed or any(
            self._key(contents[sid], k) not in settled.get(sid, set()) for k in range(len(contents[sid].content))))]
        return feedback

    def finish(self, pipeline, writer, contents: Dict[str, SlideContent], memo: dict) -> dict:
        """The deck once its bullets are settled: repeats and doubly drawn formulas or listings removed, tables placed;
        the report with its status."""
        report = memo["report"]
        # bullets the Writer rewrote or QA added may say again what another slide says
        self._unrepeat(pipeline, writer, contents, [j["spec"].slide_id for j in pipeline.jobs], report)
        self._undouble(pipeline, writer, contents, report)
        self._last_thin(pipeline, writer, contents, report)
        self._settle_tables(pipeline, writer, contents, report)
        dropped = bool(report["dropped_slides"] or report["dropped_bullets"])
        report["status"] = "passed_with_warnings" if dropped else "passed"
        report["issues"] = []
        return report

    def _triage(self, job: dict, content: SlideContent, rows: List[dict], report: dict, settled: set):
        """What the code settles of the flagged bullets, and what goes back to the Writer. QA's fix is taken when the
        code checks accept it; a bullet the sources do not support (an empty fix) is cited again from the slide's
        sentences its words point to, when there are such others, else removed; a bullet far from its sentences'
        words QA found no acceptable rewording for stays as it is when a sentence of the slide holds its words
        (`checks.anchor`), else it goes back to the Writer to state what its sentences state (nothing on the slide said
        it: the Writer's own knowledge, such as a definition another slide teaches; the last round removes it if it
        still is); every other flagged bullet goes back to the Writer with QA's reason.
        QA's rewording of a bullet far from its sentences is taken only when those sentences or the slide's hold its words
        (QA rewrote one into a definition from its own knowledge). Returns (bullets to write again, bullets fixed or
        cited again, bullets to remove)."""
        back, fixed, gone = [], 0, []
        slide_rows = self._rows(job)
        stated = lambda text, sources: (support_ratio(text, " ".join(sources)) >= Config.LANGUAGE_DRIFT_SUPPORT
                                        or bool(checks.anchor(slide_rows, text)))     # its sentences or the slide's say it
        for r in rows:
            fix = r.get("fix")
            formulas = [a["text"] for a in job["built"]["assets"].get("formula") or []]
            if fix and fix != r["text"] and checks.acceptable(fix, " ".join(r["sources"])) and (
                    r["kind"] != "unlike" or stated(fix, r["sources"])) and not checks.copied_formula(fix, formulas):
                content.content[r["index"]] = fix
                fixed += 1
                report["fixed"] += 1
                if r["kind"] == "unlike":                # written from its sentences: other words stay its own
                    settled.add(self._key(content, r["index"]))
            elif fix == "":
                cites = checks.anchor(self._rows(job), r["text"])
                if cites and set(cites) != set(content.fact_ids[r["index"]]):
                    self._cite(content, job, r["index"], cites)        # it cited the wrong sentences
                    report["recited"].append({"slide": content.slide.slide_title, "bullet": r["text"]})
                    fixed += 1
                else:
                    gone.append(r["index"])
            elif r["kind"] == "unlike" and not checks.anchor(slide_rows, r["text"]):
                # no sentence of the slide states it, nor QA's rewording: the Writer writes what its sentences state
                back.append({"index": r["index"], "text": r["text"], "kind": "unlike",
                             "reason": "no sentence of the slide states this; write only what the sentences it cites "
                                       "state, in your own words"})
            elif r["kind"] == "unlike":                 # no acceptable rewording, but the slide states it: kept
                settled.add(self._key(content, r["index"]))
            else:
                back.append({"index": r["index"], "text": r["text"], "kind": r["kind"], "reason": r["reason"]})
        return back, fixed, gone

    @staticmethod
    def _append(content: SlideContent, job: dict, chosen: List[dict], texts: Dict[str, str]) -> None:
        """QA's bullets for the facts added to a thin slide, each citing its candidate's sentences, in the last group."""
        refs = job["built"].get("refs") or {}
        start = len(content.content)
        for c in chosen:
            content.content.append(" ".join(str(texts[c["id"]]).split()))
            content.fact_ids.append(list(c["sids"]))
            if content.bullet_sources is not None:
                content.bullet_sources.append([refs[s] for s in c["sids"] if s in refs])
        new = list(range(start, len(content.content)))
        if content.groups:
            content.groups[-1] = {**content.groups[-1], "item_indices": list(content.groups[-1]["item_indices"]) + new}
        else:
            content.groups = [{"group_id": "g0", "title": "", "item_indices": new}]

    @staticmethod
    def _drawn(content: SlideContent, job: dict) -> str:
        """What the slide draws, as QA reads it: its formulas, the first lines of its listing ("" when nothing)."""
        spec = content.slide
        if spec.latex_formulas or spec.latex_block_formula:
            return "\n".join(f for f in (spec.latex_formulas or [spec.latex_block_formula]) if f)
        if spec.code_block:
            return "\n".join(str(spec.code_block.get("code") or "").splitlines()[:Config.QA_DRAWN_LINES])
        return ""

    @staticmethod
    def _undraw(writer, job: dict, content: SlideContent, verdict: str, report: dict) -> None:
        """A drawn formula or listing that another slide draws is not drawn here: it goes to the notes."""
        spec = content.slide
        drawn = ContentQAAgent._drawn(content, job)
        if not drawn:
            return
        kind = "code" if spec.code_block else "formula"
        if kind == "formula":
            content.speaker_notes = (content.speaker_notes or []) + [{"formula": f} for f in
                                                                     (spec.latex_formulas or [spec.latex_block_formula]) if f]
        spec.latex_block_formula, spec.latex_formulas, spec.code_block = None, [], None
        spec.slide_type = SlideType.CONTENT
        content.layout_structure = {**(content.layout_structure or {}),
                                    "visual_plan": {"type": "none", "source": "none", "primary": False}}
        report["visual"].append({"slide": spec.slide_title, "kind": kind, "verdict": verdict, "drawn": drawn[:200]})
        writer.relayout(job, content)

    # ── thin slides: unused facts the Packet Builder's tool can offer ─────────
    @staticmethod
    def _draws_asset(job: dict, content: SlideContent) -> bool:
        """A slide with bullets that draws a figure, table, formula or listing of the document stays however thin it is."""
        assets = job["built"]["assets"]
        return bullet_count(content) > 0 and any(assets.get(kind) for kind in ("images", "table", "formula", "code"))

    def _candidates(self, pipeline, contents, sid: str, cited: set) -> List[dict]:
        """Facts of the slide's chapter on its topic that no slide uses yet (the Packet Builder's assembly tool)."""
        job = self._job(pipeline, sid)
        state = pipeline.state[job["chapter"]]
        owners = {p: s["title"] for s in state["slides"] if s.get("slide_id") != sid for p in s["paragraphs"]}
        slide = {**self._planned(pipeline, sid), "bullets": list(contents[sid].content)}
        return pipeline.assembler(job["chapter"]).candidates(slide, cited, owners)

    def _thin(self, pipeline, contents, targets, enriched, report):
        """{slide_id: candidates} for thin slides that have some; ids of thin slides with none (to drop), unless they
        draw one of the document's assets."""
        cited = {s for c in contents.values() for ids in c.fact_ids for s in ids}
        thin, drop = {}, []
        for sid in targets:
            if sid in enriched or bullet_count(contents.get(sid)) >= Config.QA_THIN_BULLETS:
                continue
            job = self._job(pipeline, sid)
            found = self._candidates(pipeline, contents, sid, cited)
            if found:
                thin[sid] = found
            else:
                enriched.add(sid)
                if self._draws_asset(job, contents[sid]):
                    report["thin"].append({"round": report["rounds"], "slide": contents[sid].slide.slide_title,
                                           "bullets": bullet_count(contents[sid]), "candidates": 0, "added_sentences": 0,
                                           "action": "kept"})
                else:
                    drop.append(sid)
        return thin, drop

    def _last_thin(self, pipeline, writer, contents: Dict[str, SlideContent], report: dict) -> None:
        """No slide stays under QA_THIN_BULLETS after the last check and the repeat removal: such a slide gets its
        chapter's unused facts on its topic once more (QA writes a bullet for each, kept when the code checks pass it)
        and is dropped when they do not bring it to the minimum, unless it draws one of the document's assets."""
        low = [sid for sid, c in contents.items() if bullet_count(c) < Config.QA_THIN_BULLETS
               and not self._draws_asset(self._job(pipeline, sid), c)]
        if not low:
            return
        cited = {s for c in contents.values() for ids in c.fact_ids for s in ids}
        thin = {sid: found for sid in low if (found := self._candidates(pipeline, contents, sid, cited))}
        settled = {sid: {self._key(contents[sid], k) for k in range(len(contents[sid].content))} for sid in thin}
        verdicts = self._ask(pipeline, contents, list(thin), thin, report, settled) if thin else {}
        drop = []
        for sid in low:
            content, job = contents[sid], self._job(pipeline, sid)
            add = (verdicts.get(sid) or {}).get("add") or {}
            chosen = [c for c in thin.get(sid, []) if add.get(c["id"])
                      and not checks.issues(add[c["id"]], ["c"], [("c", c["text"])])]
            added = (pipeline.assembler(job["chapter"]).add_facts(self._planned(pipeline, sid), chosen)
                     if bullet_count(content) + len(chosen) >= Config.QA_THIN_BULLETS else 0)
            if not added:
                drop.append(sid)
                continue
            pipeline.refresh(sid)
            self._append(content, job, chosen, add)
            writer.relayout(job, content)
            report["thin"].append({"round": report["rounds"], "slide": content.slide.slide_title,
                                   "bullets": bullet_count(content), "candidates": len(thin[sid]),
                                   "added_sentences": added, "action": "enriched after the last check"})
        if drop:
            self._drop(pipeline, contents, drop, "thin slide after the last check: no unused fact on its topic", report)

    # ── one call per batch of slides ──────────────────────────────────────────
    @staticmethod
    def _key(content: SlideContent, k: int) -> tuple:
        """A bullet as QA judged it: its text and the sentences it cites."""
        return content.content[k], tuple(content.fact_ids[k])

    @staticmethod
    def _rows(job: dict) -> List[tuple]:
        """The slide's citable sentences in source order: its pool, then its formulas and listings."""
        return checks.citable(job["built"])

    @staticmethod
    def _cite(content: SlideContent, job: dict, k: int, cites: List[str]) -> None:
        content.fact_ids[k] = list(cites)
        if content.bullet_sources:
            refs = job["built"].get("refs") or {}
            content.bullet_sources[k] = [refs[s] for s in cites if s in refs]

    def _check(self, pipeline, contents, sid: str, fresh: List[int]) -> dict:
        """The code checks on the slide's unsettled bullets: cites anchored or completed from the slide's sentences and
        formulas (words, then numbers), then numbers and words they hardly hold. {issues (found by code), uncited (resting on no sentence), ask (every
        other bullet: sent to the review, a flagged one with its flag to be fixed)}."""
        content, job = contents[sid], self._job(pipeline, sid)
        rows = self._rows(job)
        known = {s for s, _ in rows}
        paragraph_of = pipeline.chapter_of(job["chapter"]).pid_of
        out = {"issues": [], "uncited": [], "ask": []}
        for k in fresh:
            text = content.content[k]
            cites = [s for s in content.fact_ids[k] if s in known] or checks.anchor(rows, text)
            if not cites:
                out["uncited"].append(k)
                continue
            cites = checks.with_numbers(rows, text, checks.with_own_paragraph(
                rows, text, checks.complete(rows, text, cites), paragraph_of))
            self._cite(content, job, k, cites)
            found = checks.issues(text, cites, rows, [a["text"] for a in job["built"]["assets"].get("formula") or []])
            sources = [t for s, t in rows if s in set(cites)]
            out["issues"] += [{"index": k, "text": text, "sources": sources, "fix": None, **f} for f in found[:1]]
            out["ask"].append(k)
        return out

    def _ask(self, pipeline, contents, targets, thin, report, settled) -> Dict[str, dict]:
        """Verdicts for the bullets of `targets` QA has not passed yet (and the candidates of thin slides): the code
        checks first, then one review call per batch of slides, each bullet sent with the sentences it cites (a bullet
        the code flagged with its flag, for QA to fix) and the slide's candidates."""
        size = max(1, Config.QA_BATCH)
        fresh = lambda sid: [k for k in range(len(contents[sid].content))
                             if self._key(contents[sid], k) not in settled.get(sid, set())]
        coded = {sid: self._check(pipeline, contents, sid, fresh(sid)) for sid in targets}
        asked = [sid for sid in targets if coded[sid]["ask"] or sid in thin]
        batches = [asked[k:k + size] for k in range(0, len(asked), size)]

        def one(batch):
            slides, rows = [], {}
            for n, sid in enumerate(batch):
                content, job = contents[sid], self._job(pipeline, sid)
                texts = dict(self._rows(job))
                flags = {row["index"]: row for row in coded[sid]["issues"]}
                bullets, rewrite = [], []
                for k in coded[sid]["ask"]:
                    text, cites = content.content[k], content.fact_ids[k]
                    sources = [texts[s] for s in cites if s in texts]
                    rows[(n, f"b{k + 1}")] = (sid, k, text, sources)
                    item = {"id": f"b{k + 1}", "text": text, "sources": sources}
                    if k in flags:                       # found wrong by the code: QA writes it again
                        rewrite.append({**item, "why": f"{flags[k]['kind']}: {flags[k]['reason']}"})
                    else:
                        bullets.append(item)
                entry = {"slide": n, "title": content.slide.slide_title, "bullets": bullets}
                if job["built"].get("language"):
                    entry["language"] = job["built"]["language"]
                if rewrite:
                    entry["rewrite"] = rewrite
                if sid in thin:
                    entry["candidates"] = [{"id": c["id"], "text": c["text"], "from": c["source"]} for c in thin[sid]]
                slides.append(entry)
            payload = {"slides": slides, "task": "Review and correct these slides as the output schema says."}
            messages = [{"role": "system", "content": self.prompt},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
            try:
                answer = cached_json(f"content_qa_v2.{digest(self.prompt)[:10]}", self.model, payload,
                                     lambda: parse_json_reply(chat(self.model, messages, temperature=0, max_tokens=6000,
                                                                   role="qa", json_mode=True)))
            except (ValueError, RuntimeError) as exc:
                print(f"[ContentQA] unreadable answer for {len(batch)} slide(s): {str(exc)[:120]}")
                if len(batch) > 1:               # a batch that made the model loop: each slide asked on its own
                    return {sid: verdict for s in batch for sid, verdict in one([s]).items()}
                answer = {}
            out = {sid: {"issues": [], "add": {}} for sid in batch}
            for row in (answer.get("slides") if isinstance(answer, dict) else None) or []:
                try:
                    n = int(row.get("slide"))
                    sid = batch[n]
                except (TypeError, ValueError, IndexError, AttributeError):
                    continue
                flags = {r["index"]: r for r in coded[sid]["issues"]}
                for rewritten in row.get("rewrites") or []:          # the code's findings, written again by QA
                    if isinstance(rewritten, dict) and (n, str(rewritten.get("bullet"))) in rows:
                        index = rows[(n, str(rewritten.get("bullet")))][1]
                        if index in flags and isinstance(rewritten.get("text"), str):
                            flags[index]["fix"] = " ".join(rewritten["text"].split())
                for issue in row.get("issues") or []:
                    if not isinstance(issue, dict) or (n, str(issue.get("bullet"))) not in rows:
                        continue
                    _, index, text, sources = rows[(n, str(issue.get("bullet")))]
                    if index in flags:
                        continue
                    fix = issue.get("fix")
                    fix = " ".join(str(fix).split()) if isinstance(fix, str) else None
                    kind = str(issue.get("kind") or "wrong")
                    if kind not in ("wrong", "unsupported"):
                        continue
                    claim = " ".join(str(issue.get("claim") or "").split())
                    if not claim or claim.casefold() not in " ".join(text.split()).casefold():
                        continue                 # no exact wrong words named: nothing to correct
                    if kind == "unsupported" and (support_ratio(text, " ".join(sources)) >= Config.CITE_SUPPORT_MIN
                                                  or support_ratio(claim, " ".join(sources)) >= Config.CITE_SUPPORT_MIN):
                        continue                 # the named words are the sources' own
                    if index not in {i["index"] for i in out[sid]["issues"]}:
                        out[sid]["issues"].append({"index": index, "text": text, "kind": kind, "sources": sources,
                                                   "claim": claim, "fix": fix,
                                                   "reason": " ".join(str(issue.get("reason") or kind).split())})
                ids = {c["id"] for c in thin.get(sid, [])}
                out[sid]["add"] = {str(a.get("id")): " ".join(str(a.get("text") or "").split())
                                   for a in row.get("add") or [] if isinstance(a, dict) and str(a.get("id")) in ids
                                   and str(a.get("text") or "").strip()}
            return out

        verdicts: Dict[str, dict] = {sid: {"issues": [], "add": {},
                                           "uncited": coded[sid]["uncited"]} for sid in targets}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for result in pool.map(one, batches):
                for sid, verdict in result.items():
                    verdicts[sid]["issues"] += verdict["issues"]
                    verdicts[sid]["add"] = verdict["add"]
        for sid in targets:                      # the code's findings, each with QA's fix when it wrote one
            verdicts[sid]["issues"] = coded[sid]["issues"] + verdicts[sid]["issues"]
        report["calls"] += len(batches)
        return verdicts

    # ── settling ───────────────────────────────────────────────────────────────
    @staticmethod
    def _job(pipeline, sid: str) -> dict:
        return next(j for j in pipeline.jobs if j["spec"].slide_id == sid)

    @staticmethod
    def _planned(pipeline, sid: str) -> dict:
        return next(s for state in pipeline.state.values() for s in state["slides"] if s.get("slide_id") == sid)

    def _settle_tables(self, pipeline, writer, contents: Dict[str, SlideContent], report: dict) -> None:
        """A drawn table from a section none of the slide's bullets cites does not belong beside them: it takes a page of
        its own after the slide. Tables that cannot be drawn faithfully go to the notes."""
        for sid, content in contents.items():
            spec, job = content.slide, self._job(pipeline, sid)
            chapter = pipeline.chapter_of(job["chapter"])
            cited = {chapter.by_pid[chapter.pid_of[s]]["heading"] for ids in content.fact_ids for s in ids if s in chapter.pid_of}
            tables = job["built"]["assets"].get("table") or []
            if spec.table and tables and chapter.by_pid[tables[0]["pid"]]["heading"] not in cited:
                report["moved_tables"].append({"slide": spec.slide_title, "table": spec.table.table_caption or tables[0]["pid"]})
                spec.more_tables = [spec.table, *spec.more_tables]
                spec.table = None
                spec.slide_type = SlideType.CONTENT
                writer.relayout(job, content)
            if spec.tables_not_drawn:
                content.speaker_notes = (content.speaker_notes or []) + [{"passage": t} for t in spec.tables_not_drawn]

    @staticmethod
    def _shown(content: SlideContent, job: dict) -> List[tuple]:
        """(kind, text, the sids of the slide's candidates with that text) for each formula or listing it draws."""
        spec, assets = content.slide, job["built"]["assets"]
        if spec.code_block:
            return [("code", str(spec.code_block.get("code") or ""), {a["sid"] for a in assets.get("code") or []})]
        same = lambda a, b: formula_key(a) == formula_key(b)
        return [("formula", f, {a["sid"] for a in assets.get("formula") or []
                                if same(clean_ocr_latex(a.get("normalized") or a["text"]), f)})
                for f in (spec.latex_formulas or [spec.latex_block_formula]) if f]

    def _undouble(self, pipeline, writer, contents: Dict[str, SlideContent], report: dict) -> None:
        """A formula or listing the document gives more than once is drawn on one slide only: the first in deck order
        whose bullets cite it, else the first; the other slides keep it in their notes."""
        drawn: Dict[str, List[tuple]] = {}
        for job in pipeline.jobs:
            sid = job["spec"].slide_id
            if sid not in contents:
                continue
            cited = {s for ids in contents[sid].fact_ids for s in ids}
            for kind, text, sids in self._shown(contents[sid], job):
                drawn.setdefault(kind + formula_key(text), []).append((sid, kind, text, bool(cited & sids)))
        for rows in drawn.values():
            keep = next((r[0] for r in rows if r[3]), rows[0][0])
            for sid, kind, text, _ in rows:
                if sid == keep:
                    continue
                content, job = contents[sid], self._job(pipeline, sid)
                spec, verdict = content.slide, "drawn on another slide"
                rest = [f for f in spec.latex_formulas or [] if f != text] if kind == "formula" else []
                if not rest:
                    self._undraw(writer, job, content, verdict, report)
                    continue
                content.speaker_notes = (content.speaker_notes or []) + [{"formula": text}]
                spec.latex_formulas, spec.latex_block_formula = rest, rest[0]
                report["visual"].append({"slide": spec.slide_title, "kind": kind, "verdict": verdict, "drawn": text[:200]})
                writer.relayout(job, content)

    def _repeats(self, earlier: str, later: str) -> bool:
        """Whether `later` states nothing `earlier` does not, as the LLM reads the two; False when unusable."""
        prompt = load("bullet_repeat")
        payload = {"earlier": earlier, "later": later}
        messages = [{"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        try:
            answer = cached_json(f"qa_repeat_v1.{digest(prompt)[:10]}", self.model, payload,
                                 lambda: parse_json_reply(chat(self.model, messages, temperature=0,
                                                               max_tokens=Config.REPEAT_MAX_TOKENS,
                                                               role="content_qa_repeat", json_mode=True)))
        except (ValueError, RuntimeError):
            return False
        return isinstance(answer, dict) and answer.get("repeats") is True

    def _unrepeat(self, pipeline, writer, contents: Dict[str, SlideContent], targets: List[str], report: dict) -> List[str]:
        """In deck order, a bullet that says again what an earlier slide's bullet said is removed: at
        REPEAT_BULLET_OVERLAP of shared words directly, from REPEAT_ASK_OVERLAP when the LLM finds it adds nothing; one
        stating a number the earlier lacks never is. A slide that only repeats is dropped."""
        bullets = [(sid, b) for sid in targets if sid in contents for b in contents[sid].content]
        # a bullet stating a number the earlier one does not is another fact, however alike their words
        alike = lambda b, a: not checks.unstated_numbers(b, a)
        close = {(a, b) for n, (sid, b) in enumerate(bullets) for other, a in bullets[:n]
                 if other != sid and Config.REPEAT_ASK_OVERLAP <= overlap(b, a) < Config.REPEAT_BULLET_OVERLAP
                 and alike(b, a)}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            same = {pair for pair, yes in zip(close, pool.map(lambda p: self._repeats(*p), close)) if yes}
        said: List[tuple] = []
        repeating: List[str] = []
        for sid in targets:
            content = contents.get(sid)
            if content is None:
                continue
            gone = []
            for k, bullet in enumerate(content.content):
                if any((overlap(bullet, earlier) >= Config.REPEAT_BULLET_OVERLAP and alike(bullet, earlier))
                       or (other != sid and (earlier, bullet) in same) for other, earlier in said):
                    gone.append(k)
                else:
                    said.append((sid, bullet))
            if not gone:
                continue
            report["dropped_bullets"] += [{"slide": content.slide.slide_title, "bullet": content.content[k],
                                           "reason": "it repeats an earlier bullet"} for k in gone]
            if self._remove_bullets(content, gone):
                writer.relayout(self._job(pipeline, sid), content)
            else:
                repeating.append(sid)
        if repeating:
            self._drop(pipeline, contents, repeating, "every bullet repeats an earlier slide", report)
        return [t for t in targets if t not in repeating]

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
