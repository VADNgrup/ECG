from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import fitz
from tqdm import tqdm

from src.rag.code_text import _MATH_FUNCS, _PROGRAM, _has_code_line, _word_ratio
from src.rag.structural import _looks_prose
from src.utils import lang
from src.utils.llm import b64_image, vision_chat


def _render_pages(pdf: Path, pages: tuple[int, int] | None, zoom: float = 2.0) -> list[tuple[int, bytes]]:
    out: list[tuple[int, bytes]] = []
    with fitz.open(str(pdf)) as d:
        rng = range(pages[0], min(pages[1], d.page_count - 1) + 1) if pages else range(d.page_count)
        for i in rng:
            pix = d[i].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            out.append((i, pix.tobytes("png")))
    return out


_OCR_PROMPT = (
    "You are a precise OCR engine. Transcribe ONLY the text that is ACTUALLY VISIBLE on THIS page image, "
    "EXACTLY, in reading order, as GitHub-flavored Markdown. Use #, ##, ### ONLY for real section/chapter "
    "headings that are visually larger/bolder than body text, preserving their hierarchy; a normal "
    "paragraph must stay plain text. Keep the ORIGINAL language and every diacritic precisely. "
    "CRITICAL: never invent, guess, complete, translate, summarize, correct, explain, or add ANYTHING "
    "that is not literally printed on this page — do NOT elaborate on codes, numbers, or product names. "
    "If the page is blank, a cover, or has no readable text, output NOTHING. Output ONLY the transcription."
)

_STYLE_WORD_CAP = 12


def _is_bold_span(span: dict) -> bool:
    font = (span.get("font") or "").lower()
    return "bold" in font or bool(int(span.get("flags") or 0) & 16)


def _is_italic_span(span: dict) -> bool:
    font = (span.get("font") or "").lower()
    return "italic" in font or "oblique" in font or bool(int(span.get("flags") or 0) & 2)


_CAPTION_RE = lang.caption_pattern([])


def _style_candidates(page) -> list[dict]:
    """Reads fitz's REAL font metadata (name + flags) per line -- not a visual guess -- to flag short,
    standalone lines that are bold/italic. bbox/font survive even on PDFs with a broken ToUnicode map
    (verified against data/raw/nhagiakim.pdf, which has real mojibake diacritics but intact span fonts/
    bboxes -- glyph rendering and the ToUnicode text-decoding table are independent PDF structures).
    This is a HINT for the OCR-cloud VLM prompt only -- it never auto-promotes anything to a heading
    itself; the VLM still decides using the actual page image (bold/italic != heading, e.g. inline
    emphasis or a defined term is neither).

    Verified against real pages of data/raw/Hands_On_Machine_Learning_...pdf: without the caption/
    numeric filters below, ~90% of pages triggered a candidate because italic figure/equation captions
    ("Figure 1-1. The traditional approach") and bold page-number artifacts ("3") dominated -- both
    excluded here since they are reliably NOT headings, not because they're not bold/italic."""
    d = page.get_text("dict")
    h = page.rect.height or 1
    out: list[dict] = []
    for block in d.get("blocks", []):
        lines = block.get("lines", [])
        if len(lines) != 1:  # only whole-block-is-one-line -- not embedded inside a longer paragraph
            continue
        spans = lines[0].get("spans", [])
        if not spans:
            continue
        text = "".join(s.get("text", "") for s in spans).strip()
        words = text.split()
        if not words or len(words) > _STYLE_WORD_CAP or text.endswith((".", ",", ";")):
            continue
        if text.isdigit() or _CAPTION_RE.match(text):
            continue
        bold = any(_is_bold_span(s) for s in spans)
        italic = any(_is_italic_span(s) for s in spans)
        if not (bold or italic):
            continue
        bbox = lines[0].get("bbox") or block.get("bbox") or (0, 0, 0, 0)
        y = bbox[1]
        position = "top" if y < h / 3 else ("middle" if y < 2 * h / 3 else "bottom")
        out.append({"bold": bold, "italic": italic, "position": position, "word_count": len(words)})
    return out


