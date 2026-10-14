from __future__ import annotations

from typing import NamedTuple, Optional

from src.generator.pptx_pattern_engines import (_estimate_text_lines, _find_overlaps, _image_shape,
                                                _required_text_height, _stack_items, card_grid_engine, emphasis_engine,
                                                flow_engine, radial_ring_engine, radial_spoke_engine, split_engine,
                                                tier_engine, tree_engine)

CANVAS_DEFAULT = {'w': 10.0, 'h': 5.625}
MARGIN_DEFAULT = 0.5
SHOW_PAGE_NUMBER = True

# Full pptxgenjs ShapeType preset vocabulary, minus actionButton*/flowChart* (media-control glyphs and
# flowchart-specific symbols the existing flow_engine/tree_engine already cover better). 'ellipse' only,
# not 'oval' -- not a real ShapeType value.
_SAFE_ACCENT_SHAPES = (
    'accentBorderCallout1', 'accentBorderCallout2', 'accentBorderCallout3', 'accentCallout1',
    'accentCallout2', 'accentCallout3', 'arc', 'bentArrow', 'bentUpArrow', 'bevel', 'blockArc',
    'borderCallout1', 'borderCallout2', 'borderCallout3', 'bracePair', 'bracketPair', 'callout1',
    'callout2', 'callout3', 'can', 'chartPlus', 'chartStar', 'chartX', 'chevron', 'chord',
    'circularArrow', 'cloud', 'cloudCallout', 'corner', 'cornerTabs', 'cube', 'curvedDownArrow',
    'curvedLeftArrow', 'curvedRightArrow', 'curvedUpArrow', 'decagon', 'diagStripe', 'diamond',
    'dodecagon', 'donut', 'doubleWave', 'downArrow', 'downArrowCallout', 'ellipse', 'ellipseRibbon',
    'ellipseRibbon2', 'folderCorner', 'frame', 'funnel', 'gear6', 'gear9', 'halfFrame', 'heart',
    'heptagon', 'hexagon', 'homePlate', 'horizontalScroll', 'irregularSeal1', 'irregularSeal2',
    'leftArrow', 'leftArrowCallout', 'leftBrace', 'leftBracket', 'leftCircularArrow', 'leftRightArrow',
    'leftRightArrowCallout', 'leftRightCircularArrow', 'leftRightRibbon', 'leftRightUpArrow',
    'leftUpArrow', 'lightningBolt', 'line', 'lineInv', 'mathDivide', 'mathEqual', 'mathMinus',
    'mathMultiply', 'mathNotEqual', 'mathPlus', 'moon', 'noSmoking', 'nonIsoscelesTrapezoid',
    'notchedRightArrow', 'octagon', 'parallelogram', 'pentagon', 'pie', 'pieWedge', 'plaque',
    'plaqueTabs', 'plus', 'quadArrow', 'quadArrowCallout', 'rect', 'ribbon', 'ribbon2', 'rightArrow',
    'rightArrowCallout', 'rightBrace', 'rightBracket', 'round1Rect', 'round2DiagRect', 'round2SameRect',
    'roundRect', 'rtTriangle', 'smileyFace', 'snip1Rect', 'snip2DiagRect', 'snip2SameRect',
    'snipRoundRect', 'squareTabs', 'star10', 'star12', 'star16', 'star24', 'star32', 'star4', 'star5',
    'star6', 'star7', 'star8', 'stripedRightArrow', 'sun', 'swooshArrow', 'teardrop', 'trapezoid',
    'triangle', 'upArrow', 'upArrowCallout', 'upDownArrow', 'upDownArrowCallout', 'uturnArrow',
    'verticalScroll', 'wave', 'wedgeEllipseCallout', 'wedgeRectCallout', 'wedgeRoundRectCallout',
)


class PatternValidationError(Exception):
    """Raised when a pattern's slots can't be turned into a valid slide -- caller falls back to the
    fixed template (or retries the LLM once) rather than shipping a broken/empty composition."""


class PatternDef(NamedTuple):
    name: str
    when_to_use: str
    slot_schema: str
    rhythms: tuple[str, ...]  # subset of ('anchor','dense','breathing') -- which page rhythms this pattern fits


