"""Slide layouts of the PowerPoint template: a display name and the frames (placeholders) of every layout, derived
from the same LAYOUT_PURPOSE that decides which layout a slide gets. The frames are guides for whoever edits the deck
in PowerPoint (Layout gallery, Reset); the slides themselves are drawn by deck_pptx_gen.js inside the same regions."""
from typing import Dict, List

from src.generator import layout_purpose as lp

MX, BW, BODY_Y, BODY_H = 0.5, 9.0, 1.55, 3.55        # deck_pptx_gen.js geometry (inches)

STRUCTURAL: Dict[str, str] = {
    "cover_split_layout": "Cover", "toc_layout": "Contents", "toc_vertical_layout": "Contents vertical",
    "toc_described_layout": "Contents described", "toc_cards_layout": "Contents cards", "agenda_layout": "Agenda",
    "section_divider_layout": "Section divider", "end_layout": "End", "end_with_image_layout": "End with image",
    "end_image_hero_layout": "End image hero",
}


def display_name(layout_id: str) -> str:
    if layout_id in STRUCTURAL:
        return STRUCTURAL[layout_id]
    text = layout_id.replace("_layout", "").replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _f(name: str, x: float, y: float, w: float, h: float, prompt: str, kind: str = "body") -> dict:
    return {"name": name, "kind": kind, "x": round(x, 3), "y": round(y, 3), "w": round(w, 3), "h": round(h, 3), "prompt": prompt}


def _rows(n: int, prefix: str, prompt: str, y: float = BODY_Y, h: float = BODY_H, gap: float = 0.1, x: float = MX,
          w: float = BW) -> List[dict]:
    each = (h - gap * (n - 1)) / n
    return [_f(f"{prefix} {i + 1}", x, y + i * (each + gap), w, each, prompt) for i in range(n)]


def _cols(n: int, prefix: str, prompt: str, y: float = BODY_Y, h: float = BODY_H, gap: float = 0.15, x: float = MX,
          w: float = BW) -> List[dict]:
    each = (w - gap * (n - 1)) / n
    return [_f(f"{prefix} {i + 1}", x + i * (each + gap), y, each, h, prompt) for i in range(n)]


def _grid(r: int, c: int, prefix: str, prompt: str) -> List[dict]:
    gap = 0.15
    cw, rh = (BW - gap * (c - 1)) / c, (BODY_H - gap * (r - 1)) / r
    return [_f(f"{prefix} {i * c + j + 1}", MX + j * (cw + gap), BODY_Y + i * (rh + gap), cw, rh, prompt)
            for i in range(r) for j in range(c)]


