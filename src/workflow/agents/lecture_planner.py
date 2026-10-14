"""Agent 1, Lecture Planner: the LLM divides a chapter into full slides, then states its goals.

A whole chapter (up to ~260 paragraphs) is too much for one call: the model answers with a sampled outline and skips
most paragraphs. So the chapter's compact map (sub-headings with their size; one line per paragraph: id, length, the KG
nodes it states, opening words) is cut at sub-heading boundaries into PARTS of about `LECTURE_PART_CHARS` characters,
and for each part (in parallel) the model decides the slides: how many, where each one starts (a slide runs to the
next slide's start, so it gathers several ideas when they form one subject and every paragraph lands on exactly one
slide) and which K# each teaches, or which it only mentions. One more call per chapter reads the planned slides and
states the chapter's goals and which slides serve each. Code reads the starts, gives each node the model leaves out its
reason from the KG (taught in a later or an earlier section, or named in passing) and keeps it on a slide when the KG
says this chapter teaches it, asks once more about shared nodes and thin slides, and puts a node still unsettled on the
slide holding its first paragraph (where the document introduces it); whether it is a key idea is the Packet Builder's.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from src.utils.config import Config
from src.utils.content_cache import cached_json, digest
from src.utils.llm import chat, parse_json_reply
from src.workflow.prompts import load

LEAVE_REASONS = {"forward_reference", "defined_elsewhere", "incidental"}
_OPENING = 110
_TAGS = 8

_PART = load("lecture_planner:part")
_REPAIR = load("lecture_planner:repair")
_GOALS = load("lecture_planner:goals")


def _ids(raw, allowed, head: str) -> List[str]:
    return [i for i in dict.fromkeys(str(x).strip() for x in raw or []) if i in allowed and i.startswith(head)]


def expand(raw, allowed: List[str]) -> List[str]:
    """P# ids of `raw` ("P3-P7", "P9", "P3–P7", or a whole map line the model copied back: "P9 · 368c · …"), in
    reading order, restricted to `allowed` (the map's paragraphs)."""
    order = {p: n for n, p in enumerate(allowed)}
    out = []
    for item in raw or []:
        m = re.match(r"\s*P(\d+)(?:\s*(?:[-–—]|to|\.\.)\s*P?(\d+))?", str(item))
        if m:
            a, b = sorted((int(m.group(1)), int(m.group(2) or m.group(1))))
            out += [f"P{k}" for k in range(a, b + 1)]
    return sorted({p for p in out if p in order}, key=order.get)


def ranges(pids: List[str]) -> str:
    """"P3-P7, P9" for the planner's own view of a slide."""
    nums = sorted(int(p[1:]) for p in pids)
    out, start = [], None
    for n, k in enumerate(nums):
        start = k if start is None else start
        if n + 1 == len(nums) or nums[n + 1] != k + 1:
            out.append(f"P{start}" if start == k else f"P{start}-P{k}")
            start = None
    return ", ".join(out) or "-"


class LecturePlannerAgent:
    def __init__(self, model: str):
        self.model = model

    # ── the chapter map and its parts ───────────────────────────────────────────
    @staticmethod
    def teachable(chapter) -> List[str]:
        return [p["pid"] for p in chapter.paragraphs if chapter.teachable(p)]

    @staticmethod
    def chapter_map(chapter, inventory: dict, titles: Dict[int, str], pids: Optional[List[str]] = None) -> List[str]:
        """A `##` line per sub-heading (with the size of its source), one line per paragraph of `pids` with where it
        starts in the part (`@2.4k`: characters of `pids` before it), so slide sizes read as differences of starts."""
        pids = LecturePlannerAgent.teachable(chapter) if pids is None else pids
        tags: Dict[str, List[str]] = {}
        for k, node in inventory["nodes"].items():
            for p in dict.fromkeys(node.get("pids") or []):
                tags.setdefault(p, []).append(k)
        size: Dict[int, int] = {}
        for pid in pids:
            hid = chapter.by_pid[pid]["heading"]
            size[hid] = size.get(hid, 0) + len(chapter.text_of(pid))
        lines, heading, at = [], None, 0
        for pid in pids:
            p = chapter.by_pid[pid]
            if p["heading"] != heading:
                heading = p["heading"]
                if heading != chapter.id:
                    lines.append(f"## {titles.get(heading, heading)} · {size[heading] / 1000:.1f}k chars")
            text = chapter.text_of(pid)
            k = " ".join(tags.get(pid, [])[:_TAGS]) or "-"
            body = f"[{p['kind']}]" if p["kind"] in ("formula", "table") else chapter.text_of(pid, _OPENING)
            lines.append(f"{pid} · @{at / 1000:.1f}k · {len(text)}c · {k} · {body}")
            at += len(text)
        return lines

    @staticmethod
    def parts(chapter, pids: List[str], target: int) -> List[List[str]]:
        """Consecutive paragraphs cut at sub-heading boundaries into parts of about `target` characters; a sub-heading
        much longer than that is cut between paragraphs."""
        runs: List[List[str]] = []
        for pid in pids:
            if runs and chapter.by_pid[runs[-1][-1]]["heading"] == chapter.by_pid[pid]["heading"]:
                runs[-1].append(pid)
            else:
                runs.append([pid])
        size = lambda rows: sum(len(chapter.text_of(p)) for p in rows)
        pieces: List[List[str]] = []
        for run in runs:
            if size(run) <= 1.6 * target:
                pieces.append(run)
                continue
            piece: List[str] = []
            for pid in run:
                piece.append(pid)
                if size(piece) >= target:
                    pieces.append(piece)
                    piece = []
            if piece:
                pieces.append(piece)
        out: List[List[str]] = []
        for piece in pieces:
            if out and size(out[-1]) < target and size(out[-1]) + size(piece) <= 1.6 * target:
                out[-1] += piece
            else:
                out.append(list(piece))
        if len(out) > 1 and size(out[-1]) < target / 3:          # a short tail joins the part before it
            tail = out.pop()
            out[-1] += tail
        return out

    @staticmethod
    def introduced(inventory: dict, parts: List[List[str]]) -> List[List[str]]:
        """For each part, the K# whose first teachable paragraph lies in it (the part that must teach or leave them)."""
        home = {p: n for n, rows in enumerate(parts) for p in rows}
        out: List[List[str]] = [[] for _ in parts]
        for k, node in inventory["nodes"].items():
            first = next((home[p] for p in node.get("pids") or [] if p in home), None)
            if first is not None:
                out[first].append(k)
        return out

    @staticmethod
    def _kg_lines(inventory: dict, kids: List[str]) -> List[str]:
        nodes = inventory["nodes"]
        def where(node):                           # the other section of the document that names the node most
            home = node.get("home")
            return f" · mainly in: {home['section']} ({home['side']})" if home else ""
        return [f"{k} {nodes[k]['name']} ({nodes[k]['type']}) · {nodes[k].get('mentions', 0)}× · first "
                f"{(nodes[k].get('pids') or ['-'])[0]}{where(nodes[k])}" for k in kids if k in nodes]

    def _ask(self, system: str, payload: dict, role: str, max_tokens: int) -> Optional[dict]:
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        for _ in range(2):
            try:
                answer = cached_json(f"lecture_plan_v2.{digest(system)[:10]}", self.model, payload,
                                     lambda: parse_json_reply(chat(self.model, messages, temperature=0,
                                                                   max_tokens=max_tokens, role=role, json_mode=True)))
            except (ValueError, RuntimeError) as exc:
                print(f"[lecture_planner] {role}: unusable answer ({str(exc)[:100]})")
                continue
            if isinstance(answer, dict):
                return answer
        return None

    # ── plan ───────────────────────────────────────────────────────────────────
    def plan(self, chapter, inventory: dict, titles: Dict[int, str]) -> dict:
        """{goals:[{gid, goal, questions, teaches, paragraphs}], slides:[{title, message, gid, goals, teaches, links,
        paragraphs}], left_kg:{K#: reason}, unplaced:{paragraphs, kg}, calls:int}"""
        pids = self.teachable(chapter)
        parts = self.parts(chapter, pids, Config.LECTURE_PART_CHARS) if pids else []
        owned = self.introduced(inventory, parts)
        rows = [None] * len(parts)
        with ThreadPoolExecutor(max_workers=max(1, min(len(parts), Config.LECTURE_PART_CONCURRENCY))) as pool:
            futures = {n: pool.submit(self._plan_part, chapter, inventory, titles, parts[n], owned[n],
                                      parts[n - 1] if n else []) for n in range(len(parts))}
            for n, future in futures.items():
                rows[n] = future.result()
        slides = [s for row in rows for s in row["slides"]]
        left = {k: r for row in rows for k, r in row["left"].items()}
        unplaced = {"paragraphs": [p for row in rows for p in row["unplaced"]["paragraphs"]],
                    "kg": [k for row in rows for k in row["unplaced"]["kg"]]}
        calls = sum(row["calls"] for row in rows)
        goals = self._goals(chapter, slides) if slides else []
        calls += 1 if slides else 0
        return self._finish(chapter, inventory, goals, slides, left, unplaced, calls, len(parts))

    def _plan_part(self, chapter, inventory: dict, titles: Dict[int, str], pids: List[str], kids: List[str],
                   before: List[str]) -> dict:
        nodes = {k: inventory["nodes"][k] for k in kids}
        lines = self.chapter_map(chapter, inventory, titles, pids)
        headings = list(dict.fromkeys(str(titles.get(chapter.by_pid[p]["heading"], "")) for p in before))
        chars = sum(len(chapter.text_of(p)) for p in pids)
        payload = {"section": chapter.title, "part_chars": chars, "slides_expected": self.expected(chars),
                   "before": [h for h in headings if h][-3:], "map": lines,
                   "introduced": self._kg_lines(inventory, kids),
                   "task": "Plan the slides of this part as the output schema says."}
        answer = self._ask(_PART, payload, "lecture_planner", 6000)
        calls = 1
        if answer is None:
            print(f"[lecture_planner] {chapter.title}: no plan for {pids[0]}-{pids[-1]}; one slide per sub-heading")
            slides = self._fallback(chapter, inventory, titles, pids, kids)
            return {"slides": slides, "left": {}, "unplaced": self._unsettled(slides, pids, nodes, {}), "calls": calls}
        slides = self.split(answer, pids, nodes)
        left = self._leave(answer, nodes)
        issues = self.problems(slides, pids, nodes, left)
        thin = [n for n, s in enumerate(slides) if self.chars(chapter, s) < Config.LECTURE_THIN_CHARS]
        if slides and (issues["shared_kg"] or (thin and len(slides) > 1)):
            repaired = self._repair(chapter, inventory, kids, slides, left, issues, thin)
            calls += 1
            if repaired is not None:
                slides, left = repaired
        if not slides:
            print(f"[lecture_planner] {chapter.title}: no slide starts for {pids[0]}-{pids[-1]}; one slide per sub-heading")
            slides = self._fallback(chapter, inventory, titles, pids, kids)
        slides = self.place(self._settle(slides, left), nodes, left)
        return {"slides": slides, "left": left, "unplaced": self._unsettled(slides, pids, nodes, left), "calls": calls}

    @staticmethod
    def expected(chars: int) -> str:
        """How many full slides (2000-3000 characters of source each) a part fills, e.g. "4-6"."""
        low = max(1, round(chars / 3000))
        return f"{low}-{max(low, round(chars / 2000))}"

    @classmethod
    def _unsettled(cls, slides, pids, nodes, left) -> dict:
        issues = cls.problems(slides, pids, nodes, left)
        return {"paragraphs": issues["paragraphs"], "kg": issues["kg"]}

    @staticmethod
    def split(answer: dict, pids: List[str], nodes: dict) -> List[dict]:
        """The part's slides from the model's starts: each runs to the next start (the first from the part's first
        paragraph); an unknown or repeated start is ignored."""
        starts: Dict[str, dict] = {}
        for row in answer.get("slides") or []:
            if not isinstance(row, dict):
                continue
            title = " ".join(str(row.get("title") or "").split())
            start = expand([row.get("from")], pids)
            if len(title) >= 2 and start:
                starts.setdefault(start[0], {"title": title, "message": " ".join(str(row.get("message") or "").split()),
                                             "teaches": _ids(row.get("teaches"), nodes, "K")})
        order = {p: n for n, p in enumerate(pids)}
        firsts = sorted(starts, key=order.get)
        bounds = [0] + [order[p] for p in firsts[1:]] + [len(pids)]
        return [{**starts[p], "paragraphs": pids[bounds[n]:bounds[n + 1]]} for n, p in enumerate(firsts)]

    @staticmethod
    def reason(node: dict) -> Optional[str]:
        """Why a node the model leaves out is not taught here, from the KG: another section names it most (later or
        earlier), or this chapter names it in at most LEAVE_INCIDENTAL_SPAN paragraphs; None when this chapter teaches it."""
        home = node.get("home")
        if home:
            return "forward_reference" if home["side"] == "later" else "defined_elsewhere"
        return "incidental" if node.get("span", 0) <= Config.LEAVE_INCIDENTAL_SPAN else None

    @classmethod
    def _leave(cls, answer: dict, nodes: dict) -> Dict[str, str]:
        """{K#: reason} of the nodes the model leaves out (a list of K#, or rows with `ids`) that the KG agrees are not
        this chapter's to teach."""
        out = {}
        for row in answer.get("leave") or []:
            for i in (row.get("ids") or [] if isinstance(row, dict) else [row]):
                k = str(i).strip()
                why = cls.reason(nodes[k]) if k in nodes else None
                if why:
                    out[k] = why
        return out

    @staticmethod
    def place(slides: List[dict], nodes: dict, left: Dict[str, str]) -> List[dict]:
        """A node on no slide and not left goes on the slide holding its first paragraph."""
        taught = {k for s in slides for k in s["teaches"]}
        home = {p: s for s in slides for p in s["paragraphs"]}
        for k, node in nodes.items():
            first = next((p for p in node.get("pids") or [] if p in home), None)
            if k not in taught and k not in left and first:
                home[first]["teaches"].append(k)
        return slides

    @staticmethod
    def problems(slides: List[dict], pids: List[str], nodes: dict, left: Dict[str, str]) -> Dict[str, list]:
        """Paragraphs / nodes on no slide, and those on more than one slide (a left node is settled)."""
        where_p: Dict[str, List[int]] = {}
        where_k: Dict[str, List[int]] = {}
        for n, s in enumerate(slides):
            for p in s["paragraphs"]:
                where_p.setdefault(p, []).append(n)
            for k in s["teaches"]:
                where_k.setdefault(k, []).append(n)
        return {"paragraphs": [p for p in pids if p not in where_p],
                "kg": [k for k in nodes if k not in where_k and k not in left],
                "shared_paragraphs": {p: rows for p, rows in where_p.items() if len(rows) > 1},
                "shared_kg": {k: rows for k, rows in where_k.items() if len(rows) > 1}}

    @staticmethod
    def chars(chapter, slide: dict) -> int:
        return sum(len(chapter.text_of(p)) for p in slide["paragraphs"] if p in chapter.by_pid)

    def _repair(self, chapter, inventory: dict, kids: List[str], slides: List[dict], left: Dict[str, str],
                issues: dict, thin: Optional[List[int]] = None) -> Optional[Tuple[List[dict], Dict[str, str]]]:
        """One call about only what is unsettled: nodes on no slide or on two, and thin slides; the answer places each
        node on one slide (or leaves it) and joins a thin slide into its neighbour when they teach one subject."""
        nodes = {k: inventory["nodes"][k] for k in kids}
        kg_line = dict(zip(kids, self._kg_lines(inventory, kids)))
        size = [self.chars(chapter, s) for s in slides]
        payload = {
            "section": chapter.title,
            "slides": [f"{n} | {s['title']} | {ranges(s['paragraphs'])} | {size[n]}c | {' '.join(s['teaches']) or '-'}"
                       for n, s in enumerate(slides)],
            "unplaced": [kg_line[k] for k in issues["kg"]],
            "shared": [f"{k} on slides {', '.join(map(str, rows))}" for k, rows in issues["shared_kg"].items()],
            "thin": [f"{n} ({size[n]}c, \"{slides[n]['title']}\")" for n in thin or []],
            "task": "Settle the concepts and thin slides above as the output schema says."}
        answer = self._ask(_REPAIR, payload, "lecture_planner_repair", 3000)
        if answer is None:
            return None
        asked = set(issues["kg"]) | set(issues["shared_kg"])
        left = {**left, **{k: r for k, r in self._leave(answer, nodes).items() if k in asked}}
        assign = []
        for row in answer.get("assign") or []:
            try:
                n = int(row.get("slide"))
            except (TypeError, ValueError, AttributeError):
                continue
            if 0 <= n < len(slides):
                assign.append((n, [k for k in _ids(row.get("teaches"), nodes, "K") if k in asked and k not in left]))
        placed = {k for _, ks in assign for k in ks} | (set(left) & asked)
        for s in slides:                                     # a node the answer places leaves every other slide
            s["teaches"] = [k for k in s["teaches"] if k not in placed]
        for n, ks in assign:
            slides[n]["teaches"] += [k for k in ks if k not in slides[n]["teaches"]]
        return self.join(slides, answer.get("join") or [], set(thin or [])), left

    @staticmethod
    def join(slides: List[dict], rows: list, thin: set) -> List[dict]:
        """The model's joins of thin slides into the slide before or after them: the paragraphs stay consecutive and
        each on one slide; the neighbour keeps its title and gains the thin slide's nodes."""
        into: Dict[int, int] = {}
        for row in rows:
            try:
                n, side = int(row.get("slide")), str(row.get("into") or "").lower()
            except (TypeError, ValueError, AttributeError):
                continue
            to = n - 1 if side.startswith("prev") else n + 1 if side.startswith("next") else None
            if n in thin and to is not None and 0 <= to < len(slides) and n not in into.values():
                into[n] = to
        for n in sorted(into, reverse=True):
            to = into[n]
            while to in into:                                # joined into a slide that is itself joined on
                to = into[to]
            target, gone = slides[to], slides[n]
            target["paragraphs"] = sorted(target["paragraphs"] + gone["paragraphs"], key=lambda p: int(p[1:]))
            target["teaches"] += [k for k in gone["teaches"] if k not in target["teaches"]]
            gone["paragraphs"], gone["teaches"] = [], []
        return [s for s in slides if s["paragraphs"]]

    @staticmethod
    def _settle(slides: List[dict], left: Dict[str, str]) -> List[dict]:
        """A node still on two slides stays on the first the model put it on; a left node is taught nowhere."""
        seen_p, seen_k = set(), set()
        for s in slides:
            s["paragraphs"] = [p for p in s["paragraphs"] if p not in seen_p]
            s["teaches"] = [k for k in s["teaches"] if k not in seen_k and k not in left]
            seen_p.update(s["paragraphs"])
            seen_k.update(s["teaches"])
        return [s for s in slides if s["paragraphs"]]

    # ── goals of the chapter, from its planned slides ──────────────────────────
    def _goals(self, chapter, slides: List[dict]) -> List[dict]:
        payload = {"section": chapter.title,
                   "slides": [f"{n} | {s['title']} | {s['message'][:160]}" for n, s in enumerate(slides)],
                   "goals_expected": self.goals_expected(len(slides)),
                   "task": "State the goals of these slides as the output schema says."}
        answer = self._ask(_GOALS, payload, "lecture_goals", 3000) or {}
        goals = []
        for row in answer.get("goals") or []:
            if not isinstance(row, dict):
                continue
            text = " ".join(str(row.get("goal") or "").split())
            if len(text) < 5:
                continue
            served = []
            for n in row.get("slides") or []:
                try:
                    if 0 <= int(n) < len(slides):
                        served.append(int(n))
                except (TypeError, ValueError):
                    continue
            if served:                                       # a goal no slide serves would only fill the agenda
                goals.append({"goal": text, "questions": [" ".join(str(q).split()) for q in row.get("questions") or []
                                                          if str(q).strip()][:4], "slides": list(dict.fromkeys(served))})
        return goals

    @staticmethod
    def goals_expected(slides: int) -> str:
        """How many goals a section of this many slides has: one per 3 to 6 slides, at most 10, e.g. "2-4"."""
        low = min(10, max(1, round(slides / 6)))
        return f"{low}-{min(10, max(low, round(slides / 3)))}"

    @staticmethod
    def links(inventory: dict, slides: List[dict], pid_of: Dict[str, str]) -> None:
        """A link R# goes on the slide teaching both its nodes (the later one when they are on two), else on the slide
        holding the paragraph that states it."""
        slide_of = {k: n for n, s in enumerate(slides) for k in s["teaches"]}
        para_of = {p: n for n, s in enumerate(slides) for p in s["paragraphs"]}
        for s in slides:
            s["links"] = []
        for rid, x in inventory["relations"].items():
            ends = [slide_of[k] for k in (x.get("kid_a"), x.get("kid_b")) if k in slide_of]
            home = max(ends) if ends else next((para_of[pid_of[sid]] for sid in x.get("sentences") or []
                                                if pid_of.get(sid) in para_of), None)
            if home is not None:
                slides[home]["links"].append(rid)

    def _finish(self, chapter, inventory: dict, goals: List[dict], slides: List[dict], left: Dict[str, str],
                unplaced: dict, calls: int, parts: int) -> dict:
        if not goals:
            goals = [{"goal": chapter.title, "questions": [], "slides": list(range(len(slides)))}]
        for n, goal in enumerate(goals):
            goal.update({"gid": f"G{chapter.id}.{n}", "teaches": [], "paragraphs": []})
        served = {}
        for n, goal in enumerate(goals):
            for i in goal.pop("slides", []):
                served.setdefault(i, []).append(n)
        current = 0
        for i, s in enumerate(slides):
            s["goals"] = served.get(i) or [current]            # a slide no goal names serves the goal before it
            current = s["goals"][0]
            s["gid"] = goals[current]["gid"]
            for g in s["goals"]:
                goals[g]["teaches"] += s["teaches"]
                goals[g]["paragraphs"] += s["paragraphs"]
        self.links(inventory, slides, chapter.pid_of)
        if unplaced["paragraphs"] or unplaced["kg"]:
            print(f"[lecture_planner] {chapter.title}: unplaced {len(unplaced['paragraphs'])} paragraph(s), "
                  f"{len(unplaced['kg'])} node(s)")
        return {"goals": goals, "slides": slides, "left_kg": left, "unplaced": unplaced, "calls": calls,
                "parts": parts}

    @staticmethod
    def _fallback(chapter, inventory: dict, titles: Dict[int, str], pids: List[str], kids: List[str]) -> List[dict]:
        slides: List[dict] = []
        for pid in pids:
            hid = chapter.by_pid[pid]["heading"]
            if not slides or slides[-1]["hid"] != hid:
                slides.append({"hid": hid, "title": str(titles.get(hid, chapter.title)), "message": "",
                               "paragraphs": [], "teaches": []})
            slides[-1]["paragraphs"].append(pid)
        home = {p: n for n, s in enumerate(slides) for p in s["paragraphs"]}
        for k in kids:
            first = next((p for p in inventory["nodes"][k].get("pids") or [] if p in home), None)
            if first:
                slides[home[first]]["teaches"].append(k)
        for s in slides:
            s.pop("hid")
        return slides
