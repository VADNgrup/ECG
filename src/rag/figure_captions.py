"""Which figure does a caption belong to, and which figures are page furniture? Decided from page geometry alone
(the bbox MinerU gives every image/chart block), never from keywords or a model. A figure of a document carries its own
caption ("Figure 3-5. ..."); a figure without one is not taught from and is treated as decoration.

A parser attaches a caption to the block that follows the caption text in reading order. When a small margin icon
sits right after a chart, the icon gets the chart's "Figure n-m" caption and the chart keeps none. Two rules repair it:
1. a caption held by a small figure moves to the nearest large uncaptioned figure of the same page (above it first);
2. a figure is furniture (`decorative`) when it is tiny, or small and the same geometry repeats on several pages
   (margin icons, logos; charts of standard size repeat too, so the repeat rule stops at a small area)."""
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from src.utils.config import Config

_FIGURES = ("image", "chart")


_LABEL = re.compile(r"\b(?:Figure|Fig\.|Hình)\s*\d+[-–.]\d+\s*[.:]\s")


def own_caption(text: str) -> str:
    """The figure's own caption: from the last "Figure N-M." on when OCR glued the paragraph citing the figure in front
    ("Figure 1-22 shows ... Figure 1-22. Overfitting the training data" -> "Figure 1-22. Overfitting the training data")."""
    text = " ".join(str(text or "").split())
    starts = [m.start() for m in _LABEL.finditer(text)]
    return text[starts[-1]:] if starts and starts[-1] > 0 else text


def _caption(block: dict) -> str:
    caption = block.get("image_caption") or block.get("chart_caption") or []
    return own_caption(" ".join(caption) if isinstance(caption, list) else str(caption))


def _area(bbox: List[float]) -> float:
    """Share of the page a block covers (bbox is in 0-1000 page units)."""
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1]) / 1e6


def _geometry(bbox: List[float]) -> tuple:
    return (round(bbox[0], -1), round(bbox[2] - bbox[0], -1), round(bbox[3] - bbox[1], -1))


def analyze(blocks: List[dict]) -> Dict[str, dict]:
    """{image file name: {real_caption, original_caption, decorative, area_frac, bbox, kind, page}} for every figure block."""
    figures = []
    for block in blocks:
        if block.get("type") in _FIGURES and block.get("img_path") and block.get("bbox"):
            figures.append({"name": Path(block["img_path"]).name, "kind": block["type"], "page": block.get("page_idx", 0),
                            "bbox": [float(v) for v in block["bbox"]], "caption": _caption(block)})
    pages_of = defaultdict(set)
    for fig in figures:
        pages_of[_geometry(fig["bbox"])].add(fig["page"])
    for fig in figures:
        fig["area"] = _area(fig["bbox"])
        repeated = len(pages_of[_geometry(fig["bbox"])]) >= Config.IMG_REPEAT_PAGES
        fig["decorative"] = fig["area"] < Config.IMG_ICON_MAX_AREA or (repeated and fig["area"] < Config.IMG_REPEAT_MAX_AREA)
        fig["original"] = fig["caption"]
    by_page = defaultdict(list)
    for fig in figures:
        by_page[fig["page"]].append(fig)
    for page_figs in by_page.values():
        for icon in [f for f in page_figs if f["decorative"] and f["caption"]]:
            homes = [f for f in page_figs if not f["decorative"] and not f["caption"]]
            if homes:
                home = min(homes, key=lambda f: _distance(icon["bbox"], f["bbox"]))
                home["caption"], icon["caption"] = icon["caption"], ""
    return {f["name"]: {"real_caption": f["caption"], "original_caption": f["original"], "decorative": f["decorative"],
                        "area_frac": round(f["area"], 4), "bbox": f["bbox"], "kind": f["kind"], "page": f["page"]}
            for f in figures}


def _distance(icon: List[float], big: List[float]) -> float:
    """Vertical gap from the figure to the icon (the figure above it is the natural owner), plus a penalty when the
    two do not share any horizontal extent."""
    overlap = min(icon[2], big[2]) - max(icon[0], big[0])
    above = icon[1] - big[3]                       # >= 0 when the figure ends above the icon
    gap = above if above >= 0 else abs(icon[1] - big[1]) + 1000
    return gap + (0 if overlap > 0 else 1000)
