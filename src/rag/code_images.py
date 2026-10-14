"""Render each parsed `code` block as an image cropped from the source PDF, and recover its text.

The OCR parse flattens a code listing: indentation is lost and the surrounding paragraphs are merged
into `code_body`, so a listing rebuilt from that text is wrong in exactly the way a reader notices.
The PDF itself still holds the listing as typeset — syntax colouring, alignment and all — so a crop
of the page is the faithful artefact, and the page's text layer gives back the indented source.

The code region is found by font: a listing is set in a monospaced face, prose is not. That is a
property of typesetting, not of any language, so it holds for a document in any language. Documents
whose pages carry no text layer (pure scans) simply yield nothing here and the caller keeps the
parser's text.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import pymupdf

from src.utils import collection
from src.utils.config import Config

_MONO_FLAG = 8          # PyMuPDF span flag bit for a monospaced face
_MONO_SHARE = 0.8       # a line counts as code when this much of its text is monospaced
_PAD = 6                # points of breathing room around the crop
_DPI = 220


def _clip_for(page, bbox: List[float]):
    """Parser bboxes are in 0-1000 page units; convert to the page's own coordinates."""
    rect = page.rect
    x0, y0, x1, y1 = (float(v) for v in bbox)
    return pymupdf.Rect(x0 / 1000 * rect.width, y0 / 1000 * rect.height,
                     x1 / 1000 * rect.width, y1 / 1000 * rect.height)


def _mono_lines(page, clip):
    """(union rect, text lines) of the monospaced lines inside `clip`."""
    box, lines = None, []
    for block in page.get_text("dict", clip=clip)["blocks"]:
        for line in block.get("lines", []):
            spans = line["spans"]
            total = sum(len(s["text"]) for s in spans)
            if not total:
                continue
            mono = sum(len(s["text"]) for s in spans if s["flags"] & _MONO_FLAG)
            if mono / total < _MONO_SHARE:
                continue
            rect = pymupdf.Rect(line["bbox"])
            box = rect if box is None else (box | rect)
            lines.append("".join(s["text"] for s in spans).rstrip())
    return box, lines


def render_code_blocks(items: List[dict], pdf_path: Path | None, out_dir: Path) -> Dict[int, dict]:
    """{item index: {"img_path": str, "code": str}} for every `code` item that could be cropped.

    `img_path` is relative to the project root, matching how figure assets are stored."""
    manifest = collection.manifest_of(pdf_path) if pdf_path else None
    files = collection.is_files(manifest)       # a collection of separate files: each page is in its document's file
    if not pdf_path or not (files or Path(pdf_path).exists()):
        return {}

    out_dir = Path(out_dir)
    out: Dict[int, dict] = {}
    opened: Dict[str, pymupdf.Document] = {}

    def page_of(page_idx: int):
        path, local = collection.page_source(pdf_path, manifest, page_idx) or (None, -1) if files else (pdf_path, page_idx)
        if path is None:
            return None
        doc = opened.setdefault(str(path), pymupdf.open(str(path)))
        return doc[local] if 0 <= local < doc.page_count else None

    try:
        for i, it in enumerate(items):
            if it.get("type") != "code" or not it.get("bbox"):
                continue
            page_idx = it.get("page_idx", 0)
            page = page_of(page_idx) if isinstance(page_idx, int) else None
            if page is None:
                continue
            clip = _clip_for(page, it["bbox"])
            box, lines = _mono_lines(page, clip)
            if box is None or not lines:
                continue
            box = pymupdf.Rect(box.x0 - _PAD, box.y0 - _PAD, box.x1 + _PAD, box.y1 + _PAD) & page.rect
            out_dir.mkdir(parents=True, exist_ok=True)
            dst = out_dir / f"code_p{page_idx}_{i}.png"
            try:
                page.get_pixmap(clip=box, dpi=_DPI).save(str(dst))
            except Exception:
                continue
            try:
                rel = str(dst.relative_to(Config.BASE_DIR)).replace("\\", "/")
            except ValueError:
                rel = str(dst).replace("\\", "/")
            out[i] = {"img_path": rel, "code": "\n".join(lines).strip()}
    finally:
        for doc in opened.values():
            doc.close()
    return out
