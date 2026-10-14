"""LaTeX a slide's formula box can always draw: matplotlib's mathtext has no environments (array, cases, matrix) and
rejects a few commands OCR produces, so a formula is rewritten into one or more mathtext lines before it is drawn.

- OCR repairs: `\\bfx` → `\\mathbf{x}`, size commands (`\\big(`) dropped, `\\cfrac` → `\\frac`, `\\operatorname *{x}` →
  `\\operatorname{x}`, `\\ =` → ` =`, empty groups dropped, digits split by OCR joined ("0 . 5" → "0.5").
- Environments: `cases` (or `\\left\\{ … \\right.`) become one line per case, keeping the left-hand side; a column vector
  is written as a transposed row `(a, b, c)^T`; a matrix keeps its rows joined by `;`; `aligned` becomes one line per row.
- Each line is checked with matplotlib's own parser; a command it does not know is dropped until the line parses.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from matplotlib.mathtext import MathTextParser

_ENVS = ("array", "cases", "matrix", "pmatrix", "bmatrix", "vmatrix", "aligned", "align", "gathered", "split")
_SIZE = re.compile(r"\\(?:big|Big|bigg|Bigg)[lrm]?(?![A-Za-z])")
_DELIMS = {"(": ")", "[": "]", "\\{": "\\}", "|": "|", "\\|": "\\|", "\\lbrace": "\\rbrace", ".": "."}


def _clean(tex: str) -> str:
    t = str(tex or "").strip()
    t = re.sub(r"^\$+|\$+$", "", t).strip()
    t = re.sub(r"\\bf([A-Za-z])(?![A-Za-z])", r"\\mathbf{\1}", t)
    t = _SIZE.sub("", t)
    t = re.sub(r"\\(?:scriptscript|script|display|text)style", " ", t)   # style switches, even glued to a letter
    t = re.sub(r"\\[ct]frac(?![A-Za-z])", r"\\frac", t)
    t = re.sub(r"\\operatorname\s*\*\s*", r"\\operatorname", t)
    t = t.replace("\\lVert", "\\|").replace("\\rVert", "\\|")
    t = re.sub(r"\\ (?=\s*[=<>+\-])", " ", t)
    t = re.sub(r"\{\s*\}(?!\s*[\^_])", "", t)
    for _ in range(3):                                 # OCR's x^{(i)}^{T} is (x^{(i)})^T: one superscript
        t = re.sub(r"\^\s*(\{[^{}]*\}|\w)\s*(?:\{\s*\})?\s*\^\s*(\{[^{}]*\}|\w)",
                   lambda m: "^{" + m.group(1).strip("{}") + " " + m.group(2).strip("{}") + "}", t)
    t = re.sub(r"[_^]\s*$", "", t)                     # a formula OCR cut right after a sub/superscript mark
    # words inside math lose their spaces: "0 \mathrm{if} p" would draw as "0ifp"
    t = re.sub(r"(?:\\[ ,;]\s*)*\\(?:mathrm|text|textrm)\s*\{\s*((?:if|for|otherwise|and|or|where|subject|minimize|maximize|s\.t\.)[^{}]*)\}"
               r"(?:\s*\\[ ,;])*", lambda m: r"\ \mathrm{" + m.group(1).strip() + r"}\ ", t)
    t = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", t)
    t = re.sub(r"(?<=\d)\s*,\s*(?=\d{3}(?!\d))", ",", t)
    t = re.sub(r"(?<=\d) +(?=\d)", r"\\quad ", t)       # numbers the source set apart stay apart (math drops spaces)
    return re.sub(r"\s+", " ", t).strip()


def _group_end(t: str, i: int) -> int:
    """Index just past the brace group starting at t[i] == '{'."""
    depth = 0
    for j in range(i, len(t)):
        if t[j] == "{" and (j == 0 or t[j - 1] != "\\"):
            depth += 1
        elif t[j] == "}" and (j == 0 or t[j - 1] != "\\"):
            depth -= 1
            if depth == 0:
                return j + 1
    return len(t)


def _split_top(body: str, sep: str) -> List[str]:
    """Split at `sep` outside braces (`\\\\` for rows, `&` for cells)."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(body):
        c = body[i]
        if c == "\\" and body.startswith("\\\\", i) and sep != "\\\\":
            i += 2
            continue
        if c == "{" and (i == 0 or body[i - 1] != "\\"):
            depth += 1
        elif c == "}" and (i == 0 or body[i - 1] != "\\"):
            depth -= 1
        elif depth == 0 and body.startswith(sep, i) and (sep != "&" or body[i - 1:i] != "\\"):
            parts.append(body[start:i])
            i += len(sep)
            start = i
            continue
        i += 1
    parts.append(body[start:])
    return parts


def _unwrap(cell: str) -> str:
    cell = cell.strip()
    while cell.startswith("{") and _group_end(cell, 0) == len(cell):
        cell = cell[1:-1].strip()
    return re.sub(r"^(?:\\q?quad\s*|\\[ ,;]\s*)+", "", cell)       # layout spacing of the source table


def _innermost(t: str) -> Optional[Tuple[str, int, int, int, int]]:
    """(env, begin start, body start, body end, end stop) of an environment holding no other environment."""
    pat = re.compile(r"\\begin\s*\{(" + "|".join(_ENVS) + r")\*?\}")
    found = None
    for m in pat.finditer(t):
        env = m.group(1)
        body_start = m.end()
        if env == "array":                                   # skip the column spec
            k = body_start
            while k < len(t) and t[k] == " ":
                k += 1
            if k < len(t) and t[k] == "{":
                body_start = _group_end(t, k)
        end = re.compile(r"\\end\s*\{" + env + r"\*?\}").search(t, body_start)
        if not end:
            continue
        if not pat.search(t, body_start, end.start()):
            found = (env, m.start(), body_start, end.start(), end.end())
            break
    return found


