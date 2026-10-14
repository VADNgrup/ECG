"""Agent 3, Slide Writer: writes each slide from its packets.

A slide arrives with the goal it serves, its title and message, its asset kind and several packets (one line each): KG
anchors (the key nodes the Packet Builder agent chose, with their links and the sentence stating each) and a context
packet, all pointing into the slide's pool of whole paragraphs whose sentences carry short ids (S#). The model reads
everything and decides the slide's ideas, grouping and layout itself: a bullet may draw on several packets and a packet
may give several bullets. It cites the sentences each bullet rests on. Code checks what a model cannot be trusted with
(citations, key packets used, copying, numbers, voice, fit) and settles what it can itself -- re-anchoring or dropping a
bullet, reshaping the groups or taking a roomier layout (a bullet is never dropped for the layout) -- without inventing.
A slide with no usable answer is asked again whole (WRITER_RETRIES times); bullets pasted from the source, and bullets
Content QA finds wrong, are rewritten in one small call with only those bullets and their sentences. A slide the Writer
never writes is not on the deck: code never puts the source's own sentences on a slide. A thin slide given new facts by
Content QA is written again whole.
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
from src.generator.layout_select import fits_spec
from src.models.slide import SlideContent, SlideType
from src.utils.bullet_text import support_ratio
from src.utils.config import Config
from src.utils.content_cache import cached_json, digest
from src.utils.llm import chat, parse_json_reply
from src.workflow.agents.packet_builder import _DEFAULT_LAYOUT
from src.rag.code_text import defined_names
from src.workflow.packet_assembly import clean_ocr_latex
from src.workflow.prompts import load

MAX_WORKERS = 8
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*")
_FALLBACKS = ("nested_bullets_layout", "only_content")
_NO_TEXT = {"image_comparison_layout"}       # pictures only: it has no room for the slide's bullets
COPY_RUN = 8                 # this many consecutive source words in a bullet means it was pasted, not rewritten
_SHOWN_CODE_LINES = 40       # lines of a listing the Writer reads (the notes keep all of it)
_SHOWN_VISUALS = 4           # candidate formulas or listings the Writer chooses the slide's one from
_BULLET_CHARS = 100          # a bullet the Writer writes (at most 20 words); layout capacity is measured with it
# ids of packets and sentences are for citing, never slide text: "(K36)", "[S4, S5]"
_IDS = re.compile(r"\s*[(\[]\s*(?:ctx|[KRSP][\d.]+)(?:\s*,\s*(?:ctx|[KRSP][\d.]+))*\s*[)\]]")
# the book narrating to its reader, never a slide's voice (English and Vietnamese sources)
_BOOK_VOICE = re.compile(r"\b(?:we|you|your|let[’']s|let us|our)\b|\bin this (?:chapter|section)\b|\bas we (?:saw|have seen)\b|"
                         r"\bthe following (?:code|figure|example)\b|\bwow\b|\bthat[’']s right\b|\bchúng ta\b|\bbạn\b",
                         re.IGNORECASE)
_SAMPLE = "Precision measures how many of the positive predictions of a classifier are actually correct on new data"

_INSTRUCTION = load("slide_writer")
_REWRITE = load("bullet_rewrite")


def _blocks_drawn(groups: List[dict]) -> int:
    """Blocks the deck draws: an untitled group takes the slide title, and neighbours with the same title merge."""
    keys = [" ".join(str(g.get("title") or "").casefold().split()) for g in groups]
    return sum(1 for i, k in enumerate(keys) if i == 0 or k != keys[i - 1])


class SlideWriterAgent:
    def __init__(self, model: str):
        self.model = Config.WRITER_MODEL or model

    def write(self, jobs: List[dict], issues: Optional[Dict[str, List[str]]] = None,
              taken: Iterable[str] = ()) -> List[SlideContent]:
        """jobs: [{spec, built}] in deck order -> one SlideContent each. `issues` (by slide_id) travel with a slide the
        Content QA sends back; `taken` are the titles of the slides not written now."""
        contexts = [self._context(n, job) for n, job in enumerate(jobs)]
        for c in contexts:
            c["issues"] = list((issues or {}).get(c["spec"].slide_id) or [])
        size = max(1, Config.SLIDE_WRITER_BATCH)
        batches, current, key = [], [], None
        for c in contexts:
            chapter = (c["spec"].grounding or {}).get("scope_root_id")
            if current and (len(current) >= size or chapter != key):
                batches.append(current)
                current = []
            current.append(c)
            key = chapter
        if current:
            batches.append(current)
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

    def _context(self, n: int, job: dict) -> dict:
        built = job["built"]
        short, back, passages = {}, {}, {}
        for pid, row in built["pool"].items():
            parts = []
            for sid, text in row["sentences"]:
                label = f"S{n}.{len(short) + 1}"
                short[label], back[sid] = sid, label
                parts.append(f"[{label}] {text}")
            passages[pid] = " ".join(parts)
        packets = [self.packet_line(pk, back) for pk in built["packets"]]
        assets = built["assets"]
        kind = self.asset_kind(assets)
        shown, visuals = {}, {}
        for row in (assets.get(kind) or [])[:_SHOWN_VISUALS] if kind in ("formula", "code") else []:
            label = f"S{n}.{len(short) + 1}"             # cited like a sentence: bullets explain it
            short[label], back[row["sid"]] = row["sid"], label
            visuals[row["sid"]] = row
            if kind == "formula":
                shown.setdefault("formula", []).append(f"[{label}] {row['text'][:400]}")
            else:
                body = "\n".join(f"{k}| {ln}" for k, ln in enumerate(row["text"].splitlines()[:_SHOWN_CODE_LINES], 1))
                shown.setdefault("code", []).append(f"[{label}] ```{row.get('language') or ''}\n{body}\n```")
        if assets.get("table"):
            shown["table"] = assets["table"][0]["text"][:600]
        figures = assets.get("images") or ([assets["image"]] if assets.get("image") else [])
        if figures:
            shown["image"] = [a.get("real_caption") or a.get("caption") or "" for a in figures]
        spec = job["spec"]
        specs = self.offered(assets)
        payload = {"slide": n, "goal": built.get("goal", ""), "title": spec.slide_title, "message": spec.goal,
                   "asset": kind, "bullets": self.bullets_expected(built), "packets": packets,
                   "passages": passages}
        if shown:
            payload["shows"] = shown
        for extra in ("named", "known"):
            if built.get(extra):
                payload[extra] = built[extra]
        lo, hi = (int(x) for x in payload["bullets"].split("-"))
        payload["layouts"] = self.describe(self.usable(specs, lo, hi))
        if visuals:                                  # the layouts left when the Writer draws no formula or code
            payload["text_layouts"] = self.describe(self.usable(self.offered({}), lo, hi))
        return {"n": n, "spec": spec, "built": built, "short": short, "tools": job.get("tools"), "payload": payload,
                "specs": specs, "text_specs": self.offered({}) if visuals else [], "images": len(figures), "kind": kind,
                "visuals": visuals, "visual": None, "result": None, "problems": []}

    @staticmethod
    def specs_of(c: dict) -> List[lp.LayoutSpec]:
        """The layouts the slide may take: its asset's, or the text ones once the Writer declined its visual."""
        return c["text_specs"] if c.get("visual") == "none" else c["specs"]

    @staticmethod
    def bullets_expected(built: dict) -> str:
        """How many bullets the slide's whole source supports, e.g. "7-14": one per 400 to one per 200 characters, at
        least one per key packet and at least 3 from 600 characters. No layout limit: layouts grow."""
        size = built.get("size") or {}
        chars, keys = size.get("source_chars", size.get("pool_chars", 0)), size.get("anchors", 0)
        low = max(keys, 3 if chars >= 600 else 2, round(chars / 400))
        return f"{low}-{max(low, round(chars / 200))}"

    @staticmethod
    def packet_line(pk: dict, back: Dict[str, str]) -> str:
        """`K34 Precision (metric) · P51,P55 · links: Precision — Recall (S3.2)`; the KG description only for a node
        whose name the passages do not state."""
        parts = ["ctx" if pk["id"] == "ctx" else f"{pk['id']} {pk['keyword']}", ",".join(pk["passages"]) or "-"]
        if pk.get("links"):
            parts.append("links: " + "; ".join(f"{l['a']} — {l['b']} ({back.get(l['says'], '-')})" for l in pk["links"]))
        if pk.get("via") == "description" and pk.get("hint"):
            parts.append("hint: " + pk["hint"])
        return " · ".join(parts)

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
            self._unpaste(c)
            self._repair(c)

    def needs_retry(self, c: dict) -> bool:
        """Only a slide with nothing usable is asked again whole; pasted bullets are rewritten on their own
        (`_unpaste`) and every other problem is settled by code (`_repair`)."""
        return not self.survivors(c)

    def _unpaste(self, c: dict) -> None:
        """Bullets that paste their source, rewritten in one small call with only those bullets and their sentences."""
        if c["result"] is None:
            return
        pasted = [b for g in c["result"]["groups"] for b in g["bullets"]
                  if b["cites"] and self.copied(b["text"], self._text_of(c, b["cites"]))]
        if not pasted:
            return
        texts = self.sentence_texts(c)
        items = [{"id": f"b{k + 1}", "text": b["text"], "sources": [texts[s] for s in b["cites"] if s in texts],
                  "why": "it repeats the wording of its source; say the same in your own words"}
                 for k, b in enumerate(pasted)]
        fixed = self.rewrite_bullets(items)
        for k, b in enumerate(pasted):
            text = fixed.get(f"b{k + 1}")
            if text and self.acceptable(text, " ".join(items[k]["sources"])):
                c["problems"] = [p for p in c["problems"] if f"'{b['text'][:50]}'" not in p]
                b["text"] = text

    def sentence_texts(self, c: dict) -> Dict[str, str]:
        texts = {s: t for row in c["built"]["pool"].values() for s, t in row["sentences"]}
        texts.update({sid: row["text"] for sid, row in c["visuals"].items()})
        return texts

    def acceptable(self, text: str, source: str) -> bool:
        """A rewritten bullet the checks accept: not pasted, no number its sources lack, not the document's narration."""
        return (bool(text) and not self.copied(text, source) and not self.unstated_numbers(text, source)
                and not _BOOK_VOICE.search(text))

    def rewrite_bullets(self, items: List[dict]) -> Dict[str, str]:
        """{id: new text} for bullets that failed a check, each sent with its sentences and what failed."""
        payload = {"bullets": items, "task": "Rewrite each bullet and return them in `rewritten`."}
        messages = [{"role": "system", "content": _REWRITE},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        def invoke():
            return parse_json_reply(chat(self.model, messages, temperature=0, max_tokens=1500,
                                         role="slide_writer_fix", json_mode=True))
        try:
            answer = cached_json(f"bullet_rewrite_v1.{digest(_REWRITE)[:10]}", self.model, payload, invoke)
        except ValueError as exc:
            print(f"[slide_writer] unreadable rewrite: {str(exc)[:120]}")
            return {}
        rows = (answer.get("rewritten") if isinstance(answer, dict) else None) or []
        return {str(r.get("id")): " ".join(_IDS.sub("", str(r.get("text") or "")).split())
                for r in rows if isinstance(r, dict)}

    def fix_bullets(self, job: dict, content: SlideContent, rows: List[dict]) -> int:
        """Content QA's wrong or unsupported bullets rewritten in place from their sentences and the review's reason;
        the slide keeps its layout when the bullets still fit. Returns how many were rewritten."""
        items = [{"id": f"b{r['index'] + 1}", "text": r["text"], "sources": r["sources"],
                  "why": f"{r['kind']}: '{r.get('claim', '')}' — {r['reason']}"} for r in rows]
        fixed = self.rewrite_bullets(items)
        done = 0
        for r, item in zip(rows, items):
            text = fixed.get(item["id"])
            if text and text != r["text"] and self.acceptable(text, " ".join(r["sources"])):
                content.content[r["index"]] = text
                done += 1
        if done:
            self.relayout(job, content)
        return done

    @staticmethod
    def explained(c: dict, drawn: List[str], groups: List[dict]) -> List[str]:
        """The drawn candidates that may stay: a listing only when a bullet cites it (code beside bullets about something
        else misleads); the slide's first formula always (it follows its own caption in the document), a formula drawn
        beside it when a bullet cites it or names what it defines (`recall` for recall = TP/(TP+FN))."""
        cited = {s for g in groups for b in g["bullets"] for s in b["cites"]}
        if c["kind"] != "formula":
            return [s for s in drawn if s in cited]
        said = " ".join(b["text"] for g in groups for b in g["bullets"]).casefold()
        names = lambda s: defined_names(c["visuals"][s]["text"])
        return drawn[:1] + [s for s in drawn[1:]
                            if s in cited or any(re.search(rf"\b{re.escape(n)}\b", said) for n in names(s))]

    @staticmethod
    def flawed(problem: str) -> bool:
        """A problem only the Writer can fix: a bullet pasted from its source, or a drawn asset no bullet explains."""
        return "copies its source" in problem or "explained by no bullet" in problem

    def better(self, c: dict, result: Optional[dict], problems: List[str]) -> bool:
        """Whether a new answer replaces the slide's: it keeps more bullets, or as many with fewer flaws."""
        if result is None:
            return False
        flaws = lambda ps: sum(map(self.flawed, ps))
        old, new = self.survivors(c), self.survivors(c, result)
        return new > old or (new == old and flaws(problems) < flaws(c["problems"]))

    def survivors(self, c: dict, result: Optional[dict] = None) -> int:
        """Bullets of the answer that `_repair` keeps: they cite (or can be anchored to) sentences stating their numbers."""
        result = c["result"] if result is None else result
        if result is None:
            return 0
        kept = 0
        for g in result["groups"]:
            for b in g["bullets"]:
                cites = b["cites"] or self.anchor(c, b["text"])
                kept += bool(cites) and not self.unstated_numbers(b["text"], self._text_of(c, cites))
        return kept

    @staticmethod
    def unstated_numbers(text: str, source: str) -> List[str]:
        return [x for x in _NUMBER.findall(text) if x not in source.replace(" ", "")
                and x.replace(",", "") not in source.replace(",", "")]

    @staticmethod
    def short(name: str) -> str:
        return name.removesuffix("_layout")

    @staticmethod
    def describe(specs: List[lp.LayoutSpec]) -> Dict[str, str]:
        """{name: what it shows · its blocks · how many bullets it holds once grown} for the layouts a slide may take."""
        return {SlideWriterAgent.short(s.name): f"{s.relation} · {SlideWriterAgent.blocks(s)} · up to "
                                                f"{SlideWriterAgent.capacity(s.name)}" for s in specs}

    @staticmethod
    def usable(specs: List[lp.LayoutSpec], low: int, high: int) -> List[lp.LayoutSpec]:
        """The layouts worth offering a slide of `low`-`high` bullets: they hold at least `low` and need no more blocks
        than `high`; the plain lists always (any other offered layout is still accepted if named)."""
        keep = [s for s in specs if s.name in _FALLBACKS
                or (SlideWriterAgent.capacity(s.name) >= low and s.min_blocks <= high)]
        return keep or specs

    def _ask(self, batch: List[dict], issues: Dict[int, List[str]]) -> Dict[int, dict]:
        system = _INSTRUCTION            # the same for every call, so the API serves it from its prompt cache
        indexes = [c["n"] for c in batch]
        payload = {"slides": [{**c["payload"], **({"issues": issues[c["n"]]} if c["n"] in issues else {})}
                              for c in batch],
                   "task": f"Write slide{'s' if len(batch) > 1 else ''} {', '.join(map(str, indexes))} and return "
                           f"{'them' if len(batch) > 1 else 'it'} in `written`, as the output schema says."}
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        def invoke():
            return parse_json_reply(chat(self.model, messages, temperature=0, max_tokens=6000, role="slide_writer",
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
    def _text_of(self, c: dict, sids: List[str]) -> str:
        shown = [row["text"] for sid, row in c["visuals"].items() if sid in sids]
        return " ".join([t for row in c["built"]["pool"].values() for s, t in row["sentences"] if s in sids] + shown)

    def pick(self, c: dict, row: dict):
        """The candidates the slide draws, in reading order: up to MAX_SLIDE_FORMULAS formulas or one listing ("none"
        when the Writer declined them all; the first it judged ok when it named none) and, for a listing, the
        [first, last] lines it chose (None: all of it)."""
        if not c["visuals"]:
            return None, None
        raw = row.get("visual")
        items = [str(x) for x in (raw if isinstance(raw, list) else [raw] if raw else [])]
        if isinstance(raw, list) and not raw or any(x.strip().lower() == "none" for x in items):
            return "none", None
        checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}
        judged = {sid: str(v).strip().lower() for k, v in checks.items() for sid in self.resolve(c, [k])[0]}
        named = [s for s in dict.fromkeys(self.resolve(c, items)[0]) if s in c["visuals"]]
        if not named:
            named = [next((s for s in c["visuals"] if judged.get(s, "ok") == "ok"), "none")]
        # the Writer's own verdict rules: a candidate it judged broken or off is never drawn
        drawn = [s for s in named if s != "none" and judged.get(s, "ok") == "ok"]
        drawn = drawn[:1] if c["kind"] == "code" else drawn[:Config.MAX_SLIDE_FORMULAS]
        if not drawn:
            return "none", None
        nums = [int(x) for x in re.findall(r"\d+", str(row.get("lines") or ""))][:2]
        total = len(c["visuals"][drawn[0]]["text"].splitlines())
        whole = c["kind"] != "code" or len(nums) != 2 or not 1 <= nums[0] <= nums[1] <= total
        return drawn, None if whole or nums == [1, total] else nums

    def visual(self, c: dict) -> Optional[dict]:
        """The first formula or listing row the slide draws (None when the Writer declined them all)."""
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
        ids = {pk["id"] for pk in c["built"]["packets"]}
        groups = []
        for g in row.get("groups") or []:
            if not isinstance(g, dict):
                continue
            bullets = []
            for b in g.get("bullets") or []:
                if not isinstance(b, dict) or not str(b.get("text") or "").strip():
                    continue
                text = " ".join(_IDS.sub("", str(b["text"])).split())
                cites, bad = self.resolve(c, b.get("cites") or [])
                if bad:
                    problems.append(f"bullet '{text[:50]}' cites ids that are not in its passages ({bad[:3]}); cite "
                                    f"the S# labels of this slide's own passages")
                if not cites:
                    problems.append(f"bullet '{text[:50]}' cites no sentence")
                source = self._text_of(c, cites)
                unsupported = [x for x in _NUMBER.findall(text) if x not in source.replace(" ", "")
                               and x.replace(",", "") not in source.replace(",", "")]
                if cites and unsupported:
                    problems.append(f"bullet '{text[:50]}' has numbers {unsupported} its cited sentences do not state")
                if cites and self.copied(text, source):
                    problems.append(f"bullet '{text[:50]}' copies its source; rewrite it in your own words")
                bullets.append({"text": text, "cites": cites,
                                "covers": [x for x in dict.fromkeys(str(v) for v in b.get("covers") or []) if x in ids]})
            if bullets:
                groups.append({"title": " ".join(str(g.get("title") or "").split()), "bullets": bullets})
        if not groups:
            return None, problems or ["no bullets"]
        left = {str(r.get("packet")): str(r.get("reason") or "") for r in row.get("left") or []
                if isinstance(r, dict) and str(r.get("packet")) in ids and str(r.get("reason") or "").strip()}
        narrated = [b["text"][:50] for g in groups for b in g["bullets"] if _BOOK_VOICE.search(b["text"])]
        if narrated:
            problems.append(f"bullets {narrated[:3]} keep the book's narration ('we', 'you', 'let's', ...): state them "
                            f"as the presenter's own plain statements")
        uncovered = [i for i in ids if i not in self.covered(c, groups) and i not in left]
        taught = {pk["id"] for pk in c["built"]["packets"] if pk["id"].startswith("K")}
        missed = [i for i in uncovered if i in taught]
        if missed:
            problems.append(f"KG packets {missed} are used by no bullet: give each at least one bullet, or leave it "
                            f"with the reason if it is only named in passing")
        elif uncovered:
            problems.append(f"packets {uncovered} are used by no bullet: cover them or leave them with a reason")
        drawn, lines = self.pick(c, row)
        c["drawn"] = drawn if isinstance(drawn, list) else []
        c["visual"] = c["drawn"][0] if c["drawn"] else drawn
        label = {sid: lab for lab, sid in c["short"].items()}
        unexplained = [label.get(s, s) for s in c["drawn"] if s not in self.explained(c, c["drawn"], groups)]
        if unexplained:
            what = "listing" if c["kind"] == "code" else "formula"
            problems.append(f"the drawn {what} {unexplained} is explained by no bullet: cite it in the bullets that "
                            f"explain it, or leave it out of `visual`")
        layout = self.named(c, str(row.get("layout") or ""))
        if layout not in {s.name for s in self.specs_of(c)}:
            problems.append(f"layout '{layout}' is not offered; choose from {[s.name for s in self.specs_of(c)]}")
        elif (why := self.misfit(c, layout, groups)):
            problems.append(f"the slide does not fit its layout: {why}")
        title = " ".join(str(row.get("title") or "").split())
        result = {"layout": layout, "groups": groups, "left": left, "notes": " ".join(str(row.get("notes") or "").split()),
                  "title": title if 2 <= len(title.split()) <= 8 else "", "visual": c["visual"], "drawn": c["drawn"],
                  "lines": lines}
        return result, problems

    @staticmethod
    def copied(text: str, source: str) -> bool:
        """Whether a bullet pastes its source: COPY_RUN or more consecutive source words, in a bullet long enough that
        rewording was possible."""
        words = re.findall(r"\w+", text.casefold())
        if len(words) < COPY_RUN + 2:
            return False
        src = re.findall(r"\w+", source.casefold())
        runs = {tuple(src[k:k + COPY_RUN]) for k in range(len(src) - COPY_RUN + 1)}
        return any(tuple(words[k:k + COPY_RUN]) in runs for k in range(len(words) - COPY_RUN + 1))

    @staticmethod
    def resolve(c: dict, labels) -> Tuple[List[str], List[str]]:
        """(sentence ids, labels that name nothing). A paragraph id stands for its sentences in the pool; a bare `S3`
        is read as this slide's `S{n}.3`; a label wrapped with its paragraph (`P12.S0.3`, `P12: S0.3`) is its S#."""
        out, bad = [], []
        pool = c["built"]["pool"]
        for raw in labels:
            x = str(raw).strip().strip("[](),;. ")
            inner = re.findall(r"S\d+\.\d+", x)
            if x in c["short"]:
                out.append(c["short"][x])
            elif re.fullmatch(r"S\d+", x) and f"S{c['n']}.{x[1:]}" in c["short"]:
                out.append(c["short"][f"S{c['n']}.{x[1:]}"])
            elif x in pool:
                out.extend(s for s, _ in pool[x]["sentences"])
            elif inner and all(s in c["short"] for s in inner):
                out.extend(c["short"][s] for s in inner)
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
        each = f", <= {spec.max_facts_per_block} each" if spec.max_facts_per_block < 99 else ""
        short = f", bullets <= {spec.body_chars} chars" if spec.body_chars < 100 else ""
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
        """Packets a bullet names in `covers` or cites a sentence of."""
        cited = {s for g in groups for b in g["bullets"] for s in b["cites"]}
        pool = c["built"]["pool"]
        out = {x for g in groups for b in g["bullets"] for x in b["covers"]}
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
        groups = [{"title": g.get("title") or "", "bullets": [{"text": content.content[i], "cites": [], "covers": []}
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
            texts = ([r.get("normalized") or r["text"] for r in self.drawn_rows(c)] if c.get("drawn")
                     else c["spec"].latex_formulas or [r.get("normalized") or r["text"] for r in self.drawn_rows(c)])
            if texts:                                # stacked formulas, a blank line between two (deck formulaLines)
                out["formula_lines"] = sum(len(to_mathtext(t)) for t in texts) + len(texts) - 1
        if assets.get("table"):
            out["table_rows"] = max(1, sum(1 for line in str(assets["table"][0]["text"]).splitlines()
                                           if line.strip().startswith("|") and not set(line.strip()) <= set("|-: ")) - 1)
        return out

    def anchor(self, c: dict, text: str) -> List[str]:
        """Sentences of the pool a bullet whose citations named nothing actually rests on (most of its content words
        must occur in them); [] when none does."""
        rows = [(s, t) for row in c["built"]["pool"].values() for s, t in row["sentences"]]
        ranked = sorted(rows, key=lambda r: -support_ratio(text, r[1]))[:2]
        if not ranked or support_ratio(text, ranked[0][1]) < 0.5:
            return []
        best = [ranked[0]]
        if len(ranked) > 1 and support_ratio(text, ranked[0][1] + " " + ranked[1][1]) > support_ratio(text, ranked[0][1]):
            best.append(ranked[1])
        return [s for s, _ in best]

    # ── what still fails is settled without inventing and without a new slide ──
    def _repair(self, c: dict) -> None:
        result = c["result"]
        if result is None:
            return
        notes = [result["notes"]] if result["notes"] else []
        for g in result["groups"]:
            kept = []
            for b in g["bullets"]:
                if not b["cites"]:
                    b["cites"] = self.anchor(c, b["text"])
                if b["cites"] and not self.unstated_numbers(b["text"], self._text_of(c, b["cites"])):
                    kept.append(b)
            g["bullets"] = kept
        result["groups"] = [g for g in result["groups"] if g["bullets"]]
        if not result["groups"]:                   # nothing the Writer wrote survives: the slide is not written
            c["result"] = None
            return
        c["drawn"] = self.explained(c, result.get("drawn") or [], result["groups"])
        c["visual"] = c["drawn"][0] if c["drawn"] else ("none" if c["visuals"] else None)
        result["visual"], result["drawn"] = c["visual"], c["drawn"]
        plain = [b for g in result["groups"] for b in g["bullets"] if not _BOOK_VOICE.search(b["text"])]
        if plain:                                  # the book narrating to its reader is never the lecturer's voice
            for g in result["groups"]:
                g["bullets"] = [b for b in g["bullets"] if not _BOOK_VOICE.search(b["text"])]
            result["groups"] = [g for g in result["groups"] if g["bullets"]]
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
            spec.latex_formulas = [clean_ocr_latex(r["normalized"]) for r in self.drawn_rows(c)]
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
        notes += [{"passage": text} for text in built.get("notes") or []]
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
