"""Agent 1, Outline: plans each chapter's slides and goals from what the KG and retrieval say about its sections.

Code queries first (`SectionQuery`, 0 LLM): the chapter's sections, joined into blocks, each with its sections, each with
its central KG ideas, their relations and the sentences retrieval finds stating what it says. One LLM call per chapter then
builds the outline from what each heading's edges state (the KG relations the extractor drew from this chapter's chunks,
each with its keywords and its chunk's own description): for every heading the goals those edges teach (each goal holds
edges and the ideas they join), and, block by block, the slides holding the goals (a slide's message is its goals and
their edges, its key ideas theirs); the only size it is given is what one slide holds
(`slide_max_chars`, from the bullets the Writer writes on a slide). Code checks it (every block has slides, sections covered in order, ideas
every heading has a goal, every edge in one goal that names its ends, every goal on a slide), asks once again with the
problems, and settles what is still wrong without inventing (an edge left out joins a goal holding one of its ends, else
becomes a goal in its own words); a slide over what one slide holds is cut at a heading boundary.
Code then gives each slide its paragraphs from the sections it names (a section running over several slides is cut where
their key ideas are stated), so every paragraph lands on exactly one slide and a slide's paragraphs are consecutive. A
key idea the outline leaves out gets its reason from the KG (taught in another section, or named in passing) or goes on
the slide holding its first paragraph; goals and links are filled in for the Packet Builder.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

from src.utils.bullet_text import support_ratio
from src.utils.config import Config
from src.utils.content_cache import cached_json, digest
from src.utils.llm import chat, parse_json_reply
from src.workflow.prompts import load
from src.workflow.section_query import SectionQuery

_HEAVY = re.compile(r"^slide '.*' holds \d+ chars")   # `read`'s issue about a slide over what one slide holds
_ASKED = re.compile(r"^S\d+(, S\d+)* ha(s|ve) no goal")    # `read`'s issue the model is asked about again; code settles the rest
_CACHE = "outline_v1"          # namespace of the cached answers


def _ids(raw, allowed, head: str) -> List[str]:
    return [i for i in dict.fromkeys(str(x).strip() for x in raw or []) if i in allowed and i.startswith(head)]


def _text(x) -> str:
    return " ".join(str(x or "").split())


def _said(description: str) -> str:
    """An edge's description without the extractor's record delimiters left at its end (quotes, underscores, before or
    after its last stop)."""
    said = re.sub(r"[\"'_]+(?=[.;:!?]?$)", "", _text(description)).strip(" \"'_")
    return re.sub(r"([.;:!?])[.;:!?]+$", r"\1", said)


class OutlineAgent:
    def __init__(self, model: str, domain: str = "general",
                 retrieval: Optional[Dict[int, object]] = None, kb_dir=None):
        self.model = model
        self.prompt = load("outline", domain)
        self.retrieval, self.kb_dir = retrieval or {}, kb_dir

    # ── the query: blocks of sections, each with its ideas and what it states ──
    @staticmethod
    def blocks(sections: List[dict]) -> List[dict]:
        """Consecutive sections joined until a block holds at least OUTLINE_BLOCK_MIN_CHARS (a trailing small one joins
        the block before it); `least` is the fewest slides its text fills (slide_max_chars a slide)."""
        out: List[dict] = []
        for s in sections:
            if not out or out[-1]["chars"] >= Config.OUTLINE_BLOCK_MIN_CHARS:
                out.append({"sections": [], "rows": [], "hids": [], "pids": [], "chars": 0, "central": [], "ideas": [],
                            "links": [], "assets": {}, "tables": [], "figures": []})
            b = out[-1]
            b["sections"].append(s["title"])
            b["rows"].append(s)
            b["hids"].append(s["hid"])
            for key in ("pids", "central", "ideas", "links", "tables", "figures"):
                b[key] += s.get(key, [])
            b["chars"] += s["chars"]
            for kind, n in s["assets"].items():
                b["assets"][kind] = b["assets"].get(kind, 0) + n
        if len(out) > 1 and out[-1]["chars"] < Config.OUTLINE_BLOCK_MIN_CHARS:
            tail = out.pop()
            for key in ("sections", "rows", "hids", "pids", "central", "ideas", "links", "tables", "figures"):
                out[-1][key] += tail[key]
            out[-1]["chars"] += tail["chars"]
            for kind, n in tail["assets"].items():
                out[-1]["assets"][kind] = out[-1]["assets"].get(kind, 0) + n
        number = 0
        for n, b in enumerate(out):
            b["id"] = f"B{n + 1}"
            b["least"] = max(1, math.ceil(b["chars"] / OutlineAgent.slide_max_chars()))
            for row in b["rows"]:
                number += 1
                row["sid"] = f"S{number}"
        return out

    @staticmethod
    def mark_tree(blocks: List[dict], chapter, titles: Dict[int, str]) -> None:
        """Each section row's place in the document's outline: `under` (its parent heading's S#, or that heading's quoted
        title when it holds no text of its own), `kids` (its subheadings in the chapter) and `part_chars` (its text and
        all its subheadings')."""
        parent = getattr(chapter, "parent_of", None) or {}
        rows = [row for b in blocks for row in b.get("rows") or []]
        sid = {row["hid"]: row["sid"] for row in rows}
        for row in rows:
            up = parent.get(row["hid"])
            row["under"] = sid.get(up) or (f"\"{titles[up]}\"" if up in parent and titles.get(up) else None)
            row["kids"] = sum(1 for h in parent if parent[h] == row["hid"])
            row["part_chars"] = sum(r["chars"] for r in rows if OutlineAgent.within(r["hid"], row["hid"], parent))

    @staticmethod
    def within(h, top, parent: dict) -> bool:
        """Whether heading h is `top` or lies under it in the outline."""
        seen = set()
        while h is not None and h not in seen:
            if h == top:
                return True
            seen.add(h)
            h = parent.get(h)
        return False

    def dossier(self, query: SectionQuery, inventory: dict, b: dict) -> List[str]:
        """The block's lines: `B# · chars`, then each section `S# heading · chars · assets` with its tables, figures,
        every KG idea at home there (most mentioned first) and the ends of its edges at home elsewhere, then its edges
        (`R# K# → K# [keywords · weight]: description`, in reading order). A section stating no edge shows instead the
        sentences retrieval finds stating what it says (OUTLINE_GIST_SENTENCES, without ids)."""
        nodes, relations = inventory["nodes"], inventory["relations"]
        chapter = query.chapter
        lines = [f"{b['id']} · {b['chars'] / 1000:.1f}k chars"]
        for row in b["rows"]:
            assets = ", ".join(f"{n} {kind}" for kind, n in sorted(row["assets"].items()))
            tree = (f" · under {row['under']}" if row.get("under") else "") + \
                (f" · whole {row['part_chars'] / 1000:.1f}k chars" if row.get("kids") else "")
            lines.append(f"  {row['sid']} {row['title']} · {row['chars'] / 1000:.1f}k chars" + (f" · {assets}" if assets else "") + tree)
            lines += [f"    T: \"{t[:Config.OUTLINE_ASSET_CHARS]}\"" for t in row.get("tables") or []]
            lines += [f"    F: \"{f[:Config.OUTLINE_ASSET_CHARS]}\"" for f in row.get("figures") or [] if f]
            ideas = sorted(row["ideas"], key=lambda k: -nodes[k].get("mentions", 0))
            lines += [f"    {k} {nodes[k]['name']} ({nodes[k]['type']}) · {nodes[k].get('mentions', 0)}×" for k in ideas]
            lines += [f"    {k} {nodes[k]['name']} ({nodes[k]['type']}) · at home elsewhere" for k in row.get("ends") or []]
            for r in row.get("edges") or []:
                x = relations[r]
                tags = " · ".join(t for t in (x.get("keywords"), f"{x.get('weight', 0):g}") if t)
                lines.append(f"    {r} {x['kid_a']} → {x['kid_b']} [{tags}]: {_said(x['description'])}")
            if row.get("kids"):                          # its opening, so the model sees whether it only introduces them
                opening = [chapter.sentences[s]["text"] for p in row["pids"] if chapter.by_pid[p]["kind"] == "text"
                           for s in chapter.by_pid[p]["sentences"]][:Config.OUTLINE_GIST_SENTENCES]
                lines += [f"    \"{' '.join(t.split())[:Config.OUTLINE_GIST_CHARS]}\"" for t in opening]
            elif not row.get("edges"):
                names = [nodes[k]["name"] for k in (row["central"] or ideas[:Config.OUTLINE_GIST_SENTENCES])]
                for hit in query.gist([row["hid"]], " ".join([row["title"], *names]), names, Config.OUTLINE_GIST_SENTENCES):
                    lines.append(f"    \"{' '.join(hit['text'].split())[:Config.OUTLINE_GIST_CHARS]}\"")
        return lines

    # ── plan ───────────────────────────────────────────────────────────────────
    def plan(self, chapter, inventory: dict, titles: Dict[int, str]) -> dict:
        """{goals:[{gid, goal, questions, teaches, paragraphs}], slides:[{title, message, gid, goals, teaches, keyed,
        links, paragraphs}], left_kg:{K#: reason}, unplaced:{paragraphs, kg}, calls, parts}"""
        query = SectionQuery(chapter, inventory, titles, self.retrieval.get(chapter.id), self.kb_dir)
        blocks = self.blocks(query.sections())
        if not blocks:
            return self._finish(chapter, inventory, [], [], {}, {"paragraphs": [], "kg": []}, 0, 0)
        self.mark_tree(blocks, chapter, titles)
        nodes = inventory["nodes"]
        lines = [line for b in blocks for line in self.dossier(query, inventory, b)]
        payload = {"section": chapter.title, **({"language": chapter.language} if chapter.language else {}),
                   "blocks": lines, "slide_min_chars": Config.SECTION_SLIDE_MIN_CHARS, "slide_max_chars": self.slide_max_chars(),
                   "task": f"Write the goals every heading's edges teach, then the slides of all {len(blocks)} blocks "
                           f"(B1-B{len(blocks)}), as the output schema says."}
        answer = self._ask(self.prompt, payload, "outline", Config.OUTLINE_MAX_TOKENS) or {}
        first, calls = answer, 1
        relations = inventory["relations"]
        rows, goals, issues = self.read(answer, blocks, nodes, relations)
        if any(not self.code_settles(i) for i in issues):   # what code settles alone is not asked again
            answer = self._ask(self.prompt, {**payload, "issues": issues}, "outline_fix", Config.OUTLINE_MAX_TOKENS) or {}
            calls += 1
            again, goals_again, left_issues = self.read(answer, blocks, nodes, relations)
            if len(left_issues) < len(issues):
                rows, goals, issues = again, goals_again, left_issues
                first = answer
        parts = self.read_parts(first, blocks)
        rows = self.settle_rows(rows, blocks, nodes, titles, chapter)
        rows, goals = self.settle_edges(rows, goals, blocks, nodes, relations)
        agenda = self.agenda_sections(blocks, goals)
        sid_hid = {row["sid"]: row["hid"] for b in blocks for row in b.get("rows") or []}
        agenda_pids = [p for b in blocks for row in b.get("rows") or [] if row["sid"] in agenda for p in row["pids"]]
        blocks, rows = self.without(blocks, rows, agenda)
        slides, fitted = self.split_heavy(self.allocate(rows, blocks, chapter, nodes), rows, chapter, nodes)
        slides, joined = self.join_light(slides, chapter)
        fitted += joined
        for s_ in slides:
            s_.pop("block")
            s_.pop("row", None)
        left = {k: why for k, node in nodes.items() if (why := self.reason(node)) and not any(k in s["teaches"] for s in slides)}
        slides = self._settle(slides, left)
        for s_ in slides:                                # the outline's key ideas: final, the Packet Builder keeps them
            s_["keyed"] = list(s_["teaches"])
        slides = self.place(slides, nodes, left)
        goals = self.place_goals(goals, slides, rows, chapter, nodes, relations)
        unplaced = self._unsettled(slides, [p for b in blocks for p in b["pids"]], nodes, left)
        openers = self.openers(chapter, titles, slides, goals, {sid_hid[x]: t for x, t in parts.items() if x in sid_hid},
                               {sid_hid[x] for x in agenda}, sid_hid)
        print(f"[Outline] {chapter.title}: {len(blocks)} block(s) -> {len(slides)} slide(s), {len(goals)} goal(s) in "
              f"{calls} call(s); {len(agenda)} heading(s) only introducing their subheadings, {len(openers)} opener(s)"
              f"{'; settled by code: ' + '; '.join(issues[:3]) if issues else ''}"
              f"{'; slides fitted (split over what one slide holds, joined when too light): ' + '; '.join(fitted[:4]) if fitted else ''}")
        return self._finish(chapter, inventory, goals, slides, left, unplaced, calls, len(blocks), openers, agenda_pids)

    # ── checking the answer ────────────────────────────────────────────────────
    @staticmethod
    def read(answer: dict, blocks: List[dict], nodes: dict, relations: dict) -> Tuple[List[dict], List[dict], List[str]]:
        """The answer's goals and slides, and what is wrong with them. A goal holds the headings it is about, the edges
        of those headings it teaches (each edge in one goal) and the ideas it explains (its edges' ends always among
        them); a slide is a row {block, title, goals (indices), goal (the first), key (its goals' ideas, each idea on one
        slide), message (its goals and their edges' descriptions), sections}."""
        by_id = {b["id"]: b for b in blocks}
        order = {b["id"]: n for n, b in enumerate(blocks)}
        section = {row["sid"]: row for b in blocks for row in b.get("rows") or []}
        goals, issues, owner, named_goal = [], [], {}, {}
        for g in answer.get("goals") or []:
            if not isinstance(g, dict) or len(_text(g.get("goal"))) < 5 or g.get("whole"):
                continue                                  # a part's goal introduces its subheadings: `read_parts`
            secs = [x for x in dict.fromkeys(_text(x) for x in g.get("sections") or []) if x in section]
            stated = {r: 1 for x in (secs or section) for r in section[x].get("edges") or []}
            edges = [r for r in _ids(g.get("edges"), stated, "R") if r not in owner]
            ends = [k for r in edges for k in (relations[r]["kid_a"], relations[r]["kid_b"])]
            allowed = {k: 1 for x in (secs or section) for k in section[x]["ideas"] + (section[x].get("ends") or [])}
            key = list(dict.fromkeys(_ids(g.get("key"), allowed, "K") + ends))
            n = len(goals)
            owner.update({r: n for r in edges})
            named_goal[_text(g.get("id")) or f"G{n + 1}"] = n
            text = _text(g.get("goal"))
            nameless = [r for r in edges if not any(support_ratio(nodes[k]["name"], text) >= Config.CITE_SUPPORT_MIN
                                                    for k in (relations[r]["kid_a"], relations[r]["kid_b"]))]
            if nameless:
                issues.append(f"goal '{text}' names no end of {', '.join(nameless)}: say what those edges state")
            copied = [r for r in edges if support_ratio(text, _said(relations[r]["description"])) >= Config.CITE_SUPPORT_MIN]
            if copied:
                issues.append(f"goal '{text}' restates edge {copied[0]}: state the purpose of teaching its edges")
            goals.append({"goal": text, "sections": secs, "edges": edges, "key": key,
                          "questions": [_said(relations[r]["description"]) for r in edges]})
        rows, seen, placed = [], set(), Counter()
        block_of = {row["sid"]: b["id"] for b in blocks for row in b.get("rows") or []}
        for row in answer.get("slides") or []:
            if not isinstance(row, dict):
                continue
            title = _text(row.get("title"))
            if len(title) < 2:
                continue
            mine = list(dict.fromkeys(named_goal[x] for x in (_text(v) for v in row.get("goals") or []) if x in named_goal))
            # the slide's block is where its headings are (its own, else its goals'): a mislabelled block id is ignored
            heads = [str(x) for x in row.get("sections") or [] if str(x) in block_of] or \
                [x for n in mine for x in goals[n]["sections"] if x in block_of]
            label = block_of[heads[0]] if heads else str(row.get("block"))
            if label not in by_id:
                continue
            b = by_id[label]
            placed.update(mine)
            key = [k for n in mine for k in goals[n]["key"] if k in nodes and k not in seen]
            key = list(dict.fromkeys(key))
            seen.update(key)
            own = [r["sid"] for r in b.get("rows") or []]
            named = [str(x) for x in row.get("sections") or [] if str(x) in own] or (own if len(own) == 1 else [])
            at = [own.index(x) for x in named]
            if mine and (not named or at != list(range(at[0], at[0] + len(at)))):
                held = {x for n in mine for x in goals[n]["sections"]}      # the headings of the goals it holds
                named = [x for x in own if x in held] or named
            rows.append({"block": b["id"], "title": title, "goals": mine, "goal": mine[0] if mine else None, "key": key,
                         "message": OutlineAgent.message([goals[n] for n in mine], relations), "sections": named})
        rows.sort(key=lambda r: order[r["block"]])                 # stable: the model's order within a block
        covered = {x for g in goals for x in g["sections"]}
        bare = [x for x in section if x not in covered and not section[x].get("kids")]   # a part may only introduce
        if bare:
            issues.append(f"{', '.join(bare)} {'has' if len(bare) == 1 else 'have'} no goal: every heading has the goals its edges teach")
        loose = {x: [r for r in row.get("edges") or [] if r not in owner] for x, row in section.items()}
        for x, rs in loose.items():
            if rs:
                issues.append(f"{x}: edges {', '.join(rs)} are in no goal")
        explained = {k for g in goals for k in g["key"]}
        for x, row in section.items():
            missing = [k for k in row["central"] if k not in explained]
            if missing:
                issues.append(f"{x}: ideas {', '.join(missing)} are in no goal's `key`")
        unplaced = [g["goal"] for n, g in enumerate(goals) if n not in placed]
        if unplaced:
            issues.append(f"goal(s) {'; '.join(unplaced)} on no slide")
        most = OutlineAgent.slide_max_chars()
        for b in blocks:
            mine = [r for r in rows if r["block"] == b["id"]]
            problem = OutlineAgent.section_problem(b, mine)
            if problem:
                issues.append(f"{b['id']}: {problem}")
                continue
            chars = {row["sid"]: row["chars"] for row in b.get("rows") or []}
            named = Counter(x for r in mine for x in r["sections"])
            for r in mine:                                # a slide whose sections are its own alone: their size is its
                if r["sections"] and all(named[x] == 1 for x in r["sections"]):
                    size = sum(chars.get(x, 0) for x in r["sections"])
                    if size > most:
                        issues.append(f"slide '{r['title']}' holds {size} chars, over slide_max_chars: one slide per "
                                      f"subject of its sections")
        return rows, goals, issues

    @staticmethod
    def message(goals: List[dict], relations: dict) -> str:
        """What a slide teaches, for the Writer: its goals' sentences. The edges' descriptions stay with the Outline:
        the extractor's own summaries, they read as overviews and the Writer made bullets of them that the slide's
        passages do not state; the Writer writes from the passages."""
        return "; ".join(g["goal"] for g in goals if g["goal"])

    @staticmethod
    def section_problem(block: dict, mine: List[dict]) -> Optional[str]:
        """What is wrong with the sections a block's slides name (each on one slide, in order; a long one may run from
        the end of a slide to the start of the next); None when they cover the block."""
        own = [r["sid"] for r in block.get("rows") or []]
        if not mine or not own:
            return None
        at = {sid: n for n, sid in enumerate(own)}
        seen: List[int] = []
        for r in mine:
            named = [at[x] for x in r.get("sections") or [] if x in at]
            if not named:
                return f"slide '{r['title']}' names none of its headings ({', '.join(own)}) in `sections`"
            if named != list(range(named[0], named[-1] + 1)):
                return f"slide '{r['title']}' must cover consecutive headings"
            if seen and named[0] not in (seen[-1], seen[-1] + 1):
                return f"slide '{r['title']}' does not continue from the heading before it"
            seen += named
        missing = [sid for n, sid in enumerate(own) if n not in seen]
        return f"{', '.join(missing)} on no slide" if missing else None

    @staticmethod
    def code_settles(issue: str) -> bool:
        """An issue code settles without asking again: every one but headings left without a goal (what they teach
        would be missing). `settle_edges` places left edges, splits goals of unrelated edges and puts goals on slides;
        `allocate` gives paragraphs from the key ideas when sections are named badly; `split_heavy` cuts what one slide
        cannot hold."""
        return not _ASKED.match(issue)

    @staticmethod
    def read_parts(answer: dict, blocks: List[dict]) -> Dict[str, str]:
        """{S#: the goal of its whole part} for each heading with subheadings the answer gives a part goal."""
        kids = {row["sid"] for b in blocks for row in b.get("rows") or [] if row.get("kids")}
        out: Dict[str, str] = {}
        for g in answer.get("goals") or []:
            if isinstance(g, dict) and g.get("whole") and len(_text(g.get("goal"))) >= 5:
                sid = next((x for x in (_text(v) for v in g.get("sections") or []) if x in kids), None)
                if sid:
                    out.setdefault(sid, _text(g["goal"]))
        return out

    @staticmethod
    def introduces(row: dict) -> bool:
        """A heading whose own text may only introduce its subheadings: it has some, and its text states no edge and
        holds no table, figure, formula or listing (those are content whatever the model says)."""
        return bool(row.get("kids")) and not row.get("edges") and not row["assets"] and not row.get("figures")

    @staticmethod
    def agenda_sections(blocks: List[dict], goals: List[dict]) -> List[str]:
        """The S# whose own text only introduces their subheadings: such a heading no goal is about alone (a goal naming
        it beside a subheading is that subheading's)."""
        return [row["sid"] for b in blocks for row in b.get("rows") or []
                if OutlineAgent.introduces(row) and not any(g["sections"] == [row["sid"]] for g in goals)]

    @staticmethod
    def without(blocks: List[dict], rows: List[dict], agenda: List[str]) -> Tuple[List[dict], List[dict]]:
        """The blocks and slide rows with the introducing headings taken out (their text opens their part instead)."""
        if not agenda:
            return blocks, rows
        out = []
        for b in blocks:
            kept = [r for r in b.get("rows") or [] if r["sid"] not in agenda]
            if kept:
                out.append({**b, "rows": kept, "pids": [p for r in kept for p in r["pids"]]})
        live = {b["id"] for b in out}
        for r in rows:
            r["sections"] = [x for x in r["sections"] if x not in agenda]
        return out, [r for r in rows if r["block"] in live]

    @staticmethod
    def openers(chapter, titles: Dict[int, str], slides: List[dict], goals: List[dict], parts: Dict[int, str],
                agenda: set, sid_hid: Dict[str, int]) -> Dict[int, dict]:
        """{heading id: {title, goal, agenda, before}}: the chapter's own opener, and one for every heading that only
        introduces its subheadings (or has no text of its own) whose part fills at least two slides. Its agenda is its
        subheadings' goals in order: a subheading with subheadings of its own by its part goal, any other by its goals
        (by its title when code wrote them from its ideas), each with the slides that teach it; `before` is the first
        slide of the part."""
        parent = getattr(chapter, "parent_of", None) or {}
        kids: Dict[int, List[int]] = {}
        for h in chapter.heading_ids:
            kids.setdefault(parent.get(h), []).append(h)
        rows_of = {sid_hid[x] for x in sid_hid}
        heads = lambda s: {chapter.by_pid[p]["heading"] for p in s["paragraphs"]}
        part = lambda top: [s for s in slides if any(OutlineAgent.within(h, top, parent) for h in heads(s))]
        about = [{sid_hid[x] for x in g.get("sections") or [] if x in sid_hid} for g in goals]
        out: Dict[int, dict] = {}
        for top in chapter.heading_ids:
            mine = part(top)
            root = top == chapter.id
            if not kids.get(top) or not mine or (not root and (top in rows_of and top not in agenda or len(mine) < 2)):
                continue
            items, seen = [], set()
            def add(text, where):
                if text and where and text not in seen:
                    seen.add(text)
                    items.append({"text": text, "slides": where})
            for n, g in enumerate(goals):                # what the heading's own text teaches comes first
                if top in about[n] and not about[n] - {top} and not g.get("made"):
                    add(g["goal"], [slides[i] for i in g["slides"]])
            for c in kids[top]:
                if kids.get(c):
                    add(parts.get(c) or titles.get(c), part(c))
                    continue
                held = [g for n, g in enumerate(goals) if c in about[n]]
                for g in held:
                    if not g.get("made"):
                        add(g["goal"], [slides[i] for i in g["slides"]])
                if held and all(g.get("made") for g in held):   # a goal code wrote from its ideas: the heading names it
                    add(titles.get(c), [slides[i] for g in held for i in g["slides"]])
            out[top] = {"title": titles.get(top) or chapter.title, "goal": parts.get(top) or titles.get(top) or chapter.title,
                        "agenda": items, "before": mine[0]}
        return out

    @staticmethod
    def slide_max_chars() -> int:
        """Source characters one slide presents: the bullets the Writer writes on a slide (WRITER_SLIDE_BULLETS) times
        BULLET_SOURCE_CHARS_LOOSE, the source it gives one bullet; a subject longer than that is split."""
        return Config.WRITER_SLIDE_BULLETS * Config.BULLET_SOURCE_CHARS_LOOSE

    @staticmethod
    def settle_rows(rows: List[dict], blocks: List[dict], nodes: dict, titles: Dict[int, str], chapter) -> List[dict]:
        """A block the answer left without a slide gets one: its sections' title and its central ideas (its goals are
        settled by `settle_edges`)."""
        have = {r["block"] for r in rows}
        taken = {k for r in rows for k in r["key"]}
        for b in blocks:
            if b["id"] not in have:
                rows.append({"block": b["id"], "title": b["sections"][0] or chapter.title, "message": "",
                             "key": [k for k in (b["central"] or b["ideas"]) if k not in taken], "goal": None,
                             "sections": [r["sid"] for r in b.get("rows") or []], "goals": []})
        order = {b["id"]: n for n, b in enumerate(blocks)}
        rows.sort(key=lambda r: order[r["block"]])
        return rows

    @staticmethod
    def in_reading_order(rows: List[dict], position: Dict[str, int]) -> List[dict]:
        """The slides in the order of their first heading in the document (a slide naming none stays after the one
        before it; slides of one heading keep the answer's order)."""
        keys, last = [], -1
        for r in rows:
            last = min((position[x] for x in r["sections"] if x in position), default=last)
            keys.append(last)
        return [r for _, r in sorted(zip(keys, rows), key=lambda t: t[0])]

    @staticmethod
    def settle_edges(rows: List[dict], goals: List[dict], blocks: List[dict], nodes: dict,
                     relations: dict) -> Tuple[List[dict], List[dict]]:
        """The goals made whole without inventing. An edge in no goal joins a goal of its heading holding one of its
        ends, else the last goal of its heading; a heading with no goal gets one naming its heading and ideas; a goal on no slide joins the last slide of its headings (a new one when none); a slide with no
        goal takes its headings' goals. Goals end in the order their slides come; each slide's key and message follow."""
        heading = {row["sid"]: (b["id"], row) for b in blocks for row in b.get("rows") or []}
        owner = {r: n for n, g in enumerate(goals) for r in g["edges"]}
        for sid, (_, row) in heading.items():
            for r in row.get("edges") or []:
                if r in owner:
                    continue
                ends = {relations[r]["kid_a"], relations[r]["kid_b"]}
                mine = [n for n, g in enumerate(goals) if sid in g["sections"]]
                n = next((n for n in mine if ends & set(goals[n]["key"])), mine[-1] if mine else None)
                if n is None:
                    ideas = sorted(row["ideas"], key=lambda k: -nodes[k].get("mentions", 0))
                    goals.append({"goal": row["title"] + (": " + ", ".join(nodes[k]["name"] for k in ideas) if ideas else ""),
                                  "sections": [sid], "edges": [], "key": ideas, "questions": [], "made": True})
                    n = len(goals) - 1
                goals[n]["edges"].append(r)
                goals[n]["key"] += [k for k in (relations[r]["kid_a"], relations[r]["kid_b"]) if k not in goals[n]["key"]]
                goals[n]["questions"].append(_said(relations[r]["description"]))
                owner[r] = n
            if not any(sid in g["sections"] for g in goals) and not OutlineAgent.introduces(row):
                ideas = sorted(row["ideas"], key=lambda k: -nodes[k].get("mentions", 0))
                text = row["title"] + (": " + ", ".join(nodes[k]["name"] for k in ideas) if ideas else "")
                goals.append({"goal": text, "sections": [sid], "edges": [], "key": ideas, "questions": [], "made": True})
        placed = {n for r in rows for n in r["goals"]}
        for n, g in enumerate(goals):
            if n in placed:
                continue
            host = next((r for r in reversed(rows) if set(r["sections"]) & set(g["sections"])), None)
            if host is None:
                first = next((x for x in g["sections"] if x in heading), None)
                if first is None:
                    continue
                host = {"block": heading[first][0], "title": heading[first][1]["title"], "goals": [], "goal": None,
                        "key": [], "message": "", "sections": [first]}
                rows.append(host)
            host["goals"].append(n)
        position = {sid: n for n, sid in enumerate(heading)}         # headings in reading order
        rows = OutlineAgent.in_reading_order(rows, position)
        for r in rows:
            if not r["goals"]:
                r["goals"] = [n for n, g in enumerate(goals) if set(r["sections"]) & set(g["sections"])][:1]
        first_row = {}
        for i, r in enumerate(rows):
            for n in r["goals"]:
                first_row.setdefault(n, i)
        kept = sorted(first_row, key=first_row.get)        # goals in the order of their slides
        new = {old: n for n, old in enumerate(kept)}
        goals = [goals[old] for old in kept]
        seen = set()
        for r in rows:
            r["goals"] = list(dict.fromkeys(new[n] for n in r["goals"] if n in new))
            r["goal"] = r["goals"][0] if r["goals"] else None
            r["key"] = list(dict.fromkeys(k for k in r["key"] + [k for n in r["goals"] for k in goals[n]["key"]]
                                          if k in nodes and k not in seen))
            seen.update(r["key"])
            r["message"] = OutlineAgent.message([goals[n] for n in r["goals"]], relations)
        return rows, goals

    @staticmethod
    def place_goals(goals: List[dict], slides: List[dict], rows: List[dict], chapter, nodes: dict,
                    relations: dict) -> List[dict]:
        """Each goal on the slides holding what it teaches: the paragraphs stating its edges (for a goal without edges,
        the slides teaching its ideas), else the slides its row was planned on; each slide's message is the sentences of
        the goals it opens: a slide continuing a goal (`continues`) gets none, so it writes what its own passages add
        instead of introducing the goal's subject again. Goals in the order of their first slide."""
        index = {id(s): n for n, s in enumerate(slides)}
        at = {p: n for n, s in enumerate(slides) for p in s["paragraphs"]}
        stated = lambda r: {at[chapter.pid_of[s]] for s in relations[r]["sentences"]
                            if chapter.pid_of.get(s) in at}
        out = []
        for n, g in enumerate(goals):
            where = {i for r in g["edges"] for i in stated(r)}
            if not where:
                where = {i for i, s in enumerate(slides) if set(g["key"]) & set(s["teaches"])}
            if not where:
                where = {index[id(r["obj"])] for r in rows if n in r["goals"] and id(r.get("obj")) in index}
            if where:
                out.append({**g, "slides": sorted(where)})
        out.sort(key=lambda g: g["slides"][0])
        for i, s in enumerate(slides):
            held = [g for g in out if i in g["slides"]]
            s["continues"] = bool(held) and all(g["slides"][0] != i for g in held)
            # a goal's sentence opens only its first slide: a slide continuing it is told nothing but its own passages
            mine = [{**g, "goal": g["goal"] if g["slides"][0] == i else ""} for g in held]
            s["message"] = OutlineAgent.message(mine, relations) or ("" if s["continues"] else s.get("message", ""))
        return [{"goal": g["goal"], "questions": g["questions"], "slides": g["slides"], "sections": g.get("sections", []),
                 "made": g.get("made", False)} for g in out]

    # ── paragraphs: each slide's consecutive share of its block ────────────────
    @staticmethod
    def home_paragraphs(node: dict, at: Dict[str, int], chapter) -> List[int]:
        """Positions (in `at`) of the paragraphs stating the idea within the section that states it most: an
        overview or a summary that names every idea in passing does not pull the idea's slide forward."""
        pids = [chapter.pid_of[s] for s in node["sentences"] if chapter.pid_of.get(s) in at]
        heads = Counter(chapter.by_pid[p]["heading"] for p in pids)
        if not heads:
            return []
        first = {h: min(at[p] for p in pids if chapter.by_pid[p]["heading"] == h) for h in heads}
        home = max(heads, key=lambda h: (heads[h], -first[h]))
        return [at[p] for p in pids if chapter.by_pid[p]["heading"] == home]

    @staticmethod
    def snap_to_sections(starts: List[Optional[int]], pids: List[str], chapter) -> None:
        """A slide starting inside a section starts at that section's first paragraph when no earlier slide starts in
        it, so no slide ends on the opening of a section whose rest is on the next slide."""
        heading = [chapter.by_pid[p]["heading"] for p in pids]
        last = None
        for n, start in enumerate(starts):
            if start is None:
                continue
            first = start
            while first > 0 and heading[first - 1] == heading[start]:
                first -= 1
            if n and first > (last if last is not None else -1):
                starts[n] = first
            last = starts[n]

    @staticmethod
    def allocate(rows: List[dict], blocks: List[dict], chapter, nodes: dict) -> List[dict]:
        """Within a block, a slide starts at the first paragraph of the first section it names. A slide continuing the
        section before it, or any slide when the answer's sections are unusable, starts at the first paragraph stating
        one of its key ideas in the section that states it most (after the previous slide's start); a slide whose ideas
        give no such start shares the gap by characters. A slide left with no paragraph joins the slide before it (its
        title is logged)."""
        size = lambda p: max(1, chapter.size(p))
        slides: List[dict] = []
        for b in blocks:
            mine = [r for r in rows if r["block"] == b["id"]]
            pids = b["pids"]
            if not mine or not pids:
                continue
            at = {p: n for n, p in enumerate(pids)}
            starts: List[Optional[int]] = [0]
            rows_of = b.get("rows") or []
            by_sections = not OutlineAgent.section_problem(b, mine) and len(rows_of) > 1
            opening = {row["sid"]: at[row["pids"][0]] for row in rows_of if row["pids"]}
            for m, r in zip(mine, mine[1:]):
                first = r["sections"][0] if by_sections else None
                if first and first != m["sections"][-1]:
                    starts.append(opening[first])
                    continue
                # the first paragraph stating a key idea, after the last start and before the section ends
                following = [v for v in opening.values() if v > (opening.get(first, -1) if first else len(pids))]
                limit = min(following, default=len(pids))
                firsts = [f for k in r["key"] for f in OutlineAgent.home_paragraphs(nodes[k], at, chapter)]
                last = max(s for s in starts if s is not None)
                starts.append(min((f for f in firsts if last < f < limit), default=None))
            known = [n for n, s in enumerate(starts) if s is not None]
            for a, z in zip(known, known[1:] + [len(starts)]):   # a run without starts shares its gap by characters
                gap = list(range(a + 1, z))
                if not gap:
                    continue
                lo, hi = starts[a], (starts[z] if z < len(starts) else len(pids))
                weights = [size(p) for p in pids[lo:hi]]
                share, run, cut, prev = sum(weights) / (len(gap) + 1), 0, lo, lo
                for j, n in enumerate(gap, 1):
                    while cut < hi and run < share * j:
                        run += weights[cut - lo]
                        cut += 1
                    if prev < cut < hi:
                        starts[n], prev = cut, cut
            OutlineAgent.snap_to_sections(starts, pids, chapter)
            kept = [n for n, s in enumerate(starts) if s is not None]
            for n, r in enumerate(mine):
                if starts[n] is None:                 # no paragraph of its own: it joins the slide before
                    host = mine[max(k for k in kept if k < n)]["slide"]
                    print(f"[outline] '{r['title']}' got no paragraph of its own: joined to '{host['title']}'")
                    host["teaches"] += [k for k in r["key"] if k not in host["teaches"]]
                    r["joined"] = host
                    continue
                end = next((starts[k] for k in kept if k > n), len(pids))
                slides.append({"block": b["id"], "title": r["title"], "message": r["message"],
                               "teaches": list(r["key"]), "paragraphs": pids[starts[n]:end], "row": r})
                r["slide"] = slides[-1]
        for r in rows:                                   # each goal row points at the slide that holds it
            r["obj"] = r.get("slide") or r.get("joined")
        return slides

    # ── a slide over what one slide holds ──────────────────────────────────────
    @staticmethod
    def split_heavy(slides: List[dict], rows: List[dict], chapter, nodes: dict):
        """A slide whose paragraphs hold more than slide_max_chars is split (at the heading boundary nearest its middle,
        else at the paragraph nearest it; the new slide is titled by its heading) until none does. Returns the slides
        and what was split."""
        size = lambda s: sum(chapter.size(p) for p in s["paragraphs"])
        heading = lambda p: chapter.by_pid[p]["heading"]
        most, out, split = OutlineAgent.slide_max_chars(), [], []
        todo = list(slides)
        while todo:
            big = todo.pop(0)
            paras = big["paragraphs"]
            if size(big) <= most or len(paras) < 2:
                out.append(big)
                continue
            half, run, cuts = size(big) / 2, 0, []
            for i in range(1, len(paras)):
                run += chapter.size(paras[i - 1])
                cuts.append((heading(paras[i]) == heading(paras[i - 1]), abs(run - half), i))
            cut = min(cuts)[2]                           # a heading boundary first, then the one nearest the middle
            second = paras[cut:]
            moved = [k for k in big["teaches"]
                     if next((p for p in nodes.get(k, {}).get("pids") or [] if p in paras), None) in second]
            new = {"block": big["block"], "title": chapter.heading_titles.get(heading(second[0])) or big["title"],
                   "message": "", "teaches": moved, "paragraphs": second}
            big["paragraphs"], big["teaches"] = paras[:cut], [k for k in big["teaches"] if k not in moved]
            split.append(f"split '{big['title']}'")
            todo[:0] = [big, new]
        return out, split

    @staticmethod
    def join_light(slides: List[dict], chapter):
        """A text slide whose paragraphs cannot fill a slide (under SECTION_SLIDE_MIN_CHARS: fewer than QA_THIN_BULLETS
        bullets) joins a neighbour, when together they stay within what one slide holds, so its content is taught there
        instead of being dropped as thin; a slide drawing a table, formula, listing or figure stays. It joins, in order:
        (the one before it first) a neighbour of its subject (a goal in common, the Outline's own judgement, or the headings where they meet linked
        in the document's outline below the chapter's top headings: one the other's parent, or both under one such
        heading); else a short neighbour, the two pooling until they fill a slide; else a full neighbour that has not yet
        taken a part of another subject: one such part a slide, so no slide becomes a chain of unrelated
        headings. Returns the slides and what was joined."""
        text = lambda s: sum(chapter.size(p) for p in s["paragraphs"] if chapter.by_pid[p]["kind"] == "text")
        drawn = lambda s: any(chapter.by_pid[p]["kind"] != "text" for p in s["paragraphs"])
        light = lambda s: text(s) < Config.SECTION_SLIDE_MIN_CHARS and not drawn(s)
        parent = getattr(chapter, "parent_of", None) or {}
        tops = {h for h, up in parent.items() if up not in parent}
        head = lambda s, at: chapter.by_pid[s["paragraphs"][at]].get("heading")
        under = lambda x, up: parent.get(x) == up and up in parent and up not in tops
        linked = lambda x, y: x == y or under(x, y) or under(y, x) or under(x, parent.get(y))
        goals = lambda s: set((s.get("row") or {}).get("goals") or [])
        def one_subject(first, second):
            return bool(goals(first) & goals(second)) or linked(head(first, -1), head(second, 0))
        out, joined, mixed = list(slides), [], set()     # mixed: slides already holding a part of another subject
        n = 0
        while n < len(out):
            s = out[n]
            most = OutlineAgent.slide_max_chars()          # never past what one slide holds
            def rank(m):
                if one_subject(*((out[m], s) if m < n else (s, out[m]))):
                    return 0
                return 1 if light(out[m]) else (2 if id(out[m]) not in mixed else None)
            mates = sorted((r, m > n, m) for m in (n - 1, n + 1)
                           if 0 <= m < len(out) and text(out[m]) + text(s) <= most and (r := rank(m)) is not None)
            if not light(s) or not mates:
                n += 1
                continue
            order, _, m = mates[0]
            host = out[m]
            first, second = (host, s) if m < n else (s, host)
            joined.append(f"joined '{second['title']}' to '{first['title']}'")
            host["paragraphs"] = first["paragraphs"] + second["paragraphs"]
            host["title"] = first["title"]                 # the slide that opens the subject names it
            host["teaches"] = list(dict.fromkeys(first["teaches"] + second["teaches"]))
            if order or id(s) in mixed:
                mixed.add(id(host))
            out.pop(n)
            n = max(0, n - 1)
        return out, joined

    # ── bookkeeping of the planned slides ─────────────────────────────────────
    def _ask(self, system: str, payload: dict, role: str, max_tokens: int) -> Optional[dict]:
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        for _ in range(2):
            try:
                answer = cached_json(f"{_CACHE}.{digest(system)[:10]}", self.model, payload,
                                     lambda: parse_json_reply(chat(self.model, messages, temperature=0,
                                                                   max_tokens=max_tokens, role=role, json_mode=True)))
            except (ValueError, RuntimeError) as exc:
                print(f"[Outline] {role}: unusable answer ({str(exc)[:100]})")
                continue
            if isinstance(answer, dict):
                return answer
        return None

    @classmethod
    def _unsettled(cls, slides, pids, nodes, left) -> dict:
        issues = cls.problems(slides, pids, nodes, left)
        return {"paragraphs": issues["paragraphs"], "kg": issues["kg"]}

    @staticmethod
    def reason(node: dict) -> Optional[str]:
        """Why a node the model leaves out is not taught here, from the KG: another section names it most (later or
        earlier), or this chapter names it in at most LEAVE_INCIDENTAL_SPAN paragraphs; None when this chapter teaches it."""
        home = node.get("home")
        if home:
            return "forward_reference" if home["side"] == "later" else "defined_elsewhere"
        return "incidental" if node.get("span", 0) <= Config.LEAVE_INCIDENTAL_SPAN else None

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
    def _settle(slides: List[dict], left: Dict[str, str]) -> List[dict]:
        """A node still on two slides stays on the first the model put it on; a left node is taught nowhere."""
        seen_p, seen_k = set(), set()
        for s in slides:
            s["paragraphs"] = [p for p in s["paragraphs"] if p not in seen_p]
            s["teaches"] = [k for k in s["teaches"] if k not in seen_k and k not in left]
            seen_p.update(s["paragraphs"])
            seen_k.update(s["teaches"])
        return [s for s in slides if s["paragraphs"]]

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
                unplaced: dict, calls: int, parts: int, openers: Optional[Dict[int, dict]] = None,
                agenda: Optional[List[str]] = None) -> dict:
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
            print(f"[Outline] {chapter.title}: unplaced {len(unplaced['paragraphs'])} paragraph(s), "
                  f"{len(unplaced['kg'])} node(s)")
        return {"goals": goals, "slides": slides, "left_kg": left, "unplaced": unplaced, "calls": calls,
                "parts": parts, "openers": openers or {}, "agenda": agenda or []}

