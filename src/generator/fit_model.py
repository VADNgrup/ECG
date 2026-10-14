"""Text-fit model shared by the packer/writer/QA. Mirrors the measurements in deck_pptx_gen.js
(estLines, fitBullets, fitCards) so what Python decides "fits" is what the renderer draws."""
import math
import re
from typing import Dict, List, Sequence

from src.generator.font_metrics import wrap_lines

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


def _lines75(text: str, width: float, fs: float) -> int:
    """deck_pptx_gen.js _nestLines."""
    return wrap_lines((text or "").strip(), max(0.4, width), fs, "body")


def nested_group_h(title: str, items: Sequence[str], w: float, fs: int, dense: bool = False,
                   cont: bool = False) -> float:
    """Height of one parent bullet and its sub-bullets (deck_pptx_gen.js nestedGroupH); `cont`: the items continue a
    titled group in the next column, still sub-bullets."""
    cfs, ew = child_fs(fs), max(0.4, w - 0.08 - NEST_IND)
    line, gap = BODY_LINE * (DENSE_LINE if dense else 1), 0.15 if dense else 0.3
    h = 0.0
    if title:
        h += _lines75(title, ew, fs) * fs * line / 72 + fs * gap / 72
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
    FLOOR_FS; else the roomiest (the list still shows in full). {cols, dense, fs, text_h, ok}"""
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
        last = {"cols": cols, "dense": dense, "fs": fs, "text_h": text_h, "ok": ok}
        if ok:
            return last
    return last


def title_compactable(title, w: float = BW) -> bool:
    """A title that fits one line at 20pt gives the body TITLE_LIFT more height (deck_pptx_gen.js titleCompactable);
    an unknown title is assumed short."""
    return title is None or est_lines(str(title), 0.88 * w, 20) <= 1

