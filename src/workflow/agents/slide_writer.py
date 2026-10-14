"""Agent 3, Slide Writer: writes each slide from its passages.

A slide arrives with its title, the goals it opens (none when it continues a goal an earlier slide opened), the formula
or listing the Packet Builder chose to draw, and its pool of whole paragraphs whose sentences carry short ids (S#); the
KG packets stay with the code. Consecutive slides of one chapter are written in one call (SLIDE_WRITER_BATCH, a goal's
slides together), under one system prompt; the formula and code rules join it only when a slide of the call draws one.
The model reads everything and decides the slide's points, grouping and layout itself, citing the sentences each bullet
rests on. The Writer only writes: code checks the slide's shape (citation labels, passages cited, drawn assets
explained, layout fit) and settles it -- reshaping the groups or taking a roomier layout, never dropping a bullet for
the layout. What a bullet says (its cites, numbers, wording, truth) is Content QA's to check and correct; the Writer
writes each slide once. A slide with no usable answer is asked again on its own (WRITER_RETRIES times). A slide the
Writer never writes is not on the deck: code never puts the source's own sentences on a slide.
"""
from __future__ import annotations

import json
import re
import textwrap
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Tuple

from src.generator import layout_purpose as lp
from src.generator.formula_render import to_mathtext
from src.generator.layout_registry import layout_for_content
from src.generator.layout_select import fits_spec, unnamed_terms
from src.models.slide import SlideContent, SlideType
from src.utils.config import Config
from src.utils.content_cache import cached_json, digest
from src.utils.llm import chat, parse_json_reply
from src.workflow.agents.packet_builder import _DEFAULT_LAYOUT, PacketBuilderAgent
from src.rag.code_text import defined_names
from src.workflow import bullet_checks as checks
from src.workflow.packet_assembly import arranged_latex, clean_ocr_latex
from src.workflow.prompts import load

MAX_WORKERS = 8
_FALLBACKS = ("nested_bullets_layout", "only_content")
_NO_TEXT = {"image_comparison_layout"}       # pictures only: it has no room for the slide's bullets
_BULLET_CHARS = 100          # a bullet the Writer writes (at most 20 words); layout capacity is measured with it
# ids of packets and sentences are for citing, never slide text: "(K36)", "[S4, S5]"
_IDS = re.compile(r"\s*[(\[]\s*(?:ctx|[KRSP][\d.]+)(?:\s*,\s*(?:ctx|[KRSP][\d.]+))*\s*[)\]]")
_SAMPLE = "Precision measures how many of the positive predictions of a classifier are actually correct on new data"



def _blocks_drawn(groups: List[dict]) -> int:
    """Blocks the deck draws: an untitled group takes the slide title, and neighbours with the same title merge."""
    keys = [" ".join(str(g.get("title") or "").casefold().split()) for g in groups]
    return sum(1 for i, k in enumerate(keys) if i == 0 or k != keys[i - 1])


