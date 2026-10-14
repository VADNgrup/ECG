from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Optional

# Lower-level module: zero dependency on pptx_patterns.py or deck_pptx_exporter.py (both import FROM
# here) -- this is also where the shape-overlap/duplicate-word safety checks live (moved from
# deck_pptx_exporter.py), since pptx_patterns.py's render_pattern() needs _find_overlaps for its
# defensive assertion and importing it from deck_pptx_exporter.py would be a circular import
# (deck_pptx_exporter.py imports pptx_patterns.py at module level already). Putting the shared,
# dependency-free pieces at the bottom of this DAG lets every caller use a normal top-of-file import.

_DUP_WORD_RE = re.compile(r'\b(\w+)\s+\1\b', re.IGNORECASE)


def _shape_rect(s: dict) -> tuple:
    x, y = float(s.get('x') or 0), float(s.get('y') or 0)
    w, h = float(s.get('w') or 0), float(s.get('h') or 0)
    return x, y, x + w, y + h


def _rects_overlap(a: tuple, b: tuple, tolerance: float = 0.02) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return (ax0 < bx1 - tolerance and ax1 > bx0 + tolerance and
            ay0 < by1 - tolerance and ay1 > by0 + tolerance)


def _rect_contains(outer: tuple, inner: tuple, tolerance: float = 0.02) -> bool:
    """True when `inner` sits entirely within `outer` (with a small inward tolerance) -- the
    "background box behind text" pattern: since buildComposed() always z-orders decorative shapes
    BEFORE text (see deck_pptx_gen.js zTier), a shape that fully contains a text box is a deliberate
    label/chip/highlight background, never actually hides the text -- only a shape that PARTIALLY
    overlaps (cuts through part of the text, like a line slicing through mid-word) looks visually
    broken and should still be flagged."""
    ox0, oy0, ox1, oy1 = outer
    ix0, iy0, ix1, iy1 = inner
    return (ox0 <= ix0 + tolerance and oy0 <= iy0 + tolerance and
            ox1 >= ix1 - tolerance and oy1 >= iy1 - tolerance)


def _find_overlaps(shapes: list) -> list:
    """Pairwise AABB overlap check between all TEXT-bearing shapes (title/bullet) -- a decorative shape
    may render behind another shape in z-order, but its bounding box must still never PARTIALLY
    intersect a text shape's bounding box -- real testing showed a title overlapping a "decorative"
    accent bar still looks broken even though the bar draws behind the text in z-order (the underline
    visually cuts through the last line). So: flag any pair where AT LEAST ONE side is a text shape
    (title/bullet); two purely-decorative shapes overlapping each other is allowed (may be an
    intentional layered design, e.g. a shifted duplicate for a shadow effect).

    EXCEPTIONS: an `image` and a `scrim` are allowed to sit directly under text -- a full-bleed
    background image with floating title text (optionally with a semi-transparent scrim for
    legibility) is a deliberate, desired hero-slide pattern (ppt-master's real technique). A decorative
    shape (accent_shape/freeform) that FULLY CONTAINS a text box is also allowed -- that's a
    deliberate label/chip/highlight background (text sits entirely on top, never obscured), a
    different case from a shape only partially cutting through the text, which is still flagged.
    Returns (index_a, index_b) violation pairs."""
    violations = []
    for i in range(len(shapes)):
        for j in range(i + 1, len(shapes)):
            a, b = shapes[i], shapes[j]
            if a.get('type') == 'connector' or b.get('type') == 'connector':
                continue
            if a.get('type') in ('scrim', 'image') or b.get('type') in ('scrim', 'image'):
                continue
            a_is_text = a.get('type') in ('title', 'bullet') and a.get('text')
            b_is_text = b.get('type') in ('title', 'bullet') and b.get('text')
            if not (a_is_text or b_is_text):
                continue
            if not _rects_overlap(_shape_rect(a), _shape_rect(b)):
                continue
            a_is_decor = a.get('type') in ('accent_shape', 'freeform')
            b_is_decor = b.get('type') in ('accent_shape', 'freeform')
            if a_is_text and b_is_decor and _rect_contains(_shape_rect(b), _shape_rect(a)):
                continue  # b is a background box fully containing text a -- allowed
            if b_is_text and a_is_decor and _rect_contains(_shape_rect(a), _shape_rect(b)):
                continue  # a is a background box fully containing text b -- allowed
            violations.append((i, j))
    return violations


def _find_duplicate_words(shapes: list) -> list:
    """Flags an LLM duplicating a word while copying text verbatim (e.g. "program program elements").
    Returns (index, matched_phrase) pairs."""
    violations = []
    for i, s in enumerate(shapes):
        m = _DUP_WORD_RE.search(s.get('text') or '')
        if m:
            violations.append((i, m.group(0)))
    return violations


def _find_implausible_font_size(shapes: list, min_pt: float = 6.0) -> list:
    """A font_size_hint under min_pt is never a real intentional choice for slide text -- almost
    always the LLM wrote font-size in the SAME unit as x/y/width/height (inches) instead of points
    (e.g. "0.36" meaning 0.36in ~= 26pt, not literally 0.36pt). Below OOXML's own minimum valid `sz`
    (1pt = 100 hundredths), this would otherwise silently produce a PPTX PowerPoint refuses to open.
    Returns (index, font_size) pairs."""
    violations = []
    for i, s in enumerate(shapes):
        if s.get('type') not in ('title', 'bullet') or not s.get('text'):
            continue
        fs = s.get('font_size_hint')
        if fs is not None and 0 < float(fs) < min_pt:
            violations.append((i, float(fs)))
    return violations