def _style_hint(candidates: list[dict]) -> str:
    if not candidates:
        return ""
    parts = []
    for c in candidates:
        style = "bold" if c["bold"] and not c["italic"] else ("italic" if c["italic"] and not c["bold"] else "bold/italic")
        parts.append(f"a {style} line (~{c['word_count']} words) near the {c['position']} of the page")
    return (
        f" Structural font analysis (independent of this image) detected {len(candidates)} short standalone "
        f"{'; '.join(parts)}. These MIGHT be headings, or might just be emphasis/labels -- look at the "
        f"actual image to judge: use #/##/### only if it truly functions as a section heading; if you're "
        f"unsure, wrap just that line in ** **; if it's clearly just emphasis inside body content, leave it "
        f"as plain text."
    )


def _ocr_cloud_page(png: bytes, hint: str = "") -> str:
    msg = [{"role": "user", "content": [{"type": "text", "text": _OCR_PROMPT + hint}, b64_image(png)]}]
    try:
        return vision_chat(msg, temperature=0.0, max_tokens=4096) or ""
    except Exception as e:
        tqdm.write(f"[ocr] cloud page failed: {str(e)[:80]}")
        return ""


_FENCE_OPEN = re.compile(r"^\s*(```|~~~)\s*([\w+#.-]*)\s*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_RULE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_TABLE_CAPTION = re.compile(r"^\s*(?:\*{1,2}|_)?Table\s+\d+[-–.]\d+\s*[.:]", re.IGNORECASE)
_MATH_OPEN = re.compile(r"^\s*(\\\[|\$\$|\\begin\{(equation|align|gather|multline)\*?\})")
# a displayed equation written as a line: a short left-hand side, then the inline math (`RMSE(X,h) = \(\sqrt{...}\)`)
_MATH_LINE = re.compile(r"^\s*([^\\]{0,40}?)\\\((.+)\\\)\s*[.,]?\s*$")
_LATEX = re.compile(r"\\(?:frac|sum|sqrt|prod|int|mathbf|mathrm|hat|left|right|begin|partial|theta|alpha|sigma)\b")
_CODE_WORDS = re.compile(r"(?m)^\s*(?:>>>|import |from \S+ import|def |class |print\(|for .+:|if .+:)")


def _math_line(s: str) -> Optional[str]:
    """`$$ lhs latex $$` for a line that is a displayed equation (at most a few words around one inline math span)."""
    m = _MATH_LINE.match(s)
    if not m or "\\(" in m.group(2) or len(m.group(1).split()) > 4:
        return None
    lhs = m.group(1).strip()
    return f"$$ {lhs + ' ' if lhs else ''}{m.group(2).strip()} $$"


def _latex_fence(body: str) -> bool:
    """A fenced block holding mathematics (LaTeX commands, no programming lines), not a code listing."""
    return bool(_LATEX.search(body)) and not _CODE_WORDS.search(body)


# mathematics typed in Unicode: Greek, super/subscripts, operators, arrows, bracket pieces
_MATH_CHARS = re.compile(r"[Ͱ-Ͽ⁰-₟ᴬ-ᵪ←-⇿∀-⋿⌈-⌋⎛-⎭"
                         r"⟨⟩‖√·×−≠≤≥]")
_RELATION = re.compile(r"[=<>≤≥≠←→↦∈∑∏∫·×+−⊕⊗⊙∧∨¬√∂∇⎛⎜⎝⎞⎟⎠]")


def _math_text(s: str) -> bool:
    """A line of mathematics in Unicode: math symbols and a relation or operator, no programming syntax, at most a few
    plain words around it; never a heading or a list item (a symbol's explanation reads as prose)."""
    if (not _MATH_CHARS.search(s) or not _RELATION.search(s) or _PROGRAM.search(s)
            or re.match(r"^(?:#|[-*•]\s)", s)):
        return False
    words = [w for w in re.findall(r"[A-Za-z]{3,}", s) if w.lower() not in _MATH_FUNCS]
    return len(words) <= 3


def _bare_math(s: str) -> bool:
    """A line of LaTeX without delimiters: LaTeX commands, no inline math, at most a few words outside them."""
    if "\\(" in s or not (_LATEX.search(s) or (s.startswith("=") and "\\" in s)):
        return False
    rest = re.sub(r"\\text\{[^}]*\}|\\[A-Za-z]+", " ", s)
    return len(re.findall(r"[A-Za-z]{3,}", rest)) <= 2


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _table_html(rows: list[str]) -> str:
    """A markdown table as the HTML table MinerU emits (first row the header)."""
    body = [r for r in rows if not _TABLE_RULE.match(r)]
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    head = "".join(f"<th>{esc(c)}</th>" for c in _cells(body[0]))
    rest = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in _cells(r)) + "</tr>" for r in body[1:])
    return f"<table><tr>{head}</tr>{rest}</table>"


