"""Deterministic validation/normalisation for formula and table assets.

The KG parser is deliberately permissive: PDF code listings are sometimes tagged as
equations and wide table headers may be collapsed into one OCR cell (such a table is reported invalid, never
guessed).  Slide generation must not treat those parser labels as proof.  This module creates the small, cached
``ValidatedAsset`` contract consumed by both KG loading and the packet builder.
"""
from __future__ import annotations

import math
import re
from typing import Iterable

from src.utils.config import Config


# an unbraced '_' before 2+ letters is a program identifier: LaTeX subscripts one character unless braced
_IDENTIFIER = re.compile(r"(?<![\\{])\b[A-Za-z]\w*_[A-Za-z]{2,}")
_CODE_SHAPE = re.compile(r"(?:\w+\s*=|\[[^\]]*:[^\]]*\])")
# slicing (x[:n], x[n:]) once glyph-level markup is flattened; mathematical notation has no such index
_SLICE = re.compile(r"\w\[(?::\d|\d+:)")
_LATEX_COMMAND = re.compile(r"\\(?:frac|sum|prod|sqrt|theta|alpha|beta|gamma|mathbf|boldsymbol|exp|log|nabla)\b")
_SEP_ROW = re.compile(r"^\s*\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+\s*\|?\s*$")


def _balanced(text: str, left: str, right: str) -> bool:
    depth = 0
    for index, char in enumerate(text):
        # ``\left\{`` and ``\right\}`` draw literal delimiters; they are not
        # LaTeX grouping braces and therefore do not need to balance here.
        if index and text[index - 1] == "\\":
            continue
        if char == left:
            depth += 1
        elif char == right:
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def validate_formula(latex: str) -> dict:
    """Return the formula validation record without mutating the source text."""
    raw = str(latex or "").strip()
    body = re.sub(r"^\s*\$\$|\$\$\s*$", "", raw).strip()
    plain = re.sub(r"\\[A-Za-z]+|[{}$]", " ", body)
    plain = re.sub(r"\s+", " ", plain).strip()
    flat = re.sub(r"\\[A-Za-z]+|[{}\s]", "", body)          # a listing OCR'd glyph by glyph, read back
    if (_IDENTIFIER.search(body) and _CODE_SHAPE.search(plain)) or _SLICE.search(flat):
        return {
            "original_type": "formula", "resolved_type": "code_block",
            "validation_status": "reclassified", "normalized_content": plain,
            "repair_source": "formula_code_heuristic",
            "validation_reason": "OCR equation contains program identifiers or slicing",
            "valid": False,
        }
    if not body or not _balanced(body, "{", "}") or not _balanced(body, "(", ")"):
        return {
            "original_type": "formula", "resolved_type": "formula",
            "validation_status": "invalid", "normalized_content": body,
            "repair_source": "none", "validation_reason": "unbalanced or empty LaTeX",
            "valid": False,
        }
    meaningful = bool(re.search(r"[=<>+\-*/^]", body) or _LATEX_COMMAND.search(body))
    if not meaningful:
        return {
            "original_type": "formula", "resolved_type": "formula",
            "validation_status": "invalid", "normalized_content": body,
            "repair_source": "none", "validation_reason": "no mathematical relation was detected",
            "valid": False,
        }
    return {
        "original_type": "formula", "resolved_type": "formula",
        "validation_status": "valid", "normalized_content": body,
        "repair_source": "deterministic_latex_normalizer", "validation_reason": "balanced mathematical expression",
        "valid": True,
    }


def _split_md_row(line: str) -> list[str]:
    line = str(line or "").strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith(r"\|"):
        line = line[:-1]
    return [c.replace(r"\|", "|").strip() for c in re.split(r"(?<!\\)\|", line)]


def _to_markdown(headers: list[str], rows: list[list[str]]) -> str:
    esc = lambda value: str(value or "").replace("|", r"\|").strip()
    return ("| " + " | ".join(map(esc, headers)) + " |\n"
            + "| " + " | ".join("---" for _ in headers) + " |\n"
            + "\n".join("| " + " | ".join(map(esc, row)) + " |" for row in rows))


def validate_table(markdown: str, caption: str = "") -> dict:
    lines = [line.strip() for line in str(markdown or "").splitlines() if line.strip()]
    data = [line for line in lines if not _SEP_ROW.match(line)]
    parsed = [_split_md_row(line) for line in data]
    if len(parsed) < 2:
        return {
            "original_type": "table", "resolved_type": "table", "validation_status": "invalid",
            "normalized_content": str(markdown or ""), "repair_source": "none",
            "validation_reason": "table has no header and data rows", "valid": False,
            "structured_table": {"headers": [], "rows": [], "caption": caption},
        }
    width = max(len(row) for row in parsed)
    parsed = [row + [""] * (width - len(row)) for row in parsed]
    headers, rows = parsed[0], parsed[1:]
    named = headers[1:]                          # a column of row labels often has no header of its own
    nonempty = sum(bool(h.strip()) for h in named)
    consistent = all(len(row) == len(headers) for row in rows)
    filled = any(cell.strip() for row in rows for cell in row)    # a piece a chunk boundary cut holds no data
    # a column with a header and no value: the reader shifted or lost it, and blanks would misstate the source
    whole = all(any(row[i].strip() for row in rows) for i, h in enumerate(headers) if h.strip()) if rows else False
    valid = bool(Config.TABLE_MIN_COLS <= len(headers) <= Config.TABLE_MAX_COLS and rows and consistent and filled
                 and whole and nonempty >= math.ceil(len(named) * Config.TABLE_HEADER_FILLED_MIN))
    status = "valid" if valid else "invalid"
    reason = "consistent headers and rows" if valid else "empty/collapsed headers, a named column without values or inconsistent column count"
    normalized = _to_markdown(headers, rows) if valid else str(markdown or "")
    return {
        "original_type": "table", "resolved_type": "table", "validation_status": status,
        "normalized_content": normalized,
        "repair_source": "none",
        "validation_reason": reason, "valid": valid,
        "structured_table": {"headers": headers if valid else [], "rows": rows if valid else [], "caption": caption},
    }


def holds_data(markdown: str) -> bool:
    """A table unit with at least one filled cell under its header row, valid or not."""
    rows = [_split_md_row(line) for line in str(markdown or "").splitlines() if line.strip() and not _SEP_ROW.match(line.strip())]
    return any(cell.strip() for row in rows[1:] for cell in row)


def validate_asset_record(row: dict) -> dict:
    out = dict(row)
    kind = str(out.get("type") or "")
    if kind == "formula":
        record = validate_formula(out.get("latex") or "")
        out["asset_validation"] = record
        if record["valid"]:
            out["latex"] = record["normalized_content"]
        return out
    if kind == "table":
        record = validate_table(out.get("table_body") or "", out.get("caption") or "")
        out["asset_validation"] = {k: v for k, v in record.items() if k != "structured_table"}
        out["structured_table"] = record["structured_table"]
        if record["valid"]:
            out["table_body"] = record["normalized_content"]
        return out
    out["asset_validation"] = {
        "original_type": kind, "resolved_type": kind, "validation_status": "valid",
        "normalized_content": out.get("code_body") or out.get("img_path") or "",
        "repair_source": "none", "validation_reason": "asset type requires no structural repair", "valid": True,
    }
    return out


def validate_asset_records(rows: Iterable[dict]) -> list[dict]:
    return [validate_asset_record(row) for row in rows or []]
