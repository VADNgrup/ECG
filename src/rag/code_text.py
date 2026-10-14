"""Is a line programming syntax or prose? Decided from shape alone (operator/identifier density), never a word list,
so it holds for any language. Shared by the KG build and the vision-OCR page reader; the packet assembly asks whether
an extracted formula or listing is whole enough to draw."""
import re
from typing import List

_CODE_END_RE = re.compile(r"[)\]}:]$")
_CODE_OPEN_RE = re.compile(r"[(\[=]")
_CODE_TOKEN_RE = re.compile(r"[^\W\d_][\w]*_[\w]*|[^\W\d_][\w]*\.[^\W\d_][\w]*|[^\W\d_][\w]*\(")
_PLAIN_WORD_RE = re.compile(r"^[^\W\d_]+(?:[-'\u2019][^\W\d_]+)*$")
_MATH_FUNCS = {"exp", "log", "ln", "sin", "cos", "tan", "tanh", "max", "min", "argmax", "argmin", "softmax", "sigmoid",
               "sgn", "sign", "det", "tr", "lim", "sup", "inf", "arg", "where", "and", "for", "if", "otherwise"}
# programming syntax in ASCII: snake_case, attribute access, a call of a named function, a prompt (Python or shell), a
# statement keyword
_PROGRAM = re.compile(r"[A-Za-z_]\w*_\w+|[A-Za-z_]\w*\.[A-Za-z_]\w*|>>>|^\s*\$ |^\s*(?:import|from|def|class|return|print)\b|"
                      r"\b(?!(?:" + "|".join(sorted(_MATH_FUNCS)) + r")\()[A-Za-z_]{2,}\w*\(")


def _word_ratio(line: str) -> float:
    """Share of whitespace tokens that are plain words. Code lines score low, prose high."""
    tokens = [t.strip(".,;:!?\"'()[]") for t in line.split()]
    tokens = [t for t in tokens if t]
    return sum(1 for t in tokens if _PLAIN_WORD_RE.match(t)) / len(tokens) if tokens else 1.0


def _has_code_line(text: str) -> bool:
    """Whether any line carries programming syntax rather than reading as prose."""
    return any(ln.strip() and (_word_ratio(ln) < 0.5
                               or (_CODE_END_RE.search(ln.strip()) and _CODE_OPEN_RE.search(ln)
                                   and _CODE_TOKEN_RE.search(ln)))
               for ln in text.splitlines())


_DANGLING = re.compile(r"(?:[=+\-−×·/∑∏∫←,]|\\[A-Za-z]+)\s*$")    # ends on an operator or a bare \command
_PIECES = (("⎛⎜⎝", "⎞⎟⎠"), ("⎧⎨⎩", "⎫⎬⎭"), ("⎡⎢⎣", "⎤⎥⎦"))


_RELATION = re.compile(r"[=<>≤≥≠←→≈∝∈⇒]|\\(?:leq|geq|neq|approx|leftarrow|rightarrow|in)\b")
_ASIDE = re.compile(r"^\(\s*[A-Za-z]{3,}\b.*\)\W*$")                    # "(with L = 1, 2, ..., N/2)."
_INDEX_LINE = re.compile(r",\s*\d+(?:\s*[-–]\s*\d+)?\s*$")              # "array_split(), 219"
_LATEX_COMMAND = re.compile(r"\\[A-Za-z]+")


def formula_body(text: str) -> str:
    return re.sub(r"^\s*\$\$|\$\$\s*$", "", text or "").strip()


def defined_names(text: str) -> List[str]:
    """The words a formula defines, on the left of its relation (`recall` of `recall = TP/(TP+FN)`); [] for symbols."""
    left = _RELATION.split(formula_body(text), maxsplit=1)[0]
    return [w.casefold() for w in re.findall(r"[^\W\d_]{3,}", _LATEX_COMMAND.sub(" ", left))]


def cut_off(text: str) -> bool:
    """A formula block that stops mid-expression (on an operator or '='): the extraction cut it, the next block goes on."""
    return bool(_DANGLING.search(formula_body(text)))


def joined_formula(texts) -> str:
    """Formula blocks the extraction cut apart, read as the one formula they are."""
    return "$$ " + " ".join(formula_body(t) for t in texts) + " $$"


def drawable_formula(text: str) -> bool:
    """A formula a slide can draw: a relation (=, <-, <=, ...), complete (not ending on an operator or '='), its bracket
    pieces closed, not a parenthetical aside. An extraction that broke the formula apart, or figure labels read as
    mathematics, are not drawn; the slide explains them in words."""
    body = formula_body(text)
    if not body or _DANGLING.search(body) or not _RELATION.search(body) or _ASIDE.match(body):
        return False
    return all(sum(body.count(ch) for ch in a) == sum(body.count(ch) for ch in b) for a, b in _PIECES)


def drawable_code(text: str) -> bool:
    """A listing a slide can draw: at least one line of real programming syntax, LaTeX commands aside; a fenced index
    (lines ending on page numbers), table, bullet list or formula is not code."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if lines and sum(bool(_INDEX_LINE.search(ln)) for ln in lines) * 2 >= len(lines):
        return False
    return any(_PROGRAM.search(_LATEX_COMMAND.sub(" ", ln)) for ln in lines)
