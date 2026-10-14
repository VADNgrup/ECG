"""Content QA's code checks on a written bullet against the sentences of its slide: what it cites (anchored or completed
when its cites miss the sentences it rests on), numbers its sentences do not state and words they hardly hold. How a
bullet is worded is the prompts', not a check. `rows` are the slide's citable sentences in source order,
`[(sentence id, text), ...]`."""
from __future__ import annotations

import re
from typing import Dict, List, Sequence, Tuple

from src.utils.bullet_text import stems, support_ratio
from src.utils.config import Config

NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*")

Rows = Sequence[Tuple[str, str]]


def citable(built: dict) -> List[Tuple[str, str]]:
    """A slide's citable sentences in source order: its pool, then its formulas and listings."""
    return ([(s, t) for row in built["pool"].values() for s, t in row["sentences"]]
            + [(a["sid"], a["text"]) for k in ("formula", "code") for a in built["assets"].get(k) or []])


def text_of(rows: Rows, cites: Sequence[str]) -> str:
    return " ".join(t for s, t in rows if s in set(cites))


def unstated_numbers(text: str, source: str) -> List[str]:
    return [x for x in NUMBER.findall(text) if x not in source.replace(" ", "")
            and x.replace(",", "") not in source.replace(",", "")]


def anchor(rows: Rows, text: str) -> List[str]:
    """The slide's sentences a bullet that cites nothing rests on (CITE_ANCHOR_MIN of its content words must occur in
    them); [] when none does."""
    ranked = sorted(rows, key=lambda r: -support_ratio(text, r[1]))[:2]
    if not ranked or support_ratio(text, ranked[0][1]) < Config.CITE_ANCHOR_MIN:
        return []
    best = [ranked[0]]
    if len(ranked) > 1 and support_ratio(text, ranked[0][1] + " " + ranked[1][1]) > support_ratio(text, ranked[0][1]):
        best.append(ranked[1])
    return [s for s, _ in best]


def complete(rows: Rows, text: str, cites: Sequence[str]) -> List[str]:
    """A bullet's cites widened to the slide's sentences it also rests on: when its cited sentences hold less than
    CITE_SUPPORT_MIN of its content words but the slide's sentences hold that much, the sentences adding the most
    missing words are added (each must add one; at most CITE_EXTRA_MAX). Otherwise the cites stay as they are."""
    cites = list(cites)
    if not cites:
        return cites
    want, have = stems(text), stems(text_of(rows, cites))
    if not want or len(want & have) / len(want) >= Config.CITE_SUPPORT_MIN:
        return cites
    rest = [(s, stems(t)) for s, t in rows if s not in cites]
    if len(want & (have | set().union(*(w for _, w in rest)))) / len(want) < Config.CITE_SUPPORT_MIN:
        return cites                                   # the slide does not say it either: the review judges the claim
    added = []
    for _ in range(Config.CITE_EXTRA_MAX):
        missing = want - have
        if len(want & have) / len(want) >= Config.CITE_SUPPORT_MIN or not rest:
            break
        best = max(rest, key=lambda r: len(missing & r[1]))
        if not missing & best[1]:
            break
        added.append(best[0])
        have |= best[1]
        rest.remove(best)
    order = {s: n for n, (s, _) in enumerate(rows)}
    return sorted(cites + added, key=lambda s: order.get(s, -1))


def with_numbers(rows: Rows, text: str, cites: Sequence[str]) -> List[str]:
    """A bullet stating numbers its cited sentences do not also cites the slide's sentences or formulas that state them
    as numbers of their own (a formula holds the numbers its lead-in sentence leaves out): each added one states the
    most of the bullet's numbers, at least one still missing; at most CITE_EXTRA_MAX."""
    cites = list(cites)
    if not cites:
        return cites
    said = set(NUMBER.findall(text))
    missing = set(unstated_numbers(text, text_of(rows, cites)))
    added = []
    while missing and len(added) < Config.CITE_EXTRA_MAX:
        rest = [(s, set(NUMBER.findall(t))) for s, t in rows if s not in cites and s not in added]
        best = max(rest, key=lambda r: (len(missing & r[1]), len(said & r[1])), default=None)
        if best is None or not missing & best[1]:
            break
        added.append(best[0])
        missing -= best[1]
    if not added:
        return cites
    order = {s: n for n, (s, _) in enumerate(rows)}
    return sorted(cites + added, key=lambda s: order.get(s, -1))


def with_own_paragraph(rows: Rows, text: str, cites: Sequence[str], paragraph_of: Dict[str, str]) -> List[str]:
    """A bullet whose cited sentences hold under LANGUAGE_DRIFT_SUPPORT of its words also cites the sentences of their
    paragraphs that add words of it: page reading cuts one entry (names, title, place) into several sentences."""
    cites = list(cites)
    if support_ratio(text, text_of(rows, cites)) >= Config.LANGUAGE_DRIFT_SUPPORT:
        return cites
    own = {paragraph_of.get(s) for s in cites} - {None}
    want, have = stems(text), stems(text_of(rows, cites))
    added = {s for s, t in rows if s not in cites and paragraph_of.get(s) in own and (want - have) & stems(t)}
    return [s for s, _ in rows if s in added or s in cites] if added else cites


_MATH_GAPS = re.compile(r"[\s$,.;:]+")


def copied_formula(text: str, formulas: Sequence[str]) -> str:
    """The formula of the slide a bullet is a copy of (its text made almost wholly of that formula's, at least
    CITE_SUPPORT_MIN of it, spaces, delimiters and list punctuation aside); "" when none."""
    said = _MATH_GAPS.sub("", text)
    for f in formulas:
        shown = _MATH_GAPS.sub("", f)
        if shown and said and shown in said and len(shown) >= Config.CITE_SUPPORT_MIN * len(said):
            return " ".join(f.replace("$$", " ").split())
    return ""


def issues(text: str, cites: Sequence[str], rows: Rows, formulas: Sequence[str] = ()) -> List[Dict[str, str]]:
    """What the code finds wrong with a bullet that cites `cites`: a copy of a formula the slide draws, numbers its
    sentences do not state, words its sentences hardly hold. Each `{kind, claim, reason}` as the review writes it."""
    source = text_of(rows, cites)
    out = []
    copy = copied_formula(text, formulas)
    if copy:
        out.append({"kind": "formula", "claim": text,
                    "reason": f"it copies the formula {copy}, which the slide draws: say what it computes or states "
                              f"and what its terms are, in words, citing it."})
    numbers = unstated_numbers(text, source)
    if numbers:
        out.append({"kind": "wrong", "claim": numbers[0],
                    "reason": f"its sentences do not state the number(s) {', '.join(numbers)}."})
    if support_ratio(text, source) < Config.LANGUAGE_DRIFT_SUPPORT:
        out.append({"kind": "unlike", "claim": text,
                    "reason": "its sentences hold few of its words: write only what they state, or nothing if they do not state its point."})
    return out


def acceptable(text: str, source: str) -> bool:
    """A rewritten bullet these checks accept: some text and no number its sources lack."""
    return bool(text) and not unstated_numbers(text, source)
