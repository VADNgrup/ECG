"""Slide images of a deck, rendered by PowerPoint on Windows and by LibreOffice elsewhere: the evaluation reads each
slide as a picture."""
from __future__ import annotations

import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List

import pymupdf

from src.utils.config import Config

if sys.platform == "win32":
    import pythoncom
    import win32com.client

SLIDE_IMAGES = "slide_images"           # the folder beside a deck that holds its slide pictures


def render(pptx: Path, out_dir: Path) -> List[Path]:
    """Each slide of `pptx` as `slide_<n>.png` in `out_dir`, EVAL_SLIDE_WIDTH_PX wide; the files written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    return _powerpoint(Path(pptx), out_dir) if sys.platform == "win32" else _libreoffice(Path(pptx), out_dir)


def _powerpoint(pptx: Path, out_dir: Path) -> List[Path]:
    pythoncom.CoInitialize()
    app = win32com.client.Dispatch("PowerPoint.Application")
    deck = app.Presentations.Open(str(pptx.resolve()), True, False, False)     # read-only, no window
    try:
        width = Config.EVAL_SLIDE_WIDTH_PX
        height = round(width * deck.PageSetup.SlideHeight / deck.PageSetup.SlideWidth)
        written = []
        for n in range(1, deck.Slides.Count + 1):
            path = out_dir / f"slide_{n:03d}.png"
            deck.Slides(n).Export(str(path.resolve()), "PNG", width, height)
            written.append(path)
        return written
    finally:
        deck.Close()
        if app.Presentations.Count == 0:
            app.Quit()
        pythoncom.CoUninitialize()


_RELATION = re.compile(r'<Relationship\b[^>]*?\bId="([^"]+)"[^>]*?\bTarget="([^"]+)"[^>]*/>')


def _targets(z: zipfile.ZipFile, part: str) -> Dict[str, str]:
    """{relationship id: the part it points to} of a package part."""
    rels = f"{posixpath.dirname(part)}/_rels/{posixpath.basename(part)}.rels"
    if rels not in z.namelist():
        return {}
    base = posixpath.dirname(part)
    return {rid: posixpath.normpath(posixpath.join(base, target))
            for rid, target in _RELATION.findall(z.read(rels).decode("utf-8"))}


def _drop(xml: str, element: str, gone: set) -> str:
    """`xml` without the `element` entries whose r:id is in `gone`."""
    return re.sub(rf'<p:{element}\b[^>]*?r:id="([^"]+)"[^>]*/>', lambda m: "" if m.group(1) in gone else m.group(0), xml)


def _used_designs_only(pptx: Path, out: Path) -> None:
    """A copy of the deck whose presentation lists only the slide masters and layouts its slides use: every deck
    embeds every design (switched in PowerPoint), and LibreOffice reads all of them before the first slide."""
    with zipfile.ZipFile(pptx) as z:
        slides = [n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        layouts = {t for s in slides for t in _targets(z, s).values() if "/slideLayouts/" in t}
        masters = {t for lay in layouts for t in _targets(z, lay).values() if "/slideMasters/" in t}
        edits: Dict[str, bytes] = {}
        listed = _targets(z, "ppt/presentation.xml")
        gone = {rid for rid, t in listed.items() if "/slideMasters/" in t and t not in masters}
        edits["ppt/presentation.xml"] = _drop(z.read("ppt/presentation.xml").decode("utf-8"), "sldMasterId", gone).encode("utf-8")
        rels = "ppt/_rels/presentation.xml.rels"
        edits[rels] = re.sub(r'<Relationship\b[^>]*?\bId="([^"]+)"[^>]*/>',
                             lambda m: "" if m.group(1) in gone else m.group(0), z.read(rels).decode("utf-8")).encode("utf-8")
        for master in masters:
            unused = {rid for rid, t in _targets(z, master).items() if "/slideLayouts/" in t and t not in layouts}
            edits[master] = _drop(z.read(master).decode("utf-8"), "sldLayoutId", unused).encode("utf-8")
            mrels = f"{posixpath.dirname(master)}/_rels/{posixpath.basename(master)}.rels"
            edits[mrels] = re.sub(r'<Relationship\b[^>]*?\bId="([^"]+)"[^>]*/>',
                                  lambda m: "" if m.group(1) in unused else m.group(0),
                                  z.read(mrels).decode("utf-8")).encode("utf-8")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as w:
            for item in z.infolist():
                w.writestr(item, edits.get(item.filename, z.read(item.filename)))


def _libreoffice(pptx: Path, out_dir: Path) -> List[Path]:
    """The deck (its used designs only) converted to PDF by LibreOffice (`soffice --headless`, a profile of its own
    so no other instance blocks it), each page drawn at EVAL_SLIDE_WIDTH_PX."""
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if not office:
        raise RuntimeError("rendering slides needs PowerPoint (Windows) or LibreOffice on PATH "
                           "(e.g. `sudo apt install libreoffice-impress`)")
    with tempfile.TemporaryDirectory() as tmp:
        slim = Path(tmp) / pptx.name
        _used_designs_only(pptx, slim)
        subprocess.run([office, f"-env:UserInstallation={(Path(tmp) / 'profile').as_uri()}", "--headless",
                        "--norestore", "--nologo", "--nodefault", "--nolockcheck", "--convert-to", "pdf",
                        "--outdir", tmp, str(slim)],
                       check=True, capture_output=True, timeout=Config.EVAL_RENDER_TIMEOUT_S)
        written = []
        with pymupdf.open(Path(tmp) / f"{pptx.stem}.pdf") as doc:
            for n, page in enumerate(doc, 1):
                zoom = Config.EVAL_SLIDE_WIDTH_PX / page.rect.width
                path = out_dir / f"slide_{n:03d}.png"
                page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(path)
                written.append(path)
    return written
