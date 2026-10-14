"""
OpenAI LLM Interface Module
==========================

This module provides interfaces for interacting with openai's language models,
including text generation and embedding capabilities.

Author: Lightrag team
Created: 2024-01-24
License: MIT License

Copyright (c) 2024 Lightrag

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

Version: 1.0.0

Change Log:
- 1.0.0 (2024-01-24): Initial release
    * Added async chat completion support
    * Added embedding generation
    * Added stream response capability

Dependencies:
    - openai
    - numpy
    - pipmaster
    - Python >= 3.10

Usage:
    from llm_interfaces.openai import openai_model_complete, openai_embed
"""

__version__ = "1.0.0"
__author__ = "lightrag Team"
__status__ = "Production"


import sys
import os

if sys.version_info < (3, 9):
    from typing import AsyncIterator
else:
    from collections.abc import AsyncIterator
import pipmaster as pm  # Pipmaster for dynamic library install

# install specific modules
if not pm.is_installed("openai"):
    pm.install("openai")

from openai import (
    AsyncOpenAI,
    APIConnectionError,
    RateLimitError,
    APITimeoutError,
)
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from minirag.utils import (
    wrap_embedding_func_with_attrs,
    locate_json_string_body_from_string,
    safe_unicode_decode,
    compute_args_hash,
    logger,
)
from pydantic import BaseModel
from typing import List
import numpy as np
from typing import Union


class GPTKeywordExtractionFormat(BaseModel):
    high_level_keywords: List[str]
    low_level_keywords: List[str]

# [VENDOR EDIT] retry decorator moved here from the unused class above (upstream bug). See VENDOR.md.
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type(
        (RateLimitError, APIConnectionError, APITimeoutError)
    ),
)
async def openai_complete_if_cache(
    model,
    prompt,
    system_prompt=None,
    history_messages=[],
    base_url=None,
    api_key=None,
    **kwargs,
) -> str:
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
    if base_url==None:
        base_url = os.environ["OPENAI_API_BASE"]
    # [VENDOR EDIT] explicit timeout, avoids a silent 600s hang. See VENDOR.md.
    openai_async_client = (
        AsyncOpenAI(timeout=120.0) if base_url is None else AsyncOpenAI(base_url=base_url, timeout=120.0)
    )
    # [VENDOR EDIT] keep hashing_kv as an LLM response cache. See VENDOR.md.
    hashing_kv = kwargs.pop("hashing_kv", None)
    kwargs.pop("keyword_extraction", None)
    # accept an optional token_tracker (a _Usage-like object with add_usage(dict)) so the slide
    # pipeline can log real token counts per call.
    token_tracker = kwargs.pop("token_tracker", None)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.extend(history_messages)
    messages.append({"role": "user", "content": prompt})

    # response cache: return a stored completion for the same (model, messages) if present. Only for
    # non-streaming, non-structured calls (extraction/summary). Persisted to kv_store_llm_response_cache
    # by MiniRAG._insert_done.
    _cache_id = None
    if hashing_kv is not None and "response_format" not in kwargs:
        try:
            _cache_id = compute_args_hash(model, messages)
            _hit = await hashing_kv.get_by_id(_cache_id)
            if _hit is not None and _hit.get("return") is not None:
                return _hit["return"]
        except Exception:
            _cache_id = None

    # 添加日志输出
    logger.debug("===== Query Input to LLM =====")
    logger.debug(f"Query: {prompt}")
    logger.debug(f"System prompt: {system_prompt}")
    logger.debug("Full context:")
    if "response_format" in kwargs:
        response = await openai_async_client.beta.chat.completions.parse(
            model=model, messages=messages, **kwargs
        )
    else:
        response = await openai_async_client.chat.completions.create(
            model=model, messages=messages, **kwargs
        )

    if hasattr(response, "__aiter__"):

        async def inner():
            async for chunk in response:
                content = chunk.choices[0].delta.content
                if content is None:
                    continue
                if r"\u" in content:
                    content = safe_unicode_decode(content.encode("utf-8"))
                yield content

        return inner()
    else:
        if not response or not hasattr(response, "choices") or not response.choices:
            logger.error("No valid choices returned. Full response: %s", response)
            return ""  # or raise a more specific exception
        content = response.choices[0].message.content
        if content is None:
            logger.error("The message content is None. Full response: %s", response)
            return ""
        if r"\u" in content:
            content = safe_unicode_decode(content.encode("utf-8"))
        if token_tracker is not None and getattr(response, "usage", None) is not None:
            u = response.usage
            try:
                details = getattr(u, "prompt_tokens_details", None)
                token_tracker.add_usage({
                    "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
                    "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
                    "total_tokens": getattr(u, "total_tokens", 0) or 0,
                    "cached_tokens": getattr(details, "cached_tokens", 0) or 0,  # [VENDOR EDIT] see VENDOR.md
                })
            except Exception:
                pass
        if _cache_id is not None:  # [VENDOR] store completion for cheap rebuilds
            try:
                await hashing_kv.upsert({_cache_id: {"return": content, "model": model}})
            except Exception:
                pass
        return content


