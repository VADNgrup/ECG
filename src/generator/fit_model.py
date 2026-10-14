"""Text-fit model shared by the packer/writer/QA. Mirrors the measurements in deck_pptx_gen.js
(estLines, fitBullets, fitCards) so what Python decides "fits" is what the renderer draws."""
import math
import re
from typing import Dict, List, Sequence

from src.generator.font_metrics import LINE, text_em, wrap_lines

BW = 9.0                    # full body width (inches)
BODY_H = 3.55               # body height under the title
CARD_TEXT_X = 0.68
CARD_GAP = 0.06
CARD_PAD = 0.05
MIN_BODY_FS = 9
FLOOR_FS = 12               # readability floor of body text (deck_pptx_gen.js FLOOR_FS)
DENSE_LINE = 0.9            # line spacing of a dense list (DENSE_LINE)
COL_GAP = 0.3               # gap between the two columns of a list (COL_GAP)
TWO_COL_MIN_W = 6.0         # a list splits into two columns only in a box at least this wide (TWO_COL_MIN_W)
TITLE_LIFT = 0.25           # body height a one-line title gives up (deck_pptx_gen.js frame)
BODY_LINE = 1.24            # line height of a body line as a multiple of its size (BODY_LINE)
BULLET_IND_PT = 27         # a flat list's hanging indent, pt (deck_pptx_gen.js BULLET_IND_PT)
BULLET_MARGIN = (0.05, 0.08, 0.05, 0)   # its box insets [l, r, b, t], pt (BULLET_MARGIN)
BULLET_INSET = (BULLET_IND_PT + BULLET_MARGIN[0] + BULLET_MARGIN[1]) / 72   # box width its text does not wrap in


def est_lines(text: str, width_in: float, fs: float, role: str = "any", bold: bool = False) -> int:
    """Lines needed by `text` in a box `width_in` wide (default insets) at `fs` pt (deck_pptx_gen.js estLines)."""
    s = (text or "").strip()
    return wrap_lines(s, max(0.2, width_in - 0.2), fs, role, bold) if s else 1


def bullets_h(bullets: Sequence[str], w: float, fs: float, dense: bool = False) -> float:
    """Height a flat list needs at fs in a box w wide (deck_pptx_gen.js bulletsH)."""
    ew = max(0.4, w - BULLET_INSET)
    line_h, para_gap = fs * BODY_LINE * (DENSE_LINE if dense else 1) / 72, (fs * (0.25 if dense else 0.5)) / 72
    lines = sum(wrap_lines((b or "").strip(), ew, fs, "body") for b in bullets)
    return lines * line_h + len(bullets) * para_gap + 0.06


def fit_bullets(bullets: Sequence[str], w: float = BW - 0.5, h: float = BODY_H - 0.24,
                max_fs: int = 22, dense: bool = False) -> Dict[str, float]:
    """Largest font at which a bullet list fits w x h (deck_pptx_gen.js fitBullets)."""
    sizes = [f for f in (22, 20, 18, 16, 15, 14, 13, 12, 11, 10, 9) if f <= max_fs] or [min(max_fs, 10)]
    for fs in sizes:
        text_h = bullets_h(bullets, w, fs, dense)
        if text_h <= h * 0.98:
            return {"fs": fs, "text_h": text_h, "fits": True}
    return {"fs": sizes[-1], "text_h": bullets_h(bullets, w, sizes[-1], dense), "fits": False}


NEST_IND = 18 / 72          # hanging indent per list level (deck_pptx_gen.js NEST_IND_PT)
NUM_TXT_X, NUM_PAD, NUM_COL_GAP = 0.78, 0.07, 0.45   # numbered-conclusions geometry (deck_pptx_gen.js NUM_*)
NUM_CONCL_TRIES = ((1, 14), (1, 13), (1, 12), (2, 14), (2, 13), (2, 12), (2, 11), (1, 11), (2, 10), (1, 10),
                   (2, 9), (1, 9), (2, 8), (1, 8))


def _heading_h(text: str, w: float, fs: int) -> float:
    """deck_pptx_gen.js numConclHeadH."""
    return wrap_lines(text, w, fs, "display") * fs * BODY_LINE / 72 if text else 0.0


