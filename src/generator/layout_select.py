"""Content shape -> layout. A slide's points are typed by shape (definition, contrast, sequence, ...), each shape has
ordered layout candidates, and the first candidate whose measured capacity holds the actual text wins."""
from typing import Dict, List, Optional, Sequence

from src.generator import fit_model as fm
from src.generator.layout_purpose import LAYOUT_PURPOSE

LINE = lambda fs: fs * 1.28 / 72

SHAPES = ("definition", "contrast", "sequence", "parallel_items", "taxonomy", "single_message", "checklist", "list",
          "formula", "formula_terms", "data_table")
ASSET_SHAPES = ("formula", "formula_terms", "data_table")
TEXT_SHARE_MAX = 0.74      # text share of the width beside a figure at its widest (deck_pptx_gen.js TEXT_SHARES)
MIN_IMG_H, IMG_GAP = 1.1, 0.16   # a figure above/below the text keeps this height (MIN_IMG_H, IMG_GAP)
CODE_TEXT_SHARE_MAX = 0.65       # text share of the width beside a code listing at its widest (CODE_SHARES)
MIN_CODE_H = 1.2                 # a listing above the text keeps this height (MIN_CODE_H)
TABLE_TEXT_MAX = 0.65       # share of the body the text under a table may take (buildTblAbove)


def step_geometry(rows: int, per_row: int) -> Dict[str, float]:
    """Text boxes of one step card (deck_pptx_gen.js stepGeometry): one row of up to 5, or two rows."""
    gap, row_gap = 0.12, (0.12 if rows > 1 else 0.0)
    band = (fm.BODY_H - row_gap * (rows - 1)) / rows
    one = rows == 1
    top, cir, below, pad = (0.08, 0.42, 0.12, 0.16) if one else (0.04, 0.34, 0.08, 0.1)
    title_h = 0.62 if one else 0.4
    cw = (fm.BW - gap * (per_row - 1)) / per_row
    panel = band - top - cir - below - 0.02
    return {"text_w": cw - 0.24, "title_h": title_h, "body_h": panel - pad - title_h - 0.04 - (0.14 if one else 0.08)}


