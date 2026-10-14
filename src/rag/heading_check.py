"""Which lines the parser marked as headings open no part of the text: the KG's LLM reads each alone, with the blocks
around it (read together, lines blur into each other), and says whether it is a heading or something else printed on
the page -- a figure's or table's label or caption, a label inside a diagram or infographic, a repeated header or
footer, a line about the publication. Such a line loses its heading mark in a copy of the content list; its text stays
where it was, so what follows joins the section before it. No word or pattern decides here, an outline's titles are
never asked about, and an unusable answer keeps the heading."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Iterable, List, Set

from src.rag.structural import _clean_chars
from src.utils.config import Config
from src.utils.content_cache import cached_json, digest, stored
from src.utils.llm import parse_json_reply
from src.workflow.prompts import load

_CACHE = "kg_heading_check_v1_confirm"
_CAPTION_FIELDS = ("image_caption", "table_caption")


def _captions(item: dict) -> List[str]:
    """The captions a figure or table block carries."""
    found: List[str] = []
    for field in _CAPTION_FIELDS:
        value = item.get(field) or []
        found += [str(c) for c in (value if isinstance(value, list) else [value])]
    return [c for c in found if c.strip()]


def _block(item: dict) -> dict:
    """A block as the LLM sees it: its kind and how it starts."""
    said = str(item.get("text") or "") or " ".join(_captions(item))
    return {"kind": str(item.get("type") or "text"), "text": " ".join(said.split())[:Config.HEADING_CHECK_CONTEXT_CHARS]}


def lines_to_check(items: List[dict], outline_titles: Iterable[str] = ()) -> List[int]:
    """The marked lines an embedded or printed outline does not vouch for, in reading order."""
    vouched = {_clean_chars(t) for t in outline_titles}
    return [i for i, it in enumerate(items) if it.get("type") == "text" and it.get("text_level") and str(it.get("text") or "").strip()
            and _clean_chars(str(it["text"])) not in vouched]


def _payload(items: List[dict], i: int) -> dict:
    around = Config.HEADING_CHECK_NEIGHBOURS
    return {"lines": [{"id": i, "text": " ".join(str(items[i]["text"]).split()), "page": items[i].get("page_idx"),
                       "before": [_block(b) for b in items[max(0, i - around):i]],
                       "after": [_block(b) for b in items[i + 1:i + 1 + around]]}]}


def _other(answer: object) -> bool:
    return isinstance(answer, dict) and answer.get("kind") == "other"


async def not_headings(items: List[dict], llm_func, model: str, outline_titles: Iterable[str] = ()) -> Set[int]:
    """The marked lines that open no part of the text."""
    prompt = load("heading_check")
    namespace = f"{_CACHE}.{digest(prompt)[:10]}"
    gate = asyncio.Semaphore(Config.HEADING_CHECK_CONCURRENCY)

    async def ask(i: int) -> bool:
        payload = _payload(items, i)
        kept = stored(namespace, model, payload)
        if kept is not None:
            return _other(kept)
        async with gate:
            try:
                answer = parse_json_reply(await llm_func(json.dumps(payload, ensure_ascii=False), system_prompt=prompt,
                                                         response_format={"type": "json_object"}))
            except Exception as exc:
                print(f"[heading_check] no usable answer, line {i} stays a heading ({str(exc)[:80]})")
                return False
        cached_json(namespace, model, payload, lambda: answer)
        return _other(answer)

    lines = lines_to_check(items, outline_titles)
    answers = await asyncio.gather(*(ask(i) for i in lines))
    return {i for i, other in zip(lines, answers) if other}


async def checked(content_list_path: str, llm_func, model: str, outline_titles: Iterable[str] = ()) -> str:
    """The content list without the heading marks of lines that open no part of the text: the given file when there
    is none, else a copy."""
    path = Path(content_list_path)
    items = json.loads(path.read_text(encoding="utf-8"))
    named = await not_headings(items, llm_func, model, outline_titles)
    if not named:
        return str(path)
    for i in named:
        items[i].pop("text_level", None)
    print(f"[heading_check] {len(named)} marked line(s) open no part of the text: " + "; ".join(
        f"{' '.join(str(items[i].get('text')).split())[:40]!r}" for i in sorted(named)))
    copy = path.with_name(f"{path.name}.headings.json")
    copy.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return str(copy)
