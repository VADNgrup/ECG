"""Agent 2, Packet Builder: decides what each planned slide carries besides its main ideas, then assembles it.

The Outline decided each slide's main ideas (`keyed`): they are key and never decided again. One LLM call per chapter
reads every planned slide (its title, message, main ideas, other KG nodes with their `mentions`, the asset it shows and
its formula or code candidates) and decides, per slide: `key` among its other nodes (each key node gets an anchor packet
and a bullet; at most as many as the bullets its source supports, the ones its paragraphs state most are kept; the
others are only `named`), and `draw`: which code listing the slide shows (optionally `lines` of it). Formulas are
not decided here: the Writer, who reads the passages, draws those its bullets explain. Every paragraph of the slide reaches the Writer. Code checks the decision (ids) and
`packet_assembly.PacketAssembler` assembles the packets. The deck's structure is not changed: every planned slide stays
one slide.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Dict, List, Optional

from src.utils.config import Config
from src.utils.content_cache import cached_json, digest
from src.utils.llm import chat, parse_json_reply
from src.workflow.prompts import load

_DEFAULT_LAYOUT = {"text": "nested_bullets_layout", "image": "image_right_layout", "formula": "formula_top_layout",
                   "table": "table_above_layout"}



def most_bullets(built: dict) -> int:
    """How many bullets a slide's whole source supports at most: one per BULLET_SOURCE_CHARS, at least QA_THIN_BULLETS."""
    size = built.get("size") or {}
    return max(Config.QA_THIN_BULLETS, round(size.get("source_chars", size.get("pool_chars", 0)) / Config.BULLET_SOURCE_CHARS))


def asset_kind(assets: dict) -> str:
    return ("formula" if assets.get("formula") else "table" if assets.get("table")
            else "image" if assets.get("image") else "code" if assets.get("code") else "text")


