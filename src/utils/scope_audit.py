"""Does a generated deck stay inside what was chosen? Computed again from the KB, no LLM: every cited sentence, paragraph,
table, formula and figure lies under a heading of the selection and of the slide's own chapter; a quote's words are in
the selection's text; numbers and names on the slide that the selection never states are listed with where they occur.

    python -m src.utils.scope_audit data/lectures/<id> [more ids...]
"""
from __future__ import annotations

import argparse
import json
import re as stdlib_re
from pathlib import Path
from typing import Dict, List, Optional, Set

import regex as re

from src.rag.chapter_map import load_chapters
from src.rag.idea_candidates import scope_sentences
from src.rag.kb_files import load_structural_index
from src.rag.retrieve_content import load_asset_headings
from src.workflow.scope import ScopeOrchestrator, find_kb_dir

_HARD = re.compile(r"\d[\d.,]*\d|\d|\b\p{Lu}[\p{L}\p{M}]+(?:[-'][\p{L}\p{M}]+)*\b")
_QUOTE_CHARS = 60                    # how much of a quote is looked for in the selection's text


def _norm(text: str) -> str:
    return stdlib_re.sub(r"\W+", " ", str(text or "")).casefold().strip()


def _texts(kb_dir: Path, index: dict, ids: Set[int]) -> Dict[int, str]:
    by = scope_sentences(kb_dir, index, ids)
    return {int(h): " ".join(str(s.get("text") or "") for s in (row.get("sentences") or [])) for h, row in by.items()}


def foreign_terms(text: str, inside: str, outside: Dict[int, str], titles: Dict[int, str]) -> List[tuple]:
    """(term, heading title elsewhere) for numbers and capitalised names the selection never states and another part
    does; a capitalised first word of the text is an ordinary word."""
    out = []
    for m in _HARD.finditer(str(text or "")):
        word = m.group(0).strip(".,")
        if m.start() == 0 and not word[:1].isdigit():
            continue
        key = word.casefold()
        if key in inside:
            continue
        where = next((h for h, t in outside.items() if key in t.casefold()), None)
        if where is not None:
            out.append((word, titles.get(where, "")[:40]))
    return out