def _math_block(lines: list[str], i: int) -> tuple[str, int]:
    """The display equation opening at line i, as `$$ latex $$`, and the index after it."""
    first = lines[i].strip()
    m = _MATH_OPEN.match(first)
    close = {"\\[": "\\]", "$$": "$$"}.get(m.group(1), f"\\end{{{m.group(2)}")
    inner = first[len(m.group(1)):] if m.group(1) in ("\\[", "$$") else first
    j = i
    while close not in inner and j + 1 < len(lines):
        j += 1
        inner += "\n" + lines[j].strip()
    if m.group(1) in ("\\[", "$$"):
        inner = inner.split(close, 1)[0]
    return f"$$ {inner.strip()} $$", j + 1


def _split_fence(body: str) -> list[tuple[str, list[str]]]:
    """A fenced block as runs of ("code" | "prose", lines): the model often fences a whole page, listings and the
    paragraphs around them together. A line is prose when it reads as words (>= 6, mostly plain); a blank, short or
    indented line goes with the run it sits in."""
    runs: list[tuple[str, list[str]]] = []
    for ln in body.splitlines():
        words = ln.split()
        prose = len(words) >= 6 and _word_ratio(ln) >= 0.75 and not ln[:1].isspace()
        code = (bool(ln.strip()) and not prose and not _math_text(ln) and not _bare_math(ln.strip())
                and _has_code_line(ln))
        kind = "prose" if prose else "code" if code else (runs[-1][0] if runs else None)
        if kind is None or not runs or runs[-1][0] != kind:
            runs.append((kind or "prose", [ln]))
        else:
            runs[-1][1].append(ln)
    out: list[tuple[str, list[str]]] = []
    for kind, lines in runs:                     # a short run without one real code line is prose after all
        kind = "code" if kind == "code" and _has_code_line("\n".join(lines)) else "prose"
        if out and out[-1][0] == kind:
            out[-1][1].extend(lines)
        else:
            out.append((kind, lines))
    return out