def timeline_geometry(n: int) -> Dict[str, float]:
    """Text box of one timeline row (deck_pptx_gen.js buildTimelineVertical): up to 5 rows, else two columns."""
    cols = 1 if n <= 5 else 2
    per = -(-n // cols)
    col_w = (fm.BW - 0.3 * (cols - 1)) / cols
    return {"text_w": col_w - 0.82, "body_h": (fm.BODY_H - 0.24) / per - 0.44}


def formula_min_h(lines: int = 1) -> float:
    """Height the formula box keeps whatever the text needs (deck_pptx_gen.js formulaMinH)."""
    return 0.25 + 0.4 * max(1, lines)


def table_min_h(rows: int) -> float:
    """Height a table of `rows` body rows needs at its smallest (tableFit's row minimums)."""
    return 0.42 + 0.32 * max(1, rows)
SHAPE_LAYOUTS: Dict[str, List[str]] = {
    "definition": ["definition_callout_layout"],
    "contrast": ["comparison_columns_layout", "only_content"],
    "sequence": ["steps_horizontal_layout", "timeline_vertical_layout", "nested_bullets_layout"],
    "parallel_items": ["concept_breakdown_layout", "nested_bullets_layout"],
    "taxonomy": ["concept_breakdown_layout", "nested_bullets_layout"],
    "single_message": ["takeaway_panel_layout"],
    "checklist": ["checklist_layout", "nested_bullets_layout"],
    "list": ["nested_bullets_layout", "only_content"],
    "formula": ["formula_top_layout"],
    "formula_terms": ["formula_annotated_layout", "formula_top_layout"],
    "data_table": ["table_above_layout"],
}
# layouts that render each group as a titled block (need >= 2 groups with titles)
GROUP_LAYOUTS = {"comparison_columns_layout", "key_points_layout", "concept_breakdown_layout", "steps_horizontal_layout",
                 "timeline_vertical_layout", "checklist_layout"}


def _fits_text(text: str, w: float, h: float, min_fs: int) -> bool:
    return fm.est_lines(text, w, min_fs) * LINE(min_fs) + 0.06 <= h


def _blocks(groups: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    return [{"title": str(g["title"]), "body": " ".join(g["items"]), "items": list(g["items"])} for g in groups]


def _list_fits(groups: Sequence[Dict[str, object]], content: Sequence[str], w: float, h: float, max_fs: int,
               max_cols: int = 2) -> bool:
    """An elastic list box (deck_pptx_gen.js addList/planList): smaller, denser, then two columns -- every column at
    >= 12pt; when even that cannot hold it, the slide needs a roomier layout, never a cut or flattened list."""
    return bool(fm.plan_list(groups, content, w, h, max_fs, max_cols)["ok"])


def _body_h(extent: Optional[Dict[str, object]], title_w: float = fm.BW) -> float:
    """Body height at its largest: a one-line title gives up TITLE_LIFT (deck_pptx_gen.js roomFor)."""
    title = (extent or {}).get("title")
    return fm.BODY_H + (fm.TITLE_LIFT if fm.title_compactable(title, title_w) else 0.0)


# Layouts whose real geometry `fits` models; for these the measurement is the authority.
MEASURED = frozenset({
    "key_points_layout", "concept_breakdown_layout", "three_cols_content_layout", "numbered_conclusions_layout",
    "steps_horizontal_layout", "timeline_vertical_layout", "comparison_columns_layout",
    "checklist_layout", "takeaway_panel_layout", "definition_callout_layout",
    "formula_top_layout", "formula_annotated_layout", "table_above_layout", "nested_bullets_layout",
    "image_left_layout", "image_right_layout", "image_above_layout", "image_below_layout",
    "two_image_left_layout", "two_image_right_layout", "two_image_above_layout", "two_image_below_layout",
    "code_beside_layout", "code_above_layout",
})


def fits(layout: str, groups: Sequence[Dict[str, object]], content: Sequence[str],
         extent: Optional[Dict[str, object]] = None) -> bool:
    """Does `layout` hold this text at a readable size once it has grown as far as it can? (from the renderer's own
    measurements). `extent`: the slide's `title`, `formula_lines`, `table_rows` when known."""
    extent = extent or {}
    n = len(groups)
    blocks = _blocks(groups)
    if layout == "numbered_conclusions_layout":
        return fm.numbered_fits(groups, _body_h(extent))
    if layout == "key_points_layout":
        cards = fm.fit_cards(blocks) if 2 <= n <= 6 else None
        return bool(cards and cards["fits"] and cards["body_fs"] >= 12)
    if layout == "concept_breakdown_layout":
        if not 2 <= n <= 6:
            return False
        cols = 1 if n <= 2 else 2 if n <= 4 else 3
        rows = -(-n // cols)
        cw = (fm.BW - 0.2 * (cols - 1)) / cols
        ch = (fm.BODY_H - 0.2 * (rows - 1)) / rows
        return all(fm.fit_card_body(b["items"], b["body"], cw - 0.52, ch - 1.05) and
                   _fits_text(b["title"], cw - 0.52, 0.5, 14) for b in blocks)
    if layout == "three_cols_content_layout":
        # geometry of buildThreeCols: GAP 0.15, title box 0.60 high, bullets fill the rest at <= 14pt
        if not 2 <= n <= 3:
            return False
        col_w = (fm.BW - 0.15 * (n - 1)) / n
        return all(_fits_text(str(g["title"]), col_w - 0.30, 0.60, 14)
                   and fm.fit_bullets(list(g["items"]), col_w - 0.30, fm.BODY_H - 0.89, 14)["fs"] >= 12
                   for g in groups)
    if layout == "steps_horizontal_layout":
        if not 3 <= n <= 8:
            return False
        rows = 1 if n <= 5 else 2
        g = step_geometry(rows, -(-n // rows))
        return all(fm.fit_card_body(b["items"], b["body"], g["text_w"], g["body_h"]) and
                   _fits_text(b["title"], g["text_w"], g["title_h"], 12) for b in blocks)
    if layout == "timeline_vertical_layout":
        if not 3 <= n <= 8:
            return False
        g = timeline_geometry(n)
        return all(fm.fit_card_body(b["items"], b["body"], g["text_w"], g["body_h"]) for b in blocks)
    if layout == "comparison_columns_layout":
        if n != 2:
            return False
        col_w = (fm.BW - 0.4) / 2
        return all(g["items"] and len(g["items"]) <= 4 and
                   all(_fits_text(t, col_w - 0.7, (2.87 / len(g["items"])) - 0.1, 12) for t in g["items"]) for g in groups)
    if layout == "checklist_layout":
        if not 3 <= n <= 8:
            return False
        row_h = fm.BODY_H / n
        return all(_fits_text(b["body"], 8.1, row_h * 0.5, 12) for b in blocks)
    if layout == "takeaway_panel_layout":
        lead, rest = (content[0] if content else ""), list(content[1:6])
        return len(lead) <= 230 and (not rest or fm.fit_bullets(rest, 8.25, fm.BODY_H - 1.5 - 0.8, 16)["fs"] >= 12)
    if layout == "definition_callout_layout":
        return len(content) == 1 and len(content[0]) <= 850
    if layout == "formula_top_layout":
        box = formula_min_h(int(extent.get("formula_lines") or 1))
        return not content or _list_fits(groups, content, fm.BW, _body_h(extent) - box - 0.15, 20)
    if layout == "formula_annotated_layout":
        if n < 1 or not all(str(g.get("title") or "").strip() for g in groups):
            return False                      # each row explains one named term; unlabelled points are a plain list
        box = min(fm.BODY_H * 0.4, formula_min_h(int(extent.get("formula_lines") or 1)))
        row_h = (fm.BODY_H - box - 0.22 - 0.34) / n
        return all(_fits_text(b["body"], fm.BW - 2.0, row_h, 12) for b in blocks)
    if layout == "table_above_layout":
        body = _body_h(extent)
        room = min(body * TABLE_TEXT_MAX, body - table_min_h(int(extent.get("table_rows") or 3)) - 0.12)
        return not content or _list_fits(groups, content, fm.BW, room, 16)
    if layout == "nested_bullets_layout":
        nested_text = [str(item) for group in groups for item in group.get("items", [])] or list(content)
        return bool(nested_text) and _list_fits(groups, nested_text, fm.BW - 0.5, _body_h(extent) - 0.24, 22)
    if layout in {"image_left_layout", "image_right_layout", "two_image_left_layout", "two_image_right_layout"}:
        # the text grows from 55% to TEXT_SHARE_MAX of the width beside the figure(s), one column
        return _list_fits(groups, content, fm.BW * TEXT_SHARE_MAX, _body_h(extent, fm.BW * TEXT_SHARE_MAX), 22, 1)
    if layout == "code_beside_layout":           # the code takes 35-60% of the width, the text the rest
        return _list_fits(groups, content, fm.BW * CODE_TEXT_SHARE_MAX, _body_h(extent, fm.BW * CODE_TEXT_SHARE_MAX),
                          22, 1)
    if layout == "code_above_layout":
        return _list_fits(groups, content, fm.BW, _body_h(extent) - MIN_CODE_H - IMG_GAP, 22)
    if layout in {"image_above_layout", "image_below_layout", "two_image_above_layout", "two_image_below_layout"}:
        return _list_fits(groups, content, fm.BW, _body_h(extent) - MIN_IMG_H - IMG_GAP, 22)
    return True


def choose_layout(shape: str, groups: Sequence[Dict[str, object]], content: Sequence[str],
                  recent: Sequence[str] = (), has_image: bool = False,
                  assets: Optional[Dict[str, bool]] = None) -> str:
    """First candidate for the shape that fits the measured text; a flat list (auto-shrinks) is the last resort.
    A slide that carries a formula or a table always gets a layout that can show it."""
    assets = assets or {}
    if assets.get("formula"):
        shape = shape if shape in ("formula", "formula_terms") else "formula"
    elif assets.get("table"):
        shape = "data_table"
    elif shape in ASSET_SHAPES:
        shape = "list"                 # the model named an asset the slide does not have
    candidates = SHAPE_LAYOUTS.get(shape, SHAPE_LAYOUTS["list"])
    fitting = [layout for layout in candidates if fits(layout, groups, content)]
    if shape in ASSET_SHAPES:
        return (fitting or candidates[-1:])[0]     # never fall back to a layout that would drop the asset
    if has_image or not fitting:
        return "only_content"
    fresh = [layout for layout in fitting if not recent or layout != recent[-1]]
    return (fresh or fitting)[0]


def _matrix_fits(groups: Sequence[Dict[str, object]]) -> bool:
    """Comparison matrix: criteria rows x objects; every cell readable at >= 12pt inside the body height."""
    rows = [g for g in groups if g["items"]]
    if not 2 <= len(rows) <= 6:
        return False
    cols = max(len(g["items"]) for g in rows)
    cell_w = (fm.BW - 1.7) / max(cols, 1) - 0.2
    heights = [max(fm.est_lines(t, cell_w, 12) * LINE(12) for t in g["items"]) + 0.14 for g in rows]
    return sum(heights) <= fm.BODY_H - 0.5


def fits_spec(name: str, groups: Sequence[Dict[str, object]], content: Sequence[str],
              verbatim_items: Sequence[str] = (), extent: Optional[Dict[str, object]] = None) -> bool:
    """Does the WRITTEN text fit the layout's declared contract (block count, text budgets) and, where the renderer
    has a measured model, its measured capacity? Verbatim definitions are exempt from the budgets."""
    spec = LAYOUT_PURPOSE.get(name)
    if spec is None:
        return False
    if name == "only_content":
        return _list_fits(groups, content, fm.BW - 0.5, _body_h(extent) - 0.24, 22)
    exempt = set(verbatim_items)
    texts = [t for g in groups for t in g["items"]]
    # A character budget is a stand-in for measuring. Where the renderer's own geometry IS modelled
    # below, it decides instead -- the renderer shrinks text to fit, so counting characters rejects
    # slides that would render perfectly well.
    if name not in MEASURED and any(len(t) > spec.body_chars * 1.15 for t in texts if t not in exempt):
        return False
    if any(len(str(g["title"])) > spec.title_chars * 1.2 for g in groups if g["title"]):
        return False
    if spec.typed == "comparison":
        return _matrix_fits(groups)
    if spec.unit == "points" and not spec.min_blocks <= len(groups) <= spec.max_blocks:
        return False
    if spec.unit == "facts" and not spec.min_blocks <= len(content) <= spec.max_blocks:
        return False
    return fits(name, groups, content, extent)
