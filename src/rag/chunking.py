from __future__ import annotations

from typing import Any

from minirag.utils import decode_tokens_by_tiktoken, encode_string_by_tiktoken

from src.rag.structural import STRUCT_MARK_RE


def _strip_markers(content: str) -> tuple[str, list[tuple[int, int, int]]]:
    parts: list[str] = []
    markers: list[tuple[int, int, int]] = []
    clean_len = 0
    last = 0
    for m in STRUCT_MARK_RE.finditer(content):
        seg = content[last:m.start()]
        parts.append(seg)
        clean_len += len(seg)
        markers.append((clean_len, int(m.group(1)), int(m.group(2))))
        last = m.end()
    parts.append(content[last:])
    return "".join(parts), markers


def marker_chunking_func_mini(
    content: str,
    overlap_token_size: int = 128,
    max_token_size: int = 1024,
    tiktoken_model: str = "gpt-4o",
) -> list[dict[str, Any]]:
    """MiniRAG-flavoured `marker_chunking_func`.

    MiniRAG calls its `chunking_func` as `f(content, overlap_token_size, max_token_size, tiktoken_model)`
    (see `minirag.minirag.ainsert`), a different signature from LightRAG's `f(tokenizer, content, ...)`,
    and chunks by fixed TOKEN windows: chunk `k` = ``tokens[k*step : k*step+max]`` with
    ``step = max - overlap`` (see `minirag.operate.chunking_by_token_size`). We reproduce that windowing
    on the CLEAN text (markers stripped so the LLM only sees clean content, keeping the extraction cache
    valid) and map each `[[p|n]]` marker to its chunk via exact TOKEN->char offsets -- not a fragile
    text probe. Using each chunk's UNSTRIPPED char start as the lower bound also keeps the marker that
    sits in the whitespace right before a heading's text (offsets are computed on clean, then made
    relative to the chunk's stripped `content`). Extra keys like `__struct__` survive MiniRAG's insert
    path (it stores each chunk dict via ``{**dp, ...}``)."""
    clean, markers = _strip_markers(content)
    tokens = encode_string_by_tiktoken(clean, model_name=tiktoken_model)
    n = len(tokens)
    step = max(1, max_token_size - overlap_token_size)

    def _mark_spans(char_start: int, seg_start: int, stripped_len: int) -> list[list[int]]:
        spans: list[list[int]] = []
        for off, page, node in markers:
            if char_start <= off < seg_start:            # marker in the chunk's leading whitespace
                spans.append([0, page, node])
            elif seg_start <= off < seg_start + stripped_len:
                spans.append([off - seg_start, page, node])
        return spans

    chunks: list[dict[str, Any]] = []
    char_start = 0          # char offset (in clean) of tok_start, filled incrementally
    prev_tok = 0
    for index, tok_start in enumerate(range(0, max(1, n), step)):
        if tok_start > prev_tok:
            char_start += len(decode_tokens_by_tiktoken(tokens[prev_tok:tok_start],
                                                        model_name=tiktoken_model))
            prev_tok = tok_start
        raw = decode_tokens_by_tiktoken(tokens[tok_start:tok_start + max_token_size],
                                        model_name=tiktoken_model)
        stripped = raw.strip()
        seg_start = char_start + (len(raw) - len(raw.lstrip()))
        chunks.append({
            "tokens": min(max_token_size, n - tok_start),
            "content": stripped,
            "chunk_order_index": index,
            "__struct__": _mark_spans(char_start, seg_start, len(stripped)),
        })
        if tok_start + max_token_size >= n:
            break
    return chunks