class SlideWriterAgent:
    def __init__(self, model: str, domain: str = "general"):
        self.model = Config.WRITER_MODEL or model
        self.domain = domain
        self.instruction = load("slide_writer", domain)
        self.visual_prompt = load("slide_writer_visual")     # joined only when a slide of the call draws something
        self.repair_prompt = load("slide_writer_repair", domain)

    def write(self, jobs: List[dict], issues: Optional[Dict[str, List[str]]] = None,
              taken: Iterable[str] = ()) -> List[SlideContent]:
        """jobs: [{spec, built}] in deck order -> one SlideContent each. `issues` (by slide_id) travel with a slide the
        Content QA sends back; `taken` are the titles of the slides not written now."""
        contexts = [self._context(n, job) for n, job in enumerate(jobs)]
        for c in contexts:
            c["issues"] = list((issues or {}).get(c["spec"].slide_id) or [])
        batches = self.batches(contexts)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            list(pool.map(self._settle, batches))
        contexts = [c for c in contexts if c["result"] is not None]       # unwritten: the caller drops the slide
        seen = {str(t).casefold() for t in taken}
        for c in contexts:                           # a new title another slide already has keeps the planned one
            title = c["result"].get("title") or c["spec"].slide_title
            if title.casefold() in seen:
                c["result"]["title"] = ""
                title = c["spec"].slide_title
            seen.add(title.casefold())
        return [self._content(c) for c in contexts]

    @staticmethod
    def batches(contexts: List[dict]) -> List[List[dict]]:
        """Consecutive slides of one chapter, at most SLIDE_WRITER_BATCH a call (one system prompt for them all); a goal's
        slides stay together when they fit, so a slide continuing a goal is written beside the slide opening it."""
        size = max(1, Config.SLIDE_WRITER_BATCH)
        runs: List[List[dict]] = []
        for c in contexts:
            g = c["spec"].grounding or {}
            key = (g.get("scope_root_id"), g.get("goal"))
            if runs and runs[-1][0]["_run"] == key:
                runs[-1].append(c)
            else:
                runs.append([c])
            c["_run"] = key
        out: List[List[dict]] = []
        for run in runs:
            chapter = run[0]["_run"][0]
            for k in range(0, len(run), size):
                part = run[k:k + size]
                last = out[-1] if out else None
                if last and last[0]["_run"][0] == chapter and len(last) + len(part) <= size:
                    last.extend(part)
                else:
                    out.append(list(part))
        return out

    def repair(self, jobs: List[dict], contents: Dict[str, SlideContent], pending: Dict[str, List[dict]]) -> int:
        """The QA Router's localized repair: `pending` {slide_id: [{index, text, kind, reason}]} are the bullets Content
        QA sent back; only those are written again, each from the sentences it cites, in calls of WRITER_REPAIR_BATCH
        slides. A new text is taken with its cites among those sentences (else its old ones); an empty one leaves the
        bullet for QA's next check. Returns the bullets written again."""
        items = [{"job": job, "content": contents[job["spec"].slide_id], "bullets": pending[job["spec"].slide_id]}
                 for job in jobs if job["spec"].slide_id in pending and job["spec"].slide_id in contents]
        asks = []
        for n, item in enumerate(items):
            content, job = item["content"], item["job"]
            texts, labels, bullets = dict(checks.citable(job["built"])), {}, []
            for b in item["bullets"]:
                k = b["index"]
                sources = {labels.setdefault(s, f"S{len(labels) + 1}"): texts[s] for s in content.fact_ids[k] if s in texts}
                bullets.append({"id": f"b{k + 1}", "text": content.content[k], "why": f"{b['kind']}: {b['reason']}",
                                "sources": sources})
            entry = {"slide": n, "title": content.slide.slide_title, "bullets": bullets}
            if job["built"].get("language"):
                entry["language"] = job["built"]["language"]
            asks.append((entry, {lab: s for s, lab in labels.items()}))
        size = max(1, Config.WRITER_REPAIR_BATCH)
        batches = [asks[i:i + size] for i in range(0, len(asks), size)]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            answers = {n: row for found in pool.map(self._ask_repair, batches) for n, row in found.items()}
        written = 0
        for n, (entry, back) in enumerate(asks):
            content, job = items[n]["content"], items[n]["job"]
            rows = {str(r.get("id")): r for r in answers.get(n) or [] if isinstance(r, dict)}
            for b in entry["bullets"]:
                k, row = int(b["id"][1:]) - 1, rows.get(b["id"]) or {}
                text = " ".join(_IDS.sub("", str(row.get("text") or "")).split())
                if not re.search(r"\w", text) or text == content.content[k]:     # nothing written ("", ".")
                    continue
                cites = [back[str(x).strip("[] ")] for x in row.get("cites") or [] if str(x).strip("[] ") in back]
                content.content[k] = text
                if cites:
                    content.fact_ids[k] = cites
                    if content.bullet_sources is not None:
                        refs = job["built"].get("refs") or {}
                        content.bullet_sources[k] = [refs[s] for s in cites if s in refs]
                written += 1
            self.relayout(job, content)
        return written

    def _ask_repair(self, batch: List[tuple]) -> Dict[int, List[dict]]:
        """{slide: its rewritten bullets} of one repair call; {} when the answer is unreadable."""
        payload = {"slides": [entry for entry, _ in batch],
                   "task": "Write every listed bullet again and return them as the output schema says."}
        messages = [{"role": "system", "content": self.repair_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        try:
            answer = cached_json(f"slide_writer_repair_v1.{digest(self.repair_prompt)[:10]}", self.model, payload,
                                 lambda: parse_json_reply(chat(self.model, messages, temperature=0,
                                                               max_tokens=Config.WRITER_REPAIR_MAX_TOKENS,
                                                               role="slide_writer_repair", json_mode=True)))
        except ValueError as exc:
            print(f"[slide_writer] unreadable repair answer: {str(exc)[:120]}")
            return {}
        rows = (answer.get("slides") if isinstance(answer, dict) else None) or []
        return {r["slide"]: r.get("bullets") or [] for r in rows if isinstance(r, dict) and isinstance(r.get("slide"), int)}

    # ── what the model sees ────────────────────────────────────────────────────
    @staticmethod
    def offered(assets: dict) -> List[lp.LayoutSpec]:
        """Layouts that can show the slide's asset (formula, table, or exactly its one or two figures), or the text-only
        ones."""
        images = len(assets.get("images") or ([assets["image"]] if assets.get("image") else []))
        needs = ({"formula"} if assets.get("formula") else {"table"} if assets.get("table")
                 else {"images"} if images else {"code"} if assets.get("code") else {""})
        return [s for s in lp.SPECS if s.needs in needs and not s.typed and s.name not in _NO_TEXT
                and (s.needs != "images" or lp._image_slots(s) == images)]

    def ranked(self, specs: List[lp.LayoutSpec]) -> List[lp.LayoutSpec]:
        """The offered layouts with the relations the slide's field takes first (Config.DOMAIN_RELATIONS), in its order;
        the rest keep theirs."""
        first = Config.DOMAIN_RELATIONS.get(self.domain, ())
        return sorted(specs, key=lambda s: first.index(s.relation) if s.relation in first else len(first))

    def _context(self, n: int, job: dict) -> dict:
        built = job["built"]
        short, back, passages = {}, {}, {}
        for pid, row in built["pool"].items():
            parts = []
            for sid, text in row["sentences"]:
                label = f"S{len(short) + 1}"
                short[label], back[sid] = sid, label
                parts.append(f"[{label}] {text}")
            passages[pid] = " ".join(parts)
        assets = built["assets"]
        kind = self.asset_kind(assets)
        # every formula candidate is shown (the slide draws those its bullets explain); the listing is the one the
        # Packet Builder chose (none chosen: a text slide); what is not drawn goes to the speaker notes
        visuals = {r["sid"]: r for r in PacketBuilderAgent.candidates(built)}
        draw = built.get("draw") or ({"sids": list(visuals)[:1], "lines": None} if visuals else None)
        drawn = list(visuals) if kind == "formula" else [sid for sid in (draw or {}).get("sids") or [] if sid in visuals]
        lines = (draw or {}).get("lines") if drawn else None
        shown = {}
        for sid in drawn:
            row = visuals[sid]
            label = f"S{len(short) + 1}"                 # cited like a sentence: bullets explain it
            short[label], back[sid] = sid, label
            if kind == "formula":
                shown.setdefault("formula", []).append(f"[{label}] {row['text'][:Config.SHOWN_FORMULA_CHARS]}")
            else:
                first, last = lines or (1, Config.SHOWN_CODE_LINES)
                body = "\n".join(f"{k}| {ln}" for k, ln in
                                 enumerate(row["text"].splitlines()[first - 1:last][:Config.SHOWN_CODE_LINES], first))
                shown.setdefault("code", []).append(f"[{label}] ```{row.get('language') or ''}\n{body}\n```")
        if assets.get("table"):
            shown["table"] = assets["table"][0]["text"][:600]
        figures = assets.get("images") or ([assets["image"]] if assets.get("image") else [])
        if figures:
            shown["image"] = [a.get("real_caption") or a.get("caption") or "" for a in figures]
        spec = job["spec"]
        declined = bool(visuals) and not drawn
        specs = self.ranked(self.offered({} if declined else assets))
        payload = {"slide": n, "title": spec.slide_title, **({"message": spec.goal} if spec.goal else {}),
                   "asset": "text" if declined else kind, "bullets": self.bullets_expected(built), "passages": passages}
        if built.get("language"):
            payload["language"] = built["language"]
        if shown:
            payload["shows"] = shown
        for extra in ("named", "known"):
            if built.get(extra):
                payload[extra] = built[extra]
        if len(built.get("sections") or []) > 1:     # several sections on one slide: each gets bullets
            payload["sections"] = built["sections"]
        lo, hi = (int(x) for x in payload["bullets"].split("-"))
        payload["layouts"] = self.describe(self.usable(specs, lo, hi))
        return {"n": n, "spec": spec, "built": built, "short": short, "tools": job.get("tools"), "payload": payload,
                "specs": specs, "text_specs": self.ranked(self.offered({})) if visuals else [], "images": len(figures),
                "kind": kind, "visuals": visuals, "drawn": drawn, "lines": lines,
                "visual": drawn[0] if drawn else ("none" if visuals else None), "result": None, "problems": []}

    @staticmethod
    def specs_of(c: dict) -> List[lp.LayoutSpec]:
        """The layouts the slide may take: its asset's, or the text ones when it draws no formula or listing."""
        return c["text_specs"] if c.get("visual") == "none" else c["specs"]

    @staticmethod
    def bullets_expected(built: dict) -> str:
        """How many points the slide's whole source supports, e.g. "7-14": one per BULLET_SOURCE_CHARS_LOOSE to one per
        BULLET_SOURCE_CHARS, at least QA_THIN_BULLETS once the source can fill a slide; not one per key idea, since one
        point may teach several. No layout limit: layouts grow."""
        size = built.get("size") or {}
        chars = size.get("source_chars", size.get("pool_chars", 0))
        least = Config.QA_THIN_BULLETS if chars >= Config.SECTION_SLIDE_MIN_CHARS else Config.QA_THIN_BULLETS - 1
        low = max(least, round(chars / Config.BULLET_SOURCE_CHARS_LOOSE))
        return f"{low}-{max(low, round(chars / Config.BULLET_SOURCE_CHARS))}"

    @staticmethod
    def asset_kind(assets: dict) -> str:
        return ("formula" if assets.get("formula") else "table" if assets.get("table")
                else "image" if assets.get("image") else "code" if assets.get("code") else "text")

    # ── one batch ──────────────────────────────────────────────────────────────
    def _settle(self, batch: List[dict]) -> None:
        answers = self._ask(batch, {c["n"]: c["issues"] for c in batch if c.get("issues")})
        for c in batch:
            c["result"], c["problems"] = self._check(c, answers.get(c["n"]))
        for attempt in range(1, Config.WRITER_RETRIES + 1):
            again = [c for c in batch if self.needs_retry(c)]
            if not again:
                break
            answers = {}
            for c in again:                          # one slide at a time, told what was wrong with its answer
                told = c.get("issues", []) + c["problems"] + [f"attempt {attempt + 1}: write the slide again."]
                answers.update(self._ask([c], {c["n"]: told}))
            for c in again:
                result, problems = self._check(c, answers.get(c["n"]))
                if self.better(c, result, problems):
                    c["result"], c["problems"] = result, problems
        for c in batch:
            self._repair(c)

    def needs_retry(self, c: dict) -> bool:
        """Only a slide with nothing usable is asked again whole; what its bullets say is Content QA's to check."""
        return not self.survivors(c)

    @staticmethod
    def rows(c: dict) -> List[Tuple[str, str]]:
        """The slide's citable sentences in source order: its pool, then the formulas or listings shown to the Writer."""
        return ([(s, t) for row in c["built"]["pool"].values() for s, t in row["sentences"]]
                + [(sid, row["text"]) for sid, row in c["visuals"].items()])

    @staticmethod
    def explained(c: dict, drawn: List[str], groups: List[dict]) -> List[str]:
        """The candidates the slide draws: a listing only when a bullet cites it (code beside bullets about something
        else misleads); in reading order, at most MAX_SLIDE_FORMULAS formulas a bullet cites or names what it defines
        (`recall` for recall = TP/(TP+FN)) -- a formula no bullet explains is not drawn, wherever it stands."""
        cited = {s for g in groups for b in g["bullets"] for s in b["cites"]}
        if c["kind"] != "formula":
            return [s for s in drawn if s in cited]
        said = " ".join(b["text"] for g in groups for b in g["bullets"]).casefold()
        names = lambda s: defined_names(c["visuals"][s]["text"])
        return [s for s in drawn if s in cited
                or any(re.search(rf"\b{re.escape(n)}\b", said) for n in names(s))][:Config.MAX_SLIDE_FORMULAS]

    @staticmethod
    def flawed(problem: str) -> bool:
        """A problem only the Writer can fix: a drawn asset no bullet explains."""
        return "explained by no bullet" in problem

    def better(self, c: dict, result: Optional[dict], problems: List[str]) -> bool:
        """Whether a new answer replaces the slide's: it keeps more bullets, or as many with fewer flaws."""
        if result is None:
            return False
        flaws = lambda ps: sum(map(self.flawed, ps))
        old, new = self.survivors(c), self.survivors(c, result)
        return new > old or (new == old and flaws(problems) < flaws(c["problems"]))

    def survivors(self, c: dict, result: Optional[dict] = None) -> int:
        """Bullets of the answer that rest on the slide's sentences: they cite some, or their words point to some."""
        result = c["result"] if result is None else result
        if result is None:
            return 0
        rows = self.rows(c)
        return sum(bool(b["cites"] or checks.anchor(rows, b["text"])) for g in result["groups"] for b in g["bullets"])

    @staticmethod
    def short(name: str) -> str:
        return name.removesuffix("_layout")

    @staticmethod
    def describe(specs: List[lp.LayoutSpec]) -> Dict[str, str]:
        """{name: what it shows · its blocks · how many bullets it holds once grown} for the layouts a slide may take."""
        return {SlideWriterAgent.short(s.name): f"{s.relation} · {SlideWriterAgent.blocks(s)} · "
                                                f"≤{SlideWriterAgent.capacity(s.name)}" for s in specs}

    @staticmethod
    def usable(specs: List[lp.LayoutSpec], low: int, high: int) -> List[lp.LayoutSpec]:
        """The layouts worth offering a slide of `low`-`high` bullets: they hold at least `low` and need no more blocks
        than `high`; the plain lists always (any other offered layout is still accepted if named)."""
        keep = [s for s in specs if s.name in _FALLBACKS
                or (SlideWriterAgent.capacity(s.name) >= low and s.min_blocks <= high)]
        return keep or specs

    def _ask(self, batch: List[dict], issues: Dict[int, List[str]]) -> Dict[int, dict]:
        drawing = any(c["payload"].get("shows") for c in batch)
        system = self.instruction + ("\n\n" + self.visual_prompt if drawing else "")
        indexes = [c["n"] for c in batch]
        payload = {"slides": [{**c["payload"], **({"issues": issues[c["n"]]} if c["n"] in issues else {})}
                              for c in batch],
                   "task": f"Write slide{'s' if len(batch) > 1 else ''} {', '.join(map(str, indexes))} and return "
                           f"{'them' if len(batch) > 1 else 'it'} in `written`, as the output schema says."}
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        def invoke():
            room = Config.WRITER_MAX_TOKENS + Config.WRITER_SLIDE_TOKENS * (len(batch) - 1)
            return parse_json_reply(chat(self.model, messages, temperature=0, max_tokens=room, role="slide_writer",
                                         json_mode=True))
        try:
            answer = cached_json(f"slide_writer_v3.{digest(system)[:10]}", self.model, payload, invoke)
        except ValueError as exc:
            print(f"[slide_writer] unreadable answer for slides {[c['n'] for c in batch]}: {str(exc)[:120]}")
            return {}
        out = {}
        rows = [r for k in ("written", "slides") for r in ((answer.get(k) if isinstance(answer, dict) else None) or [])
                if isinstance(r, dict)]
        if len(batch) == 1 and len(rows) == 1:              # one slide asked, one answer: whatever index it carries
            return {batch[0]["n"]: rows[0]}
        for row in rows:
            try:
                out[int(row.get("slide"))] = row
            except (TypeError, ValueError, AttributeError):
                continue
        return out

    # ── checks ─────────────────────────────────────────────────────────────────
    def visual(self, c: dict) -> Optional[dict]:
        """The first formula or listing row the slide draws (None when it draws none)."""
        if c.get("visual") == "none":
            return None
        return c["visuals"].get(c.get("visual")) or next(iter(c["visuals"].values()), None)

    def drawn_rows(self, c: dict) -> List[dict]:
        """Every formula or listing row the slide draws, in reading order."""
        if c.get("visual") == "none":
            return []
        rows = [c["visuals"][s] for s in c.get("drawn") or [] if s in c["visuals"]]
        return rows or [r for r in [self.visual(c)] if r]

    def _check(self, c: dict, row: Optional[dict]) -> Tuple[Optional[dict], List[str]]:
        """The answer in real sentence ids, and what is still wrong with it."""
        if not isinstance(row, dict):
            return None, ["no answer for this slide"]
        if not row.get("groups") and ("passages" in row or "packets" in row):
            return None, ["the answer repeated the input slide instead of writing it: return the slide you write "
                          "(title, layout, groups of bullets, notes) in `written`"]
        problems: List[str] = []
        groups = []
        for g in row.get("groups") or []:
            if not isinstance(g, dict):
                continue
            bullets = []
            for b in g.get("bullets") or []:
                if not isinstance(b, dict) or not str(b.get("text") or "").strip():
                    continue
                text = " ".join(_IDS.sub("", str(b["text"])).replace("$$", " ").split())   # no display-math marks
                cites, bad = self.resolve(c, b.get("cites") or [])
                cites = checks.with_numbers(self.rows(c), text, cites)
                if bad:
                    problems.append(f"bullet '{text[:50]}' cites ids that are not in its passages ({bad[:3]}); cite "
                                    f"the S# labels of this slide's own passages")
                if not cites:
                    problems.append(f"bullet '{text[:50]}' cites no sentence")
                bullets.append({"text": text, "cites": cites})
            if bullets:
                groups.append({"title": " ".join(str(g.get("title") or "").split()), "bullets": bullets})
        if not groups:
            return None, problems or ["no bullets"]
        left: Dict[str, str] = {}
        cited = {s for g in groups for b in g["bullets"] for s in b["cites"]}
        uncited = [pid for pid, r in c["built"]["pool"].items() if not any(s in cited for s, _ in r["sentences"])]
        if uncited:
            problems.append(f"passages {uncited} are cited by no bullet: teach what they say")
        label = {sid: lab for lab, sid in c["short"].items()}
        chosen = self.explained(c, c["drawn"], groups)
        if c["kind"] == "code" and c["drawn"] and not chosen:
            problems.append(f"the drawn listing {[label.get(s, s) for s in c['drawn']]} is explained by no bullet: "
                            f"explain it in bullets that cite it")
        arrange = self.arrangement(c, chosen, row.get("arrange"))
        # the layout is checked with what this answer draws
        view = {**c, "drawn": chosen, "visual": chosen[0] if chosen else ("none" if c["visuals"] else None),
                "result": {"arrange": arrange}}
        layout = self.named(view, str(row.get("layout") or ""))
        if layout not in {s.name for s in self.specs_of(view)}:
            problems.append(f"layout '{layout}' is not offered; choose from {[s.name for s in self.specs_of(view)]}")
        elif (why := self.misfit(view, layout, groups)):
            problems.append(f"the slide does not fit its layout: {why}")
        title = " ".join(str(row.get("title") or "").split())
        result = {"layout": layout, "groups": groups, "left": left, "notes": " ".join(str(row.get("notes") or "").split()),
                  "title": title if Config.SLIDE_TITLE_MIN_WORDS <= len(title.split()) <= Config.SLIDE_TITLE_MAX_WORDS else "", "visual": c["visual"], "drawn": c["drawn"],
                  "lines": c["lines"], "arrange": arrange}
        return result, problems

    @staticmethod
    def arrangement(c: dict, chosen: List[str], answer) -> Dict[str, str]:
        """{sid: the formula stacked as the Writer laid out its lines} for each drawn formula whose lines are its own
        text in order (`arranged_latex`); any other answer leaves the formula as it is."""
        if c["kind"] != "formula" or not isinstance(answer, dict):
            return {}
        out = {}
        for label, lines in answer.items():
            sid = c["short"].get(str(label).strip("[] "))
            if sid not in chosen or not isinstance(lines, list):
                continue
            row = c["visuals"][sid]
            for source in (clean_ocr_latex(row.get("normalized") or row["text"]), clean_ocr_latex(row["text"])):
                if (stacked := arranged_latex(source, lines)):
                    out[sid] = stacked
                    break
        return out

    def formula_texts(self, c: dict) -> List[str]:
        """The drawn formulas, each as the Writer arranged its lines or as the document gives it."""
        arrange = (c.get("result") or {}).get("arrange") or {}
        return [arrange.get(r.get("sid")) or clean_ocr_latex(r.get("normalized") or r["text"])
                for r in self.drawn_rows(c)]

    @staticmethod
    def resolve(c: dict, labels) -> Tuple[List[str], List[str]]:
        """(sentence ids, labels that name nothing). Labels are the slide's own `S1`, `S2` …; a paragraph id stands for
        its sentences in the pool; `S{slide}.{k}` (this slide's index) is read as `S{k}`; a label wrapped with its
        paragraph (`P12.S3`, `P12: S3`) is its S#."""
        def own(label: str) -> Optional[str]:
            m = re.fullmatch(r"S(\d+)\.(\d+)", label)
            label = f"S{m.group(2)}" if m and int(m.group(1)) == c["n"] else label
            return c["short"].get(label)
        out, bad = [], []
        pool = c["built"]["pool"]
        for raw in labels:
            x = str(raw).strip().strip("[](),;. ")
            inner = [own(s) for s in re.findall(r"S\d+(?:\.\d+)?", x)]
            if own(x):
                out.append(own(x))
            elif x in pool:
                out.extend(s for s, _ in pool[x]["sentences"])
            elif inner and all(inner):
                out.extend(inner)
            else:
                bad.append(x)
        return list(dict.fromkeys(out)), bad

    # ── layouts: what a block is, how much fits ────────────────────────────────
    @staticmethod
    def blocks(spec: lp.LayoutSpec) -> str:
        """The layout's block contract in a few words (a short-label layout also states its bullet length)."""
        span = (f"{spec.min_blocks}+" if spec.max_blocks >= 99 else str(spec.min_blocks)
                if spec.min_blocks == spec.max_blocks else f"{spec.min_blocks}-{spec.max_blocks}")
        if spec.unit == "facts":
            return f"{span} bullet{'s' if span != '1' else ''}"
        each = f" ≤{spec.max_facts_per_block} each" if spec.max_facts_per_block < 99 else ""
        short = f", bullets ≤{spec.body_chars} chars" if spec.body_chars < 100 else ""
        return f"{span} group{'s' if span != '1' else ''}{each}{short}"

    @staticmethod
    @lru_cache(maxsize=None)
    def capacity(name: str) -> int:
        """How many one-sentence bullets the layout shows once it has grown as far as it can (renderer's fit model)."""
        spec = lp.LAYOUT_PURPOSE[name]
        rendered = layout_for_content(name, 1) if spec.needs == "images" else name
        text = _SAMPLE[:min(spec.body_chars, _BULLET_CHARS, len(_SAMPLE))].rsplit(" ", 1)[0] + "."
        most = spec.max_blocks * spec.max_facts_per_block if spec.unit == "points" else spec.max_blocks
        best = 0
        for k in range(1, min(most, 12) + 1):
            if spec.unit == "points":
                per = min(spec.max_facts_per_block, 3)
                g = min(spec.max_blocks, max(spec.min_blocks, -(-k // per)))
                if g > k or -(-k // g) > spec.max_facts_per_block:
                    continue
                shaped = [{"title": "Short title", "items": [text] * (k // g + (1 if i < k % g else 0))} for i in range(g)]
            else:
                shaped = [{"title": "", "items": [text] * k}]
            if fits_spec(rendered, shaped, [text] * k, ()):
                best = k
        return best

    def misfit(self, c: dict, layout: str, groups: List[dict]) -> Optional[str]:
        """Why the written groups do not fit the layout, precisely enough to fix it; None when they fit."""
        spec = lp.LAYOUT_PURPOSE.get(layout)
        if spec is None:
            return f"'{layout}' is not a layout"
        bullets = [b["text"] for g in groups for b in g["bullets"]]
        if spec.unit != "points":
            count = len(bullets)
        else:
            count = _blocks_drawn(groups) if spec.min_blocks >= 2 else len(groups)
        if not spec.min_blocks <= count <= spec.max_blocks:
            span = f"{spec.min_blocks}-{spec.max_blocks}" if spec.max_blocks < 99 else f"at least {spec.min_blocks}"
            if spec.unit == "points":
                fix = (" -- make every step, term or side its own group with its own short title (untitled groups, "
                       "or neighbours with the same title, are drawn as one block)" if count < spec.min_blocks
                       else " -- regroup, or pick a layout for more groups")
                return f"{layout} needs {span} groups, you gave {count}{fix}"
            return f"{layout} needs {span} bullets, you gave {count}"
        if any(len(g["bullets"]) > spec.max_facts_per_block for g in groups):
            return f"{layout} shows at most {spec.max_facts_per_block} bullet(s) per group"
        if layout == "formula_annotated_layout":
            unnamed = unnamed_terms([{"title": g["title"]} for g in groups], self.extent(c).get("formula_text") or "")
            if unnamed and self.extent(c).get("formula_text"):
                return (f"{layout} explains the terms written in its formula, one group each, titled by that term; "
                        f"{unnamed[:2]} name nothing in the formula -- title each group by the symbol or word it "
                        f"explains, or pick another formula layout")
        rendered = layout_for_content(layout, c["images"]) if c["images"] else layout
        shaped = [{"title": g["title"], "items": [b["text"] for b in g["bullets"]]} for g in groups]
        if fits_spec(rendered, shaped, bullets, (), self.extent(c)):
            return None
        long = [t[:30] for t in bullets if len(t) > spec.body_chars * 1.15]
        if long:
            return f"bullets longer than {spec.body_chars} characters ({long[:2]}): shorten them"
        titles = [g["title"] for g in groups if g["title"] and len(g["title"]) > spec.title_chars * 1.2]
        if titles:
            return f"group titles longer than {spec.title_chars} characters ({titles[:2]}): shorten them"
        return (f"{len(bullets)} bullets / {sum(map(len, bullets))} characters overflow {layout} even grown to its "
                f"largest (about {self.capacity(layout)} short bullets): pick a roomier layout or shorten them")

    def covered(self, c: dict, groups: List[dict]) -> set:
        """Packets a bullet cites a sentence of."""
        cited = {s for g in groups for b in g["bullets"] for s in b["cites"]}
        pool = c["built"]["pool"]
        out = set()
        for pk in c["built"]["packets"]:
            if any(s in cited for pid in pk["passages"] for s, _ in (pool.get(pid) or {}).get("sentences", [])):
                out.add(pk["id"])
        return out

    def fits(self, c: dict, layout: str, groups: List[dict]) -> bool:
        return self.misfit(c, layout, groups) is None

    def reshape(self, c: dict, layout: str, groups: List[dict]) -> Optional[List[dict]]:
        """Bullets written as one group for a layout of several blocks (steps, kinds, columns): each bullet becomes its
        own block, titled by its lead-in before a colon when it has one."""
        spec = lp.LAYOUT_PURPOSE.get(layout)
        bullets = [b for g in groups for b in g["bullets"]]
        if spec is None or spec.unit != "points" or len(groups) >= spec.min_blocks \
                or not spec.min_blocks <= len(bullets) <= spec.max_blocks:
            return None
        out = []
        for b in bullets:
            head, sep, rest = b["text"].partition(": ")
            if sep and rest and len(head) <= spec.title_chars:
                out.append({"title": head, "bullets": [{**b, "text": rest[:1].upper() + rest[1:]}]})
            else:
                out.append({"title": "", "bullets": [b]})
        return out if self.fits(c, layout, out) else None

    def relayout(self, job: dict, content: SlideContent) -> None:
        """A slide whose bullets changed outside the Writer (QA removed some): keep its layout if the bullets still fit
        it, else take the offered layout that holds them."""
        layout = content.layout_hint
        if layout not in lp.LAYOUT_PURPOSE:
            return
        c = self._context(0, job)
        if c["visuals"] and not (job["spec"].latex_block_formula or job["spec"].code_block):
            c["visual"] = "none"
        groups = [{"title": g.get("title") or "", "bullets": [{"text": content.content[i], "cites": []}
                                                              for i in g.get("item_indices") or []]}
                  for g in content.groups or []]
        if self.fits(c, layout, groups):
            return
        content.layout_hint = layout_for_content(self.roomier(c, groups), c["images"])
        content.layout_reason = "; ".join(filter(None, [content.layout_reason,
                                                        f"bullets removed by QA: {layout} -> {content.layout_hint}"]))[:400]

    def roomier(self, c: dict, groups: List[dict]) -> str:
        """The offered layout that holds every bullet: the plain lists first, then the others from the roomiest; when
        none can, the roomiest (it still shows every bullet, smaller). A bullet is never dropped for the layout."""
        offered = [s.name for s in self.specs_of(c)]
        order = [n for n in offered if n in _FALLBACKS] + sorted((n for n in offered if n not in _FALLBACKS),
                                                                 key=lambda n: -self.capacity(n))
        return next((n for n in order if self.fits(c, n, groups)), max(order, key=self.capacity))

    @staticmethod
    def named(c: dict, layout: str) -> str:
        """The offered layout the model named: listed without its `_layout` suffix, or by the slide's asset kind
        ("text", "image", ...) for that kind's usual layout."""
        names = {s.name for s in SlideWriterAgent.specs_of(c)}
        layout = re.split(r"[:·]", layout)[0].strip()          # a name copied with its description
        if layout not in names and f"{layout}_layout" in names:
            return f"{layout}_layout"
        default = _DEFAULT_LAYOUT.get(layout.lower())
        return default if default and default in names else layout

    def extent(self, c: dict) -> dict:
        """What the fit check needs to know of the slide: its title, formula lines and table rows."""
        assets = c["built"]["assets"]
        out = {"title": (c.get("result") or {}).get("title") or c["spec"].slide_title}
        if c["kind"] == "formula" and c.get("visual") != "none":
            texts = (self.formula_texts(c) if c.get("drawn")
                     else c["spec"].latex_formulas or self.formula_texts(c))
            if texts:                                # stacked formulas, a blank line between two (deck formulaLines)
                out["formula_lines"] = sum(len(to_mathtext(t)) for t in texts) + len(texts) - 1
                out["formula_text"] = "\n".join(texts)
        if assets.get("table"):
            out["table_rows"] = max(1, sum(1 for line in str(assets["table"][0]["text"]).splitlines()
                                           if line.strip().startswith("|") and not set(line.strip()) <= set("|-: ")) - 1)
        return out

    # ── the slide's shape settled without inventing and without a new slide (what bullets say: Content QA) ──
    def _repair(self, c: dict) -> None:
        result = c["result"]
        if result is None:
            return
        notes = [result["notes"]] if result["notes"] else []
        if not self.survivors(c):                  # nothing the Writer wrote rests on the slide: it is not written
            c["result"] = None
            return
        c["drawn"] = self.explained(c, result.get("drawn") or [], result["groups"])
        c["visual"] = c["drawn"][0] if c["drawn"] else ("none" if c["visuals"] else None)
        result["visual"], result["drawn"] = c["visual"], c["drawn"]
        specs = self.specs_of(c)
        if result["layout"] not in {s.name for s in specs}:
            result["layout"] = next((s.name for s in specs if s.name in _FALLBACKS), specs[0].name)
        reshaped = None if self.fits(c, result["layout"], result["groups"]) \
            else self.reshape(c, result["layout"], result["groups"])
        if reshaped:
            result["groups"] = reshaped
        if not self.fits(c, result["layout"], result["groups"]):
            result["layout"] = self.roomier(c, result["groups"])
        result["notes"] = " ".join(notes)
        c["result"] = result

    # ── into the deck's model ──────────────────────────────────────────────────
    def _content(self, c: dict) -> SlideContent:
        result, built, spec = c["result"], c["built"], c["spec"]
        if result.get("title"):                     # the title names what the written bullets actually teach
            spec.slide_title = result["title"]
        row = self.visual(c)
        if c.get("visual") == "none":               # declined: the slide is text only (the notes keep any listing)
            spec.latex_block_formula, spec.latex_formulas, spec.code_block = None, [], None
            spec.slide_type = SlideType.CONTENT
        if row and c["kind"] == "formula":         # the formulas or listing the Writer chose are the ones drawn
            spec.latex_formulas = self.formula_texts(c)
            spec.latex_block_formula = spec.latex_formulas[0]
        elif row and c["kind"] == "code":
            lines = row["text"].splitlines()
            first, last = result.get("lines") or (1, len(lines))
            spec.code_block = {"code": textwrap.dedent("\n".join(lines[first - 1:last])).strip("\n"),
                               "language": row.get("language") or ""}
        refs = {s: r for s, r in built.get("refs", {}).items()}
        content, fact_ids, sources, groups = [], [], [], []
        for n, g in enumerate(result["groups"]):
            start = len(content)
            for b in g["bullets"]:
                content.append(b["text"])
                fact_ids.append(list(b["cites"]))
                sources.append([refs[s] for s in b["cites"] if s in refs])
            groups.append({"group_id": f"g{n}", "title": g["title"], "item_indices": list(range(start, len(content)))})
        cited = {s for ids in fact_ids for s in ids}
        notes = [{"presenter": result["notes"]}] if result["notes"] else []
        notes += [{"code": a["text"]} for a in built["assets"].get("code") or []]
        drawn = {r["sid"] for r in self.drawn_rows(c)} if c["kind"] == "formula" else set()
        notes += [{"formula": a["text"]} for a in built["assets"].get("formula") or [] if a["sid"] not in drawn]
        notes += [{"sources": [refs[s] for s, _ in row["sentences"] if s in refs]}
                  for pid, row in built["pool"].items() if not any(s in cited for s, _ in row["sentences"])]
        return SlideContent(slide=spec, content=content, fact_ids=fact_ids, bullet_sources=sources, groups=groups,
                            layout_hint=layout_for_content(result["layout"], c["images"]),
                            layout_reason="; ".join(c["problems"])[:400],
                            layout_structure={"visual_plan": ({"type": "none", "source": "none", "primary": False}
                                                              if c.get("visual") == "none"
                                                              else built.get("visual_plan") or {}),
                                              "left": result["left"],
                                              "uncovered": [pk["id"] for pk in built["packets"]
                                                            if pk["id"] not in self.covered(c, result["groups"])
                                                            and pk["id"] not in result["left"]]},
                            speaker_notes=notes)
