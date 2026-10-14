"""How well a generated deck covers its document, measured from the run's own files (no LLM): slides, coverage of
paragraphs and KG nodes, bullets that repeat another slide, the document's captioned tables and figures that reached
the deck, and formula rows whose term is not in the formula.

    python -m src.utils.deck_report data/lectures/<id> [more ids...] --out report.md
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import List, Optional

from pptx import Presentation
from pptx.oxml.ns import qn

from src.generator.slide_gen import output_name
from src.utils import scope_audit
from src.utils.bullet_text import overlap, support_ratio
from src.utils.config import Config

_TOKEN = re.compile(r"[^\W_]+")


def _one(folder: Path, pattern: str) -> Optional[Path]:
    hits = sorted(folder.glob(pattern))
    return hits[0] if hits else None


def _content_slides(lecture: dict) -> List[dict]:
    return [s for s in lecture.get("slides") or [] if not (s.get("slide") or {}).get("is_section_opener")]


def repeats(slides: List[dict]) -> tuple[int, int, float]:
    """(bullets, bullets that say what a bullet of another slide says, highest overlap of two slides' texts)."""
    bullets = [[b for b in s.get("content") or [] if isinstance(b, str) and b.strip()] for s in slides]
    total = sum(len(b) for b in bullets)
    same = 0
    for i, mine in enumerate(bullets):
        others = [b for j, bs in enumerate(bullets) if j != i for b in bs]
        same += sum(1 for b in mine if others and max(overlap(b, o) for o in others) >= Config.REPORT_REPEAT_OVERLAP)
    texts = [" ".join(b) for b in bullets]
    pair = max((overlap(texts[i], texts[j]) for i in range(len(texts)) for j in range(i + 1, len(texts))), default=0.0)
    return total, same, pair


def unlike_sources(slides: List[dict]) -> float:
    """Share of bullets that hardly share a word with the sentences they cite: written in another language, usually."""
    pairs = [(b, " ".join(str(r.get("quote") or "") for r in src)) for s in slides
             for b, src in zip(s.get("content") or [], s.get("bullet_sources") or []) if src]
    return round(sum(support_ratio(b, q) < Config.LANGUAGE_DRIFT_SUPPORT for b, q in pairs) / len(pairs), 3) if pairs else 0.0


def bad_formula_rows(slides: List[dict]) -> int:
    """Rows of an annotated formula whose term (the group title) names nothing in the formula."""
    bad = 0
    for s in slides:
        if s.get("layout_hint") != "formula_annotated_layout":
            continue
        formula = set(t.casefold() for t in _TOKEN.findall(str((s.get("slide") or {}).get("latex_block_formula") or "")))
        for g in s.get("groups") or []:
            terms = set(t.casefold() for t in _TOKEN.findall(str(g.get("title") or "")))
            bad += bool(terms) and not terms & formula
    return bad


def deck_visuals(deck: Optional[Path]) -> tuple[int, int]:
    """(tables, pictures) drawn in the deck."""
    if not deck or not deck.is_file():
        return 0, 0
    tables = pictures = 0
    for slide in Presentation(str(deck)).slides:
        for shape in slide.shapes:
            tables += bool(getattr(shape, "has_table", False) and shape.has_table)
        pictures += sum(1 for _ in slide._element.iter(qn("p:pic")))      # a placeholder may hold the picture
    return tables, pictures


def deck_of(lecture_id: str) -> Optional[Path]:
    """The deck generated for a lecture (whichever model wrote it)."""
    return next(iter(sorted((Config.OUTPUT_DIR / output_name(lecture_id)).glob("*/*.pptx"))), None)


def measure(folder: Path, deck: Optional[Path] = None) -> dict:
    """One run's numbers; {} when the run left no lecture."""
    folder = Path(folder)
    lecture_path = folder / f"{folder.name}.json"
    if not lecture_path.is_file():
        return {}
    lecture = json.loads(lecture_path.read_text(encoding="utf-8"))
    slides = _content_slides(lecture)
    scope = _one(folder, "*_scope_manifest.json")
    scope = json.loads(scope.read_text(encoding="utf-8")) if scope else {}
    ledger = list((scope.get("ledger") or {}).values())
    para = [sum(r["paragraphs"].get(k, 0) for r in ledger) for k in ("on_slide", "in_packet", "none")]
    nodes = [sum(r["nodes"].get(k, 0) for r in ledger) for k in ("on_slide", "left")]
    inventory = scope.get("asset_inventory") or []
    captioned = lambda kind: sum(1 for a in inventory if a.get("type") == kind and (a.get("caption") or kind == "table"))
    total, same, pair = repeats(slides)
    tables, pictures = deck_visuals(deck)
    checked = scope_audit.audit(folder)
    text = [sum(r.get("text_chars", {}).get(k, 0) for r in ledger) for k in ("on_slide", "in_packet", "none")]
    # a picture merged into the editable table it shows reached the deck as that table
    pictures += len({name for p in lecture.get("slide_packets") or []
                     for name in (p.get("assets") or {}).get("merged_pictures") or []})
    return {"domain": (lecture.get("metadata") or {}).get("domain", "-"), "slides": len(slides),
            "sections": len(scope.get("heading_tree") or []),
            "paragraphs": round(para[0] / sum(para), 3) if sum(para) else 0.0,
            "nodes": round(nodes[0] / sum(nodes), 3) if sum(nodes) else 0.0,
            "bullets": total, "repeated": same, "repeat_rate": round(same / total, 3) if total else 0.0,
            "slide_pair": round(pair, 3), "tables_kb": captioned("table"), "figures_kb": captioned("image"),
            "tables": tables, "pictures": pictures, "bad_formula_rows": bad_formula_rows(slides),
            "outside": checked["outside_scope"] + checked["outside_chapter"] + checked["quotes_elsewhere"],
            "sections_per_slide": checked["sections_per_slide"], "not_drawn": checked["not_drawn"],
            "empty_tables": checked["empty_tables"], "unlike_sources": unlike_sources(slides),
            "text": round(text[0] / sum(text), 3) if sum(text) else 0.0}


COLUMNS = [("outside", "Outside selection"), ("sections_per_slide", "Sections per slide"), ("not_drawn", "Tables not drawn"),
           ("empty_tables", "Empty tables"), ("unlike_sources", "Bullets unlike their sources"),
           ("text", "Text on slides"), ("slides", "Slides"), ("sections", "Sections"), ("paragraphs", "Paragraphs on slides"),
           ("nodes", "KG nodes"), ("repeat_rate", "Repeated bullets"), ("slide_pair", "Closest slide pair"),
           ("tables", "Tables (deck/KB)"), ("pictures", "Pictures (deck/KB figures)"), ("bad_formula_rows", "Bad WHERE rows")]


def cells(m: dict) -> List[str]:
    if not m:
        return ["-"] * len(COLUMNS)
    out = []
    for key, _ in COLUMNS:
        if key == "tables":
            out.append(f"{m['tables']}/{m['tables_kb']}")
        elif key == "pictures":
            out.append(f"{m['pictures']}/{m['figures_kb']}")
        elif isinstance(m[key], float):
            out.append(f"{m[key]:.0%}" if key not in ("slide_pair", "sections_per_slide") else f"{m[key]:.2f}")
        else:
            out.append(str(m[key]))
    return out


def main():
    parser = argparse.ArgumentParser(description="Coverage, repetition and visuals of generated decks")
    parser.add_argument("lectures", nargs="+", help="data/lectures/<id> folders")
    parser.add_argument("--out", default=None, help="Markdown table to write (default: print)")
    args = parser.parse_args()
    lines = ["| Lecture | " + " | ".join(h for _, h in COLUMNS) + " |", "|---|" + "--:|" * len(COLUMNS)]
    for folder in map(Path, args.lectures):
        lines.append(f"| {folder.name} | " + " | ".join(cells(measure(folder, deck_of(folder.name)))) + " |")
    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