def _find_missing_text_size(shapes: list) -> list:
    """A <text> element without explicit width/height silently falls back to a hardcoded 2.0in x
    0.4in (_element_to_shapes's default) -- far too small for most real bullet text, and the single
    largest source of "needs height" violations seen in practice (the LLM omits these two required
    attributes even though the vocabulary lists them, same failure mode already fixed for missing
    fill/font-size). Catch the omission itself instead of silently using a wrong-sized default.
    Returns violating indices."""
    return [i for i, s in enumerate(shapes)
            if s.get('type') in ('title', 'bullet') and s.get('text') and not s.get('size_declared', True)]


def _find_duplicate_item_refs(shapes: list) -> list:
    """Two separate <text> elements referencing the SAME data-item index -- the LLM drew the same
    content item twice (usually while trying to "wrap" one item across multiple boxes), which
    almost always overlaps itself. Returns (first_idx, second_idx, item_index) triples."""
    seen: dict[int, int] = {}
    violations = []
    for i, s in enumerate(shapes):
        idx = s.get('item_index')
        if idx is None:
            continue
        if idx in seen:
            violations.append((seen[idx], i, idx))
        else:
            seen[idx] = i
    return violations


def _estimate_text_lines(text: str, font_size_pt: float, box_w_in: float) -> int:
    text = (text or '').strip()
    if not text:
        return 1
    # Average glyph width for Aptos/Arial-like presentation fonts.  The previous
    # uppercase-width approximation made ordinary prose appear 35-45% taller than
    # PowerPoint actually renders it and caused pathological one-bullet pages.
    char_w_in = max(0.01, font_size_pt * 0.0070)
    chars_per_line = max(1, int(box_w_in / char_w_in))
    lines, cur = 1, 0
    for word in text.split():
        wl = len(word) + 1
        if cur + wl > chars_per_line and cur > 0:
            lines += 1
            cur = wl
        else:
            cur += wl
    return lines


def _required_text_height(text: str, font_size_pt: float, box_w_in: float) -> float:
    lines = _estimate_text_lines(text, font_size_pt, box_w_in)
    line_h_in = font_size_pt * 1.28 / 72
    return round(lines * line_h_in + 0.06, 3)


def _autoshrink_text_shapes(shapes: list, min_pt: float = 10.0) -> list:
    """Deterministically shrink a text shape's own font size (never its box) until its own text fits
    the box it was given -- removes the single largest source of retry/fallback (text overflow)
    WITHOUT an extra LLM round-trip, same shrink-to-fit principle _stack_items()/addBullets() already
    use for the fixed-template path. A shape that still doesn't fit at min_pt is left alone for
    _find_text_overflow to catch -- a genuinely too-small box is a real layout mistake worth
    surfacing, not silently clipping/shrinking into illegibility."""
    for s in shapes:
        if s.get('type') not in ('title', 'bullet') or not s.get('text'):
            continue
        w, h = float(s.get('w') or 0), float(s.get('h') or 0)
        if w <= 0 or h <= 0:
            continue
        fs = float(s.get('font_size_hint') or (28 if s.get('type') == 'title' else 14))
        while fs > min_pt and _required_text_height(s['text'], fs, w) > h:
            fs -= 1
        s['font_size_hint'] = fs
    return shapes


def _image_shape(source: str, path_or_prompt: str, x: float, y: float, w: float, h: float) -> dict:
    shape = {'type': 'image', 'source': source, 'x': x, 'y': y, 'w': w, 'h': h}
    if source == 'document':
        shape['path'] = path_or_prompt
    else:
        shape['image_prompt'] = path_or_prompt
    return shape


def _stack_items(items: list[str], x: float, y: float, w: float, font_size: float = 16,
                 color: Optional[str] = None, gap: float = 0.14,
                 max_h: Optional[float] = None, bulleted: bool = False) -> tuple[list[dict], float]:
    """Sequential-y stacking -- disjoint by construction. Duplicated here (not imported from
    pptx_patterns.py) to keep this module dependency-free; pptx_patterns.py imports ITS copy from here
    instead, so there is exactly one implementation, just re-exported upward.

    `bulleted=True` marks each shape with `list_marker` so deck_pptx_gen.js draws a real native PPTX
    bullet glyph -- default False because `type:'bullet'` is also used for captions/kickers/labels
    (single short lines that shouldn't get a list marker); callers stacking genuine multi-point body
    content (e.g. render_narrative) opt in explicitly."""
    if not items:
        return [], y
    fs = font_size

    def _total_h(fs_: float) -> float:
        return sum(_required_text_height(it, fs_, w) + gap for it in items) - gap

    if max_h:
        while _total_h(fs) > max_h and fs > 11:
            fs -= 1
    shapes = []
    cy = y
    for it in items:
        ih = _required_text_height(it, fs, w)
        shape = {'type': 'bullet', 'text': it, 'x': x, 'y': cy, 'w': w, 'h': ih, 'font_size_hint': fs}
        if color:
            shape['color'] = color
        if bulleted:
            shape['list_marker'] = True
        shapes.append(shape)
        cy += ih + gap
    return shapes, cy - gap


# ---------------------------------------------------------------------------
# SVG-composer validator: the checks that gate compose_pptx_layout's freeform authoring path before a
# slide ships, adapted (at a scale appropriate to this project, not copied) from ppt-master's
# svg_quality/checker.py category list -- bounds, text-fit, preset-name, contrast, image-contract.
# _find_overlaps/_find_duplicate_words above already work unmodified for 'freeform' shapes since
# _freeform_shape() (deck_pptx_exporter.py) always sets a real x/y/w/h bounding box on them.
# ---------------------------------------------------------------------------

