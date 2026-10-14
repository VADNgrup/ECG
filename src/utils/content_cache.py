"""Versioned, content-addressed cache for small structured LLM decisions."""
import hashlib
import json
import os
import shutil
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


_FRESH = {"on": False}
_RUN = {"on": False}


def cache_path(namespace, model, payload):
    """Path of a decision cache entry, also used for observability before a call."""
    root = Path(Config.DECISION_CACHE_DIR) / (Config.RUN_DECISIONS if _RUN["on"] else "") / namespace
    key = digest([PIPELINE_VERSION, model, Config.LLM_BASE_URL, payload])
    return root / f"{key}.json"


def run_decisions(on=True):
    """While on (a deck's agents), decisions are kept for this run only: a later run asks them again, so its token
    count is what making the decks costs. Off (the KG step), they are kept across runs."""
    _RUN["on"] = bool(on)


def end_run():
    """The run made all its decks: its agents' decisions are removed. A run stopped before that keeps them, so the
    next one continues where it stopped."""
    shutil.rmtree(Path(Config.DECISION_CACHE_DIR) / Config.RUN_DECISIONS, ignore_errors=True)


def fresh(on=True):
    """While on, every decision is asked again (stored answers are not read; the new ones are still saved)."""
    _FRESH["on"] = bool(on)


def stored(namespace, model, payload):
    """The stored answer to a decision, or None when there is none or every decision is being asked again."""
    if _FRESH["on"]:
        return None
    try:
        return json.loads(cache_path(namespace, model, payload).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cached_json(namespace, model, payload, invoke):
    path = cache_path(namespace, model, payload)
    if path.exists() and not _FRESH["on"]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    result = invoke()
    _write(path, result)
    if _RUN["on"]:                           # the run's store goes with the run; the archive keeps a copy to measure on
        _write(Path(Config.DECISION_ARCHIVE_DIR) / namespace / path.name, result)
    return result


def _write(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
