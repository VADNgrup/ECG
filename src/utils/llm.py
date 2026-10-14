import asyncio
import base64
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from src.utils.config import Config

_RUN_LOCK = threading.RLock()
_RUN_STATE: dict[str, Any] = {
    "run_id": None,
    "document_id": None,
    "output_id": None,
    "phase": None,
    "started_at": None,
    "calls": [],
}

def start_llm_run(document_id: str, output_id: Optional[str]=None) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_doc = re.sub(r"[^A-Za-z0-9_.-]+", "_", document_id or "unknown").strip("_") or "unknown"
    run_id = f"{safe_doc}_{timestamp}"
    with _RUN_LOCK:
        _RUN_STATE["run_id"] = run_id
        _RUN_STATE["document_id"] = document_id
        _RUN_STATE["output_id"] = output_id
        _RUN_STATE["phase"] = None
        _RUN_STATE["started_at"] = datetime.now(timezone.utc).isoformat()
        _RUN_STATE["calls"] = []
    return run_id

def llm_run_active() -> bool:
    with _RUN_LOCK:
        return bool(_RUN_STATE.get("run_id"))

def set_llm_phase(phase: str) -> None:
    with _RUN_LOCK:
        _RUN_STATE["phase"] = phase

def end_llm_run(status: str="completed", output_path: Optional[str]=None) -> dict:
    with _RUN_LOCK:
        calls = list(_RUN_STATE.get("calls") or [])
        run_id = _RUN_STATE.get("run_id")
        document_id = _RUN_STATE.get("document_id")
        output_id = _RUN_STATE.get("output_id")
        started_at = _RUN_STATE.get("started_at")
        _RUN_STATE["run_id"] = None
        _RUN_STATE["document_id"] = None
        _RUN_STATE["output_id"] = None
        _RUN_STATE["phase"] = None
        _RUN_STATE["started_at"] = None
        _RUN_STATE["calls"] = []
    summary = _summarise_run(
        run_id=run_id,
        document_id=document_id,
        output_id=output_id,
        started_at=started_at,
        status=status,
        output_path=output_path,
        calls=calls,
    )
    if run_id:
        _write_run_summary(summary)
    return summary

def _normalise_usage(token_usage: dict | None) -> dict:
    usage = token_usage or {}
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
    completion = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    total = usage.get("total_tokens", 0) or 0
    if not total:
        total = prompt + completion
    return {
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
        "total_tokens": int(total or 0),
        "raw": usage,
    }

def _write_log(model: str, token_usage: dict, elapsed: float, api_type: str, request_chars: int, response_chars: int,
               role: Optional[str] = None) -> None:
    usage = _normalise_usage(token_usage)
    with _RUN_LOCK:
        run_id = _RUN_STATE.get("run_id")
        phase = _RUN_STATE.get("phase")
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "document_id": _RUN_STATE.get("document_id"),
            "output_id": _RUN_STATE.get("output_id"),
            "phase": phase,
            "role": role,
            "api_type": api_type,
            "model": model,
            "token_usage": usage["raw"],
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "total_tokens": usage["total_tokens"],
            "request_chars": request_chars,
            "response_chars": response_chars,
            "elapsed_s": round(elapsed, 3),
        }
        if run_id:
            _RUN_STATE["calls"].append(entry)
    log_path = Config.get_log_path()
    with log_path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')

def log_retrieval(role: str, queries: int = 1) -> None:
    """A MiniRAG lookup made for `role`: counted in the run summary (by_role), no LLM tokens."""
    for _ in range(queries):
        _write_log("minirag", {}, 0.0, "retrieval", 0, 0, f"{role}:retrieve")