def audit(folder: Path) -> dict:
    """{slides, cited, outside_scope, outside_chapter, quotes_elsewhere, foreign, sections_per_slide, off_section_tables,
    empty_tables, not_drawn, problems}: `problems` lists every violation; `foreign` are terms to read, not violations."""
    folder = Path(folder)
    lecture = json.loads((folder / f"{folder.name}.json").read_text(encoding="utf-8"))
    meta = lecture.get("metadata") or {}
    kb_dir = find_kb_dir(meta["source_document_id"])
    index = load_structural_index(kb_dir)
    scope = ScopeOrchestrator(index, kb_dir)
    roots = scope.resolve_roots(meta.get("root_heading"), scope.visible)
    allowed = set(scope.ordered_scope(roots, scope.visible))
    every = {int(h) for h in index["headings"]}
    titles = {h: scope.title(h) for h in every}
    text = _texts(kb_dir, index, every)
    inside = " ".join(text.get(h, "") + " " + titles[h] for h in allowed).casefold()
    inside_norm = _norm(inside)
    outside = {h: t for h, t in text.items() if h not in allowed}
    chapters = {c.id: c for c in load_chapters(kb_dir, index, roots, scope.visible)}
    figures = {str(a.get("img_path")): a for a in load_asset_headings(kb_dir) or [] if a.get("type") == "image"}
    packets = json.loads(next(iter(folder.glob("*_slide_packets.json"))).read_text(encoding="utf-8"))
    packet_of = {p.get("slide_id"): p for p in packets}

    problems: List[str] = []
    foreign: List[tuple] = []
    counts = {"cited": 0, "outside_scope": 0, "outside_chapter": 0, "quotes_elsewhere": 0, "off_section_tables": 0,
              "empty_tables": 0, "not_drawn": 0}
    sections: List[int] = []
    slides = [s for s in lecture.get("slides") or [] if not (s.get("slide") or {}).get("is_section_opener")]
    for s in slides:
        sl = s["slide"]
        title = sl.get("slide_title", "")
        own = (sl.get("grounding") or {}).get("scope_root_id")
        own_ids = set(scope.ordered_scope([own], scope.visible)) if own in roots else allowed
        cited: Set[int] = set()
        for bullet, sources in zip(s.get("content") or [], s.get("bullet_sources") or []):
            foreign += [(title[:30], str(bullet)[:70], t) for t in foreign_terms(bullet, inside, outside, titles)]
            for ref in sources:
                counts["cited"] += 1
                hid = ref.get("heading_id")
                if hid is not None:
                    cited.add(int(hid))
                    if int(hid) not in allowed:
                        counts["outside_scope"] += 1
                        problems.append(f"'{title}' cites heading {titles.get(int(hid), hid)!r}, outside the selection")
                    elif int(hid) not in own_ids:
                        counts["outside_chapter"] += 1
                        problems.append(f"'{title}' cites {titles.get(int(hid), hid)!r}, another chapter of the deck")
                quote = _norm(ref.get("quote"))[:_QUOTE_CHARS]
                if len(quote) >= _QUOTE_CHARS and quote not in inside_norm:
                    counts["quotes_elsewhere"] += 1
                    problems.append(f"'{title}': quote not in the selection's text: {quote!r}")
        foreign += [(title[:30], "(title)", t) for t in foreign_terms(title, inside, outside, titles)]
        sections.append(len(cited))
        packet = packet_of.get(sl.get("slide_id")) or {}
        chapter = chapters.get(own)
        for kind in ("table", "formula", "code"):
            for row in (packet.get("assets") or {}).get(kind) or []:
                hid = chapter.by_pid[row["pid"]]["heading"] if chapter and row.get("pid") in chapter.by_pid else None
                if hid is None or int(hid) not in allowed:
                    counts["outside_scope"] += 1
                    problems.append(f"'{title}' draws a {kind} ({row.get('pid')}) outside the selection")
        for pid in packet.get("pool") or {}:
            if chapter and pid in chapter.by_pid and int(chapter.by_pid[pid]["heading"]) not in allowed:
                counts["outside_scope"] += 1
                problems.append(f"'{title}' has paragraph {pid} outside the selection")
        for image in packet.get("section_assets") or []:
            node = (figures.get(str(image.get("id"))) or {}).get("node")
            if node is not None and int(node) not in allowed:
                counts["outside_scope"] += 1
                problems.append(f"'{title}' shows figure {image.get('id')} outside the selection")
        tables = [t for t in [sl.get("table"), *(sl.get("more_tables") or [])] if t]
        for t in tables:
            rows = (t.get("structured_table") or {}).get("rows") or []
            if rows and not any(str(c).strip() for r in rows for c in r):
                counts["empty_tables"] += 1
                problems.append(f"'{title}' draws a table with no data")
        drawn = (packet.get("assets") or {}).get("table") or []
        if sl.get("table") and drawn and chapter and drawn[0]["pid"] in chapter.by_pid \
                and int(chapter.by_pid[drawn[0]["pid"]]["heading"]) not in cited:
            counts["off_section_tables"] += 1
            problems.append(f"'{title}' draws a table of a section none of its bullets cites")
        counts["not_drawn"] += len(sl.get("tables_not_drawn") or [])
    return {"slides": len(slides), **counts, "foreign": foreign,
            "sections_per_slide": round(sum(sections) / len(sections), 2) if sections else 0.0,
            "most_sections": max(sections, default=0), "problems": problems,
            "root_heading": meta.get("root_heading")}


def main():
    parser = argparse.ArgumentParser(description="Check generated decks against the selection they were made from")
    parser.add_argument("lectures", nargs="+", help="data/lectures/<id> folders")
    parser.add_argument("--foreign", action="store_true", help="List the terms the selection never states")
    args = parser.parse_args()
    failed = 0
    for folder in map(Path, args.lectures):
        try:
            r = audit(folder)
        except ValueError as exc:                # a deck of an older KB: its selection names headings no longer there
            print(f"old  {folder.name[:60]} | {str(exc)[:70]}")
            continue
        failed += bool(r["problems"])
        print(f"{'FAIL' if r['problems'] else 'ok  '} {folder.name[:60]} | slides {r['slides']} | cited {r['cited']} | "
              f"outside selection {r['outside_scope']} | other chapter {r['outside_chapter']} | quotes elsewhere "
              f"{r['quotes_elsewhere']} | off-section tables {r['off_section_tables']} | empty tables {r['empty_tables']} | "
              f"not drawn {r['not_drawn']} | sections/slide {r['sections_per_slide']} (max {r['most_sections']}) | "
              f"terms to read {len(r['foreign'])}")
        for p in r["problems"][:12]:
            print("     ", p)
        if args.foreign:
            for row in r["foreign"]:
                print("     ?", row)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