PATTERN_CATALOG: list[PatternDef] = [
    PatternDef(
        'narrative',
        'prose-heavy content, one main idea, or a short list of items with no natural grouping',
        '{"item_indices": [0,1,2]}  -- reference this slide\'s content items by index, in display order',
        ('anchor', 'dense', 'breathing'),
    ),
    PatternDef(
        'bullet_columns',
        'enumerable content that naturally splits into 1-3 parallel groups/categories',
        '{"columns": [[0,1],[2,3,4]]}  -- each inner list is one column of item indices, 1-3 columns total',
        ('dense',),
    ),
    PatternDef(
        'image_caption',
        "one image (from AVAILABLE DOCUMENT IMAGES, or newly generated) illustrates this slide's content",
        '{"image_source":"document|generate","image_path_or_prompt":"...","caption":"short new caption",'
        '"item_indices":[0,1]}  -- item_indices optional: supporting bullets shown beside the image',
        ('dense', 'breathing'),
    ),
    PatternDef(
        'hero_image',
        'ONE striking image should fill the whole slide with the title floating on top -- use sparingly, '
        'for a strong opening/closing/section-break moment, not routine content',
        '{"image_source":"document|generate","image_path_or_prompt":"...","kicker":"short label above '
        'title, optional","subtitle":"short line below title, optional"}',
        ('anchor', 'breathing'),
    ),
    PatternDef(
        'stat_row',
        "ONLY when this slide's own content contains 2-4 concrete number+label facts worth calling out "
        '(a percentage, a count, a dollar figure, etc.) -- never invent numbers that are not in the content',
        '{"stats":[{"value":"92%","label":"short description"}, ... 2-4 items]}  -- value MUST be copied '
        'verbatim from the slide content',
        ('dense',),
    ),
    PatternDef(
        'comparison',
        'a two-item comparison (before/after, A vs B, two approaches) with roughly parallel content on '
        'each side',
        '{"left":{"label":"short heading","item_indices":[0]},"right":{"label":"short heading",'
        '"item_indices":[1]}}',
        ('dense',),
    ),
    PatternDef(
        'quote_focus',
        'one single striking sentence, definition, or short quote from the content deserves to stand alone',
        '{"quote_item_index": 0}  -- index of the ONE item to feature; only use if that item is a '
        'genuinely self-contained, quotable statement',
        ('breathing', 'anchor'),
    ),
    PatternDef(
        'numbered_list',
        'ranked/ordered points, OR independent findings/takeaways that benefit from a numeral per item '
        '(distinct from bullet_columns: this pattern numbers each item individually)',
        '{"item_indices": [0,1,2,3]}  -- up to 8 items, in the order they should be numbered',
        ('dense',),
    ),
    PatternDef(
        'pillar_columns',
        'independent PARALLEL categories/topics (not a ranked sequence) -- each gets its own labeled '
        'column with a short header, distinct from bullet_columns which has no per-column header',
        '{"columns": [{"header":"short label","item_indices":[0,1]}, ...]}  -- 1-4 columns',
        ('dense',),
    ),
    PatternDef(
        'cover_hook',
        "ONLY for the deck's own cover/title slide -- features one real supporting fact/sentence "
        "alongside the title instead of a bare title, using the slide's own stashed hook text if present",
        '{"hook_text":"...","kicker":"short label above title, optional"}  -- hook_text MUST be copied '
        'verbatim from the provided hook (or from a content item) -- never invented',
        ('anchor',),
    ),
    PatternDef(
        'agenda_map',
        'a table-of-contents / agenda / outline slide listing the deck\'s sections as a clean numbered map',
        '{"item_indices": [0,1,2,...]}  -- section labels in display order',
        ('anchor', 'dense'),
    ),
    PatternDef(
        'process_flow',
        'one or more sequential steps connected by arrows; geometry expands and paginates with the data',
        '{"item_indices": [0,1,2]}  -- one item per step, in order',
        ('dense',),
    ),
    PatternDef(
        'chevron_process',
        'one or more methodology phases with progression markers; geometry expands with the data',
        '{"item_indices": [0,1,2]}  -- one item per phase, in order',
        ('dense',),
    ),
    PatternDef(
        'timeline',
        'one or more ordered milestone events with no meaningful duration between them',
        '{"item_indices": [0,1,2]}  -- one item per milestone, in order',
        ('dense',),
    ),
    PatternDef(
        'pipeline_with_stages',
        'pipeline stages where EACH stage also produces a named output/artifact worth calling out',
        '{"stages": [{"item_index": 0, "output": "short output/artifact name"}, ...]}',
        ('dense',),
    ),
    PatternDef(
        'roadmap_vertical',
        'milestones better shown on a VERTICAL timeline than horizontal (e.g. many milestones, or a '
        'narrow content shape)',
        '{"item_indices": [0,1,2]}  -- one item per milestone, top to bottom',
        ('dense',),
    ),
    PatternDef(
        'hub_spoke',
        '1 central idea (the slide title) with surrounding related items radiating OUT from it',
        '{"item_indices": [0,1,2]}  -- one item per spoke',
        ('dense',),
    ),
    PatternDef(
        'hub_inward_arrows',
        '1 central idea (the slide title) under pressure/influence FROM 3-8 surrounding forces (e.g. '
        'competitive forces, risk factors converging on a core asset)',
        '{"item_indices": [0,1,2]}  -- one item per surrounding force',
        ('dense',),
    ),
    PatternDef(
        'segmented_wheel',
        '1 central topic split into 4-8 equally-weighted parallel dimensions/facets arranged as wheel '
        'segments (not a sequence, not forces acting on the center)',
        '{"item_indices": [0,1,2]}  -- one item per segment',
        ('dense',),
    ),
    PatternDef(
        'concentric_circles',
        '2-4 nested priority rings from core to periphery (bullseye/onion model, sphere of influence)',
        '{"item_indices": [0,1]}  -- outer ring first, innermost last',
        ('dense',),
    ),
    PatternDef(
        'pyramid_chart',
        'two or more stratified hierarchy layers, narrow at top (maturity model, capability tiers)',
        '{"item_indices": [0,1,2]}  -- top (narrowest) tier first',
        ('dense',),
    ),
    PatternDef(
        'funnel',
        'two or more sequential stages with monotonic narrowing/drop-off (conversion/filtering stages)',
        '{"item_indices": [0,1,2]}  -- widest (first) stage first',
        ('dense',),
    ),
    PatternDef(
        'iceberg',
        'exactly 2 items contrasting a small "visible" part against a much larger "hidden" part (surface '
        'cost vs. true cost, what people see vs. what actually matters)',
        '{"item_indices": [0,1]}  -- [visible item, hidden item]',
        ('breathing',),
    ),
    PatternDef(
        'top_down_tree',
        '2-4 level STRICT hierarchy with parent-to-children reporting/decomposition lines (org chart, '
        'decomposition of one concept into sub-concepts)',
        '{"levels": [[0], [1,2], [3,4,5]]}  -- each inner list is one level\'s item indices, root first',
        ('dense',),
    ),
    PatternDef(
        'layered_architecture',
        '2-4 horizontal architecture/system layers, each containing 2-4 components -- layers do NOT '
        'report to each other (no parent-child lines), just stacked',
        '{"levels": [[0,1], [2,3]]}  -- each inner list is one layer\'s item indices, top layer first',
        ('dense',),
    ),
    PatternDef(
        'module_composition',
        'one parent concept that visibly CONTAINS several child parts (a system made of components)',
        '{"parent_index": 0, "child_indices": [1,2,3]}',
        ('dense',),
    ),
    PatternDef(
        'icon_grid',
        'parallel features/capabilities/services shown as a data-sized grid of cards',
        '{"item_indices": [0,1,2]}  -- one item per card',
        ('dense',),
    ),
    PatternDef(
        'quadrant_text_bullets',
        'a 2x2 framework where each quadrant holds a title + supporting points (SWOT, Ansoff, any named '
        'two-axis framework with text content, not plotted data)',
        '{"quadrants": [{"label": "Strengths", "item_indices": [0,1]}, ... exactly 4 quadrants]}',
        ('dense',),
    ),
    PatternDef(
        'matrix_2x2',
        'items plotted in a 2x2 grid where the two AXES themselves have meaning (e.g. Impact vs. Effort) '
        'that should be labeled',
        '{"quadrants": [{"label": "...", "item_indices": [0]}, ... exactly 4], "x_axis_low": "...", '
        '"x_axis_high": "...", "y_axis_low": "...", "y_axis_high": "..."}',
        ('dense',),
    ),
    PatternDef(
        'checklist',
        'items that are literally a checklist/readiness list (each is a thing to verify or complete)',
        '{"item_indices": [0,1,2]}',
        ('dense',),
    ),
    PatternDef(
        'feature_matrix',
        'a dense comparison table with column headers and multiple data rows (competitive comparison, '
        'spec sheet) -- values are short (numbers, short phrases), not full sentences',
        '{"headers": ["Feature","Option A","Option B"], "rows": [["Speed","Fast","Slow"], ...]}',
        ('dense',),
    ),
    PatternDef(
        'pros_cons',
        'a bilateral pros/cons list with 2-5 items per side',
        '{"left": {"label": "Pros", "item_indices": [0,1]}, "right": {"label": "Cons", "item_indices": [2]}}',
        ('dense',),
    ),
    PatternDef(
        'problem_solution',
        'a clear stated problem contrasted with its solution',
        '{"left": {"label": "Problem", "item_indices": [0]}, "right": {"label": "Solution", "item_indices": [1]}}',
        ('dense',),
    ),
    PatternDef(
        'before_after_images',
        'TWO real images (before/after, two states of the same subject) side by side -- only use if '
        'images genuinely fit; needs both sides to have a usable image',
        '{"left": {"image_source":"document|generate","image_path_or_prompt":"...","caption":"...","label":"Before"}, '
        '"right": {"image_source":"document|generate","image_path_or_prompt":"...","caption":"...","label":"After"}}',
        ('breathing',),
    ),
    PatternDef(
        'spectrum_continuum',
        'a single dimension between two opposite poles, optionally with a position marked on it (not a '
        '2x2 grid -- only one axis)',
        '{"left": {"label": "..."}, "right": {"label": "..."}}  -- left may add "position": 0.0-1.0 '
        'ONLY if the content states a specific position; omit otherwise',
        ('breathing',),
    ),
    PatternDef(
        'editorial_pullquote',
        'one striking quote/sentence featured large beside supporting body text (not standing fully alone '
        '-- pair it with 2-4 related points)',
        '{"quote_item_index": 0, "item_indices": [1,2]}',
        ('breathing',),
    ),
    PatternDef(
        'big_number',
        'ONE single hero statistic deserves to dominate the whole slide with a one-line interpretation '
        '(stronger emphasis than stat_row\'s row-of-cards treatment) -- value MUST be copied verbatim '
        'from the slide content, never invented',
        '{"stat": {"value": "92%", "interpretation": "short context sentence"}}',
        ('breathing',),
    ),
    PatternDef(
        'section_divider',
        'a mid-deck transition/section-break moment -- minimal, high-impact, just the title (and '
        'optionally one short subtitle), no body content',
        '{"subtitle": "short line, optional"}',
        ('anchor', 'breathing'),
    ),
    PatternDef(
        'closing_summary',
        'the deck\'s closing slide with 2-4 short key-takeaway bullets (no image needed, unlike hero_image)',
        '{"item_indices": [0,1,2]}',
        ('anchor',),
    ),
    PatternDef(
        'faq_qa',
        'content structured as question/answer pairs',
        '{"pairs": [{"question": "short question text", "answer_item_index": 0}, ...]}  -- answer MUST '
        'reference a real content item by index; question is a short framing phrase',
        ('dense',),
    ),
]


