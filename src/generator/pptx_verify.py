"""Read the exported PPTX back and check that what the manifest promised is really drawn on each page."""
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List

_TEXT = re.compile(r"<a:t>([^<]*)</a:t>")
_SLIDE = re.compile(r"ppt/slides/slide(\d+)\.xml$")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_MATH = re.compile(r"\$\$.+?\$\$|\$[^$]+\$|\\\(.+?\\\)|\\\[.+?\\\]", re.S)


def _words(text: str) -> List[str]:
    return [w for w in re.findall(r"[^\W_]{3,}", text.translate(_QUOTES).casefold())]


def slide_texts(pptx_path: Path) -> Dict[int, str]:
    """Page number -> all text drawn on it (a:t runs)."""
    out: Dict[int, str] = {}
    with zipfile.ZipFile(pptx_path) as archive:
        for name in archive.namelist():
            match = _SLIDE.search(name)
            if match:
                xml = archive.read(name).decode("utf-8", "ignore")
                out[int(match.group(1))] = " ".join(t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                                                    .replace("&quot;", '"').replace("&apos;", "'") for t in _TEXT.findall(xml))
    return out


def verify_pptx(pptx_path: Path, manifest: Dict[str, Any], min_share: float = 0.7) -> Dict[str, Any]:
    """empty_body: a content page whose body draws (almost) nothing; missing_text: bullets that are not on the page."""
    texts = slide_texts(Path(pptx_path))
    empty: List[int] = []
    missing: List[Dict[str, Any]] = []
    for page, slide in enumerate(manifest.get("slides", []), 1):
        items = [i for i in slide.get("content_items", []) if str(i).strip()]
        if not items and slide.get("type") not in ("formula", "formula_hero", "formula_annot"):
            continue
        drawn = texts.get(page, "")
        drawn_words = set(_words(drawn))
        title_words = set(_words(str(slide.get("title", ""))))
        if len(drawn_words - title_words) < 2 and not slide.get("formula_image_path"):
            empty.append(page)
            continue
        for item in items:
            wanted = _words(_MATH.sub(" ", str(item)))       # math is drawn as symbols, its words are LaTeX commands
            if wanted and sum(1 for w in wanted if w in drawn_words) < min_share * len(wanted):
                missing.append({"page": page, "text": str(item)[:120]})
    return {"pages_checked": len(manifest.get("slides", [])), "empty_body": empty, "missing_text": missing}
