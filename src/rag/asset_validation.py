"""Deterministic validation/normalisation for formula and table assets.

The KG parser is deliberately permissive: PDF code listings are sometimes tagged as
equations and wide table headers may be collapsed into one OCR cell.  Slide generation
must not treat those parser labels as proof.  This module creates the small, cached
``ValidatedAsset`` contract consumed by both KG loading and the packet builder.
"""
from __future__ import annotations

import math
import re
from typing import Iterable


_CODE_WORDS = re.compile(r"(?:^|[_\W])(?:train|test|shuffle|dtype|import|from|predict|sklearn|numpy|np)(?=$|[_\W])", re.I)
_CODE_SHAPE = re.compile(r"(?:\w+\s*=|\[[^\]]*:[^\]]*\]|\w+_\w+)")
_SPACED_CODE_WORD = re.compile(r"(?:t\s+r\s+a\s+i\s+n|t\s+e\s+s\s+t)", re.I)
_LATEX_COMMAND = re.compile(r"\\(?:frac|sum|prod|sqrt|theta|alpha|beta|gamma|mathbf|boldsymbol|exp|log|nabla)\b")
_SEP_ROW = re.compile(r"^\s*\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+\s*\|?\s*$")
_KNOWN_HEADERS = (
    "Algorithm", "Large m", "Out-of-core support", "Large n", "Hyperparams",
    "Scaling required", "Scikit-Learn",
)
_SVM_HEADERS = ("Class", "Time complexity", "Out-of-core support", "Scaling required", "Kernel trick")


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
    code_identifier = bool(_CODE_WORDS.search(plain) or _SPACED_CODE_WORD.search(body))
    code_like = bool(code_identifier and _CODE_SHAPE.search(plain))
    # MinerU's failed code-as-math OCR emits long runs of \mathsf single glyphs
    # wrapped in nested arrays.  Genuine equations may use \mathsf, but not dozens
    # of glyph-level commands together with train/test slicing tokens.
    glyph_mathsf = len(re.findall(r"\\mathsf\s*\{\s*[A-Za-z]\s*\}", body))
    if code_like or (glyph_mathsf >= 8 and code_identifier):
        return {
            "original_type": "formula", "resolved_type": "code_block",
            "validation_status": "reclassified", "normalized_content": plain,
            "repair_source": "formula_code_heuristic",
            "validation_reason": "OCR equation contains assignment/slicing and train/test identifiers",
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


def _repair_known_collapsed_header(headers: list[str], rows: list[list[str]]) -> tuple[list[str], list[list[str]], bool]:
    """Repair the common OCR shape where all seven Table 4-1 headers land in one cell.

    The repair is evidence-preserving: every recovered label must occur verbatim in the
    collapsed header and the row shift is allowed only for a trailing Fast/Slow value.
    """
    text = " ".join(h for h in headers if h).strip()
    schema = next((candidate for candidate in (_KNOWN_HEADERS, _SVM_HEADERS)
                   if len(headers) == len(candidate)
                   and all(h.casefold() in text.casefold() for h in candidate)), None)
    if schema is None:
        return headers, rows, False
    fixed_rows = []
    for row in rows:
        row = list(row) + [""] * (len(headers) - len(row))
        if schema == _KNOWN_HEADERS:
            match = re.match(r"^(.*\S)\s+(Fast|Slow)$", row[0], re.I)
        else:
            match = re.match(r"^(SGDClassifier)\s+(.+)$", row[0], re.I)
        if match and not row[1]:
            row[0], row[1] = match.group(1), match.group(2)
        fixed_rows.append(row[:len(headers)])
    return list(schema), fixed_rows, True


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
    headers, rows, repaired = _repair_known_collapsed_header(headers, rows)
    nonempty = sum(bool(h.strip()) for h in headers)
    consistent = all(len(row) == len(headers) for row in rows)
    valid = bool(2 <= len(headers) <= 12 and rows and consistent and nonempty >= math.ceil(len(headers) * 0.75))
    status = "repaired" if valid and repaired else "valid" if valid else "invalid"
    reason = ("collapsed header reconstructed from its source labels" if repaired else
              "consistent headers and rows" if valid else
              "empty/collapsed headers or inconsistent column count")
    normalized = _to_markdown(headers, rows) if valid else str(markdown or "")
    return {
        "original_type": "table", "resolved_type": "table", "validation_status": status,
        "normalized_content": normalized,
        "repair_source": "deterministic_header_reconstruction" if repaired else "none",
        "validation_reason": reason, "valid": valid,
        "structured_table": {"headers": headers if valid else [], "rows": rows if valid else [], "caption": caption},
    }


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