def _md_to_items(md: str, page_idx: int) -> list[dict]:
    """A page's markdown as content_list items: headings and paragraphs as text, and -- as MinerU gives them -- display
    equations (`equation`), markdown tables (`table`, HTML) and code listings (`code`, lines and indentation kept). A
    fence the model wrapped around prose is read as prose."""
    items: list[dict] = []
    buf: list[str] = []

    def _flush() -> None:
        if buf:
            t = " ".join(buf).strip()
            if t:
                items.append({"type": "text", "text": t, "page_idx": page_idx})
            buf.clear()

    lines = (md or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        fence = _FENCE_OPEN.match(line)
        if fence:
            _flush()
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith(fence.group(1)):
                j += 1
            body = "\n".join(ln.rstrip() for ln in lines[i + 1:j])
            if _latex_fence(body):                   # mathematics (and prose) the model fenced: read it as markdown
                lines[i:j + 1] = lines[i + 1:j]
                continue
            pieces, prose = [], []
            for kind, run in _split_fence(body):
                if kind == "code" and "\n".join(run).strip():
                    pieces.append((list(prose), "\n".join(run).strip("\n")))
                    prose = []
                else:
                    prose.extend(run)
            if not pieces:                           # prose the model fenced: read its lines as markdown
                lines[i:j + 1] = lines[i + 1:j]
                continue
            for before, code in pieces:
                items.extend(_md_to_items("\n".join(before), page_idx))
                items.append({"type": "code", "sub_type": fence.group(2) or "code", "page_idx": page_idx,
                              "code_body": f"```{fence.group(2)}\n{code}\n```"})
            items.extend(_md_to_items("\n".join(prose), page_idx))
            i = j + 1
            continue
        if _MATH_OPEN.match(s):
            _flush()
            latex, i = _math_block(lines, i)
            items.append({"type": "equation", "text": latex, "page_idx": page_idx})
            continue
        if _math_line(s):
            _flush()
            items.append({"type": "equation", "text": _math_line(s), "page_idx": page_idx})
            i += 1
            continue
        if _bare_math(s) or _math_text(s):           # LaTeX without delimiters, or Unicode mathematics; its lines join
            _flush()
            more = _bare_math if _bare_math(s) else _math_text
            j = i + 1
            while j < len(lines) and more(lines[j].strip()):
                j += 1
            latex = " ".join(ln.strip() for ln in lines[i:j])
            items.append({"type": "equation", "text": f"$$ {latex} $$", "page_idx": page_idx})
            i = j
            continue
        if _TABLE_ROW.match(s) and i + 1 < len(lines) and _TABLE_RULE.match(lines[i + 1]):
            _flush()
            j = i
            while j < len(lines) and _TABLE_ROW.match(lines[j]):
                j += 1
            caption = []
            if items and items[-1]["type"] == "text" and _TABLE_CAPTION.match(items[-1]["text"]):
                caption = [items.pop()["text"]]
            elif j < len(lines) and _TABLE_CAPTION.match(lines[j]):
                caption, j = [lines[j].strip()], j + 1
            items.append({"type": "table", "table_body": _table_html(lines[i:j]), "table_caption": caption,
                          "page_idx": page_idx})
            i = j
            continue
        i += 1
        if not s or s == "---":
            _flush()
            continue
        m = re.match(r"^(#{1,6})\s+(.+)$", s)
        mb = re.match(r"^(?:\*\*|__)(.+?)(?:\*\*|__)$", s)
        if m:
            _flush()
            items.append({"type": "text", "text": m.group(2).strip(),
                          "text_level": len(m.group(1)), "page_idx": page_idx})
        elif mb and 0 < len(mb.group(1).split()) <= 12:
            _flush()
            items.append({"type": "text", "text": mb.group(1).strip(),
                          "text_level": 2, "page_idx": page_idx})
        else:
            buf.append(re.sub(r"^[-*+]\s+", "", s))
    _flush()
    return items


_TOC_LINE_RE = re.compile(r"\S\s+\d{1,3}$")
_TOC_MIN_RUN = 12


def _is_toc_line(line: str) -> bool:
    return bool(_TOC_LINE_RE.search(re.sub(r"^#{1,6}\s+", "", (line or "").strip())))


def _split_toc(page_md: dict[int, str]) -> tuple[str, dict[int, str]]:
    flat: list[tuple[int, str]] = []
    for idx in sorted(page_md):
        for ln in (page_md[idx] or "").splitlines():
            flat.append((idx, ln))
    runs: list[tuple[int, int, int]] = []
    start = last_hit = None
    hits = 0
    for j, (_idx, l) in enumerate(flat):
        if _is_toc_line(l):
            if start is None:
                start, hits = j, 0
            last_hit, hits = j, hits + 1
        elif start is not None and l.strip() and (j - last_hit) > 3:
            runs.append((start, last_hit, hits))
            start = None
    if start is not None:
        runs.append((start, last_hit, hits))
    runs = [r for r in runs if r[2] >= _TOC_MIN_RUN]
    if not runs:
        return "", dict(page_md)
    s, e, _ = max(runs, key=lambda r: r[2])
    while s > 0 and flat[s - 1][0] == flat[s][0]:
        prev = flat[s - 1][1].strip()
        if not prev:
            s -= 1
            continue
        if not _looks_prose(re.sub(r"^#{1,6}\s+", "", prev)):
            s -= 1
            break
        break
    toc_text = "\n".join(flat[j][1] for j in range(s, e + 1) if flat[j][1].strip())
    kept: dict[int, list[str]] = {idx: [] for idx in page_md}
    for j, (idx, l) in enumerate(flat):
        if not (s <= j <= e):
            kept[idx].append(l)
    return toc_text, {idx: "\n".join(ls) for idx, ls in kept.items()}


_FIG_CAPTION = re.compile(r"^\s*(?:Figure|Fig\.|Hình)\s*\d+[-–.]\d+\s*[.:]")
_FIG_GAP = 36            # points: graphics this close above one another are parts of one figure
_CAPTION_GAP = 60        # points: the figure ends at most this far above its caption
_FIG_MIN_AREA = 0.02     # share of the page below which a captioned region is not a figure
_PROSE_WORDS = 12        # a text block this long is a paragraph, never a label inside a figure


def _norm_bbox(r, page) -> list[float]:
    """A rect in the 0-1000 page units of a MinerU block."""
    w, h = page.rect.width, page.rect.height
    return [round(r.x0 / w * 1000, 1), round(r.y0 / h * 1000, 1), round(r.x1 / w * 1000, 1), round(r.y1 / h * 1000, 1)]


def _page_figures(page, i: int, stem: str, images_dir: Path) -> list[dict]:
    """Figures of a page with a text layer, found from their captions: the graphics (raster images and vector
    drawings) stacked right above a "Figure N-M." caption, with the labels inside them, rendered as one image."""
    blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
    captions = sorted((b for b in blocks if _FIG_CAPTION.match(b[4])), key=lambda b: b[1])
    if not captions:
        return []
    page_area = page.rect.width * page.rect.height
    graphics = [fitz.Rect(info["bbox"]) for info in page.get_image_info()]
    graphics += [fitz.Rect(dr["rect"]) for dr in page.get_drawings()]
    graphics = [g for g in graphics if (g.width > 0 or g.height > 0) and g.width * g.height < 0.9 * page_area]  # a rule line has no height
    text = [(fitz.Rect(b[:4]), len(b[4].split())) for b in blocks if not _FIG_CAPTION.match(b[4])]
    labels = [r for r, words in text if words < _PROSE_WORDS]
    prose = [r for r, words in text if words >= _PROSE_WORDS]
    out = []
    for k, cap in enumerate(captions):
        c = fitz.Rect(cap[:4])
        ceiling = max([fitz.Rect(o[:4]).y1 for o in captions if o[3] <= c.y0] + [page.rect.y0])
        region, top = None, c.y0
        for g in sorted((g for g in graphics if g.y1 <= c.y0 + 2 and g.y0 >= ceiling - 2), key=lambda g: -g.y1):
            if g.y1 < top - (_FIG_GAP if region is not None else _CAPTION_GAP) or any(r.y0 >= g.y1 - 2 and r.y1 <= top + 2 for r in prose):
                break                                # a gap or a paragraph: the figure ends here
            region = fitz.Rect(g) if region is None else region | g
            top = min(top, g.y0)
        if region is None:
            continue
        for b in labels:
            if b.y0 >= region.y0 - 2 and b.y1 <= c.y0 + 2:
                region |= b
        region &= page.rect
        if region.width * region.height < _FIG_MIN_AREA * page_area:
            continue
        fname = f"{stem}_p{i}_fig{k}.png"
        fpath = images_dir / fname
        if not fpath.exists():
            page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), clip=region).save(str(fpath))
        out.append({"type": "image", "img_path": f"images/{fname}", "image_caption": [" ".join(cap[4].split())],
                    "image_footnote": [], "bbox": _norm_bbox(region, page), "page_idx": i})
    return out


