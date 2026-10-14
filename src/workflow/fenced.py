"""The fenced blocks of a chapter that are not listings: page reading fences prose, references and links, and a pattern
cannot tell them from code in any language. The LLM reads each block once (cached); one that is no listing is read as text,
so its sentences reach the slides. An unusable answer leaves the block as it is."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from src.utils.config import Config
from src.utils.content_cache import cached_json, digest
from src.utils.llm import chat, parse_json_reply
from src.workflow.prompts import load

_NAME = "fenced_block"


def _listing(model: str, block: str) -> bool:
    """Whether the block is a listing; True without a usable answer."""
    prompt = load(_NAME)
    payload = {"block": block[:Config.FENCED_SAMPLE_CHARS]}
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    try:
        answer = cached_json(f"{_NAME}_v1.{digest(prompt)[:10]}", model, payload,
                             lambda: parse_json_reply(chat(model, messages, temperature=0, max_tokens=Config.FENCED_MAX_TOKENS,
                                                           role=_NAME, json_mode=True)))
    except (ValueError, RuntimeError) as exc:
        print(f"[{_NAME}] no usable answer ({str(exc)[:80]})")
        return True
    return not isinstance(answer, dict) or answer.get("listing") is not False


def read(chapter, model: str) -> int:
    """Reads the chapter's fenced blocks that are no listings as text; returns how many."""
    blocks = [p["pid"] for p in chapter.paragraphs if p["kind"] == "code"]
    if not blocks:
        return 0
    with ThreadPoolExecutor(max_workers=Config.FENCED_CONCURRENCY) as pool:
        answers = list(pool.map(lambda pid: _listing(model, chapter.text_of(pid)), blocks))
    return sum(chapter.read_as_text(pid) for pid, listing in zip(blocks, answers) if not listing)
