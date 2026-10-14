"""Which uncaptioned pictures of a document carry content: the vision model only says what a picture is (content, a
table, formulas, decoration) and writes no caption. A table or formulas drawn as a picture stays out when the page's
reading already holds a table or a formula of its own (the editable one is shown instead); else it is content. A
picture the page geometry already calls furniture, or that the document captions, is not asked; an unusable answer
leaves the picture out."""
from __future__ import annotations

import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List

from src.utils.config import Config
from src.utils.content_cache import cached_json, digest
from src.utils.llm import parse_json_reply, vision_chat
from src.workflow.prompts import load

_CACHE = "kg_image_role_v1"
_READ_AS = {"table": "table", "formula": "equation"}      # a picture's role -> the content-list block that reads it


def _ask(path: Path, model: str, base_url: str, api_key: str) -> str:
    """What the picture at `path` is, as the vision model sees it; "" when unusable."""
    prompt = load("image_role")
    data = path.read_bytes()
    mime = f"image/{path.suffix.lstrip('.').lower().replace('jpg', 'jpeg') or 'png'}"
    image = {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}",
                                                "detail": Config.IMAGE_ROLE_DETAIL}}
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": [image]}]
    try:
        answer = cached_json(f"{_CACHE}.{digest(prompt)[:10]}", model, {"image": hashlib.sha256(data).hexdigest()},
                             lambda: parse_json_reply(vision_chat(messages, model=model, base_url=base_url,
                                                                  api_key=api_key, temperature=0,
                                                                  max_tokens=Config.IMAGE_ROLE_MAX_TOKENS)))
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"[image_role] no usable answer for {path.name}, left out ({str(exc)[:80]})")
        return ""
    return str(answer.get("role") or "") if isinstance(answer, dict) else ""


def content_images(items: List[dict], base: Path, figures: Dict[str, dict], model: str, base_url: str,
                   api_key: str) -> set:
    """File names of the uncaptioned, non-furniture pictures that carry content and are not already read as text."""
    asked: Dict[str, tuple] = {}
    for it in items:
        rel = it.get("img_path") or ""
        info = figures.get(Path(rel).name, {}) if rel else {}
        if it.get("type") in ("image", "chart") and rel and not info.get("decorative") and not info.get("real_caption") \
                and (base / rel).exists():
            asked[Path(rel).name] = (base / rel, it.get("page_idx"))
    if not asked:
        return set()
    read = {(it.get("type"), it.get("page_idx")) for it in items}
    with ThreadPoolExecutor(max_workers=Config.IMAGE_ROLE_CONCURRENCY) as pool:
        roles = list(pool.map(lambda v: _ask(v[0], model, base_url, api_key), asked.values()))
    found, twice = set(), 0
    for (name, (_, page)), role in zip(asked.items(), roles):
        if role in _READ_AS and (_READ_AS[role], page) in read:
            twice += 1                                   # the page's own table or formula shows it, editable
        elif role in ("content", *_READ_AS):
            found.add(name)
    print(f"[image_role] {len(found)} of {len(asked)} uncaptioned picture(s) carry content "
          f"({twice} table or formula picture(s) already read as text)")
    return found
