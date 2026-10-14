"""What a source sentence does for a listener: define, explain, give steps, show an example, compare, warn.

A lecture is not a list of facts: a section mixes definitions with mechanisms, procedures, examples and caveats, and
the slide layout should follow that mix. The cues are surface markers (English and Vietnamese); anything without a
clear marker is `explanation`.
"""
import re
from typing import Iterable, List

ROLES = ("definition", "procedure", "example", "comparison", "caveat", "explanation", "code", "formula")

_CUES = [
    ("example", r"\b(for (example|instance)|e\.g\.|such as|suppose|imagine|consider|let'?s say|ví dụ|chẳng hạn)\b"),
    ("comparison", r"\b(whereas|unlike|compared (to|with)|in contrast|on the other hand|versus|vs\.?|rather than|"
                   r"better than|worse than|faster than|slower than|so với|ngược lại|trong khi đó)\b"),
    ("caveat", r"\b(however|but note|note that|beware|warning|caution|be careful|make sure|keep in mind|unless|"
               r"only if|does not|cannot|won'?t|lưu ý|tuy nhiên|cẩn thận)\b"),
    ("procedure", r"^(first|then|next|finally|second|third|to \w+ (a|an|the)\b|step \d|bước \d|đầu tiên|sau đó|cuối cùng)\b|"
                  r"^\d+[.)]\s"),
    ("definition", r"\b(is (a|an|the) (kind|type|form|way|measure|method|technique|model|algorithm|process|set)|"
                   r"refers to|is defined as|is called|are called|is known as|means that|stands for|là một|được gọi là|"
                   r"được định nghĩa)\b"),
]
_COMPILED = [(role, re.compile(pattern, re.IGNORECASE)) for role, pattern in _CUES]


def role_of(text: str, kind: str = "text") -> str:
    """The role of one sentence; `kind` of the source unit decides for code and formulas."""
    if kind == "code":
        return "code"
    if kind == "formula":
        return "formula"
    clean = (text or "").strip()
    for role, pattern in _COMPILED:
        if pattern.search(clean):
            return role
    return "explanation"


def dominant_role(roles: Iterable[str]) -> str:
    """The role that shapes a slide: a procedure or comparison run wins over plain explanation."""
    counts: dict = {}
    for role in roles:
        counts[role] = counts.get(role, 0) + 1
    if not counts:
        return "explanation"
    ranked: List[str] = sorted(counts, key=lambda r: (-counts[r], ROLES.index(r) if r in ROLES else 99))
    top = ranked[0]
    if top == "explanation" and len(ranked) > 1 and counts[ranked[1]] * 2 >= counts[top]:
        return ranked[1]
    return top