def patterns_for_rhythm(rhythm: str) -> list[PatternDef]:
    """Filter PATTERN_CATALOG to patterns compatible with a page's rhythm tag. An unrecognized/missing
    rhythm returns the FULL catalog rather than an empty menu -- filtering is a prompt-narrowing aid, never
    a hard gate that could leave a slide with nothing to choose from."""
    matches = [p for p in PATTERN_CATALOG if rhythm in p.rhythms]
    return matches or list(PATTERN_CATALOG)


_NON_CONTENT_KEYS = {
    'type', 'page_num', 'is_light', 'title', 'slide_title', 'heading', 'images', 'table',
    'latex_block_formula', 'formula_latex', 'formula_text', 'formula_image_path', 'kg_node_id',
}
_NON_CONTENT_SUBKEYS = {'ix', 'icon', 'tag', 'num', 'n'}  # short decorative labels, not real content


def _flatten_value(value) -> list[str]:
    """Recursively pull every meaningful string out of one slide-content value, regardless of whether
    it's a plain string, a dict (icon/title/body-shaped), or a list of either. The ~30 distinct fixed
    layout functions each shape their args differently (bullets/content/points/cols/sides/cells/steps/
    stats/...), so this walks the actual structure instead of hardcoding each one's field names -- a
    hardcoded key list previously missed most of them entirely (silent content loss) and mishandled
    dict-shaped items (printed their Python repr as literal slide text)."""
    if value is None:
        return []
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    if isinstance(value, dict):
        leaf_parts, nested = [], []
        for k, v in value.items():
            if k in _NON_CONTENT_SUBKEYS:
                continue
            if isinstance(v, str):
                v = v.strip()
                if v:
                    leaf_parts.append(v)
            elif v:
                nested.append(v)
        out = [': '.join(leaf_parts)] if len(leaf_parts) > 1 else list(leaf_parts)
        for n in nested:
            out.extend(_flatten_value(n))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_flatten_value(item))
        return out
    return []


def _extract_slide_items(slide: dict) -> list[str]:
    items: list[str] = []
    for key, value in slide.items():
        if key in _NON_CONTENT_KEYS:
            continue
        items.extend(_flatten_value(value))
    return items


def _resolve_indices(items: list[str], indices) -> list[str]:
    if not isinstance(indices, list):
        return list(items)
    out = [items[i] for i in indices if isinstance(i, int) and 0 <= i < len(items)]
    return out or list(items)


def _verbatim_in_slide(text: str, slide: dict, title: str = '', items: Optional[list[str]] = None) -> bool:
    text = (text or '').strip()
    if not text:
        return False
    haystack_parts = [str(slide.get('title') or title or '')]
    haystack_parts.extend(str(i) for i in (items or []))
    haystack_parts.extend(str(v) for v in slide.values() if isinstance(v, str))
    return text in ' '.join(haystack_parts)


def _usable(canvas: dict, margin: float) -> tuple:
    return margin, margin, canvas['w'] - margin, canvas['h'] - margin


def _title_block(title: str, theme: dict, canvas: dict, margin: float,
                 font_size: float = 30, accent_bar: bool = True) -> tuple[list[dict], float]:
    """Title text + optional thin accent underline, sized to its own content. Returns
    (shapes, next_free_y) so callers can stack body content right below it."""
    x0, y0, x1, _y1 = _usable(canvas, margin)
    w = x1 - x0
    h = max(0.6, _required_text_height(title or '', font_size, w))
    shapes = [{'type': 'title', 'text': title or '', 'x': x0, 'y': y0, 'w': w, 'h': h,
               'font_size_hint': font_size}]
    y = y0 + h + 0.06
    if accent_bar:
        shapes.append({'type': 'accent_shape', 'shape': 'rect', 'x': x0, 'y': y, 'w': 1.0, 'h': 0.045,
                       'fill': theme.get('accent', 'FFCC33')})
        y += 0.045
    return shapes, y + 0.14


