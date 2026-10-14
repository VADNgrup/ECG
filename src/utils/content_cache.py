"""Versioned, content-addressed cache for small structured LLM decisions."""
import hashlib
import json
import os
import tempfile
from pathlib import Path

from src.utils.config import Config

PIPELINE_VERSION = "heading-kb-semantic-assets-v3"
# Invalidates saved lecture/PPTX artifacts without throwing away independent
# agent decision-cache entries.
LECTURE_CONTRACT_VERSION = "formula-symbol-context-v2"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    default=str).encode("utf-8")).hexdigest()


def cache_path(namespace, model, payload):
    """Path of a decision cache entry, also used for observability before a call."""
    root = Path(Config.DECISION_CACHE_DIR) / namespace
    key = digest([PIPELINE_VERSION, model, Config.LLM_BASE_URL, payload])
    return root / f"{key}.json"


_FRESH = {"on": False}


def fresh(on=True):
    """While on, every decision is asked again (stored answers are not read; the new ones are still saved)."""
    _FRESH["on"] = bool(on)


def cached_json(namespace, model, payload, invoke):
    path = cache_path(namespace, model, payload)
    root = path.parent
    if path.exists() and not _FRESH["on"]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    result = invoke()
    root.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=root, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return result
