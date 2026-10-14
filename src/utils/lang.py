"""Language-agnostic text helpers: no word lists of any language.

- sentence splitting that works for cased scripts (Latin, Cyrillic, Greek), uncased ones (Arabic, Hebrew, Indic, Thai)
  and CJK;
- a length measure in "words" whatever the script (CJK and Thai have no spaces);
- figure / table / chapter labels LEARNED FROM THE DOCUMENT (captions and headings such as "Figure 4-20." or
  "Hình 4.2:"), used wherever the pipeline must recognise or strip a numbered reference;
- salient terms of a sentence (for the "keeps its key terms" check), by IDF inside the document.
"""
import json
import math
from collections import Counter
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import regex as re

_TERMINATORS = ".!?。！？؟…।։۔"
_CJK_END = "。！？"
_OPEN_OR_QUOTE = "\"'“‘«(["
_NO_SPACE_SCRIPTS = re.compile(r"[\p{Han}\p{Hiragana}\p{Katakana}\p{Thai}\p{Lao}\p{Khmer}\p{Myanmar}]")
_WORD = re.compile(r"[\p{L}\p{M}\p{N}]+")
_SCRIPTS = ("Han", "Hiragana", "Katakana", "Hangul", "Thai", "Arabic", "Hebrew", "Devanagari", "Cyrillic", "Greek", "Latin")


def _cased(ch: str) -> bool:
    return ch.lower() != ch.upper()


def split_sentences(text: str, min_chars: int = 12) -> List[str]:
    """Sentences of `text`. A terminator ends a sentence when what follows starts a new one: an uppercase letter, a
    digit, a quote/bracket, a letter of an UNCASED script, or (for the CJK full stops) anything."""
    text = re.sub(r"\s+", " ", text or "").strip()
    out, start, n = [], 0, len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch in _TERMINATORS:
            j = i + 1
            while j < n and text[j] in _TERMINATORS + "\"'”’»)]":
                j += 1
            if ch in _CJK_END or j >= n:
                cut = True
            elif text[j] == " " and j + 1 < n:
                nxt = text[j + 1]
                prev = re.search(r"(\S+?)[.]+$", text[start:i + 1])
                word = prev.group(1) if prev else ""
                abbreviation = ch == "." and (len(word) == 1 or "." in word
                                              or (nxt.isdigit() and len(word) <= 4 and word.islower()))
                cut = not abbreviation and (nxt.isupper() or nxt.isdigit() or nxt in _OPEN_OR_QUOTE
                                            or (nxt.isalpha() and not _cased(nxt)))
            else:
                cut = False
            if cut:
                out.append(text[start:j].strip())
                start = j
            i = j
        else:
            i += 1
    if start < n:
        out.append(text[start:].strip())
    return [s for s in out if len(s) >= min_chars]