def _stack_numbered_items(items: list[str], x: float, y: float, w: float, theme: dict,
                          font_size: float = 16, gap: float = 0.16, max_h: Optional[float] = None,
                          start_n: int = 1, badge_w: float = 0.5) -> tuple[list[dict], float]:
    """Like _stack_items, but each item gets a small numeral badge ("01","02"...) in the theme accent
    color to its left. Badge x-range [x, x+badge_w] and text x-range [x+badge_w+0.12, ...] are disjoint
    by construction regardless of y, so badge/text pairs can never trip the overlap checker."""
    if not items:
        return [], y
    text_x = x + badge_w + 0.12
    text_w = max(0.5, w - badge_w - 0.12)
    fs = font_size

    def _total_h(fs_: float) -> float:
        return sum(_required_text_height(it, fs_, text_w) + gap for it in items) - gap

    if max_h:
        while _total_h(fs) > max_h and fs > 11:
            fs -= 1
    shapes = []
    cy = y
    for i, it in enumerate(items):
        ih = max(0.36, _required_text_height(it, fs, text_w))
        shapes.append({'type': 'title', 'text': f'{start_n + i:02d}', 'x': x, 'y': cy, 'w': badge_w,
                       'h': 0.34, 'font_size_hint': max(14, fs), 'color': theme.get('accent', 'FFCC33')})
        shapes.append({'type': 'bullet', 'text': it, 'x': text_x, 'y': cy, 'w': text_w, 'h': ih,
                       'font_size_hint': fs})
        cy += ih + gap
    return shapes, cy - gap


def _page_number_shape(page_num, theme: dict, canvas: dict, margin: float) -> Optional[dict]:
    if not SHOW_PAGE_NUMBER or not page_num:
        return None
    w, h = 0.36, 0.2
    x = canvas['w'] - w - 0.1
    y = canvas['h'] - h - 0.08
    return {'type': 'bullet', 'text': str(page_num), 'x': x, 'y': y, 'w': w, 'h': h,
            'font_size_hint': 10, 'color': theme.get('dim_dark', '888888'), 'align': 'right'}