def frames_for(layout_id: str) -> List[dict]:
    """Placeholders (guides) of one layout, in inches on the 10 x 5.625 slide."""
    spec = lp.LAYOUT_PURPOSE.get(layout_id)
    if spec is None:
        toc = _rows(5, "Entry", "Section title", h=3.4)
        return {"cover_split_layout": [_f("Meta", MX, 3.6, BW, 1.2, "Speaker, institution, date")],
                "toc_layout": toc, "toc_vertical_layout": toc, "toc_described_layout": toc, "toc_cards_layout": _grid(2, 3, "Entry", "Section title"),
                "agenda_layout": _rows(5, "Item", "Agenda item", h=3.4),
                "section_divider_layout": [_f("Section", MX, 2.2, BW, 1.4, "Section heading")],
                "end_layout": [_f("Closing", MX, 2.4, BW, 1.0, "Closing line")],
                "end_with_image_layout": [_f("Closing", MX, 2.4, 4.5, 1.0, "Closing line"), _f("Figure", 5.2, 1.0, 4.3, 3.6, "Picture", kind="pic")],
                "end_image_hero_layout": [_f("Figure", 0, 0, 10, 5.625, "Full-bleed picture", kind="pic")]}.get(layout_id, [])
    r = spec.relation
    if r == "definition":
        return [_f("Definition", MX, BODY_Y, BW, BODY_H, "Term and its formal definition")]
    if r in ("parallel", "summary", "criteria", "hierarchy", "long_list"):
        return _rows(4, "Point", "Idea title and its explanation")
    if r == "kinds":
        return _grid(2, 2, "Concept", "Term and a short definition")
    if r == "comparison":
        head = _cols(3, "Object", "Object being compared", y=BODY_Y, h=0.42, x=MX + 1.7, w=BW - 1.7)
        return [_f("Criteria", MX, BODY_Y + 0.5, 1.6, BODY_H - 0.5, "Criteria (one per row)")] + head + \
            [_f("Cells", MX + 1.7, BODY_Y + 0.5, BW - 1.7, BODY_H - 0.5, "One sentence per object and criterion")]
    if r in ("contrast", "two_parts"):
        return _cols(2, "Side" if r == "contrast" else "Part", "Heading and a few short points")
    if r == "three_parallel":
        return _cols(3, "Column", "Heading and short bullets")
    if r == "quadrants":
        return _grid(2, 2, "Quadrant", "Category and one or two sentences")
    if r == "procedure":
        return _cols(4, "Step", "Action and what happens")
    if r == "sequence":
        return _rows(5, "Node", "Lead and one sentence", w=BW - 0.6, x=MX + 0.6)
    if r in ("loop", "hub"):
        centre = [_f("Centre", 4.0, BODY_Y + 1.2, 2.0, 1.1, "Central concept")] if r == "hub" else []
        return centre + _grid(2, 3, "Stage" if r == "loop" else "Branch", "Short label")
    if r in ("levels", "narrowing"):
        return _rows(4, "Level", "Short line", x=MX + 1.0, w=BW - 2.0, gap=0.08)
    if r == "dominant":
        return [_f("Main idea", MX, BODY_Y, 4.6, BODY_H, "The one dominant idea")] + \
            _rows(3, "Support", "Short supporting point", x=5.3, w=BW - 4.8)
    if r == "lead_support":
        return [_f("Lead", MX, BODY_Y, BW, 1.1, "Standout lead sentence")] + \
            _rows(3, "Support", "Supporting sentence", y=BODY_Y + 1.3, h=BODY_H - 1.3)
    if r == "thesis":
        return [_f("Statement", MX, BODY_Y, BW, 2.2, "Standalone principle or thesis")] + \
            [_f("Support", MX, BODY_Y + 2.4, BW, BODY_H - 2.4, "Small supporting points")]
    if r == "catalog":
        return _rows(6, "Item", "Label: one-line detail", gap=0.06)
    if r == "names":
        return _grid(2, 3, "Name", "Short name")
    if r == "milestones":
        return _cols(4, "Milestone", "Result or stage")
    if r == "quotation":
        return [_f("Quote", MX + 0.4, BODY_Y, BW - 0.8, 2.4, "Verbatim quotation"), _f("Source", MX + 0.4, BODY_Y + 2.6, BW - 0.8, 0.6, "Who said it")]
    if r in ("key_figure", "key_figures"):
        return _cols(3, "Figure", "Number and label")
    if r == "options":
        return _cols(3, "Option", "Name, headline figure and properties")
    if r == "profile":
        return [_f("Description", MX, BODY_Y, 4.4, BODY_H, "One-sentence description")] + \
            _rows(4, "Attribute", "Key: value", x=5.1, w=BW - 4.6, gap=0.08)
    if r == "questions":
        return [_f("Main question", MX, BODY_Y, BW, 1.0, "Central question")] + \
            _cols(3, "Sub-question", "Follow-up question", y=BODY_Y + 1.2, h=BODY_H - 1.2)
    if r in ("formula", "formula_result", "formula_terms"):
        return [_f("Equation", MX, BODY_Y, BW, 1.2, "Equation", kind="pic"),
                _f("Explanation", MX, BODY_Y + 1.35, BW, BODY_H - 1.35, "What each part means")]
    if r in ("table", "source_table", "table_only", "table_callout", "table_takeaway"):
        return [_f("Table", MX, BODY_Y, BW, 2.4, "Table", kind="pic"), _f("Notes", MX, BODY_Y + 2.55, BW, BODY_H - 2.55, "Key takeaway")]
    if r in ("figures", "visual_comparison"):
        return _cols(2, "Figure", "Picture", h=2.6, y=BODY_Y) + [_f("Text", MX, BODY_Y + 2.75, BW, BODY_H - 2.75, "Explanation")]
    if r == "code_walkthrough":
        return [_f("Text", MX, BODY_Y, 4.5, BODY_H, "Explanation"), _f("Code", 5.2, BODY_Y, BW - 4.7, BODY_H, "Code")]
    if r == "code_walkthrough_wide":
        return [_f("Code", MX, BODY_Y, BW, 2.3, "Code"), _f("Text", MX, BODY_Y + 2.45, BW, BODY_H - 2.45, "Explanation")]
    if r == "figure_wide":
        return [_f("Figure", MX, BODY_Y, BW, 2.3, "Picture", kind="pic"), _f("Text", MX, BODY_Y + 2.45, BW, BODY_H - 2.45, "Explanation")]
    if r in ("figure", "visual_emphasis", "distribution"):
        text, figure = _f("Text", MX, BODY_Y, 4.5, BODY_H, "Explanation"), _f("Figure", 5.2, BODY_Y, BW - 4.7, BODY_H, "Picture", kind="pic")
        if layout_id == "image_left_layout":
            text, figure = _f("Text", 5.2, BODY_Y, BW - 4.7, BODY_H, "Explanation"), _f("Figure", MX, BODY_Y, 4.5, BODY_H, "Picture", kind="pic")
        return [text, figure]
    return [_f("Content", MX, BODY_Y, BW, BODY_H, "Content")]


def all_layouts() -> List[dict]:
    """Every layout of the template: content layouts of LAYOUT_PURPOSE plus the structural ones."""
    ids = list(STRUCTURAL) + [s.name for s in lp.SPECS]
    layouts = []
    for layout_id in ids:
        frames = frames_for(layout_id)
        # Stable placeholder indices are the native contract PowerPoint uses
        # when a user switches layouts or presses Reset.  Title is always idx 0;
        # content slots are 1..N in catalogue order on every embedded design.
        for idx, frame in enumerate(frames, 1):
            frame["idx"] = idx
            frame["role"] = "picture" if frame["kind"] == "pic" else "body"
        layouts.append({"id": layout_id, "name": display_name(layout_id), "frames": frames,
                        "title_idx": 0, "structural": layout_id in STRUCTURAL})
    return layouts