def _delims(t: str, start: int, stop: int) -> Tuple[str, int, str, int]:
    """The `\\left<d>` just before `start` and `\\right<d>` just after `stop` (empty when absent), with their spans."""
    left = re.search(r"\\left\s*(\\\{|\\lbrace|\\\||[\(\[\|\.])\s*$", t[:start])
    right = re.match(r"\s*\\right\s*(\\\}|\\rbrace|\\\||[\)\]\|\.])", t[stop:])
    return (left.group(1) if left else "", left.start() if left else start,
            right.group(1) if right else "", stop + right.end() if right else stop)


def _rows(body: str) -> List[List[str]]:
    rows = [[_unwrap(c) for c in _split_top(r, "&")] for r in _split_top(body, "\\\\")]
    return [r for r in rows if any(c for c in r)]


def to_mathtext(latex: str) -> List[str]:
    """The formula as LaTeX lines without environments (several when it lists cases or aligned rows)."""
    t = _clean(latex)
    lines: Optional[List[str]] = None
    for _ in range(12):
        found = _innermost(t)
        if not found:
            break
        env, b0, s0, s1, e1 = found
        rows = _rows(t[s0:s1])
        left, l0, right, r1 = _delims(t, b0, e1)
        cases = env == "cases" or (left in ("\\{", "\\lbrace") and right in (".", ""))
        stacked = env in ("aligned", "align", "gathered", "split") or (env == "array" and not left)
        if cases or stacked:
            head, tail = t[:l0 if left else b0].strip(), t[r1 if right else e1:].strip()
            # later rows repeat only the defined term ("sign(θ_i) =" of "... where sign(θ_i) = {cases}")
            parts = re.split(r"\\quad|\\ \\mathrm\{\s*where[^{}]*\}\\ ", head)
            short = parts[-1].strip() if len(head) > 60 and len(parts) > 1 else head
            sep = r" \quad " if cases else " "
            lines = [f"{head if n == 0 else short} {sep.join(c for c in r if c)} "
                     f"{tail if n == len(rows) - 1 else ''}".strip() for n, r in enumerate(rows)]
            t = ""
            break
        if all(len(r) == 1 for r in rows) and len(rows) > 1:
            entries = [r[0] for r in rows]
            inner = (r";\ " if any("," in e for e in entries) else r",\ ").join(entries)   # 1,416 is one entry
            after = t[r1 if right else e1:]
            transposed = re.match(r"\s*\^\s*(\{\s*T\s*\}|T)", after)
            op, cl = ("\\left" + (left if left and left != "." else "("), "\\right" + (right if right and right != "." else ")"))
            vec = f"{op} {inner} {cl}"
            if transposed:                                   # a column already transposed is a plain row
                after = after[transposed.end():]
            else:
                vec += "^{T}"
            t = t[:l0 if left else b0] + vec + after
        else:
            inner = r";\ ".join(r",\ ".join(c for c in r) for r in rows)
            op = "\\left" + left if left and left != "." else "\\left("
            cl = "\\right" + right if right and right != "." else "\\right)"
            t = t[:l0 if left else b0] + f"{op} {inner} {cl}" + t[r1 if right else e1:]
    out = lines if lines is not None else [t]
    return [line for line in (_balance(re.sub(r"\s+", " ", line).strip()) for line in out) if line]


def mathtext_lines(latex: str) -> Optional[List[str]]:
    """Lines matplotlib draws, or None when any line cannot be drawn (the box then shows text, never a partial image)."""
    lines = [_parseable(line) for line in to_mathtext(latex)]
    return lines if lines and all(lines) else None


def renderable(line: str) -> bool:
    return _parse_error(line) is None


def _parse_error(line: str) -> Optional[str]:
    try:
        MathTextParser("path").parse(f"${line}$")
        return None
    except Exception as exc:
        return str(exc) or "error"


def _balance(line: str) -> str:
    """Drop braces that close nothing or are never closed (a line cut out of a larger formula keeps its own groups)."""
    keep, opened = list(line), []
    for i, c in enumerate(line):
        if c in "{}" and i and line[i - 1] == "\\":
            continue
        if c == "{":
            opened.append(i)
        elif c == "}":
            if opened:
                opened.pop()
            else:
                keep[i] = ""
    for i in opened:
        keep[i] = ""
    return "".join(keep).strip()


def _parseable(line: str) -> str:
    """The line when mathtext parses it; else with the commands it rejects removed, then sizing delimiters freed
    ('' when still not drawable)."""
    for _ in range(8):
        error = _parse_error(line) if line else None
        if error is None:
            return line
        unknown = re.search(r"Unknown symbol:\s*(\\[A-Za-z]+)", error)
        if unknown:
            line = re.sub(re.escape(unknown.group(1)) + r"(?![A-Za-z])", " ", line)
            continue
        freed = re.sub(r"\\(?:left|right)\s*\.", " ", line)
        freed = re.sub(r"\\(?:left|right)\s*(?=\\\{|\\\}|\\\||[\(\)\[\]\|])", "", freed)
        freed = _balance(freed)
        if freed == line:
            return ""
        line = freed
    return line if renderable(line) else ""
