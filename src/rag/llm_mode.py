"""`--llm local`: the KG build's entity extraction and captioning go to a local Ollama server.

Must run before `src.rag.build_kg` is imported (it reads these variables at import). Values already set in .env win;
the main pipeline's own LLM_*/VLM_* are never touched. `ensure_ready` checks the server before a build: an Ollama server
loads a model with a 4k context by default, which cuts the extraction prompt (about 3.7k tokens, 4.5k with gleaning)."""
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

_LOCAL = {"KG_LLM_BASE_URL": "http://localhost:11434/v1", "KG_LLM_API_KEY": "ollama", "KG_LLM_MODEL": "qwen3.5:9b",
          "KG_VLM_BASE_URL": "http://localhost:11434/v1", "KG_VLM_API_KEY": "ollama", "KG_VLM_MODEL": "qwen3.5:9b",
          "KG_MAX_ASYNC": "4"}
MIN_CONTEXT = 8192           # tokens the extraction prompt + glean history + answer need


def requested() -> str:
    """The `--llm` value on the command line ('cloud' when absent)."""
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--llm" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--llm="):
            return a.split("=", 1)[1]
    return "cloud"


def apply() -> str:
    """Set the local-LLM defaults when `--llm local` was asked for; returns the mode."""
    mode = requested()
    if mode == "local":
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        for key, value in _LOCAL.items():
            os.environ.setdefault(key, value)
    return mode


def ensure_ready() -> None:
    """With `--llm local`: the Ollama server answers, has the model, and loads it with a context of at least
    MIN_CONTEXT tokens; otherwise stop with what to do (a build would fail late or extract from a cut prompt)."""
    if requested() != "local":
        return
    model = os.environ.get("KG_LLM_MODEL", _LOCAL["KG_LLM_MODEL"])
    root = os.environ.get("KG_LLM_BASE_URL", _LOCAL["KG_LLM_BASE_URL"]).rstrip("/").removesuffix("/v1")
    start = f"start it with `OLLAMA_CONTEXT_LENGTH={MIN_CONTEXT} ollama serve`"
    try:
        names = {m.get("name") for m in httpx.get(f"{root}/api/tags", timeout=10).json().get("models") or []}
    except (httpx.HTTPError, ValueError) as exc:
        raise SystemExit(f"--llm local: no Ollama server at {root} ({type(exc).__name__}); {start}")
    if model not in names and f"{model}:latest" not in names:
        raise SystemExit(f"--llm local: model '{model}' is not on the server ({sorted(n for n in names if n)}); run "
                         f"`ollama pull {model}` (a new model may need a newer Ollama), or set KG_LLM_MODEL in .env")
    try:                                                       # load it now, so its context length can be read
        httpx.post(f"{root}/api/generate", json={"model": model, "prompt": "", "keep_alive": "30m"}, timeout=600)
        loaded = httpx.get(f"{root}/api/ps", timeout=10).json().get("models") or []
    except (httpx.HTTPError, ValueError) as exc:
        raise SystemExit(f"--llm local: Ollama could not load '{model}' ({type(exc).__name__}: {exc})")
    context = next((m.get("context_length") for m in loaded if m.get("name") in (model, f"{model}:latest")), None)
    if context is not None and context < MIN_CONTEXT:
        raise SystemExit(f"--llm local: Ollama loads '{model}' with a {context}-token context, which cuts the "
                         f"extraction prompt; restart the server with `OLLAMA_CONTEXT_LENGTH={MIN_CONTEXT} ollama serve`")
    print(f"[llm local] {model} on {root} · context {context or 'unknown'} tokens")
