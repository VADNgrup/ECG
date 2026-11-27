"""The language a chapter is written in, named by the LLM piece by piece from the chapter's own text, and the deck's fixed
words (cover, contents, dividers, the formula's key, the closing, the speaker's and institution's labels) in it: small cached calls, so no list of languages or
translations lives in the code."""
from __future__ import annotations

import json
from collections import Counter
from typing import Dict, List

from src.utils.config import Config
from src.utils.content_cache import cached_json, digest
from src.utils.llm import chat, parse_json_reply
from src.workflow.prompts import load

KEYS = ("lecture", "section", "contents", "where", "thanks", "summary", "source", "presenter", "institution",
        "figure", "table")


def sample(chapter) -> List[str]:
    """Pieces of paragraphs spread across the chapter, from its longer half: prose, not its names and references."""
    texts = [chapter.text_of(p["pid"]) for p in chapter.paragraphs if chapter.teachable(p) and p["kind"] == "text"]
    if not texts:
        return []
    floor = sorted(map(len, texts))[len(texts) // 2]
    long = [t for t in texts if len(t) >= floor]
    pieces = min(Config.DECK_LABEL_SAMPLE_PIECES, len(long))
    return [long[k * len(long) // pieces][:Config.DECK_LABEL_SAMPLE_CHARS // pieces] for k in range(pieces)]


def _ask(name: str, model: str, payload: dict, max_tokens: int) -> dict:
    """The small call's JSON answer; {} when it is unusable."""
    prompt = load(name)
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    try:
        answer = cached_json(f"{name}_v1.{digest(prompt)[:10]}", model, payload,
                             lambda: parse_json_reply(chat(model, messages, temperature=0, max_tokens=max_tokens,
                                                           role=name, json_mode=True)))
    except (ValueError, RuntimeError) as exc:
        print(f"[{name}] no usable answer ({str(exc)[:80]})")
        return {}
    return answer if isinstance(answer, dict) else {}


def language(model: str, pieces: List[str]) -> str:
    """The language most pieces are written in; "" when there is no text or no usable answer."""
    if not pieces:
        return ""
    payload = {"pieces": pieces, "task": f"Name the language of each of the {len(pieces)} pieces."}
    named = [" ".join(str(n).split()) for n in _ask("source_language", model, payload, Config.DECK_LABEL_MAX_TOKENS).get("languages") or []]
    counted = Counter(n for n in named if n)
    return counted.most_common(1)[0][0] if counted else ""


def labels(model: str, name: str) -> Dict[str, str]:
    """{key: word} in the named language; a word without a usable answer is left out (the renderer's default)."""
    if not name:
        return {}
    answer = _ask("deck_labels", model, {"language": name, "task": "Word the labels in this language."}, Config.DECK_LABEL_MAX_TOKENS)
    words = {k: " ".join(str(answer.get(k) or "").split()) for k in KEYS}
    return {k: w for k, w in words.items() if w and len(w) <= Config.DECK_LABEL_MAX_CHARS}