def _page_image_items(d, i: int, stem: str, images_dir: Path) -> list[dict]:
    """The page's embedded raster images with their place on the page (a scanned page has no captions to anchor
    figures to; its captions are assigned from the OCR text afterwards)."""
    out: list[dict] = []
    page = d[i]
    for img_i, img in enumerate(page.get_images(full=True)):
        try:
            rects = page.get_image_rects(img[0])
            if not rects:
                continue
            base = d.extract_image(img[0])
            ext = base.get("ext", "png")
            fname = f"{stem}_p{i}_img{img_i}.{ext}"
            fpath = images_dir / fname
            if not fpath.exists():
                fpath.write_bytes(base["image"])
            out.append({"type": "image", "img_path": f"images/{fname}", "image_caption": [], "image_footnote": [],
                        "bbox": _norm_bbox(rects[0], page), "page_idx": i})
        except Exception:
            continue
    return out


def _caption_scanned(images: list[dict], texts: list[dict]) -> None:
    """Scanned page: its "Figure N-M." lines read by OCR go, in reading order, to its figures large enough to be one."""
    big = sorted((im for im in images if (im["bbox"][2] - im["bbox"][0]) * (im["bbox"][3] - im["bbox"][1]) / 1e6
                  >= _FIG_MIN_AREA), key=lambda im: im["bbox"][1])
    lines = [t for t in texts if _FIG_CAPTION.match(t.get("text", ""))]
    for im, line in zip(big, lines):
        im["image_caption"] = [line["text"]]


