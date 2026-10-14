"""The deck's title: the section's own title for one section, a short subject title (one small LLM call) for several."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List

import fitz

from src.utils.content_cache import cached_json, digest
from src.utils.llm import chat, parse_json_reply

_PROMPT = (
    "Give a short title (2-6 words) for one university lecture that covers the chapters listed in `chapters` (from the "
    "book `book`, if given). It names their common subject, never a list of the chapters, and uses the chapters' "
    'language. Return JSON {"title": "..."}.')
_NUMBERED = re.compile(r"^\s*(chapter|chương|part|phần|section|mục)\s+(\d+)", re.IGNORECASE)
_TRIMMED = re.compile(r"_p\d+-\d+$")


def book_title(source_file: str) -> str:
    """The PDF's own title (its metadata; a page-trimmed copy falls back to the original book in the same raw folder)."""
    path = Path(source_file or "")
    candidates = [path]
    if _TRIMMED.search(path.stem):
        original = _TRIMMED.sub("", path.stem) + path.suffix
        candidates += [path.parent / original, path.parent.parent / original]
    for candidate in candidates:
        try:
            if candidate.suffix.lower() == ".pdf" and candidate.is_file():
                title = " ".join(str((fitz.open(candidate).metadata or {}).get("title") or "").split())
                if title:
                    return title
        except Exception:
            continue
    return ""


def fallback(titles: List[str]) -> str:
    """`Chapters 1–4` when every title is numbered, else the first title."""
    numbered = [_NUMBERED.match(t) for t in titles]
    if titles and all(numbered):
        word = numbered[0].group(1)
        plural = {"chapter": "Chapters", "part": "Parts", "section": "Sections"}.get(word.lower(), word)
        numbers = [int(m.group(2)) for m in numbered]
        return f"{plural} {min(numbers)}–{max(numbers)}"
    return titles[0] if titles else ""


def deck_title(model: str, titles: List[str], book: str = "") -> str:
    titles = [" ".join(str(t).split()) for t in titles if str(t).strip()]
    if len(titles) <= 1:
        return titles[0] if titles else book
    payload = {"book": book, "chapters": titles}
    messages = [{"role": "system", "content": _PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    try:
        answer = cached_json(f"deck_title_v1.{digest(_PROMPT)[:10]}", model, payload,
                             lambda: parse_json_reply(chat(model, messages, temperature=0, max_tokens=60,
                                                           role="deck_title", json_mode=True)))
    except (ValueError, RuntimeError):
        return fallback(titles)
    title = " ".join(str((answer or {}).get("title") or "").split()).strip(' ."')
    if 2 <= len(title.split()) <= 8 and len(title) <= 60 and ";" not in title:
        return title
    return fallback(titles)