class PacketBuilderAgent:
    def __init__(self, model: str, domain: str = "general"):
        self.model = model
        self.prompt = load("packet_builder", domain)

    def build(self, chapter, inventory: dict, slides: List[dict], fill: Callable[[], None]) -> Dict[int, dict]:
        """Assemble with every node a key (to learn each slide's assets), decide, assemble again with the decision."""
        fill()
        decisions = self.decide(chapter, inventory, slides)
        for n, slide in enumerate(slides):
            slide["key"], slide["draw"] = decisions[n]["key"], decisions[n]["draw"]
        fill()
        return decisions

    @staticmethod
    def candidates(built: dict) -> List[dict]:
        """The formula or code rows a slide may draw (the slide's asset kind decides which)."""
        kind = asset_kind(built["assets"])
        return list(built["assets"].get(kind) or [])[:Config.SHOWN_VISUALS] if kind in ("formula", "code") else []

    @staticmethod
    def candidate_line(n: int, row: dict, kind: str) -> str:
        if kind == "formula":
            return f"V{n}: {row['text'][:Config.SHOWN_FORMULA_CHARS]}"
        body = "\n".join(f"{k}| {ln}" for k, ln in enumerate(row["text"].splitlines()[:Config.SHOWN_CODE_LINES], 1))
        return f"V{n}:\n{body}"

    def decide(self, chapter, inventory: dict, slides: List[dict]) -> Dict[int, dict]:
        """{slide index: {key, draw}}, always valid for every slide (a failed call keeps the most stated nodes and the
        first candidate)."""
        nodes = inventory["nodes"]
        lines = []
        for n, s in enumerate(slides):
            keyed = s.get("keyed") or []
            main = "; ".join(nodes[k]["name"] for k in keyed if k in nodes)
            kg = "; ".join(f"{k} {nodes[k]['name']} ({nodes[k]['type']}, {nodes[k].get('mentions', 0)} mentions, first "
                           f"{(nodes[k].get('pids') or ['-'])[0]})" for k in s["teaches"] if k in nodes and k not in keyed)
            kind = asset_kind(s["built"]["assets"])
            line = (f"{n} | {s['title']} | {(s.get('message') or '')[:160]} | asset: {kind} | bullets: up to "
                    f"{most_bullets(s['built'])} | main: {main or '-'} | nodes: {kg or '-'}")
            listings = self.candidates(s["built"]) if kind == "code" else []
            lines.append("\n".join([line] + [self.candidate_line(i, row, kind) for i, row in enumerate(listings, 1)]))
        payload = {"section": chapter.title, "slides": lines,
                   "task": "Decide every slide's key concepts and drawn candidates as the output schema says."}
        messages = [{"role": "system", "content": self.prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        try:
            answer = cached_json(f"packet_builder_v2.{digest(self.prompt)[:10]}", self.model, payload,
                                 lambda: parse_json_reply(chat(self.model, messages, temperature=0, max_tokens=6000,
                                                               role="packet_builder", json_mode=True)))
        except (ValueError, RuntimeError) as exc:
            print(f"[packet_builder] {chapter.title}: no usable decision ({str(exc)[:100]}); keeping the most stated nodes")
            answer = {}
        return self.validate(answer, chapter, inventory, slides)

    @classmethod
    def validate(cls, answer: dict, chapter, inventory: dict, slides: List[dict]) -> Dict[int, dict]:
        """Known ids only. Key: the outline's main ideas, then of the other nodes those chosen (at least a few nodes in
        all, at most the bullets its source supports: those its own paragraphs state most). Draw: the chosen listing
        (with valid `lines`); with no answer for the slide, its first one."""
        nodes = inventory["nodes"]
        rows = {}
        for row in (answer.get("slides") if isinstance(answer, dict) else None) or []:
            try:
                rows[int(row.get("index"))] = row
            except (TypeError, ValueError, AttributeError):
                continue
        stated = lambda k: -nodes.get(k, {}).get("mentions", 0)
        out: Dict[int, dict] = {}
        for n, s in enumerate(slides):
            row = rows.get(n) or {}
            keyed = [k for k in s.get("keyed") or [] if k in s["teaches"]]
            others = [k for k in s["teaches"] if k not in keyed]
            chosen = {str(k).strip() for k in row.get("key") or []}
            picked = [k for k in others if k in chosen] if row else sorted(others, key=stated)
            floor = min(len(s["teaches"]), 3)                # a slide teaches at least a few of its own nodes
            if len(keyed) + len(picked) < floor:
                picked += [k for k in sorted(others, key=stated) if k not in picked][:floor - len(keyed) - len(picked)]
            cap = max(0, most_bullets(s.get("built") or {}) - len(keyed))
            if len(picked) > cap:                             # the rest are named: each key node needs its own bullet
                own = set(s["paragraphs"])
                here = lambda k: sum(chapter.pid_of.get(sid) in own for sid in nodes.get(k, {}).get("sentences", []))
                picked = sorted(picked, key=lambda k: (-here(k), stated(k)))[:cap]
            key = [k for k in s["teaches"] if k in set(keyed) | set(picked)]
            out[n] = {"key": key, "draw": cls.drawn(row, s.get("built") or {})}
        return out

    @classmethod
    def drawn(cls, row: dict, built: dict) -> Optional[dict]:
        """{sids, lines} of the listing the slide draws; None for a slide without one (formulas: the Writer's)."""
        cands = cls.candidates(built) if built.get("assets") and asset_kind(built["assets"]) == "code" else []
        if not cands:
            return None
        kind = asset_kind(built["assets"])
        if "draw" not in row:                                # no decision: the first candidate, as the source orders it
            return {"sids": [cands[0]["sid"]], "lines": None}
        ids = [str(x).strip().upper() for x in (row.get("draw") or [])]
        sids = [cands[int(x[1:]) - 1]["sid"] for x in dict.fromkeys(ids)
                if re.fullmatch(r"V\d+", x) and 1 <= int(x[1:]) <= len(cands)]
        sids = sids[:1]
        lines = None
        nums = [int(x) for x in re.findall(r"\d+", str(row.get("lines") or ""))][:2]
        if kind == "code" and sids and len(nums) == 2:
            total = len(next(c for c in cands if c["sid"] == sids[0])["text"].splitlines())
            lines = nums if 1 <= nums[0] <= nums[1] <= total and nums != [1, total] else None
        return {"sids": sids, "lines": lines}