def length_units(text: str) -> int:
    """Length in word-like units: whitespace-separated words, with each run of a no-space script counted by 2 characters."""
    units = 0
    for word in _WORD.findall(text or ""):
        if _NO_SPACE_SCRIPTS.search(word):
            units += max(1, len(word) // 2)
        else:
            units += 1
    return units


def script_profile(text: str) -> dict:
    """Share of letters per script (top 3), e.g. {"Latin": 0.98}. Reporting/diagnostics only."""
    counts = Counter()
    for ch in text or "":
        if ch.isalpha():
            counts[next((name for name in _SCRIPTS if re.match(rf"\p{{{name}}}", ch)), "Other")] += 1
    total = sum(counts.values()) or 1
    return {k: round(v / total, 3) for k, v in counts.most_common(3)}


def terms(text: str) -> List[str]:
    """Lower-cased terms of a text: words for spaced scripts, character bigrams inside no-space-script runs."""
    out: List[str] = []
    for word in _WORD.findall((text or "").casefold()):
        if _NO_SPACE_SCRIPTS.search(word):
            out.extend(word[k:k + 2] for k in range(max(1, len(word) - 1)))
        else:
            out.append(word)
    return out


def salient_terms(sentence: str, idf: dict, top: int = 6) -> List[str]:
    """The terms of `sentence` that carry its meaning: numbers, then the highest-IDF terms (rare inside the document).
    No stopword list: frequent function words have low IDF by construction."""
    seen, ranked = set(), []
    for term in terms(sentence):
        if term in seen or len(term) < 2 and not term.isdigit():
            continue
        seen.add(term)
        ranked.append((2.0 if any(c.isdigit() for c in term) else idf.get(term, 1.0), term))
    return [t for _, t in sorted(ranked, key=lambda x: -x[0])[:top]]


def build_idf(sentences: Iterable[str]) -> dict:
    df, n = Counter(), 0
    for s in sentences:
        n += 1
        df.update(set(terms(s)))
    return {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}


# ── document-learned labels ───────────────────────────────────────────────────────────────────────────────────────────

_LEAD_LABEL = re.compile(r"^\s*(\p{L}[\p{L}\p{M}.\-]*(?:\s\p{L}[\p{L}\p{M}.\-]*)?)\s*(\d+(?:[-.]\d+)*)\s*[.:：)\-–—]?(?:\s|$)")
_NUMBERED = re.compile(r"\d+(?:[-.]\d+)*")


def learn_labels(lines: Sequence[str], min_count: int = 2) -> List[str]:
    """Labels ("Figure", "Table", "Chapter", "Hình", "图", ...) = the word(s) that open captions and headings right before a
    number. Only labels that repeat are kept, so a heading such as "Stage 1" does not make "Stage" a label."""
    counts = Counter()
    for line in lines:
        m = _LEAD_LABEL.match(line or "")
        if m:
            counts[m.group(1).strip().rstrip(".").casefold()] += 1
    return sorted(label for label, c in counts.items() if c >= min_count and len(label) <= 24)


def labels_for_kb(kb_dir: Path, refresh: bool = False) -> List[str]:
    """`label_vocab.json` of a KB (learned once from its figure/table captions and its headings)."""
    path = Path(kb_dir) / "label_vocab.json"
    if path.exists() and not refresh:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    lines: List[str] = []
    try:
        for asset in json.loads((Path(kb_dir) / "asset_headings.json").read_text(encoding="utf-8")):
            lines.append(str(asset.get("real_caption") or asset.get("caption") or ""))
        index = json.loads((Path(kb_dir) / "structural_index.json").read_text(encoding="utf-8"))
        lines.extend(str(h.get("title", "")) for h in (index.get("headings") or {}).values())
    except (OSError, ValueError):
        pass
    labels = learn_labels(lines)
    try:
        path.write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return labels


_ACTIVE: List[str] = []


def use_labels(labels: Optional[Sequence[str]]) -> None:
    """Labels of the document being processed (set once per run by the Packet Builder; packets carry them on)."""
    global _ACTIVE
    if labels is not None:
        _ACTIVE = [str(l) for l in labels]


def active_labels() -> List[str]:
    return list(_ACTIVE)


def reference_pattern(labels: Optional[Sequence[str]] = None) -> "re.Pattern":
    """Regex for a numbered reference such as "Figure 4-20", "Hình 4.2", "图 3". With no learned label it accepts any
    word directly followed by a dash/dot-numbered label (a caption number), which is what real references look like."""
    labels = list(labels if labels is not None else _ACTIVE)
    if labels:
        words = "|".join(sorted((re.escape(l) for l in labels), key=len, reverse=True))
        return re.compile(rf"(?:{words})\.?\s*\d+(?:[-.]\d+)*", re.IGNORECASE)
    return re.compile(r"\p{L}[\p{L}\p{M}]{0,15}\.?\s*\d+[-.]\d+")


def ref_numbers(text: str, labels: Optional[Sequence[str]] = None) -> List[str]:
    """Numbers of the references in `text` ("Figure 4-20 and Table 3.1" -> ["4-20", "3.1"])."""
    return [_NUMBERED.findall(m)[-1] for m in reference_pattern(labels).findall(text or "")]


def caption_text_pattern(labels: Optional[Sequence[str]] = None) -> "re.Pattern":
    """A caption with its text on one line: reference, optional stop, then 4-90 characters."""
    return re.compile(rf"{reference_pattern(labels).pattern}[.:：]?\s+[^\n]{{4,90}}", re.IGNORECASE)


def caption_pattern(labels: Optional[Sequence[str]] = None) -> "re.Pattern":
    """A caption line: a reference at the start followed by the separator that closes a caption label."""
    ref = reference_pattern(labels).pattern
    return re.compile(rf"^\s*{ref}\s*[.:：]", re.IGNORECASE)
