"""Text checks shared by the Bullet Writer and Content QA: cross-references, source support, redundancy.

Language-agnostic: reference labels ("Figure", "Hình", "图"...) are learned from the document (`src.utils.lang`), and
content words are compared as stems (word prefixes) or character bigrams, never through per-language word lists.
"""
import regex as re

from src.utils import lang


def rejoin_splits(text: str, vocabulary) -> str:
    """Rejoin a word the extraction split in two ("signifi cantly"): two adjacent words are joined when the joined
    form is a word of the document and a half is not one. With document IDF weights, a half that exists only because
    of such splits ("chap" of "Chap ter") is far rarer than the whole word and counts as a fragment."""
    if not vocabulary:
        return text
    weights = vocabulary if isinstance(vocabulary, dict) else None

    def fragment(part: str, whole: str) -> bool:
        if part not in vocabulary:
            return True
        return weights is not None and weights[part] > weights[whole] + 1.0

    word = re.compile(r"^\p{L}[\p{L}\p{M}]*$")
    tokens = (text or "").split(" ")
    out, i = [], 0
    while i < len(tokens):                 # every adjacent pair is considered, left to right
        left = tokens[i]
        right = tokens[i + 1] if i + 1 < len(tokens) else ""
        whole = (left + right).casefold()
        if (right and word.match(left) and word.match(right) and whole in vocabulary
                and (fragment(left.casefold(), whole) or fragment(right.casefold(), whole))):
            out.append(left + right)
            i += 2
            continue
        out.append(left)
        i += 1
    return " ".join(out)


def _ref() -> str:
    return lang.reference_pattern().pattern


def has_crossref(text: str) -> bool:
    return bool(re.search(_ref(), text or "", re.IGNORECASE))


def strip_crossrefs(text: str) -> str:
    """Remove numbered pointers to figures/tables/equations/chapters: a parenthesis holding one ("(Figure 1-5)") and a
    comma-set trailing clause that ends in one (", as shown in Equation 1-1")."""
    ref = _ref()
    paren = re.compile(rf"\s*[(（](?:[^()（）]*?\b)?{ref}[^()（）]*[)）]", re.IGNORECASE)
    tail = re.compile(rf"\s*[,，]\s*(?:\p{{L}}+\s+){{0,3}}{ref}(?=\s*[.!?。]|\s*$)", re.IGNORECASE)
    out = tail.sub("", paren.sub("", text or ""))
    out = re.sub(r"\s+([.,;:])", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out).strip(" ,;")
    return out if not out or out[-1] in ".!?…。" or not (text or "").rstrip().endswith((".", "!", "?", "。")) else out + "."


def stems(text: str) -> set:
    """Content-word stems: 6-letter word prefixes for spaced scripts (>=4 letters), character bigrams for CJK/Thai."""
    out = set()
    for term in lang.terms(text):
        if lang._NO_SPACE_SCRIPTS.search(term):
            out.add(term)
        elif len(term) >= 4 and not term.isdigit():
            out.add(term[:6])
    return out


def support_ratio(bullet: str, source: str) -> float:
    """Share of the bullet's content stems that occur in its source sentences."""
    words = stems(bullet)
    return 1.0 if not words else len(words & stems(source)) / len(words)


def overlap(a: str, b: str) -> float:
    """Jaccard overlap of content stems: how much two texts say the same thing."""
    x, y = stems(a), stems(b)
    return len(x & y) / len(x | y) if x | y else 0.0