def _summarise_run(run_id: Optional[str], document_id: Optional[str], output_id: Optional[str], started_at: Optional[str], status: str, output_path: Optional[str], calls: list[dict]) -> dict:
    total_calls = len(calls)
    prompt_tokens = sum(call.get("prompt_tokens", 0) for call in calls)
    completion_tokens = sum(call.get("completion_tokens", 0) for call in calls)
    total_tokens = sum(call.get("total_tokens", 0) for call in calls)
    elapsed_s = sum(call.get("elapsed_s", 0) for call in calls)
    summary = {
        "run_id": run_id,
        "document_id": document_id,
        "output_id": output_id,
        "status": status,
        "started_at": started_at,
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "output_path": output_path,
        "total_calls": total_calls,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "avg_prompt_tokens_per_call": round(prompt_tokens / total_calls, 2) if total_calls else 0,
        "avg_completion_tokens_per_call": round(completion_tokens / total_calls, 2) if total_calls else 0,
        "avg_total_tokens_per_call": round(total_tokens / total_calls, 2) if total_calls else 0,
        "total_llm_elapsed_s": round(elapsed_s, 3),
        "missing_usage_calls": sum(1 for call in calls if not call.get("total_tokens")),
        "by_phase": _group_calls(calls, "phase"),
        "by_model": _group_calls(calls, "model"),
        "by_api_type": _group_calls(calls, "api_type"),
        "by_role": _group_calls(calls, "role"),
        "cached_prompt_tokens": sum(((call.get("token_usage") or {}).get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
                                    for call in calls),
    }
    return summary

def _group_calls(calls: list[dict], key: str) -> dict:
    grouped: dict[str, dict[str, Any]] = {}
    for call in calls:
        name = str(call.get(key) or "unknown")
        bucket = grouped.setdefault(
            name,
            {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "elapsed_s": 0.0,
            },
        )
        bucket["calls"] += 1
        bucket["prompt_tokens"] += call.get("prompt_tokens", 0)
        bucket["completion_tokens"] += call.get("completion_tokens", 0)
        bucket["total_tokens"] += call.get("total_tokens", 0)
        bucket["elapsed_s"] += call.get("elapsed_s", 0)
    for bucket in grouped.values():
        calls_count = bucket["calls"]
        bucket["elapsed_s"] = round(bucket["elapsed_s"], 3)
        bucket["avg_total_tokens_per_call"] = round(bucket["total_tokens"] / calls_count, 2) if calls_count else 0
    return grouped

def _write_run_summary(summary: dict) -> None:
    log_dir = Config.BASE_DIR / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    run_id = summary.get("run_id") or "unknown"
    summary_path = log_dir / f"llm_run_{run_id}.json"
    summary["summary_path"] = str(summary_path)
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    runs_path = log_dir / "llm_runs.jsonl"
    with runs_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")

def _messages_chars(messages: list) -> int:
    try:
        return len(json.dumps(messages, ensure_ascii=False))
    except Exception:
        return len(str(messages))
_MAX_RETRIES = 6
# (endpoint, model, parameter) an endpoint rejected once: not sent again, so later calls skip the failing round trip
_REJECTED_PARAMS: set = set()

def chat(model: str, messages: list, temperature: float=0.3, max_tokens: Optional[int]=None, base_url: Optional[str]=None, api_key: Optional[str]=None,
         role: Optional[str]=None, json_mode: bool=False) -> str:
    resolved_base = (base_url or Config.LLM_BASE_URL).rstrip('/')
    resolved_key = api_key or Config.OPENAI_API_KEY
    url = f'{resolved_base}/chat/completions'
    headers = {'Authorization': f'Bearer {resolved_key}', 'Content-Type': 'application/json'}
    # OpenAI GPT-5 chat-completions rejects both ``max_tokens`` and non-default
    # temperature.  Previously every call paid for two avoidable HTTP 400s before
    # the generic compatibility retry corrected them.  Restrict this fast path to
    # the official endpoint so compatible gateways keep their existing contract.
    official_gpt5 = 'api.openai.com' in resolved_base.casefold() \
                    and re.match(r'^(?:openai/)?gpt-5(?:[.-]|$)', model or '', re.IGNORECASE)
    payload: dict = {'model': model, 'messages': messages}
    if not official_gpt5:
        payload['temperature'] = temperature
    if max_tokens is not None:                      # a reasoning model's hidden thinking counts against the cap
        payload['max_completion_tokens' if official_gpt5 else 'max_tokens'] =             max_tokens + Config.LLM_REASONING_HEADROOM if official_gpt5 else max_tokens
    # Writing bullets from supplied sentences is not a reasoning task; left at the default effort a
    # reasoning model spent ~5.5k hidden tokens and 40s per call. Unknown field is dropped on 400.
    if Config.LLM_REASONING_EFFORT:
        payload['reasoning_effort'] = Config.LLM_REASONING_EFFORT
    if json_mode:                                   # the reply must be one syntactically valid JSON object
        payload['response_format'] = {'type': 'json_object'}
    for name in [p for p in payload if (resolved_base, model, p) in _REJECTED_PARAMS]:
        payload.pop(name)
    last_exc: Exception = RuntimeError('No attempts made')
    t0 = time.monotonic()
    for attempt in range(_MAX_RETRIES):
        try:
            with httpx.Client(timeout=120) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                try:
                    data = response.json()
                except Exception as exc:
                    raise ValueError(f'LLM API returned non-JSON response (status {response.status_code}): {response.text!r}') from exc
            content = data['choices'][0]['message']['content']
            _write_log(model, data.get('usage', {}), time.monotonic() - t0, "llm", _messages_chars(messages), len(content or ""), role)
            return _strip_think(content)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if isinstance(exc, httpx.HTTPStatusError):
                status_code = exc.response.status_code
                if 400 <= status_code < 500 and status_code != 429:
                    body = exc.response.text[:1000]
                    # Newer reasoning models renamed the output cap; retry once under the new name
                    # rather than failing a whole run over a parameter spelling.
                    if 'max_tokens' in payload and 'max_completion_tokens' in body:
                        payload['max_completion_tokens'] = payload.pop('max_tokens')
                        continue
                    if 'temperature' in payload and "'temperature'" in body:
                        payload.pop('temperature')
                        _REJECTED_PARAMS.add((resolved_base, model, 'temperature'))
                        continue
                    if 'reasoning_effort' in payload and 'reasoning_effort' in body:
                        payload.pop('reasoning_effort')
                        _REJECTED_PARAMS.add((resolved_base, model, 'reasoning_effort'))
                        continue
                    if 'response_format' in payload and 'response_format' in body:
                        payload.pop('response_format')
                        _REJECTED_PARAMS.add((resolved_base, model, 'response_format'))
                        continue
                    raise RuntimeError(f'LLM request failed with HTTP {status_code}: {body}') from exc
            wait = min(2 ** attempt, 30)
            print(f'[llm] Attempt {attempt + 1}/{_MAX_RETRIES} failed: {exc}. Retrying in {wait}s...')
            time.sleep(wait)
    raise RuntimeError(f'LLM request failed after {_MAX_RETRIES} attempts') from last_exc

def parse_json_reply(raw: str):
    """The JSON object or list a model returned, fences and <think> blocks removed; ValueError otherwise."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", _strip_think(raw or "").strip())
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            return json.loads(text[start:end + 1])
        raise


def _strip_think(text: str) -> str:
    return re.sub('<think>.*?</think>', '', text, flags=re.DOTALL).strip()

async def achat(model: str, messages: list, temperature: float=0.3, max_tokens: Optional[int]=None) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda : chat(model, messages, temperature, max_tokens))

def _vlm_config() -> tuple[str, str, str]:
    base = Config.VLM_BASE_URL.rstrip('/')
    if not base.endswith('/v1'):
        base = f'{base}/v1'
    return (base, Config.VLM_API_KEY, Config.VLM_MODEL_NAME)

def b64_image(image_bytes: bytes, mime: str='image/png') -> dict:
    encoded = base64.b64encode(image_bytes).decode('utf-8')
    return {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{encoded}'}}

def vision_chat(messages: list, model: Optional[str]=None, temperature: float=0.3, max_tokens: Optional[int]=None, base_url: Optional[str]=None, api_key: Optional[str]=None) -> str:
    (vlm_base, vlm_key, default_model) = _vlm_config()
    resolved_base = (base_url or vlm_base).rstrip('/')
    resolved_key = api_key or vlm_key
    url = f'{resolved_base}/chat/completions'
    headers = {'Authorization': f'Bearer {resolved_key}', 'Content-Type': 'application/json'}
    payload: dict = {'model': model or default_model, 'messages': messages, 'temperature': temperature}
    if max_tokens is not None:
        payload['max_tokens'] = max_tokens
    last_exc: Exception = RuntimeError('No attempts made')
    t0 = time.monotonic()
    for attempt in range(_MAX_RETRIES):
        try:
            with httpx.Client(timeout=120) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                try:
                    data = response.json()
                except Exception as exc:
                    raise ValueError(f'VLM API returned non-JSON response (status {response.status_code}): {response.text!r}') from exc
            content = data['choices'][0]['message']['content']
            _write_log(payload['model'], data.get('usage', {}), time.monotonic() - t0, "vlm", _messages_chars(messages), len(content or ""))
            return _strip_think(content)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if isinstance(exc, httpx.HTTPStatusError):
                status_code = exc.response.status_code
                if 400 <= status_code < 500 and status_code != 429:
                    body = exc.response.text[:1000]
                    raise RuntimeError(f'VLM request failed with HTTP {status_code}: {body}') from exc
            wait = min(2 ** attempt, 30)
            print(f'[vlm] Attempt {attempt + 1}/{_MAX_RETRIES} failed: {exc}. Retrying in {wait}s...')
            time.sleep(wait)
    raise RuntimeError(f'VLM request failed after {_MAX_RETRIES} attempts') from last_exc

async def avision_chat(messages: list, model: Optional[str]=None, temperature: float=0.3, max_tokens: Optional[int]=None) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda : vision_chat(messages, model, temperature, max_tokens))

def generate_image(prompt: str, model: Optional[str]=None, size: str='1024x1024', base_url: Optional[str]=None, api_key: Optional[str]=None) -> bytes:
    resolved_base = (base_url or Config.LLM_BASE_URL).rstrip('/')
    resolved_key = api_key or Config.OPENAI_API_KEY
    resolved_model = model or Config.IMAGE_GEN_MODEL
    url = f'{resolved_base}/images/generations'
    headers = {'Authorization': f'Bearer {resolved_key}', 'Content-Type': 'application/json'}
    payload = {'model': resolved_model, 'prompt': prompt, 'size': size, 'n': 1}
    last_exc: Exception = RuntimeError('No attempts made')
    t0 = time.monotonic()
    for attempt in range(_MAX_RETRIES):
        try:
            with httpx.Client(timeout=120) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                try:
                    data = response.json()
                except Exception as exc:
                    raise ValueError(f'Image API returned non-JSON response (status {response.status_code}): {response.text!r}') from exc
            b64_data = data['data'][0]['b64_json']
            image_bytes = base64.b64decode(b64_data)
            _write_log(resolved_model, data.get('usage', {}), time.monotonic() - t0, 'image', len(prompt), len(image_bytes))
            return image_bytes
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if isinstance(exc, httpx.HTTPStatusError):
                status_code = exc.response.status_code
                if 400 <= status_code < 500 and status_code != 429:
                    body = exc.response.text[:1000]
                    raise RuntimeError(f'Image request failed with HTTP {status_code}: {body}') from exc
            wait = min(2 ** attempt, 30)
            print(f'[image] Attempt {attempt + 1}/{_MAX_RETRIES} failed: {exc}. Retrying in {wait}s...')
            time.sleep(wait)
    raise RuntimeError(f'Image request failed after {_MAX_RETRIES} attempts') from last_exc