def render_narrative(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    chosen = _resolve_indices(items, slots.get('item_indices'))
    body_shapes, _bottom = _stack_items(chosen, x0, y, x1 - x0, font_size=17, max_h=y1 - y, bulleted=True)
    return shapes + body_shapes


def render_bullet_columns(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    cols = slots.get('columns')
    if not isinstance(cols, list) or not cols:
        cols = [list(range(len(items)))]
    cols = cols[:3]
    n = len(cols)
    col_gap = 0.3
    col_w = (x1 - x0 - col_gap * (n - 1)) / n
    for c, idxs in enumerate(cols):
        cx = x0 + c * (col_w + col_gap)
        chosen = _resolve_indices(items, idxs)
        col_shapes, bottom = _stack_items(chosen, cx + 0.15, y, col_w - 0.15, font_size=15, max_h=y1 - y,
                                          bulleted=True)
        bar_h = max(0.3, min(y1 - y, bottom - y))
        shapes.append({'type': 'accent_shape', 'shape': 'rect', 'x': cx, 'y': y, 'w': 0.035, 'h': bar_h,
                       'fill': theme.get('accent', 'FFCC33')})
        shapes.extend(col_shapes)
    return shapes


def render_image_caption(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    img_ref = slots.get('image_path_or_prompt')
    img_source = slots.get('image_source') if slots.get('image_source') in ('document', 'generate') else None
    if not img_ref or not img_source:
        return render_narrative(title, slots, items, theme, canvas, margin, mode, slide)

    shapes, y = _title_block(title, theme, canvas, margin, font_size=26)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    chosen = _resolve_indices(items, slots.get('item_indices')) if slots.get('item_indices') else []
    caption = str(slots.get('caption') or '')[:200]

    if chosen:
        img_w = (x1 - x0) * 0.52
        img_h = (y1 - y) - 0.4
        shapes.append(_image_shape(img_source, img_ref, x0, y, img_w, img_h))
        if caption:
            shapes.append({'type': 'bullet', 'text': caption, 'x': x0, 'y': y + img_h + 0.06, 'w': img_w,
                           'h': 0.32, 'font_size_hint': 12, 'color': theme.get('dim_dark', '888888')})
        text_x = x0 + img_w + 0.3
        text_w = x1 - text_x
        body_shapes, _bottom = _stack_items(chosen, text_x, y, text_w, font_size=15, max_h=y1 - y)
        shapes.extend(body_shapes)
    else:
        img_w = (x1 - x0) * 0.7
        img_x = x0 + ((x1 - x0) - img_w) / 2
        img_h = (y1 - y) - 0.4
        shapes.append(_image_shape(img_source, img_ref, img_x, y, img_w, img_h))
        if caption:
            shapes.append({'type': 'bullet', 'text': caption, 'x': img_x, 'y': y + img_h + 0.06, 'w': img_w,
                           'h': 0.32, 'font_size_hint': 12, 'color': theme.get('dim_dark', '888888'),
                           'align': 'center'})
    return shapes


def render_hero_image(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    img_ref = slots.get('image_path_or_prompt')
    img_source = slots.get('image_source') if slots.get('image_source') in ('document', 'generate') else None
    if not img_ref or not img_source:
        return render_narrative(title, slots, items, theme, canvas, margin, mode, slide)

    shapes = [_image_shape(img_source, img_ref, 0, 0, canvas['w'], canvas['h'])]
    band_y = canvas['h'] * 0.56
    band_h = canvas['h'] - band_y
    shapes.append({'type': 'scrim', 'fill': '#000000', 'opacity': 0.48, 'x': 0, 'y': band_y,
                   'w': canvas['w'], 'h': band_h})

    x0 = margin
    w = canvas['w'] - 2 * margin
    cy = band_y + 0.22
    kicker = str(slots.get('kicker') or '')[:60]
    if kicker:
        shapes.append({'type': 'bullet', 'text': kicker, 'x': x0, 'y': cy, 'w': w, 'h': 0.28,
                       'font_size_hint': 13, 'color': theme.get('accent', 'FFCC33')})
        cy += 0.32
    title_h = max(0.6, _required_text_height(title or '', 32, w))
    shapes.append({'type': 'title', 'text': title or '', 'x': x0, 'y': cy, 'w': w, 'h': title_h,
                   'font_size_hint': 32, 'color': theme.get('text_dark', 'FFFFFF')})
    cy += title_h + 0.08
    subtitle = str(slots.get('subtitle') or '')[:160]
    if subtitle:
        shapes.append({'type': 'bullet', 'text': subtitle, 'x': x0, 'y': cy, 'w': w, 'h': 0.4,
                       'font_size_hint': 15, 'color': theme.get('dim_light', 'DDDDDD')})
    return shapes


def render_stat_row(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    stats = slots.get('stats') if isinstance(slots.get('stats'), list) else []
    valid = [s for s in stats if isinstance(s, dict) and s.get('value') and s.get('label')
             and _verbatim_in_slide(str(s['value']), slide, title, items)]
    valid = valid[:4]
    if len(valid) < 2:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)

    x0, _y0, x1, _y1 = _usable(canvas, margin)
    shapes = [{'type': 'bullet', 'text': title or '', 'x': x0, 'y': margin + 0.1, 'w': x1 - x0, 'h': 0.34,
              'font_size_hint': 14, 'color': theme.get('accent', 'FFCC33')}]
    n = len(valid)
    stat_y = margin + 0.6
    gap = 0.41
    card_w = (x1 - x0 - gap * (n - 1)) / n
    for i, st in enumerate(valid):
        cx = x0 + i * (card_w + gap)
        shapes.append({'type': 'accent_shape', 'shape': 'rect', 'x': cx, 'y': stat_y, 'w': card_w,
                       'h': 0.017, 'fill': theme.get('accent', 'FFCC33')})
        shapes.append({'type': 'title', 'text': str(st['value']), 'x': cx, 'y': stat_y + 0.1, 'w': card_w,
                       'h': 1.3, 'font_size_hint': 36, 'color': theme.get('accent', 'FFCC33')})
        shapes.append({'type': 'bullet', 'text': str(st['label'])[:90], 'x': cx, 'y': stat_y + 1.5,
                       'w': card_w, 'h': 0.6, 'font_size_hint': 14, 'color': theme.get('dim_dark', 'AAAAAA')})
    return shapes


def render_comparison(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    left = slots.get('left') if isinstance(slots.get('left'), dict) else {}
    right = slots.get('right') if isinstance(slots.get('right'), dict) else {}
    if not left or not right:
        return render_narrative(title, slots, items, theme, canvas, margin, mode, slide)

    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    gap = 0.3
    half_w = (x1 - x0 - gap) / 2
    shapes.append({'type': 'accent_shape', 'shape': 'rect', 'x': x0 + half_w + gap / 2 - 0.005, 'y': y,
                   'w': 0.01, 'h': y1 - y, 'fill': theme.get('dim_dark', '888888')})
    for side, sx in ((left, x0), (right, x0 + half_w + gap)):
        cy = y
        label = str(side.get('label') or '')[:60]
        if label:
            shapes.append({'type': 'bullet', 'text': label, 'x': sx, 'y': cy, 'w': half_w, 'h': 0.3,
                           'font_size_hint': 15, 'color': theme.get('accent', 'FFCC33')})
            cy += 0.36
        chosen = _resolve_indices(items, side.get('item_indices')) if side.get('item_indices') else []
        body_shapes, _bottom = _stack_items(chosen, sx, cy, half_w, font_size=14, max_h=y1 - cy)
        shapes.extend(body_shapes)
    return shapes


def render_quote_focus(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    idx = slots.get('quote_item_index')
    quote = items[idx] if isinstance(idx, int) and 0 <= idx < len(items) else None
    if not quote:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)

    x0, y0, x1, y1 = _usable(canvas, margin)
    w = (x1 - x0) * 0.8
    x = x0 + ((x1 - x0) - w) / 2
    qh = max(0.8, _required_text_height(quote, 26, w))
    y = y0 + max(0.2, (y1 - y0 - qh) / 2 - 0.3)
    shapes = [
        {'type': 'accent_shape', 'shape': 'rect', 'x': x, 'y': y - 0.2, 'w': 0.6, 'h': 0.05,
         'fill': theme.get('accent', 'FFCC33')},
        {'type': 'title', 'text': quote, 'x': x, 'y': y, 'w': w, 'h': qh, 'font_size_hint': 26},
    ]
    attribution = str(slots.get('attribution') or '')[:80]
    if attribution:
        shapes.append({'type': 'bullet', 'text': f'— {attribution}', 'x': x, 'y': y + qh + 0.15,
                       'w': w, 'h': 0.3, 'font_size_hint': 14, 'color': theme.get('dim_dark', '888888')})
    return shapes


def render_numbered_list(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    chosen_idx = slots.get('item_indices')
    chosen = (_resolve_indices(items, chosen_idx) if isinstance(chosen_idx, list) else list(items))[:8]
    if not chosen:
        return render_narrative(title, slots, items, theme, canvas, margin, mode, slide)
    if len(chosen) <= 5:
        body, _bottom = _stack_numbered_items(chosen, x0, y, x1 - x0, theme, font_size=16, max_h=y1 - y)
        shapes.extend(body)
    else:
        mid = (len(chosen) + 1) // 2
        col_w = (x1 - x0 - 0.4) / 2
        left, _b1 = _stack_numbered_items(chosen[:mid], x0, y, col_w, theme, font_size=14,
                                          max_h=y1 - y, start_n=1)
        right, _b2 = _stack_numbered_items(chosen[mid:], x0 + col_w + 0.4, y, col_w, theme, font_size=14,
                                           max_h=y1 - y, start_n=mid + 1)
        shapes.extend(left)
        shapes.extend(right)
    return shapes


def render_pillar_columns(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    cols = [c for c in (slots.get('columns') or []) if isinstance(c, dict)][:4]
    if not cols:
        return render_bullet_columns(title, slots, items, theme, canvas, margin, mode, slide)

    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    n = len(cols)
    col_gap = 0.3
    col_w = (x1 - x0 - col_gap * (n - 1)) / n
    palette = [theme.get('accent', 'FFCC33'), theme.get('c1', '5B9BD5'),
               theme.get('c2', 'E07B6A'), theme.get('c3', '7B68C8')]
    for c, col in enumerate(cols):
        cx = x0 + c * (col_w + col_gap)
        fill = palette[c % len(palette)]
        shapes.append({'type': 'accent_shape', 'shape': 'rect', 'x': cx, 'y': y, 'w': col_w, 'h': 0.05,
                       'fill': fill})
        cy = y + 0.15
        shapes.append({'type': 'title', 'text': f'{c + 1:02d}', 'x': cx, 'y': cy, 'w': col_w, 'h': 0.3,
                       'font_size_hint': 15, 'color': fill})
        cy += 0.34
        header = str(col.get('header') or '')[:50]
        if header:
            hh = _required_text_height(header, 15, col_w)  # real height so a 2-line header doesn't
            shapes.append({'type': 'bullet', 'text': header, 'x': cx, 'y': cy, 'w': col_w, 'h': hh,
                           'font_size_hint': 15})           # get overlapped by the body below it
            cy += hh + 0.1
        chosen = _resolve_indices(items, col.get('item_indices')) if col.get('item_indices') else []
        body, _bottom = _stack_items(chosen, cx, cy, col_w, font_size=13, max_h=y1 - cy)
        shapes.extend(body)
    return shapes


def render_cover_hook(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    hook_raw = str(slots.get('hook_text') or '').strip()
    if not (hook_raw and _verbatim_in_slide(hook_raw, slide, title, items)):
        # Demote to real content when available (items[0] IS slide['_cover_hook'] itself, picked up
        # automatically by _extract_slide_items since '_cover_hook' isn't in _NON_CONTENT_KEYS) --
        # never blank, never fabricated; identical to today's bare-title baseline when no hook exists.
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)

    hook = hook_raw[:220]
    x0, y0, x1, y1 = _usable(canvas, margin)
    w = x1 - x0
    shapes = []
    cy = y0 + (y1 - y0) * 0.2
    kicker = str(slots.get('kicker') or '')[:60]
    if kicker:
        shapes.append({'type': 'bullet', 'text': kicker, 'x': x0, 'y': cy, 'w': w, 'h': 0.3,
                       'font_size_hint': 14, 'color': theme.get('accent', 'FFCC33')})
        cy += 0.36
    title_h = max(0.7, _required_text_height(title or '', 40, w))
    shapes.append({'type': 'title', 'text': title or '', 'x': x0, 'y': cy, 'w': w, 'h': title_h,
                   'font_size_hint': 40})
    cy += title_h + 0.12
    shapes.append({'type': 'accent_shape', 'shape': 'rect', 'x': x0, 'y': cy, 'w': 1.1, 'h': 0.05,
                   'fill': theme.get('accent', 'FFCC33')})
    cy += 0.22
    hook_w = w * 0.82
    hook_h = _required_text_height(hook, 18, hook_w)
    shapes.append({'type': 'bullet', 'text': hook, 'x': x0, 'y': cy, 'w': hook_w, 'h': hook_h,
                   'font_size_hint': 18, 'color': theme.get('dim_light', 'DDDDDD')})
    return shapes


def render_agenda_map(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    chosen_idx = slots.get('item_indices')
    chosen = _resolve_indices(items, chosen_idx) if isinstance(chosen_idx, list) else list(items)
    if not chosen:
        return render_narrative(title, slots, items, theme, canvas, margin, mode, slide)

    shapes, y = _title_block(title, theme, canvas, margin, font_size=28)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    if len(chosen) <= 3:
        body, _bottom = _stack_numbered_items(chosen, x0, y, x1 - x0, theme, font_size=17, max_h=y1 - y)
        shapes.extend(body)
    else:
        mid = (len(chosen) + 1) // 2
        col_w = (x1 - x0 - 0.5) / 2
        left, _b1 = _stack_numbered_items(chosen[:mid], x0, y, col_w, theme, font_size=15,
                                          max_h=y1 - y, start_n=1)
        right, _b2 = _stack_numbered_items(chosen[mid:], x0 + col_w + 0.5, y, col_w, theme, font_size=15,
                                           max_h=y1 - y, start_n=mid + 1)
        shapes.extend(left)
        shapes.extend(right)
    return shapes


# ---------------------------------------------------------------------------
# Round 4: 29 new patterns -- thin wrappers around the 8 shared engines in pptx_pattern_engines.py.
# Each still validates its own slots and demotes to render_narrative on missing/invalid input, same
# convention as the patterns above.
# ---------------------------------------------------------------------------

def render_process_flow(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    idxs = slots.get('item_indices')
    chosen = _resolve_indices(items, idxs) if isinstance(idxs, list) else list(items)
    if not chosen:
        return render_narrative(title, slots, items, theme, canvas, margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    nodes = [{'label': t} for t in chosen[:6]]
    shapes.extend(flow_engine(nodes, x0, y, x1, y1, theme, orientation='horizontal',
                              marker_shape='roundRect', connector_shape='rightArrow'))
    return shapes


def render_chevron_process(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    idxs = slots.get('item_indices')
    chosen = _resolve_indices(items, idxs) if isinstance(idxs, list) else list(items)
    if not chosen:
        return render_narrative(title, slots, items, theme, canvas, margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    nodes = [{'label': t} for t in chosen[:6]]
    shapes.extend(flow_engine(nodes, x0, y, x1, y1, theme, orientation='horizontal',
                              marker_shape='chevron', connector_shape=None))
    return shapes


def render_timeline(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    idxs = slots.get('item_indices')
    chosen = _resolve_indices(items, idxs) if isinstance(idxs, list) else list(items)
    if not chosen:
        return render_narrative(title, slots, items, theme, canvas, margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    nodes = [{'label': t} for t in chosen[:6]]
    shapes.extend(flow_engine(nodes, x0, y, x1, y1, theme, orientation='horizontal',
                              marker_shape='roundRect', connector_shape=None))
    return shapes


def render_pipeline_with_stages(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    stages = slots.get('stages') if isinstance(slots.get('stages'), list) else []
    valid = [s for s in stages if isinstance(s, dict) and isinstance(s.get('item_index'), int)
             and 0 <= s['item_index'] < len(items)]
    if not valid:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    nodes = [{'label': items[s['item_index']], 'sublabel': str(s.get('output') or '')[:50]}
             for s in valid[:5]]
    shapes.extend(flow_engine(nodes, x0, y, x1, y1, theme, orientation='horizontal',
                              marker_shape='roundRect', connector_shape='rightArrow', show_sublabel=True))
    return shapes


def render_roadmap_vertical(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    idxs = slots.get('item_indices')
    chosen = _resolve_indices(items, idxs) if isinstance(idxs, list) else list(items)
    if not chosen:
        return render_narrative(title, slots, items, theme, canvas, margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    nodes = [{'label': t} for t in chosen[:6]]
    shapes.extend(flow_engine(nodes, x0, y, x1, y1, theme, orientation='vertical',
                              marker_shape='roundRect', connector_shape='downArrow'))
    return shapes


def render_hub_spoke(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    idxs = slots.get('item_indices')
    chosen = _resolve_indices(items, idxs) if isinstance(idxs, list) else list(items)
    if len(chosen) < 3:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    x0, y0, x1, y1 = _usable(canvas, margin)
    spokes = [{'label': t} for t in chosen[:8]]
    return radial_spoke_engine(title or None, spokes, x0, y0, x1, y1, theme, connector_mode='arrow_out')


def render_hub_inward_arrows(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    idxs = slots.get('item_indices')
    chosen = _resolve_indices(items, idxs) if isinstance(idxs, list) else list(items)
    if len(chosen) < 3:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    x0, y0, x1, y1 = _usable(canvas, margin)
    spokes = [{'label': t} for t in chosen[:8]]
    return radial_spoke_engine(title or None, spokes, x0, y0, x1, y1, theme, connector_mode='arrow_in')


def render_segmented_wheel(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    idxs = slots.get('item_indices')
    chosen = _resolve_indices(items, idxs) if isinstance(idxs, list) else list(items)
    if len(chosen) < 3:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    x0, y0, x1, y1 = _usable(canvas, margin)
    spokes = [{'label': t} for t in chosen[:8]]
    return radial_spoke_engine(title or None, spokes, x0, y0, x1, y1, theme, node_touching=True,
                               connector_mode='none')


def render_concentric_circles(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    idxs = slots.get('item_indices')
    chosen = _resolve_indices(items, idxs) if isinstance(idxs, list) else list(items)
    if len(chosen) < 2:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    rings = [{'label': t} for t in chosen[:4]]
    shapes.extend(radial_ring_engine(rings, x0, y, x1, y1, theme))
    return shapes


def render_pyramid_chart(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    idxs = slots.get('item_indices')
    chosen = _resolve_indices(items, idxs) if isinstance(idxs, list) else list(items)
    if len(chosen) < 2:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    tiers = [{'label': t} for t in chosen[:7]]
    shapes.extend(tier_engine(tiers, x0, y, x1, y1, theme, direction='narrow_top'))
    return shapes


def render_funnel(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    idxs = slots.get('item_indices')
    chosen = _resolve_indices(items, idxs) if isinstance(idxs, list) else list(items)
    if len(chosen) < 2:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    tiers = [{'label': t} for t in chosen[:7]]
    shapes.extend(tier_engine(tiers, x0, y, x1, y1, theme, direction='narrow_bottom'))
    return shapes


def render_iceberg(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    idxs = slots.get('item_indices')
    chosen = _resolve_indices(items, idxs) if isinstance(idxs, list) else list(items)
    if len(chosen) < 2:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    tiers = [{'label': chosen[0]}, {'label': chosen[1]}]
    shapes.extend(tier_engine(tiers, x0, y, x1, y1, theme, height_fracs=[0.25, 0.75]))
    return shapes


def render_top_down_tree(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    levels_idx = slots.get('levels') if isinstance(slots.get('levels'), list) else []
    levels = []
    for lvl in levels_idx[:4]:
        if isinstance(lvl, list):
            texts = _resolve_indices(items, lvl)
            levels.append([{'label': t} for t in texts[:5]])
    if not levels:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    shapes.extend(tree_engine(levels, x0, y, x1, y1, theme, connectors=True))
    return shapes


def render_layered_architecture(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    levels_idx = slots.get('levels') if isinstance(slots.get('levels'), list) else []
    levels = []
    for lvl in levels_idx[:4]:
        if isinstance(lvl, list):
            texts = _resolve_indices(items, lvl)
            levels.append([{'label': t} for t in texts[:5]])
    if not levels:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    shapes.extend(tree_engine(levels, x0, y, x1, y1, theme, connectors=False))
    return shapes


def render_module_composition(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    parent_idx = slots.get('parent_index')
    child_idxs = slots.get('child_indices')
    if not isinstance(parent_idx, int) or not (0 <= parent_idx < len(items)) \
            or not isinstance(child_idxs, list):
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    children = _resolve_indices(items, child_idxs)
    if not children:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    parent_h = 0.55
    shapes.extend(tree_engine([[{'label': items[parent_idx]}]], x0, y, x1, y + parent_h, theme,
                              connectors=False))
    gap = 0.15
    cells = [{'label': t} for t in children[:8]]
    shapes.extend(card_grid_engine(cells, x0, y + parent_h + gap, x1, y1, theme))
    return shapes


def render_icon_grid(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    idxs = slots.get('item_indices')
    chosen = _resolve_indices(items, idxs) if isinstance(idxs, list) else list(items)
    if not chosen:
        return render_narrative(title, slots, items, theme, canvas, margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    cells = [{'label': t} for t in chosen[:9]]
    shapes.extend(card_grid_engine(cells, x0, y, x1, y1, theme, icon=True))
    return shapes


def _quadrant_cells(items: list[str], quads) -> list[dict]:
    cells = []
    if isinstance(quads, list):
        for q in quads[:4]:
            if not isinstance(q, dict):
                continue
            label = str(q.get('label') or '')[:40]
            body_items = _resolve_indices(items, q.get('item_indices')) if q.get('item_indices') else []
            cells.append({'label': label, 'body': ' | '.join(body_items)[:200]})
    return cells


def render_quadrant_text_bullets(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    cells = _quadrant_cells(items, slots.get('quadrants'))
    if len(cells) < 2:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    shapes.extend(card_grid_engine(cells, x0, y, x1, y1, theme, cols=2))
    return shapes


def render_matrix_2x2(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    cells = _quadrant_cells(items, slots.get('quadrants'))
    if len(cells) < 2:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    axis_labels = {
        'top_left': str(slots.get('x_axis_low') or '')[:20],
        'top_right': str(slots.get('x_axis_high') or '')[:20],
        'bottom_left': str(slots.get('y_axis_low') or '')[:20],
        'bottom_right': str(slots.get('y_axis_high') or '')[:20],
    }
    shapes.extend(card_grid_engine(cells, x0, y, x1, y1, theme, cols=2, axis_labels=axis_labels))
    return shapes


def render_checklist(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    idxs = slots.get('item_indices')
    chosen = _resolve_indices(items, idxs) if isinstance(idxs, list) else list(items)
    if not chosen:
        return render_narrative(title, slots, items, theme, canvas, margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    cells = [{'label': t} for t in chosen[:10]]
    shapes.extend(card_grid_engine(cells, x0, y, x1, y1, theme, cols=1))
    return shapes


def render_feature_matrix(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    headers = slots.get('headers') if isinstance(slots.get('headers'), list) else []
    rows = slots.get('rows') if isinstance(slots.get('rows'), list) else []
    if not headers or not rows:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    n_cols = max(1, min(len(headers), 4))
    cells = [{'label': str(h)[:24]} for h in headers[:n_cols]]
    for row in rows[:4]:
        if not isinstance(row, list):
            continue
        for v in row[:n_cols]:
            cells.append({'label': str(v)[:24]})
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    shapes.extend(card_grid_engine(cells, x0, y, x1, y1, theme, cols=n_cols, header_row=True))
    return shapes


def _split_side(items: list[str], side: dict, default_label: str = '') -> dict:
    return {
        'label': side.get('label') or default_label,
        'body_items': _resolve_indices(items, side.get('item_indices')) if side.get('item_indices') else [],
    }


def render_pros_cons(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    left = slots.get('left') if isinstance(slots.get('left'), dict) else {}
    right = slots.get('right') if isinstance(slots.get('right'), dict) else {}
    if not left or not right:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    left_d = _split_side(items, left, 'Pros')
    right_d = _split_side(items, right, 'Cons')
    shapes.extend(split_engine(left_d, right_d, x0, y, x1, y1, theme, mode='columns', style='plus_minus'))
    return shapes


def render_problem_solution(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    left = slots.get('left') if isinstance(slots.get('left'), dict) else {}
    right = slots.get('right') if isinstance(slots.get('right'), dict) else {}
    if not left or not right:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    left_d = _split_side(items, left, 'Problem')
    right_d = _split_side(items, right, 'Solution')
    shapes.extend(split_engine(left_d, right_d, x0, y, x1, y1, theme, mode='columns', style='plain'))
    return shapes


def render_before_after_images(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    left = slots.get('left') if isinstance(slots.get('left'), dict) else {}
    right = slots.get('right') if isinstance(slots.get('right'), dict) else {}
    if not (left.get('image_source') and left.get('image_path_or_prompt') and
            right.get('image_source') and right.get('image_path_or_prompt')):
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    shapes.extend(split_engine(left, right, x0, y, x1, y1, theme, mode='columns', style='images'))
    return shapes


def render_spectrum_continuum(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    left = slots.get('left') if isinstance(slots.get('left'), dict) else {}
    right = slots.get('right') if isinstance(slots.get('right'), dict) else {}
    if not left.get('label') or not right.get('label'):
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    shapes.extend(split_engine(left, right, x0, y, x1, y1, theme, mode='spectrum'))
    return shapes


def render_editorial_pullquote(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    qidx = slots.get('quote_item_index')
    quote = items[qidx] if isinstance(qidx, int) and 0 <= qidx < len(items) else None
    body_idxs = slots.get('item_indices')
    body_items = _resolve_indices(items, body_idxs) if isinstance(body_idxs, list) else []
    if not quote or not body_items:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    left_d = {'label': '', 'body_items': [quote]}
    right_d = {'label': '', 'body_items': body_items}
    shapes.extend(split_engine(left_d, right_d, x0, y, x1, y1, theme, mode='columns', style='pullquote'))
    return shapes


def render_big_number(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    stat = slots.get('stat') if isinstance(slots.get('stat'), dict) else {}
    value = str(stat.get('value') or '').strip()
    if not (value and _verbatim_in_slide(value, slide, title, items)):
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    x0, y0, x1, y1 = _usable(canvas, margin)
    interp = str(stat.get('interpretation') or '')[:180]
    return emphasis_engine(value, interp, x0, y0, x1, y1, theme, mode='number')


def render_section_divider(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    x0, y0, x1, y1 = _usable(canvas, margin)
    subtitle = str(slots.get('subtitle') or '')[:160]
    return emphasis_engine(title or '', subtitle, x0, y0, x1, y1, theme, mode='section')


def render_closing_summary(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    idxs = slots.get('item_indices')
    chosen = _resolve_indices(items, idxs) if isinstance(idxs, list) else list(items)
    if not chosen:
        return render_narrative(title, slots, items, theme, canvas, margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin, font_size=36)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    shapes.extend(emphasis_engine('', chosen, x0, y, x1, y1, theme, mode='summary'))
    return shapes


def render_faq_qa(title, slots, items, theme, canvas, margin, mode, slide) -> list[dict]:
    pairs_in = slots.get('pairs') if isinstance(slots.get('pairs'), list) else []
    pairs = []
    for p in pairs_in[:5]:
        if not isinstance(p, dict):
            continue
        q = str(p.get('question') or '')[:120]
        aidx = p.get('answer_item_index')
        if q and isinstance(aidx, int) and 0 <= aidx < len(items):
            pairs.append((q, items[aidx]))
    if not pairs:
        return render_narrative(title, {'item_indices': list(range(len(items)))}, items, theme, canvas,
                                margin, mode, slide)
    shapes, y = _title_block(title, theme, canvas, margin)
    x0, _y0, x1, y1 = _usable(canvas, margin)
    shapes.extend(emphasis_engine('', pairs, x0, y, x1, y1, theme, mode='qa_pairs'))
    return shapes


_RENDERERS = {
    'narrative': render_narrative,
    'bullet_columns': render_bullet_columns,
    'image_caption': render_image_caption,
    'hero_image': render_hero_image,
    'stat_row': render_stat_row,
    'comparison': render_comparison,
    'quote_focus': render_quote_focus,
    'numbered_list': render_numbered_list,
    'pillar_columns': render_pillar_columns,
    'cover_hook': render_cover_hook,
    'agenda_map': render_agenda_map,
    'process_flow': render_process_flow,
    'chevron_process': render_chevron_process,
    'timeline': render_timeline,
    'pipeline_with_stages': render_pipeline_with_stages,
    'roadmap_vertical': render_roadmap_vertical,
    'hub_spoke': render_hub_spoke,
    'hub_inward_arrows': render_hub_inward_arrows,
    'segmented_wheel': render_segmented_wheel,
    'concentric_circles': render_concentric_circles,
    'pyramid_chart': render_pyramid_chart,
    'funnel': render_funnel,
    'iceberg': render_iceberg,
    'top_down_tree': render_top_down_tree,
    'layered_architecture': render_layered_architecture,
    'module_composition': render_module_composition,
    'icon_grid': render_icon_grid,
    'quadrant_text_bullets': render_quadrant_text_bullets,
    'matrix_2x2': render_matrix_2x2,
    'checklist': render_checklist,
    'feature_matrix': render_feature_matrix,
    'pros_cons': render_pros_cons,
    'problem_solution': render_problem_solution,
    'before_after_images': render_before_after_images,
    'spectrum_continuum': render_spectrum_continuum,
    'editorial_pullquote': render_editorial_pullquote,
    'big_number': render_big_number,
    'section_divider': render_section_divider,
    'closing_summary': render_closing_summary,
    'faq_qa': render_faq_qa,
}

_REQUIRED_IMAGE_COUNT = {'hero_image': 1, 'image_caption': 1, 'before_after_images': 2}


def render_pattern(choice: dict, slide: dict, theme: dict, canvas: Optional[dict] = None,
                   margin: float = MARGIN_DEFAULT, add_page_number: bool = True) -> tuple[list[dict], str]:
    """Dispatch choice['pattern'] to its render_<pattern> function, compute REAL geometry (no LLM
    coordinate-guessing), append a page-number corner mark, and defensively assert zero overlap (imported
    lazily from deck_pptx_exporter to avoid a circular import -- by call time both modules are loaded).
    Raises PatternValidationError for anything not auto-repairable; each render_* function already
    demotes itself to `narrative` for common recoverable mistakes (bad stat count, missing comparison
    side, invalid quote index, missing image ref) without needing an LLM round-trip. `add_page_number`
    can be turned off when the caller draws its own header/footer chrome (e.g. the composed path's
    addChrome) so the page number isn't drawn twice."""
    canvas = canvas or CANVAS_DEFAULT
    if not isinstance(choice, dict):
        raise PatternValidationError('choice must be a JSON object')
    pattern_name = choice.get('pattern')
    render_fn = _RENDERERS.get(pattern_name)
    if render_fn is None:
        raise PatternValidationError(f'unknown pattern name: {pattern_name!r}')

    mode = choice.get('mode') if choice.get('mode') in ('dark', 'light') else (theme.get('default_mode') or 'dark')
    slots = choice.get('slots') if isinstance(choice.get('slots'), dict) else {}
    title = slide.get('title') or slide.get('slide_title') or ''
    items = _extract_slide_items(slide)

    try:
        shapes = render_fn(title, slots, items, theme, canvas, margin, mode, slide)
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise PatternValidationError(f'{pattern_name} rendering failed: {e}') from e
    if not shapes:
        raise PatternValidationError(f'{pattern_name} produced no shapes')

    if add_page_number:
        page_shape = _page_number_shape(slide.get('page_num'), theme, canvas, margin)
        if page_shape:
            shapes.append(page_shape)

    if _find_overlaps(shapes):
        raise PatternValidationError(f'{pattern_name} produced overlapping shapes (internal geometry bug)')

    return shapes, mode