async def openai_complete(
    prompt, system_prompt=None, history_messages=[], keyword_extraction=False, **kwargs
) -> Union[str, AsyncIterator[str]]:
    keyword_extraction = kwargs.pop("keyword_extraction", None)
    if keyword_extraction:
        kwargs["response_format"] = "json"
    model_name = kwargs["hashing_kv"].global_config["llm_model_name"]
    return await openai_complete_if_cache(
        model_name,
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        **kwargs,
    )


async def gpt_4o_complete(
    prompt, system_prompt=None, history_messages=[], keyword_extraction=False, **kwargs
) -> str:
    keyword_extraction = kwargs.pop("keyword_extraction", None)
    if keyword_extraction:
        kwargs["response_format"] = GPTKeywordExtractionFormat
    return await openai_complete_if_cache(
        "gpt-4o",
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        **kwargs,
    )


async def gpt_4o_mini_complete(
    prompt, system_prompt=None, history_messages=[], keyword_extraction=False, **kwargs
) -> str:
    keyword_extraction = kwargs.pop("keyword_extraction", None)
    if keyword_extraction:
        kwargs["response_format"] = GPTKeywordExtractionFormat
    return await openai_complete_if_cache(
        "gpt-4o-mini",
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        **kwargs,
    )


async def nvidia_openai_complete(
    prompt, system_prompt=None, history_messages=[], keyword_extraction=False, **kwargs
) -> str:
    keyword_extraction = kwargs.pop("keyword_extraction", None)
    result = await openai_complete_if_cache(
        "nvidia/llama-3.1-nemotron-70b-instruct",  # context length 128k
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        base_url="https://integrate.api.nvidia.com/v1",
        **kwargs,
    )
    if keyword_extraction:  # TODO: use JSON API
        return locate_json_string_body_from_string(result)
    return result

async def openrouter_openai_complete(
    prompt, 
    system_prompt=None, 
    history_messages=[], 
    keyword_extraction=False, 
    api_key: str = None, 
    **kwargs,
) -> str:
    if api_key:
        os.environ["OPENROUTER_API_KEY"] = api_key

    keyword_extraction = kwargs.pop("keyword_extraction", None)
    result = await openai_complete_if_cache(
        "google/gemini-2.0-flash-001",  # change accordingly
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        **kwargs,
    )
    if keyword_extraction:  # TODO: use JSON API
        return locate_json_string_body_from_string(result)
    return result

@wrap_embedding_func_with_attrs(embedding_dim=1536, max_token_size=8192)
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    retry=retry_if_exception_type(
        (RateLimitError, APIConnectionError, APITimeoutError)
    ),
)
async def openai_embed(
    texts: list[str],
    model: str = "text-embedding-3-small",
    base_url: str = None,
    api_key: str = None,
) -> np.ndarray:
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key

    openai_async_client = (
        AsyncOpenAI() if base_url is None else AsyncOpenAI(base_url=base_url)
    )
    response = await openai_async_client.embeddings.create(
        model=model, input=texts, encoding_format="float"
    )
    return np.array([dp.embedding for dp in response.data])
