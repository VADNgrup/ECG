"""The document's field of study (Config.DOMAINS): given by the user (main.py --domain), else named by one small LLM
call from its title, headings, most mentioned concepts and opening text. A field named with low confidence takes the
general prompts."""
from __future__ import annotations

import json
from typing import List, Tuple

from src.utils.config import Config
from src.utils.content_cache import cached_json, digest
from src.utils.llm import chat, parse_json_reply
from src.workflow.prompts import load

_PROMPT = load("domain")


def detect(model: str, title: str, headings: List[str], concepts: List[str], opening: str) -> Tuple[str, float]:
    """(field, confidence); ("general", 0.0) when the answer is unusable."""
    payload = {"title": title, "headings": headings, "concepts": concepts,
               "opening": opening[:Config.DOMAIN_SAMPLE_CHARS], "task": "Name the document's field as the output says."}
    messages = [{"role": "system", "content": _PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    try:
        answer = cached_json(f"domain_v1.{digest(_PROMPT)[:10]}", model, payload,
                             lambda: parse_json_reply(chat(model, messages, temperature=0, max_tokens=60,
                                                           role="domain", json_mode=True)))
    except (ValueError, RuntimeError) as exc:
        print(f"[domain] no usable answer ({str(exc)[:80]}); general prompts")
        return "general", 0.0
    domain = str((answer or {}).get("domain") or "").strip().lower()
    try:
        confidence = float((answer or {}).get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if domain not in Config.DOMAINS:
        return "general", 0.0
    return domain, confidence


def resolve(model: str, requested: str | None, title: str, headings: List[str], concepts: List[str],
            opening: str) -> str:
    """The field the agents use: the requested one, else the detected one when it is named with enough confidence."""
    if requested:
        print(f"[domain] {requested} (given)")
        return requested
    domain, confidence = detect(model, title, headings, concepts, opening)
    chosen = domain if confidence >= Config.DOMAIN_MIN_CONFIDENCE else "general"
    print(f"[domain] {chosen} (detected {domain}, confidence {confidence:.2f})")
    return chosen
