"""Agent 2, Packet Builder: decides what each planned slide's packets carry, then assembles them.

One LLM call per chapter reads every planned slide (its KG nodes with their `mentions`, its paragraphs and the asset
it shows) and decides, per slide:
- `key`: the nodes the slide must teach -- each gets an anchor packet and a bullet (layouts grow to hold them), at most
  as many as the bullets its source supports (the ones its paragraphs state most are kept);
- the other nodes of the slide are only `named` (no packet, no required bullet);
- `notes`: paragraphs of the slide meant only for the presenter (anecdotes, lead-ins, code walkthroughs).
Code checks the decision (ids, never a note that holds a key idea) and `packet_assembly.PacketAssembler`
assembles the packets. The deck's structure is not changed: every planned slide stays one slide.
"""
from __future__ import annotations

import json
from typing import Callable, Dict, List

from src.utils.config import Config
from src.utils.content_cache import cached_json, digest
from src.utils.llm import chat, parse_json_reply
from src.workflow.prompts import load

_OPENING = 110
_DEFAULT_LAYOUT = {"text": "nested_bullets_layout", "image": "image_right_layout", "formula": "formula_top_layout",
                   "table": "table_above_layout"}

_DECIDE = load("packet_builder")


def most_bullets(built: dict) -> int:
    """How many bullets a slide's whole source supports at most: one per 200 characters, at least 3."""
    size = built.get("size") or {}
    return max(3, round(size.get("source_chars", size.get("pool_chars", 0)) / 200))


def asset_kind(assets: dict) -> str:
    return ("formula" if assets.get("formula") else "table" if assets.get("table")
            else "image" if assets.get("image") else "code" if assets.get("code") else "text")


class PacketBuilderAgent:
    def __init__(self, model: str):
        self.model = model

    def build(self, chapter, inventory: dict, slides: List[dict], fill: Callable[[], None]) -> Dict[int, dict]:
        """Assemble with every node a key (to learn each slide's asset), decide, assemble again with the decision."""
        fill()
        decisions = self.decide(chapter, inventory, slides)
        for n, slide in enumerate(slides):
            slide["key"], slide["notes"] = decisions[n]["key"], decisions[n]["notes"]
        fill()
        return decisions

    def decide(self, chapter, inventory: dict, slides: List[dict]) -> Dict[int, dict]:
        """{slide index: {key, notes}}, always valid for every slide (a failed call keeps the most stated nodes)."""
        nodes = inventory["nodes"]
        lines = []
        for n, s in enumerate(slides):
            kg = "; ".join(f"{k} {nodes[k]['name']} ({nodes[k]['type']}, {nodes[k].get('mentions', 0)} mentions, first "
                           f"{(nodes[k].get('pids') or ['-'])[0]})" for k in s["teaches"] if k in nodes)
            paras = "; ".join(f"{p} {chapter.text_of(p, _OPENING)}" for p in self._text_paragraphs(chapter, s))
            lines.append(f"{n} | {s['title']} | {(s.get('message') or '')[:160]} | asset: "
                         f"{asset_kind(s['built']['assets'])} | bullets: up to {most_bullets(s['built'])} | "
                         f"nodes: {kg or '-'} | paragraphs: {paras or '-'}")
        payload = {"section": chapter.title, "slides": lines,
                   "task": "Decide key concepts and notes for every slide as the output schema says."}
        messages = [{"role": "system", "content": _DECIDE},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        try:
            answer = cached_json(f"packet_builder_v1.{digest(_DECIDE)[:10]}", self.model, payload,
                                 lambda: parse_json_reply(chat(self.model, messages, temperature=0, max_tokens=6000,
                                                               role="packet_builder", json_mode=True)))
        except (ValueError, RuntimeError) as exc:
            print(f"[packet_builder] {chapter.title}: no usable decision ({str(exc)[:100]}); keeping the most stated nodes")
            answer = {}
        return self.validate(answer, chapter, inventory, slides)

    @staticmethod
    def _text_paragraphs(chapter, slide: dict) -> List[str]:
        return [p for p in slide["paragraphs"] if p in chapter.by_pid and chapter.by_pid[p]["kind"] == "text"]

    @classmethod
    def validate(cls, answer: dict, chapter, inventory: dict, slides: List[dict]) -> Dict[int, dict]:
        """Known ids only; key nodes within the slide (at least a few of its own, at most the bullets its source
        supports: those its own paragraphs state most); a note never holds a key node's statement and never takes every
        paragraph of the slide."""
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
            chosen = {str(k).strip() for k in row.get("key") or []}
            key = [k for k in s["teaches"] if k in chosen] or sorted(s["teaches"], key=stated)
            floor = min(len(s["teaches"]), 3)                # a slide teaches at least a few of its own nodes
            if len(key) < floor:
                extra = [k for k in sorted(s["teaches"], key=stated) if k not in key][:floor - len(key)]
                key = [k for k in s["teaches"] if k in set(key) | set(extra)]
            cap = most_bullets(s.get("built") or {})
            if len(key) > cap:                                # the rest are named: each key node needs its own bullet
                own = set(s["paragraphs"])
                here = lambda k: sum(chapter.pid_of.get(sid) in own for sid in nodes.get(k, {}).get("sentences", []))
                kept = set(sorted(key, key=lambda k: (-here(k), stated(k)))[:cap])
                key = [k for k in key if k in kept]
            texts = cls._text_paragraphs(chapter, s)
            anchored = {chapter.pid_of.get(sid) for k in key for sid in nodes.get(k, {}).get("sentences", [])}
            notes = [p for p in dict.fromkeys(str(x).strip() for x in row.get("notes") or [])
                     if p in texts and p not in anchored]
            if notes and len(notes) >= len(texts):
                notes = notes[:len(texts) - 1]
            out[n] = {"key": key, "notes": notes}
        return out