def _find_offcanvas(shapes: list, canvas: dict, margin: float = 0.0, tolerance: float = 0.02,
                    top_margin: Optional[float] = None) -> list:
    """Any shape extending past the canvas edges (minus `margin`, the same safe-usable-area inset every
    pattern engine respects). `top_margin` optionally overrides just the top edge (e.g. to reserve a
    title band) -- defaults to `margin`. Returns (index, "which edge", overflow_amount) triples."""
    violations = []
    x0, y0 = margin, top_margin if top_margin is not None else margin
    x1, y1 = canvas.get('w', 10) - margin, canvas.get('h', 5.625) - margin
    for i, s in enumerate(shapes):
        sx0, sy0, sx1, sy1 = _shape_rect(s)
        if sx0 < x0 - tolerance:
            violations.append((i, 'left', round(x0 - sx0, 3)))
        if sy0 < y0 - tolerance:
            violations.append((i, 'top', round(y0 - sy0, 3)))
        if sx1 > x1 + tolerance:
            violations.append((i, 'right', round(sx1 - x1, 3)))
        if sy1 > y1 + tolerance:
            violations.append((i, 'bottom', round(sy1 - y1, 3)))
    return violations


def _find_text_overflow(shapes: list, slack: float = 0.05) -> list:
    """For every title/bullet shape, compare its declared `h` against the height its own text actually
    needs at its own declared font size (_required_text_height, the same estimator every pattern engine
    already trusts for its own layout math) -- catches a box the LLM sized too small for what it put in
    it, before that shows up as visually clipped/overflowing text. Returns (index, needed_h, declared_h)
    triples."""
    violations = []
    for i, s in enumerate(shapes):
        if s.get('type') not in ('title', 'bullet') or not s.get('text'):
            continue
        w = float(s.get('w') or 0)
        if w <= 0:
            continue
        fs = float(s.get('font_size_hint') or (28 if s.get('type') == 'title' else 14))
        needed = _required_text_height(s['text'], fs, w)
        declared = float(s.get('h') or 0)
        if needed > declared + slack:
            violations.append((i, needed, declared))
    return violations


def _find_invalid_preset(shapes: list, safe_shapes: frozenset) -> list:
    """accent_shape entries must name a real, known-safe pptxgenjs preset -- an unrecognized name (typo,
    hallucinated preset, or one of the deliberately-excluded actionButton*/flowChart* groups) fails loud
    rather than silently rendering as whatever fallback shape the JS side happens to pick. Returns
    (index, bad_name) pairs."""
    violations = []
    for i, s in enumerate(shapes):
        if s.get('type') == 'accent_shape' and s.get('shape') not in safe_shapes:
            violations.append((i, s.get('shape')))
    return violations


