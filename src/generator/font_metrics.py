"""Real glyph widths of the deck's fonts, shared by the renderer (deck_pptx_gen.js) and the Python fit model.

Every embedded design can be switched to at any time without resizing, so text is measured per ROLE (display, body,
mono, math = Cambria Math, any = display or body; regular and bold) with, for each character, the widest advance
among the role's fonts in all designs. PowerPoint draws a line 1.2x the font size in every one of these fonts and
wraps greedily at spaces: measured on 840 boxes over the 15 fonts (English and Vietnamese, 9-28pt, 1.4-8.5"), this
model with WRAP_SAFE of the width never counts fewer lines than PowerPoint draws. Widths
are in em (advance / unitsPerEm) read from the installed TrueType files; a character no role font has takes its width
from the fallback fonts PowerPoint substitutes (Cambria Math, Segoe UI Symbol), else DEFAULT_EM. Regenerate with
`python -m src.generator.font_metrics` after adding a design font.
"""
from __future__ import annotations

import glob
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, Optional

from fontTools.ttLib import TTCollection, TTFont

PATH = Path(__file__).with_name("font_metrics.json")
FONT_DIRS = (r"C:\Windows\Fonts", os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Fonts"))
FALLBACK = ("Cambria Math", "Segoe UI Symbol")
DEFAULT_EM = 0.62
LINE = 1.2                   # PowerPoint's single line height / font size, the same in every design font
WRAP_SAFE = 0.98             # share of a box's text width the wrap model fills
SYNTHETIC_BOLD = 1.03        # a family without a bold face is emboldened by PowerPoint, a little wider
RANGES = ((0x20, 0x24F), (0x300, 0x36F), (0x370, 0x3FF), (0x1D00, 0x1DBF), (0x1E00, 0x1EFF), (0x2000, 0x20CF),
          (0x2100, 0x214F), (0x2150, 0x218F), (0x2190, 0x21FF), (0x2200, 0x22FF), (0x2300, 0x23FF), (0x2460, 0x24FF),
          (0x2500, 0x25FF), (0x2600, 0x26FF), (0x2C60, 0x2C7F), (0x27C0, 0x27EF), (0x2980, 0x29FF), (0xFB00, 0xFB06))


def _faces() -> Dict[tuple, TTFont]:
    """(family, "Regular" | "Bold") -> its TrueType face, for every installed font."""
    out: Dict[tuple, TTFont] = {}
    for d in FONT_DIRS:
        for f in glob.glob(os.path.join(d, "*.tt[fc]")):
            try:
                fonts = TTCollection(f).fonts if f.lower().endswith(".ttc") else [TTFont(f)]
            except Exception:
                continue
            for ft in fonts:
                name = ft["name"]
                family = name.getDebugName(16) or name.getDebugName(1)
                sub = name.getDebugName(17) or name.getDebugName(2)
                if sub in ("Regular", "Bold"):
                    out.setdefault((family, sub), ft)
    return out


def _widths(face: TTFont, chars: Iterable[int]) -> Dict[str, float]:
    cmap, hmtx, upem = face.getBestCmap(), face["hmtx"], face["head"].unitsPerEm
    return {chr(c): round(hmtx[cmap[c]][0] / upem, 4) for c in chars if c in cmap}


def font_widths(family: str, bold: bool = False, faces: Optional[dict] = None) -> Dict[str, float]:
    """One font's widths in em (synthetic bold when the family has no bold face)."""
    faces = faces if faces is not None else _faces()
    chars = [c for lo, hi in RANGES for c in range(lo, hi + 1)]
    face = faces.get((family, "Bold" if bold else "Regular"))
    if face is not None:
        return _widths(face, chars)
    regular = faces.get((family, "Regular"))
    if regular is None:
        raise FileNotFoundError(f"font {family!r} is not installed")
    return {k: round(v * SYNTHETIC_BOLD, 4) for k, v in _widths(regular, chars).items()}


def build(themes: Iterable[dict]) -> dict:
    """{role: {"regular"|"bold": {char: em}}, "fonts": {role: [...]}, "default": em} for the given designs."""
    faces = _faces()
    roles = {"display": set(), "body": set(), "mono": set(), "math": {"Cambria Math"}}
    for t in themes:
        for role in roles:
            if role != "math" and t.get(f"font_{role}"):
                roles[role].add(t[f"font_{role}"])
    fallback = {}
    for family in FALLBACK:
        for ch, w in font_widths(family, False, faces).items():
            fallback[ch] = max(w, fallback.get(ch, 0))
    out = {"default": DEFAULT_EM, "fonts": {r: sorted(f) for r, f in roles.items()}}
    for role, families in roles.items():
        out[role] = {}
        for weight in ("regular", "bold"):
            table = dict(fallback)
            own: Dict[str, float] = {}
            for family in families:
                for ch, w in font_widths(family, weight == "bold", faces).items():
                    own[ch] = max(w, own.get(ch, 0))
            table.update(own)
            out[role][weight] = table
    out["fonts"]["any"] = sorted(roles["display"] | roles["body"])
    out["any"] = {weight: {ch: max(out["display"][weight].get(ch, 0), out["body"][weight].get(ch, 0))
                           for ch in set(out["display"][weight]) | set(out["body"][weight])}
                  for weight in ("regular", "bold")}
    return out


@lru_cache(maxsize=1)
def load() -> dict:
    return json.loads(PATH.read_text(encoding="utf-8"))


def text_em(text: str, role: str = "body", bold: bool = False) -> float:
    """Width of `text` in em for the role (the widest of its fonts per character)."""
    m = load()
    table = m[role]["bold" if bold else "regular"]
    return sum(table.get(ch, m["default"]) for ch in text)


def wrap_lines(text: str, w: float, fs: float, role: str = "any", bold: bool = False) -> int:
    """Lines `text` takes in a text area w inches wide at fs pt ("\n" starts a paragraph); a word wider than the line
    breaks between characters (deck_pptx_gen.js wrapLines)."""
    avail = max(0.05, w) * WRAP_SAFE * 72 / fs
    lines = 0
    for para in str(text if text is not None else "").split("\n"):
        lines += 1
        cur = text_em(re.match(r"\s*", para).group(0), role, bold)
        for m in re.finditer(r"(\S+)(\s*)", para):
            ww = text_em(m.group(1), role, bold)
            if cur > 0 and cur + ww > avail:
                lines, cur = lines + 1, 0.0
            if ww > avail:
                for ch in m.group(1):
                    c = text_em(ch, role, bold)
                    if cur > 0 and cur + c > avail:
                        lines, cur = lines + 1, 0.0
                    cur += c
            else:
                cur += ww
            cur += text_em(m.group(2), role, bold)
    return max(1, lines)


if __name__ == "__main__":
    from src.generator.pptx_pipeline import embedded_deck_themes

    PATH.write_text(json.dumps(build(embedded_deck_themes()), ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    print(f"wrote {PATH} ({PATH.stat().st_size // 1024} KB)")