def numbered_fits(rows: Sequence[Dict[str, object]], h: float, min_fs: int = 12) -> bool:
    """Do the rows (heading + items) fit the numbered-conclusions layout at >= min_fs, in one column or two, every row
    as tall as its text (deck_pptx_gen.js numConclPlan)?"""
    for cols, bfs in NUM_CONCL_TRIES:
        if bfs < min_fs or (cols > 1 and len(rows) < 3):
            continue
        rpc, col_w = -(-len(rows) // cols), (BW - NUM_COL_GAP * (cols - 1)) / cols
        w, hfs = col_w - NUM_TXT_X, min(20, bfs + 4)
        heights = []
        for r in rows:
            items = [str(x) for x in r.get("items") or [] if str(x).strip()]
            body = bullets_h(items, w + BULLET_INSET - 0.08 - (NEST_IND if len(items) >= 2 else 0), bfs) if items else 0.0
            heights.append(max(0.5, NUM_PAD * 1.5 + _heading_h(str(r.get("title") or ""), w, hfs) + 0.03 + body))
        if max(sum(heights[c * rpc:(c + 1) * rpc]) for c in range(cols)) <= h:
            return True
    return False


def child_fs(fs: int) -> int:
    """Sub-bullet size under a parent at `fs` (deck_pptx_gen.js childFs)."""
    return fs - 2 if fs >= 14 else fs


def _lines75(text: str, width: float, fs: float, bold: bool = False) -> int:
    """deck_pptx_gen.js _nestLines."""
    return wrap_lines((text or "").strip(), max(0.4, width), fs, "body", bold)


def nested_group_h(title: str, items: Sequence[str], w: float, fs: int, dense: bool = False,
                   cont: bool = False) -> float:
    """Height of one parent bullet and its sub-bullets (deck_pptx_gen.js nestedGroupH); `cont`: the items continue a
    titled group in the next column, still sub-bullets."""
    cfs, ew = child_fs(fs), max(0.4, w - 0.08 - NEST_IND)
    line, gap = BODY_LINE * (DENSE_LINE if dense else 1), 0.15 if dense else 0.3
    h = 0.0
    if title:
        h += _lines75(title, ew, fs, True) * fs * line / 72 + fs * gap / 72      # titles are drawn bold
    child = bool(title or cont)
    ifs, cw = (cfs, ew - NEST_IND) if child else (fs, ew)
    for n, item in enumerate(items):          # the last child's gap is the group gap below
        h += _lines75(item, cw, ifs) * ifs * line / 72 + (ifs * gap / 72 if n < len(items) - 1 else 0)
    return h + fs * (0.2 if dense else 0.4) / 72


def nested_h(groups: Sequence[Dict[str, object]], w: float, fs: int, dense: bool = False) -> float:
    return 0.06 + sum(nested_group_h(str(g.get("title") or ""), [str(i) for i in g.get("items") or []], w, fs, dense,
                                     bool(g.get("cont"))) for g in groups)


def has_hierarchy(groups: Sequence[Dict[str, object]]) -> bool:
    return any(g.get("title") and g.get("items") for g in groups)


def fit_nested(groups: Sequence[Dict[str, object]], w: float, h: float, max_fs: int = 20,
               dense: bool = False) -> Dict[str, float]:
    """Largest parent size at which a two-level list fits w x h (deck_pptx_gen.js fitNested); `fits` holds the
    12pt readability floor for the parents AND their sub-bullets."""
    sizes = [f for f in (22, 20, 18, 16, 15, 14, 13, 12, 11, 10, 9) if f <= max_fs] or [min(max_fs, 10)]
    need = lambda fs: nested_h(groups, w, fs, dense)
    for fs in sizes:
        text_h = need(fs)
        if text_h <= h * 0.98:
            return {"fs": fs, "child_fs": child_fs(fs), "text_h": text_h, "fits": min(fs, child_fs(fs)) >= 12}
    return {"fs": sizes[-1], "child_fs": child_fs(sizes[-1]), "text_h": need(sizes[-1]), "fits": False}


def fit_card_body(items: Sequence[str], text: str, w: float, h: float, min_fs: int = 12) -> bool:
    """A card body fits at min_fs: several items as a – list (deck_pptx_gen.js cardBodyFs), else one paragraph."""
    items = [i for i in items if str(i).strip()]
    if len(items) >= 2:
        return fit_bullets(items, w + BULLET_INSET - 0.08 - NEST_IND, h, max_fs=min_fs)["fits"]
    return est_lines(text, w, min_fs) * min_fs * 1.28 / 72 + 0.06 <= h


def fit_cards(cards: Sequence[Dict[str, str]], text_w: float = BW - CARD_TEXT_X - 0.03,
              avail_h: float = BODY_H) -> Dict[str, object]:
    """Card layout (deck_pptx_gen.js fitCards): largest body font where every card's own text fits."""
    def line_h(fs):
        return fs * 1.28 / 72
    def body_lines(card, b_fs):
        items = [str(i) for i in card.get("items") or [] if str(i).strip()]
        if len(items) >= 2:           # drawn as – sub-bullets (deck_pptx_gen.js fitCards)
            return sum(est_lines(i, text_w - NEST_IND, b_fs) for i in items) + 0.35 * (len(items) - 1)
        return est_lines(card.get("body") or "", text_w, b_fs)

    def need(card, t_fs, b_fs):
        body = (card.get("body") or "").strip()
        return (CARD_PAD + est_lines(card.get("title", ""), text_w, t_fs) * line_h(t_fs) + 0.04
                + (body_lines(card, b_fs) * line_h(b_fs) + 0.02 if body else 0) + CARD_PAD)
    gaps = CARD_GAP * (len(cards) - 1)
    pick = None
    for b_fs in (13, 12, 11, 10, 9):
        t_fs = min(18, b_fs + 3)
        rows = [need(c, t_fs, b_fs) for c in cards]
        if sum(rows) + gaps <= avail_h:
            pick = {"title_fs": t_fs, "body_fs": b_fs, "rows": rows, "fits": True}
            break
        if b_fs == MIN_BODY_FS:
            pick = {"title_fs": t_fs, "body_fs": b_fs, "rows": rows, "fits": False}
    return pick


def lines_cost(bullets: Sequence[str], fs: int = 14, w: float = BW - 0.5 - BULLET_INSET) -> float:
    """Height (inches) a bullet list needs at a fixed font size, for packing decisions."""
    return fit_bullets(bullets, max_fs=fs, w=w + BULLET_INSET)["text_h"] if bullets else 0.0


# ── elastic text: a list grows to hold its content (deck_pptx_gen.js planList) ─────────────────────────────────
def _units(groups: Sequence[Dict[str, object]], content: Sequence[str]) -> Dict[str, object]:
    if has_hierarchy(groups):
        return {"nested": True, "groups": [{"title": str(g.get("title") or ""), "items": [str(i) for i in g.get("items") or []]}
                                           for g in groups if g.get("title") or g.get("items")]}
    return {"nested": False, "groups": [{"title": "", "items": [str(c) for c in content if str(c).strip()]}]}


def _units_h(u: Dict[str, object], w: float, fs: int, dense: bool) -> float:
    return nested_h(u["groups"], w, fs, dense) if u["nested"] else bullets_h(u["groups"][0]["items"], w, fs, dense)


def _units_fit(u: Dict[str, object], w: float, h: float, max_fs: int, dense: bool) -> int:
    if u["nested"]:
        return fit_nested(u["groups"], w, h, min(max_fs, 20), dense)["fs"]
    return fit_bullets(u["groups"][0]["items"], w, h, max_fs, dense)["fs"]


def split_columns(u: Dict[str, object], w: float) -> List[Dict[str, object]]:
    """Two columns of about equal height, cut between groups (between the items of one group or a flat list)."""
    groups = u["groups"]
    if u["nested"] and len(groups) >= 2:
        parts = [[g] for g in groups]
    else:
        g = groups[0]
        parts = [[{"title": g["title"] if i == 0 else "", "cont": i > 0 and bool(g["title"]), "items": [it]}]
                 for i, it in enumerate(g["items"])]
    hof = lambda part: nested_h(part, w, 14) if u["nested"] else bullets_h(part[0]["items"], w, 14)
    hs = [hof(p) for p in parts]
    total, run, best, best_max = sum(hs), 0.0, 1, float("inf")
    for k in range(1, len(parts)):
        run += hs[k - 1]
        m = max(run, total - run)
        if m < best_max - 1e-9:
            best, best_max = k, m

    def join(ps):
        if u["nested"] and len(groups) >= 2:
            return {"nested": True, "groups": [g for p in ps for g in p]}
        return {"nested": u["nested"], "groups": [{"title": ps[0][0]["title"], "cont": ps[0][0]["cont"],
                                                   "items": [p[0]["items"][0] for p in ps]}]}
    return [join(parts[:best]), join(parts[best:])]


def plan_list(groups: Sequence[Dict[str, object]], content: Sequence[str], w: float, h: float, max_fs: int = 22,
              max_cols: int = 2) -> Dict[str, object]:
    """The first of: one column, one dense column, two columns, two dense columns whose every column fits at >=
    FLOOR_FS; else the roomiest (the list still shows in full). {cols, dense, fs, text_h, ok, nested}"""
    u = _units(groups, content)
    count = len(u["groups"]) if u["nested"] and len(u["groups"]) >= 2 else len(u["groups"][0]["items"])
    tries = [(1, False), (1, True)]
    if max_cols >= 2 and w >= TWO_COL_MIN_W and count >= 2:
        tries += [(2, False), (2, True)]
    last = None
    for cols, dense in tries:
        cw = w if cols == 1 else (w - COL_GAP) / 2
        columns = [u] if cols == 1 else split_columns(u, cw)
        fs = min(_units_fit(c, cw, h, max_fs, dense) for c in columns)
        text_h = max(_units_h(c, cw, fs, dense) for c in columns)
        ok = min(fs, child_fs(fs) if u["nested"] else fs) >= FLOOR_FS and text_h <= h * 0.98
        last = {"cols": cols, "dense": dense, "fs": fs, "text_h": text_h, "ok": ok, "nested": u["nested"]}
        if ok:
            return last
    return last


def title_compactable(title, w: float = BW) -> bool:
    """A title that fits one line at 20pt gives the body TITLE_LIFT more height (deck_pptx_gen.js titleCompactable);
    an unknown title is assumed short."""
    return title is None or est_lines(str(title), 0.88 * w, 20) <= 1



# ── tables (deck_pptx_gen.js tableFit / tableTextPlan); same names as the renderer's constants ─────────────────────
COMP_HDR_PAD = 0.18              # comparison columns: margin of a side's label in its header band
COMP_HDR_MIN_FS = 12             # and the smallest size the one-line label takes (deck_pptx_gen.js)
TABLE_CELL_INSET = 0.12          # a cell's left + right text insets; also a body row's vertical room
TABLE_HEAD_ROOM = 0.16           # a header row's vertical room
TABLE_WORD_ROOM = 0.16           # room around a column's longest word
TABLE_WORD_SLACK = 0.06          # a column holds its longest word plus this
TABLE_COL_MIN = 0.72             # narrowest column the content measure gives
TABLE_TYPICAL = "abcd"           # a column is at least half this text wide (plus TABLE_TYPICAL_ROOM)
TABLE_TYPICAL_CHARS = 30         # the opening of a cell that sets its column's typical width
TABLE_TYPICAL_ROOM = 0.34        # added to half the typical width
TABLE_COL_FLOOR = 0.82           # no column narrower than this or TABLE_COL_FLOOR_SHARE of an equal share
TABLE_COL_FLOOR_SHARE = 0.72
TABLE_FS_GAP = 2                 # a table with text is at most this many pt smaller than the text
TABLE_FLOOR_FS = FLOOR_FS - TABLE_FS_GAP   # readability floor of a table's cells
TABLE_MIN_FS = 7                 # smallest size a table is drawn at
TABLE_ABOVE_MAX_FS = 14          # largest size of a table above text
TABLE_SIDE_MAX_FS = 12           # largest size of a table beside text
TABLE_SIDE_SHARES = (0.34, 0.42, 0.50, 0.58)   # width shares a table beside the text tries
TABLE_SIDE_GAP = 0.3             # gap between a table and the text beside it
TABLE_TEXT_GAP = 0.10            # gap between a table and the text under it
TABLE_TEXT_MIN_H = 0.35          # least height of the text under a table
TABLE_TEXT_MAX_FS = 16           # largest size of the text with a table
TABLE_ALONE_MAX_FS = 18          # largest size of a table on its own page (buildTable)
TABLE_CAPTION_ROOM = 0.35        # body height a table alone leaves for its caption


def parse_md_table(md: str) -> Dict[str, List]:
    """{headers, rows} of a markdown table, empty columns dropped (deck_pptx_gen.js parseMdTable)."""
    lines = [l.strip() for l in str(md or "").split("\n") if l.strip()]
    data = [l for l in lines if not re.fullmatch(r"\|?[-:| ]+\|?", l)]
    rows = [r for r in ([c.strip() for c in re.split(r"(?<!\\)\|", l)] for l in data) if len(r) > 1]
    if not rows:
        return {"headers": [], "rows": []}
    width = max(len(r) for r in rows)
    normed = [r + [""] * (width - len(r)) for r in rows]
    keep = [any(r[i].strip() for r in normed) for i in range(width)]
    filt = [[c for c, k in zip(r, keep) if k] for r in normed]
    return {"headers": filt[0], "rows": filt[1:]}


def _cell(text) -> str:
    """A cell as measured: whitespace collapsed (math markup is measured as written, a little wider than drawn)."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def table_fit(headers: Sequence[str], rows: Sequence[Sequence[str]], width: float, max_height: float,
              max_fs: int = 18, min_fs: int = 12) -> Dict[str, object]:
    """Largest size from max_fs down at which the table fits width x max_height with every word whole
    (deck_pptx_gen.js tableFit); else the min_fs geometry with fits False. {fits, fs, col_w, row_h, height, whole}"""
    clean = [[_cell((list(row or []) + [""] * len(headers))[j]) for j in range(len(headers))]
             for row in [headers, *rows]]
    n = len(headers) or 1
    em = lambda text, fs, bold=False: text_em(text, "body", bold) * fs / 72

    def geometry(fs):
        words = [max([0.1] + [em(w, fs, ri == 0) for ri, row in enumerate(clean) for w in re.split(r"\s+", row[j])])
                 + TABLE_WORD_ROOM for j in range(n)]
        raw = [max(TABLE_COL_MIN, words[j] + TABLE_WORD_SLACK,
                   max([em(TABLE_TYPICAL, fs)] + [em(row[j][:TABLE_TYPICAL_CHARS], fs) for row in clean]) / 2
                   + TABLE_TYPICAL_ROOM) for j in range(n)]
        floor = min(TABLE_COL_FLOOR, width / n * TABLE_COL_FLOOR_SHARE)
        base = [max(floor, v) for v in raw]
        col_w = [v * width / sum(base) for v in base]
        row_h = [max([1] + [wrap_lines(cell, max(0.2, col_w[j] - TABLE_CELL_INSET), fs, "body", ri == 0)
                            for j, cell in enumerate(row)]) * fs * LINE / 72
                 + (TABLE_HEAD_ROOM if ri == 0 else TABLE_CELL_INSET) for ri, row in enumerate(clean)]
        return {"fs": fs, "col_w": col_w, "row_h": row_h, "height": sum(row_h),
                "whole": all(w >= words[j] for j, w in enumerate(col_w))}
    for fs in range(max_fs, min_fs - 1, -1):
        g = geometry(fs)
        if g["whole"] and g["height"] <= max_height * 0.98:
            return {"fits": True, **g}
    return {"fits": False, **geometry(min_fs)}


def shown_fs(plan: Dict[str, object], h: float) -> float:
    """The size a list plan's text is really shown at in h (deck_pptx_gen.js shownFs)."""
    fs = child_fs(plan["fs"]) if plan["nested"] else plan["fs"]
    return fs if plan["text_h"] <= h * 0.98 else fs * h * 0.98 / plan["text_h"]


def table_text_plan(groups: Sequence[Dict[str, object]], content: Sequence[str], headers: Sequence[str],
                    rows: Sequence[Sequence[str]], bh: float) -> Dict[str, object]:
    """The table above or beside the text, sized together (deck_pptx_gen.js tableTextPlan). {table_fs, text_fs, side}"""
    best = None

    def consider(table_fs, text_fs, side):
        nonlocal best
        score = min(text_fs, table_fs + TABLE_FS_GAP)
        if (best is None or score > best["score"] + 1e-6
                or (abs(score - best["score"]) < 1e-6 and text_fs > best["text_fs"])):
            best = {"table_fs": table_fs, "text_fs": text_fs, "side": side, "score": score}
    for tfs in range(TABLE_ABOVE_MAX_FS, TABLE_MIN_FS - 1, -1):
        fit = table_fit(headers, rows, BW, 0, tfs, tfs)
        room = bh - fit["height"] - TABLE_TEXT_GAP
        if room < TABLE_TEXT_MIN_H or (not fit["whole"] and tfs > TABLE_MIN_FS):
            continue
        consider(tfs, shown_fs(plan_list(groups, content, BW, room, TABLE_TEXT_MAX_FS), room), False)
    for share in TABLE_SIDE_SHARES:
        tw = BW * share
        text = shown_fs(plan_list(groups, content, BW - tw - TABLE_SIDE_GAP, bh, TABLE_TEXT_MAX_FS, 1), bh)
        for tfs in range(TABLE_SIDE_MAX_FS, TABLE_MIN_FS - 1, -1):
            fit = table_fit(headers, rows, tw, 0, tfs, tfs)
            if fit["height"] > bh or not fit["whole"]:
                continue
            consider(tfs, text, True)
            break
    if best is None:                     # nothing leaves the text room: the smallest table, the text squeezed under it
        fit = table_fit(headers, rows, BW, 0, TABLE_MIN_FS, TABLE_MIN_FS)
        room = max(TABLE_TEXT_MIN_H, bh - fit["height"] - TABLE_TEXT_GAP)
        best = {"table_fs": TABLE_MIN_FS, "side": False,
                "text_fs": shown_fs(plan_list(groups, content, BW, room, TABLE_TEXT_MAX_FS), room)}
    return best


def table_text_fits(groups: Sequence[Dict[str, object]], content: Sequence[str], headers: Sequence[str],
                    rows: Sequence[Sequence[str]], title=None) -> bool:
    """Can one page hold the table at >= TABLE_FLOOR_FS and its text at >= FLOOR_FS (deck_pptx_gen.js buildTblAbove)?"""
    plan = table_text_plan(groups, content, headers, rows, BODY_H + (TITLE_LIFT if title_compactable(title) else 0.0))
    return plan["table_fs"] >= TABLE_FLOOR_FS and plan["text_fs"] >= FLOOR_FS


# ── a figure beside its formula or table (deck_pptx_gen.js figAssetPlan); same names as the renderer's constants ──
FORMULA_TEXT_W = 0.23            # width the formula box's left and right insets take
FORMULA_TEXT_H = 0.12            # height its top and bottom insets take
EQ_WRAP_INSET = 0.2              # box width an equation's text does not wrap in
EQ_LINE = 1.35                   # an equation's line height as a multiple of its size
EQ_PAD = 0.12                    # height its box keeps beyond its lines
EQ_MIN_FS = 10                   # smallest size an equation is drawn at
EQ_WHOLE_MIN_FS = 14             # an equation keeps every line whole down to this size before one wraps
EQ_REL = "=<>≤≥≠≈≡≅∝∼≪≫⩽⩾→⇒⇔↔∈∉⊂⊆⊃⊇"   # relation signs: a further relation may start a new line
EQ_OPS = "+-−·×÷/*±∓∧∨∪∩⊕⊗"     # binary operators: they join the terms of one side
EQ_JOIN = ","                    # a side's terms listed with a comma (x₁, x₂ ≥ 0)
EQ_SEP = "    "                  # between two equations drawn on one line
FIG_ASSET_GAP = 0.3              # between the figure and the formula or table
FIG_ASSET_TEXT_GAP = 0.15        # between the row and the text under it
FIG_ASSET_ROW_SHARES = (0.75, 0.68, 0.6, 0.52, 0.45)   # shares of the body the row tries, largest first
FIG_TEXT_MAX_FS = 20             # largest size of the text under the row
FIG_FORMULA_MAX_FS = 24          # largest size of the formula beside a figure
FIG_FORMULA_MIN_FS = 14          # smallest size the formula may take beside a figure
FORMULA_TEXT_MAX_FS = 20         # largest size of the text with a formula
FORMULA_SIDE_SHARES = (0.35, 0.42, 0.50)   # the formula column's share of the width beside its text, narrowest first
FORMULA_SIDE_GAP = 0.3           # between the text and the formula column
DIVIDER_VARIANTS = 5             # section-divider designs the renderer draws (buildSectionDivider)
END_VARIANTS = 3                 # closing-slide designs the renderer draws (buildEnd)


def equation_lines(formula_text: str) -> List[str]:
    """The lines an equation box draws: every formula (Unicode math), a blank line between two (equationLines)."""
    eqs = [e.strip() for e in re.split(r"\n\s*\n", str(formula_text or "")) if re.search(r"[a-zA-Z0-9]", e)]
    return "\n\n".join(eqs).split("\n")


def equation_segments(line: str) -> List[str]:
    """The pieces a line may break between: before the left side of each further relation whose previous relation
    has a right side, never inside a chain or a side (deck_pptx_gen.js equationSegments)."""
    t = str(line).split()
    is_op = lambda tok: all(c in EQ_OPS for c in tok)
    rels = [i for i, tok in enumerate(t) if any(c in EQ_REL for c in tok)]
    cuts = [0]
    for p, i in zip(rels, rels[1:]):
        j = i - 1 if t[i][0] in EQ_REL else i
        while j - 1 > p:
            if is_op(t[j - 1]) and j - 2 > p:
                j -= 2
            elif t[j - 1][-1] in EQ_OPS + EQ_JOIN:
                j -= 1
            else:
                break
        if j <= p or j <= cuts[-1]:
            continue
        k = max((n for n, c in enumerate(t[p]) if c in EQ_REL), default=-1)
        if (t[p][k + 1:] + "".join(t[p + 1:j])).strip():
            cuts.append(j)
    return [" ".join(t[c:e]) for c, e in zip(cuts, cuts[1:] + [len(t)])]


def equation_break(lines: Sequence[str], w: float, fs: float) -> List[str]:
    """The lines drawn at fs in a box w wide: the equations of a line as many on a line as fit, EQ_SEP between two
    (deck_pptx_gen.js equationBreak)."""
    whole = lambda line: wrap_lines(line, w - EQ_WRAP_INSET, fs, "math") <= 1
    out: List[str] = []
    for line in lines:
        segs = equation_segments(line)
        if len(segs) < 2:
            out.append(line)
            continue
        drawn: List[str] = []
        for seg in segs:
            if drawn and whole(drawn[-1] + EQ_SEP + seg):
                drawn[-1] += EQ_SEP + seg
            else:
                drawn.append(seg)
        out += drawn
    return out


def equation_plan(lines: Sequence[str], w: float, h: float, max_fs: int) -> Dict[str, object]:
    """{fs, lines, fits} of an equation in a text box w x h: each equation on its own line, else the lines broken only
    where too wide, every line whole (down to EQ_WHOLE_MIN_FS); else the largest size with a line wrapped
    (deck_pptx_gen.js equationPlan)."""
    rows = lambda ls, fs: [wrap_lines(line, w - EQ_WRAP_INSET, fs, "math") for line in ls]
    fits_h = lambda r, fs: sum(r) * fs * EQ_LINE / 72 + EQ_PAD <= h
    split = [seg for line in lines for seg in (equation_segments(line) if len(equation_segments(line)) > 1
                                               else [line])]
    for make in (lambda fs: split, lambda fs: equation_break(lines, w, fs)):
        for fs in range(max_fs, EQ_WHOLE_MIN_FS - 1, -1):
            ls = make(fs)
            r = rows(ls, fs)
            if fits_h(r, fs) and all(k <= 1 for k in r):
                return {"fs": fs, "lines": ls, "fits": True}
    for fs in range(min(max_fs, EQ_WHOLE_MIN_FS - 1), EQ_MIN_FS, -1):
        ls = equation_break(lines, w, fs)
        if fits_h(rows(ls, fs), fs):
            return {"fs": fs, "lines": ls, "fits": True}
    return {"fs": EQ_MIN_FS, "lines": equation_break(lines, w, EQ_MIN_FS), "fits": False}


def figure_asset_fits(w: float, h: float, formula_text: str = "", table=None, caption: str = "") -> bool:
    """Is the formula (>= FIG_FORMULA_MIN_FS) or the table (>= TABLE_FLOOR_FS, its caption under it) readable in
    w x h (deck_pptx_gen.js figAssetFits)?"""
    if table and table.get("headers"):
        room = h - (TABLE_CAPTION_ROOM if caption else 0.0)
        return table_fit(table["headers"], table["rows"], w, room, TABLE_SIDE_MAX_FS, TABLE_FLOOR_FS)["fits"]
    plan = equation_plan(equation_lines(formula_text), w - FORMULA_TEXT_W, h - FORMULA_TEXT_H, FIG_FORMULA_MAX_FS)
    return plan["fits"] and plan["fs"] >= FIG_FORMULA_MIN_FS


def figure_asset_row(groups: Sequence[Dict[str, object]], content: Sequence[str], bh: float, formula_text: str = "",
                     table=None, caption: str = "") -> Dict[str, object]:
    """In a body bh tall, the row share that leaves the text its largest size, the largest such row
    (deck_pptx_gen.js figAssetPlan). {ok, row, fs}"""
    half = (BW - FIG_ASSET_GAP) / 2
    if not content:
        return {"ok": figure_asset_fits(half, bh, formula_text, table, caption), "row": bh, "fs": 0}
    best = None
    for share in FIG_ASSET_ROW_SHARES:
        row = bh * share
        if not figure_asset_fits(half, row, formula_text, table, caption):
            continue
        p = plan_list(groups, content, BW, bh - row - FIG_ASSET_TEXT_GAP, FIG_TEXT_MAX_FS)
        if p["ok"] and (best is None or p["fs"] > best["fs"]):
            best = {"ok": True, "row": row, "fs": p["fs"]}
    if best:
        return best
    row = bh * FIG_ASSET_ROW_SHARES[-1]
    return {"ok": False, "row": row,
            "fs": plan_list(groups, content, BW, bh - row - FIG_ASSET_TEXT_GAP, FIG_TEXT_MAX_FS)["fs"]}


def formula_side_row(groups: Sequence[Dict[str, object]], content: Sequence[str], bh: float,
                     formula_text: str) -> Dict[str, object]:
    """In a body bh tall, the text on the left and the formulas in a column on the narrowest share that draws every line
    whole at FIG_FORMULA_MIN_FS or more (deck_pptx_gen.js formulaSidePlan). {ok, fs, share}"""
    lines = equation_lines(formula_text)
    last = {"ok": False, "fs": 0, "share": None}
    for share in FORMULA_SIDE_SHARES:
        fw = BW * share
        eq = equation_plan(lines, fw - FORMULA_TEXT_W, bh - FORMULA_TEXT_H, FIG_FORMULA_MAX_FS)
        if not eq["fits"] or eq["fs"] < FIG_FORMULA_MIN_FS:
            continue
        p = plan_list(groups, content, BW - fw - FORMULA_SIDE_GAP, bh, FORMULA_TEXT_MAX_FS, 1)
        last = {"ok": p["ok"], "fs": p["fs"], "share": share}
        if p["ok"]:
            return last
    return last


def formula_side_plan(groups: Sequence[Dict[str, object]], content: Sequence[str], title=None,
                      formula_text: str = "") -> Dict[str, object]:
    """The text beside its formulas in the body the title leaves (deck_pptx_gen.js buildFormula with roomFor)."""
    first = formula_side_row(groups, content, BODY_H, formula_text)
    if first["ok"] or not title_compactable(title):
        return first
    compact = formula_side_row(groups, content, BODY_H + TITLE_LIFT, formula_text)
    return compact if compact["ok"] or compact["fs"] > first["fs"] else first


def figure_asset_plan(groups: Sequence[Dict[str, object]], content: Sequence[str], title=None, formula_text: str = "",
                      table=None, caption: str = "") -> Dict[str, object]:
    """The figure and its formula or table side by side, the text under them, in the body the title leaves
    (deck_pptx_gen.js buildFigAsset with roomFor). {ok, row, fs}"""
    first = figure_asset_row(groups, content, BODY_H, formula_text, table, caption)
    if first["ok"] or not title_compactable(title):
        return first
    compact = figure_asset_row(groups, content, BODY_H + TITLE_LIFT, formula_text, table, caption)
    return compact if compact["ok"] or compact["fs"] > first["fs"] else first