def _hex_luminance(hex_color: str) -> float:
    """Relative luminance per the WCAG 2.x formula (sRGB -> linear -> weighted sum)."""
    h = (hex_color or '').lstrip('#')
    if len(h) != 6:
        return 0.5
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return 0.5

    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = _lin(r), _lin(g), _lin(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    la, lb = _hex_luminance(hex_a), _hex_luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def _find_low_contrast(shapes: list, bg_color: str, min_ratio: float = 3.0) -> list:
    """WCAG-style contrast check between each text shape's own color (or the slide background if
    unset) and whatever's directly behind it -- an accent_shape/freeform occupying the SAME footprint
    (already a z-order-0 "may sit under text" case _find_overlaps allows for image/scrim) can otherwise
    produce genuinely unreadable text no overlap/bounds check would ever catch. min_ratio=3.0 (not the
    stricter 4.5 body-text AA minimum) intentionally -- large presentation type and this being a
    best-effort geometric approximation (real backing color, not rendered pixels) both argue for a
    forgiving floor. Returns (index, ratio) pairs."""
    violations = []
    for i, s in enumerate(shapes):
        if s.get('type') not in ('title', 'bullet') or not s.get('text'):
            continue
        text_color = s.get('color') or bg_color
        behind = bg_color
        srect = _shape_rect(s)
        for other in shapes:
            if other is s or other.get('type') not in ('accent_shape', 'freeform', 'image'):
                continue
            if other.get('fill') and _rects_overlap(srect, _shape_rect(other)):
                behind = other['fill']
        ratio = _contrast_ratio(text_color, behind)
        if ratio < min_ratio:
            violations.append((i, round(ratio, 2)))
    return violations


def _find_missing_text_color(shapes: list) -> list:
    """A text shape with no `fill` silently defaults to the background color at contrast-check time
    (invisible text) -- catch the omission itself as a violation so the LLM is asked to fix it,
    instead of a wrong default being guessed. Returns violating indices."""
    return [i for i, s in enumerate(shapes)
            if s.get('type') in ('title', 'bullet') and s.get('text') and not s.get('color')]


def _find_image_contract_violation(shapes: list) -> list:
    """image_source:"document" must resolve to a real path already on disk (never a hallucinated one --
    _resolve_generated_images only post-processes source:"generate", so a bad "document" path would
    otherwise reach the JS renderer unchecked); "generate" must carry a non-empty prompt. Returns
    (index, reason) pairs."""
    violations = []
    for i, s in enumerate(shapes):
        if s.get('type') != 'image':
            continue
        if s.get('source') == 'document':
            path = s.get('path')
            if not path or not Path(path).exists():
                violations.append((i, f'document image path does not exist: {path!r}'))
        elif s.get('source') == 'generate':
            if not (s.get('image_prompt') or '').strip():
                violations.append((i, 'generate source has an empty prompt'))
    return violations


# ---------------------------------------------------------------------------
# 1. Sequential/Flow: process_flow, chevron_process, timeline, pipeline_with_stages, roadmap_vertical
# ---------------------------------------------------------------------------

def flow_engine(nodes: list[dict], x0: float, y0: float, x1: float, y1: float, theme: dict, *,
                orientation: str = 'horizontal', marker_shape: str = 'roundRect',
                connector_shape: Optional[str] = 'chevron', show_sublabel: bool = False) -> list[dict]:
    """N sequential nodes along one axis. `nodes` = [{'label':str,'sublabel':str?}, ...]. Disjoint by
    construction: the main axis divides into N equal slots with fixed gaps (same division formula as
    stat_row's card_w); a connector, if any, lives ONLY inside the gap between slot i and i+1, so it can
    never share range with either node's slot regardless of y."""
    n = max(2, min(len(nodes), 6))
    nodes = nodes[:n]
    accent = theme.get('accent', 'FFCC33')
    dim = theme.get('dim_dark', '888888')
    gap = 0.35
    shapes: list[dict] = []

    if orientation == 'vertical':
        total = y1 - y0
        slot = (total - gap * (n - 1)) / n
        label_x = x0 + 0.5
        label_w = (x1 - x0) - 0.5
        for i, node in enumerate(nodes):
            sy = y0 + i * (slot + gap)
            marker_h = min(0.34, slot)
            shapes.append({'type': 'accent_shape', 'shape': marker_shape, 'x': x0, 'y': sy,
                           'w': 0.34, 'h': marker_h, 'fill': accent})
            label = node.get('label', '')
            lh = min(slot, max(0.2, _required_text_height(label, 14, label_w)))
            shapes.append({'type': 'bullet', 'text': label, 'x': label_x, 'y': sy, 'w': label_w,
                           'h': lh, 'font_size_hint': 14, 'color': accent})
            if show_sublabel and node.get('sublabel'):
                sub_h = min(max(0.0, slot - lh - 0.04), _required_text_height(node['sublabel'], 11, label_w))
                if sub_h > 0.1:
                    shapes.append({'type': 'bullet', 'text': node['sublabel'], 'x': label_x,
                                   'y': sy + lh + 0.04, 'w': label_w, 'h': sub_h,
                                   'font_size_hint': 11, 'color': dim})
            if connector_shape and i < n - 1:
                conn_h = max(0.05, gap - 0.08)
                shapes.append({'type': 'accent_shape', 'shape': 'downArrow', 'x': x0 + 0.09,
                               'y': sy + slot + 0.04, 'w': 0.16, 'h': conn_h, 'fill': dim})
        return shapes

    total = x1 - x0
    slot = (total - gap * (n - 1)) / n
    marker_h = 0.34
    for i, node in enumerate(nodes):
        sx = x0 + i * (slot + gap)
        shapes.append({'type': 'accent_shape', 'shape': marker_shape, 'x': sx, 'y': y0,
                       'w': slot, 'h': marker_h, 'fill': accent})
        cy = y0 + marker_h + 0.1
        label = node.get('label', '')
        label_h = min(y1 - cy, max(0.2, _required_text_height(label, 14, slot)))
        shapes.append({'type': 'bullet', 'text': label, 'x': sx, 'y': cy, 'w': slot, 'h': label_h,
                       'font_size_hint': 14, 'color': accent})
        cy += label_h + 0.06
        if show_sublabel and node.get('sublabel') and cy < y1 - 0.1:
            sub_h = min(y1 - cy, _required_text_height(node['sublabel'], 11, slot))
            shapes.append({'type': 'bullet', 'text': node['sublabel'], 'x': sx, 'y': cy, 'w': slot,
                           'h': sub_h, 'font_size_hint': 11, 'color': dim})
        if connector_shape and i < n - 1:
            conn_w = max(0.06, gap - 0.08)
            shapes.append({'type': 'accent_shape', 'shape': connector_shape,
                           'x': sx + slot + (gap - conn_w) / 2, 'y': y0 + marker_h / 2 - 0.06,
                           'w': conn_w, 'h': 0.12, 'fill': dim})
    return shapes


# ---------------------------------------------------------------------------
# 2. Radial/Hub (spokes): hub_spoke, hub_inward_arrows, segmented_wheel
# ---------------------------------------------------------------------------

def radial_spoke_engine(center_label: Optional[str], spokes: list[dict], x0: float, y0: float,
                        x1: float, y1: float, theme: dict, *, connector_mode: str = 'arrow_out',
                        node_touching: bool = False) -> list[dict]:
    """1 center + N surrounding nodes placed by trigonometry.

    IMPORTANT: `_find_overlaps` is rotation-BLIND -- it always checks the shape's raw unrotated x/y/w/h
    box, regardless of any `rotate` field. So (a) node_touching's petals, which ARE rotated for visual
    effect, must have their label clearance sized from the petal's half-DIAGONAL (its true worst-case
    reach at any rotation angle: rotating a W x H rectangle can never push a corner farther from center
    than hypot(W,H)/2), not a small circular radius; (b) the LABEL is also an axis-aligned box whose own
    corner can reach inward at diagonal angles, so its half-diagonal must be cleared too, not just the
    node's; and (c) connectors are deliberately NOT rotated long bars (a long thin unrotated box combined
    with a `rotate` field would check completely wrong against its own visual footprint) -- instead a
    tiny near-square dot placed along the spoke, small/symmetric enough that rotation is moot.

    The CENTER marker+label is a separate footprint that spoke nodes must also clear -- kept deliberately
    compact (see below) since its own corner-distance from center becomes a lower bound on the spoke
    radius, and an oversized center label would force spokes uncomfortably far out or off-canvas."""
    n = max(3, min(len(spokes), 8))
    spokes = spokes[:n]
    accent = theme.get('accent', 'FFCC33')
    palette = [accent, theme.get('c1', '5B9BD5'), theme.get('c2', 'E07B6A'), theme.get('c3', '7B68C8')]
    dim = theme.get('dim_dark', '888888')
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    half_w, half_h = (x1 - x0) / 2, (y1 - y0) / 2
    # A TRUE CIRCLE (not an ellipse fit to the wider dimension), sized off the TIGHTER half-extent, so
    # it reaches the same max distance at every angle -- an ellipse wide enough to use full canvas width
    # would push labels off the top/bottom at the north/south spoke on a landscape canvas.
    fit_r = min(half_w, half_h)
    node_r = 0.28
    label_gap = 0.22
    shapes: list[dict] = []

    center_footprint_r = 0.0
    if center_label:
        # Compact marker + label BELOW it (y-disjoint), never text-inside-shape, to avoid the
        # text-vs-accent_shape overlap _find_overlaps flags.
        cmr = 0.2
        shapes.append({'type': 'accent_shape', 'shape': 'ellipse', 'x': cx - cmr, 'y': cy - cmr,
                       'w': cmr * 2, 'h': cmr * 2, 'fill': accent})
        cw = 1.0
        cgap = 0.06
        ch = min(0.4, max(0.18, _required_text_height(center_label, 11, cw)))
        cly = cy + cmr + cgap
        shapes.append({'type': 'bullet', 'text': center_label, 'x': cx - cw / 2, 'y': cly,
                       'w': cw, 'h': ch, 'font_size_hint': 11, 'color': theme.get('text_dark', 'FFFFFF'),
                       'align': 'center'})
        center_footprint_r = math.hypot(cw / 2, cmr + cgap + ch)

    # label_w depends on r (via the angular chord) and r's own lower bound depends on label_w (via the
    # node/label footprint below) -- resolved with a single estimation pass using a provisional r from
    # the outer (canvas-edge) constraint alone; label_w is clamped to a narrow 0.8-1.3in range for any
    # reasonably sized r, so one pass is accurate enough in practice (the defensive overlap check in
    # render_pattern() is the final backstop regardless of any residual imprecision here).
    provisional_r = max(0.6, fit_r - 1.1)
    chord = 2 * math.sin(math.pi / n) * provisional_r
    label_w = max(0.8, min(1.3, chord * 0.5))  # 0.5x factor: safety margin beyond naive Euclidean bound
    if node_touching:
        petal_w, petal_h = label_w * 0.6, 0.28
        node_half_extent = math.hypot(petal_w, petal_h) / 2  # worst-case reach at ANY rotation angle
    else:
        node_half_extent = node_r
    label_half_extent = math.hypot(label_w, 0.45) / 2
    outer_reserve = node_half_extent + label_half_extent + label_gap
    inner_reserve = center_footprint_r + node_half_extent + 0.15  # spokes must also clear the center

    r = max(inner_reserve, fit_r - outer_reserve)
    label_r = r + node_half_extent + label_half_extent + label_gap

    for i, spoke in enumerate(spokes):
        angle = math.radians(-90 + i * (360 / n))
        nx, ny = cx + r * math.cos(angle), cy + r * math.sin(angle)
        fill = palette[i % len(palette)]

        if node_touching:
            shapes.append({'type': 'accent_shape', 'shape': 'roundRect', 'x': nx - petal_w / 2,
                           'y': ny - petal_h / 2, 'w': petal_w, 'h': petal_h, 'fill': fill,
                           'rotate': math.degrees(angle)})
        else:
            shapes.append({'type': 'accent_shape', 'shape': 'ellipse', 'x': nx - 0.13, 'y': ny - 0.13,
                           'w': 0.26, 'h': 0.26, 'fill': fill})
            if connector_mode != 'none' and center_label:
                # Map onto [safe_inner, safe_outer] rather than a fixed fraction of the full radius --
                # a fixed fraction can land inside the center's OWN label footprint (which reaches
                # further in the south direction than the marker alone) for spokes near that direction.
                # Deriving the range from the actual reserved center footprint keeps the dot clear at
                # every angle and n.
                safe_inner = center_footprint_r + 0.1
                safe_outer = r - 0.15
                if safe_outer > safe_inner:
                    t = 0.75 if connector_mode == 'arrow_out' else 0.25
                    dist = safe_inner + t * (safe_outer - safe_inner)
                    mx, my = cx + (nx - cx) / r * dist, cy + (ny - cy) / r * dist
                    dot_r = 0.05
                    shapes.append({'type': 'accent_shape', 'shape': 'ellipse', 'x': mx - dot_r,
                                   'y': my - dot_r, 'w': dot_r * 2, 'h': dot_r * 2, 'fill': dim})

        lx, ly = cx + label_r * math.cos(angle), cy + label_r * math.sin(angle)
        # label_w is narrow (0.8-1.3in), so full bullet-length text would overflow the height assumed
        # in label_half_extent above; truncate to roughly what 2 lines can hold.
        label = str(spoke.get('label', ''))
        if len(label) > 32:
            label = label[:31].rstrip() + '…'
        lh = min(0.5, max(0.2, _required_text_height(label, 12, label_w)))
        # Defensive clamp (belt-and-suspenders on top of the radius budget above): keep the label's own
        # rect fully inside [x0,x1]x[y0,y1] regardless of any residual rounding/estimation slack.
        lbx = max(x0, min(x1 - label_w, lx - label_w / 2))
        lby = max(y0, min(y1 - lh, ly - lh / 2))
        shapes.append({'type': 'bullet', 'text': label, 'x': lbx, 'y': lby,
                       'w': label_w, 'h': lh, 'font_size_hint': 12, 'align': 'center'})
    return shapes


# ---------------------------------------------------------------------------
# 3. Radial/Hub (rings): concentric_circles
# ---------------------------------------------------------------------------

def radial_ring_engine(rings: list[dict], x0: float, y0: float, x1: float, y1: float,
                       theme: dict) -> list[dict]:
    """N concentric rings, largest-first. Rings are accent-vs-accent (never overlap-checked). Legend is a
    fully separate column to the RIGHT of the diagram -- x-disjoint from every ring regardless of radius,
    so labels never need to reason about annulus geometry at all."""
    n = max(2, min(len(rings), 4))
    rings = rings[:n]
    palette = [theme.get('accent', 'FFCC33'), theme.get('c1', '5B9BD5'),
               theme.get('c2', 'E07B6A'), theme.get('c3', '7B68C8')]
    diagram_w = (x1 - x0) * 0.55
    cx = x0 + diagram_w / 2
    cy = (y0 + y1) / 2
    max_r = min(diagram_w, y1 - y0) / 2 - 0.1
    shapes: list[dict] = []
    for i, _ring in enumerate(rings):
        r = max_r * (n - i) / n
        shapes.append({'type': 'accent_shape', 'shape': 'ellipse', 'x': cx - r, 'y': cy - r,
                       'w': r * 2, 'h': r * 2, 'fill': palette[i % len(palette)]})

    legend_x = x0 + diagram_w + 0.4
    legend_w = max(0.8, x1 - legend_x)
    ly = y0 + 0.1
    for i, ring in enumerate(rings):
        shapes.append({'type': 'accent_shape', 'shape': 'rect', 'x': legend_x, 'y': ly, 'w': 0.16,
                       'h': 0.16, 'fill': palette[i % len(palette)]})
        label = ring.get('label', '')
        lh = max(0.22, _required_text_height(label, 12, legend_w - 0.26))
        shapes.append({'type': 'bullet', 'text': label, 'x': legend_x + 0.24, 'y': ly,
                       'w': legend_w - 0.24, 'h': lh, 'font_size_hint': 12})
        ly += max(0.32, lh + 0.14)
    return shapes


# ---------------------------------------------------------------------------
# 4. Tiered: pyramid_chart, funnel, iceberg
# ---------------------------------------------------------------------------

def tier_engine(tiers: list[dict], x0: float, y0: float, x1: float, y1: float, theme: dict, *,
                direction: str = 'narrow_top', height_fracs: Optional[list[float]] = None) -> list[dict]:
    """Stacked horizontal bands, tapering wider/narrower per `direction`. Labels live in a column
    strictly right of the WIDEST possible band (fixed diagram_w regardless of any tier's own taper
    fraction) -- x-disjoint from every band no matter how narrow/wide that specific tier is."""
    n = max(2, min(len(tiers), 7))
    tiers = tiers[:n]
    palette = [theme.get('accent', 'FFCC33'), theme.get('c1', '5B9BD5'),
               theme.get('c2', 'E07B6A'), theme.get('c3', '7B68C8')]
    diagram_w = (x1 - x0) * 0.42
    diag_cx = x0 + diagram_w / 2
    gap = 0.06
    fracs = (height_fracs[:n] if height_fracs else [1.0 / n] * n)
    band_h_total = (y1 - y0) - gap * (n - 1)
    label_x = x0 + diagram_w + 0.35
    label_w = max(0.8, x1 - label_x)
    shapes: list[dict] = []
    by = y0
    for i, tier in enumerate(tiers):
        bh = max(0.2, band_h_total * fracs[i])
        w_frac = (i + 1) / n if direction == 'narrow_top' else (n - i) / n
        bw = max(0.3, diagram_w * w_frac)
        bx = diag_cx - bw / 2
        if height_fracs is not None:
            shape_name = 'rect'
        elif i == 0 and direction == 'narrow_top':
            shape_name = 'triangle'
        else:
            shape_name = 'trapezoid'
        shapes.append({'type': 'accent_shape', 'shape': shape_name, 'x': bx, 'y': by, 'w': bw, 'h': bh,
                       'fill': palette[i % len(palette)]})
        label = tier.get('label', '')
        lh = min(bh, max(0.2, _required_text_height(label, 14, label_w)))
        shapes.append({'type': 'bullet', 'text': label, 'x': label_x, 'y': by + max(0, (bh - lh) / 2),
                       'w': label_w, 'h': lh, 'font_size_hint': 14})
        by += bh + gap
    return shapes


# ---------------------------------------------------------------------------
# 5. Hierarchy Tree: top_down_tree, layered_architecture, module_composition
# ---------------------------------------------------------------------------

def tree_engine(levels: list[list[dict]], x0: float, y0: float, x1: float, y1: float, theme: dict, *,
                connectors: bool = True) -> list[dict]:
    """Each level is a full-width horizontal band divided into disjoint columns (bullet_columns' formula
    applied per row). A connector, if enabled and adjacent rows have equal node counts, is a thin
    vertical bar living entirely in the row-gap between them (y-disjoint from both rows' content)."""
    n_levels = max(1, min(len(levels), 4))
    levels = levels[:n_levels]
    accent = theme.get('accent', 'FFCC33')
    dim = theme.get('dim_dark', '888888')
    row_gap = 0.3
    row_h = ((y1 - y0) - row_gap * (n_levels - 1)) / n_levels
    shapes: list[dict] = []
    prev_positions: Optional[list[float]] = None
    ry = y0
    for level_nodes in levels:
        m = max(1, min(len(level_nodes), 5))
        level_nodes = level_nodes[:m]
        col_gap = 0.25
        col_w = (x1 - x0 - col_gap * (m - 1)) / m
        positions = []
        for c, node in enumerate(level_nodes):
            cx = x0 + c * (col_w + col_gap)
            positions.append(cx + col_w / 2)
            shapes.append({'type': 'accent_shape', 'shape': 'rect', 'x': cx, 'y': ry, 'w': col_w,
                           'h': 0.04, 'fill': accent})
            cy = ry + 0.12
            label = node.get('label', '')
            lh = min(row_h - 0.16, max(0.22, _required_text_height(label, 14, col_w)))
            shapes.append({'type': 'bullet', 'text': label, 'x': cx, 'y': cy, 'w': col_w, 'h': lh,
                           'font_size_hint': 14})
            cy += lh + 0.04
            body = node.get('body', '')
            if body and cy < ry + row_h - 0.1:
                bh = min(ry + row_h - cy, _required_text_height(body, 11, col_w))
                shapes.append({'type': 'bullet', 'text': body, 'x': cx, 'y': cy, 'w': col_w, 'h': bh,
                               'font_size_hint': 11, 'color': dim})
        if connectors and prev_positions and len(prev_positions) == len(positions) and ry > y0:
            for px in positions:  # positions == prev_positions when column counts match (same formula)
                shapes.append({'type': 'accent_shape', 'shape': 'rect', 'x': px - 0.005,
                               'y': ry - row_gap + 0.04, 'w': 0.01, 'h': max(0.02, row_gap - 0.08),
                               'fill': dim})
        prev_positions = positions
        ry += row_h + row_gap
    return shapes


# ---------------------------------------------------------------------------
# 6. Grid: icon_grid, quadrant_text_bullets, matrix_2x2, checklist, feature_matrix
# ---------------------------------------------------------------------------

def card_grid_engine(cells: list[dict], x0: float, y0: float, x1: float, y1: float, theme: dict, *,
                     cols: Optional[int] = None, icon: bool = False, header_row: bool = False,
                     axis_labels: Optional[dict] = None) -> list[dict]:
    """2D generalization of bullet_columns' 1D division: both rows and columns split into disjoint
    cell_w x cell_h slots. Every cell uses the same "thin accent bar above text" convention as
    _title_block/render_stat_row -- NEVER a filled background behind text (the overlap checker flags
    text-vs-accent_shape unconditionally, so a colored full-cell background under a label would always
    fail; header cells are differentiated by accent-colored TEXT instead)."""
    n = max(1, min(len(cells), 12))
    cells = cells[:n]
    accent = theme.get('accent', 'FFCC33')
    margin_y = 0.26 if axis_labels else 0.0
    gy0, gy1 = y0 + margin_y, y1 - margin_y
    n_cols = min(cols or max(1, math.ceil(math.sqrt(n))), n)
    n_rows = math.ceil(n / n_cols)
    col_gap, row_gap = 0.25, 0.2
    cell_w = (x1 - x0 - col_gap * (n_cols - 1)) / n_cols
    cell_h = ((gy1 - gy0) - row_gap * (n_rows - 1)) / n_rows
    shapes: list[dict] = []

    if axis_labels:
        for corner, text in axis_labels.items():
            if not text:
                continue
            cx = x0 if 'left' in corner else max(x0, x1 - 2.2)
            cy = y0 if 'top' in corner else y1 - 0.2
            shapes.append({'type': 'bullet', 'text': str(text)[:40], 'x': cx, 'y': cy, 'w': 2.2, 'h': 0.2,
                           'font_size_hint': 10, 'color': theme.get('dim_dark', '888888')})

    for idx, cell in enumerate(cells):
        r, c = divmod(idx, n_cols)
        cx = x0 + c * (cell_w + col_gap)
        cy = gy0 + r * (cell_h + row_gap)
        is_header = header_row and r == 0
        bar_h = 0.05 if is_header else 0.03
        shapes.append({'type': 'accent_shape', 'shape': 'rect', 'x': cx, 'y': cy, 'w': cell_w,
                       'h': bar_h, 'fill': accent})
        ty = cy + bar_h + 0.06
        th = max(0.2, cell_h - bar_h - 0.06)
        icon_h = 0.0
        if icon and not is_header:
            icon_h = min(0.3, th * 0.3)
            shapes.append({'type': 'accent_shape', 'shape': 'donut', 'x': cx, 'y': ty, 'w': icon_h,
                           'h': icon_h, 'fill': accent})
            icon_h += 0.08
        label = cell.get('label') or cell.get('title') or ''
        fs = 14 if is_header else 13
        lh = min(max(0.15, th - icon_h), max(0.2, _required_text_height(label, fs, cell_w - 0.1)))
        lshape = {'type': 'bullet', 'text': label, 'x': cx + 0.05, 'y': ty + icon_h, 'w': cell_w - 0.1,
                  'h': lh, 'font_size_hint': fs}
        if is_header:
            lshape['color'] = accent
        shapes.append(lshape)
        body = cell.get('body', '')
        if body:
            by_ = ty + icon_h + lh + 0.04
            bh = th - icon_h - lh - 0.04
            if bh > 0.15:
                shapes.append({'type': 'bullet', 'text': body, 'x': cx + 0.05, 'y': by_,
                               'w': cell_w - 0.1, 'h': bh, 'font_size_hint': 11})
    return shapes


# ---------------------------------------------------------------------------
# 7. Split/Contrast: pros_cons, problem_solution, before_after_images, spectrum_continuum,
#    editorial_pullquote
# ---------------------------------------------------------------------------

def split_engine(left: dict, right: dict, x0: float, y0: float, x1: float, y1: float, theme: dict, *,
                 mode: str = 'columns', style: str = 'plain') -> list[dict]:
    """`mode='columns'` generalizes render_comparison's proven geometry (2 halves + midline divider) --
    `style` only changes cosmetics (a "+"/"-" prefix, image+caption instead of stacked text, larger
    pull-quote-style type), never the coordinate formula. `mode='spectrum'` is a single full-width axis
    with two pole labels (outside the bar's own y-range) and an optional position marker."""
    accent = theme.get('accent', 'FFCC33')
    dim = theme.get('dim_dark', '888888')
    shapes: list[dict] = []

    if mode == 'spectrum':
        bar_y = (y0 + y1) / 2 - 0.05
        shapes.append({'type': 'accent_shape', 'shape': 'rect', 'x': x0, 'y': bar_y, 'w': x1 - x0,
                       'h': 0.1, 'fill': accent})
        pole_w = (x1 - x0) * 0.4
        shapes.append({'type': 'bullet', 'text': str(left.get('label', ''))[:40], 'x': x0,
                       'y': bar_y - 0.44, 'w': pole_w, 'h': 0.3, 'font_size_hint': 15})
        shapes.append({'type': 'bullet', 'text': str(right.get('label', ''))[:40], 'x': x1 - pole_w,
                       'y': bar_y - 0.44, 'w': pole_w, 'h': 0.3, 'font_size_hint': 15, 'align': 'right'})
        position = left.get('position')
        if isinstance(position, (int, float)):
            pos = max(0.03, min(0.97, position))
            mx = x0 + pos * (x1 - x0)
            shapes.append({'type': 'accent_shape', 'shape': 'triangle', 'x': mx - 0.12, 'y': bar_y + 0.16,
                           'w': 0.24, 'h': 0.2, 'fill': dim, 'rotate': 180})
        return shapes

    gap = 0.3
    half_w = (x1 - x0 - gap) / 2
    shapes.append({'type': 'accent_shape', 'shape': 'rect', 'x': x0 + half_w + gap / 2 - 0.005, 'y': y0,
                   'w': 0.01, 'h': y1 - y0, 'fill': dim})
    for side, sx, sign in ((left, x0, '+'), (right, x0 + half_w + gap, '−')):
        cy = y0
        label = str(side.get('label') or '')[:60]
        if style == 'plus_minus' and label:
            label = f'{sign} {label}'
        font_size = 20 if style == 'pullquote' else 15
        if label:
            shapes.append({'type': 'bullet', 'text': label, 'x': sx, 'y': cy, 'w': half_w, 'h': 0.3,
                           'font_size_hint': 15, 'color': accent})
            cy += 0.36
        if style == 'images' and side.get('image_source') and side.get('image_path_or_prompt'):
            img_h = max(0.5, (y1 - cy) - 0.35)
            shapes.append(_image_shape(side['image_source'], side['image_path_or_prompt'], sx, cy,
                                       half_w, img_h))
            caption = str(side.get('caption') or '')[:100]
            if caption:
                shapes.append({'type': 'bullet', 'text': caption, 'x': sx, 'y': cy + img_h + 0.06,
                               'w': half_w, 'h': 0.28, 'font_size_hint': 11, 'color': dim})
        else:
            body_items = side.get('body_items') or []
            body_shapes, _bottom = _stack_items(body_items, sx, cy, half_w, font_size=font_size,
                                                max_h=y1 - cy)
            shapes.extend(body_shapes)
    return shapes


# ---------------------------------------------------------------------------
# 8. Narrative/Emphasis: big_number, section_divider, closing_summary, faq_qa
# ---------------------------------------------------------------------------

def emphasis_engine(headline: str, payload, x0: float, y0: float, x1: float, y1: float, theme: dict, *,
                    mode: str = 'number') -> list[dict]:
    """`number`/`section` reuse quote_focus's centered-block math. `summary` is a plain _stack_items
    call. `qa_pairs` interleaves two _required_text_height-sized blocks per pair down one sequential-y
    column (literally _stack_items run twice per item)."""
    accent = theme.get('accent', 'FFCC33')
    w = x1 - x0
    shapes: list[dict] = []

    if mode in ('number', 'section'):
        fs = 54 if mode == 'number' else 34
        hw = w * 0.85
        hx = x0 + w * 0.075
        hh = max(0.9, _required_text_height(headline, fs, hw))
        cy = y0 + max(0.1, (y1 - y0 - hh) / 2 - 0.35)
        title_shape = {'type': 'title', 'text': headline, 'x': hx, 'y': cy, 'w': hw, 'h': hh,
                       'font_size_hint': fs, 'align': 'center'}
        if mode == 'number':
            title_shape['color'] = accent
        shapes.append(title_shape)
        sub = str(payload or '')[:180]
        if sub:
            sy = cy + hh + 0.15
            sh = min(max(0.0, y1 - sy), _required_text_height(sub, 16, hw))
            if sh > 0.1:
                shapes.append({'type': 'bullet', 'text': sub, 'x': hx, 'y': sy, 'w': hw, 'h': sh,
                               'font_size_hint': 16, 'align': 'center'})
        return shapes

    if mode == 'summary':
        items = (payload or [])[:4]
        body_shapes, _bottom = _stack_items(items, x0, y0 + 0.1, w, font_size=18, max_h=y1 - y0 - 0.1)
        return body_shapes

    if mode == 'qa_pairs':
        pairs = (payload or [])[:5]
        cy = y0
        for q, a in pairs:
            if cy >= y1 - 0.15:
                break
            qh = min(y1 - cy, max(0.2, _required_text_height(q, 15, w)))
            shapes.append({'type': 'bullet', 'text': q, 'x': x0, 'y': cy, 'w': w, 'h': qh,
                           'font_size_hint': 15, 'color': accent})
            cy += qh + 0.05
            if a and cy < y1 - 0.1:
                ah = min(y1 - cy, _required_text_height(a, 13, w))
                shapes.append({'type': 'bullet', 'text': a, 'x': x0, 'y': cy, 'w': w, 'h': ah,
                              'font_size_hint': 13})
                cy += ah + 0.16
        return shapes

    return []