def ocr_pdf(pdf: Path, out_dir: Path, pages: tuple[int, int] | None, on_progress=None) -> Path:
    stem = pdf.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(exist_ok=True)
    images_dir = out_dir / "images"
    images_dir.mkdir(exist_ok=True)

    page_md: dict[int, str] = {}
    todo_idxs: list[int] = []
    image_items: list[dict] = []
    with fitz.open(str(pdf)) as d:
        idxs = list(range(pages[0], min(pages[1], d.page_count - 1) + 1)) if pages else list(range(d.page_count))
        for i in idxs:
            f = pages_dir / f"{i}.md"
            if f.exists():
                page_md[i] = f.read_text(encoding="utf-8")
            else:
                todo_idxs.append(i)
            figures = _page_figures(d[i], i, stem, images_dir)
            image_items += figures or _page_image_items(d, i, stem, images_dir)
        todo = [(i, d[i].get_pixmap(matrix=fitz.Matrix(2.0, 2.0)).tobytes("png"),
                 _style_hint(_style_candidates(d[i]))) for i in todo_idxs]
    if todo:
        total = len(todo)
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = ex.map(lambda pg: (pg[0], _ocr_cloud_page(pg[1], pg[2])), todo)
            if on_progress is None:
                results = tqdm(results, total=total, desc="cloud OCR", unit="pg")
            for done, (idx, md) in enumerate(results, 1):
                (pages_dir / f"{idx}.md").write_text(md, encoding="utf-8")
                page_md[idx] = md
                if on_progress:
                    on_progress(done, total)

    toc_text, page_md = _split_toc(page_md)

    text_items: list[dict] = []
    for idx in sorted(page_md):
        page_text = _md_to_items(page_md[idx], idx)
        page_images = [im for im in image_items if im["page_idx"] == idx]
        if page_images and not any(im["image_caption"] for im in page_images):
            _caption_scanned(page_images, page_text)
        held = {" ".join(c.split()).casefold() for im in page_images for c in im["image_caption"]}
        text_items += [it for it in page_text if " ".join(it.get("text", "").split()).casefold() not in held]

    items = text_items + image_items
    items.sort(key=lambda it: it.get("page_idx", 0))
    cl = out_dir / f"{stem}_content_list.json"
    cl.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    toc_sidecar = Path(f"{cl}.printed_toc.txt")
    if toc_text.strip():
        toc_sidecar.write_text(toc_text, encoding="utf-8")
        tqdm.write(f"[ocr] harvested printed TOC ({toc_text.count(chr(10)) + 1} lines) -> {toc_sidecar.name}")
    elif toc_sidecar.exists():
        toc_sidecar.unlink()
    return cl
