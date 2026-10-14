"""Several documents as one collection, described by a sidecar `<stem>.collection.json`: either merged into one PDF
(`merge`), or kept as separate files (`index`: no PDF is written; each file is read on its own and the readings are
joined for the KG, its pages numbered on after the documents before it).

Each document is one top-level entry of the collection's outline (its own outline, if it has one, nested below), so the
KG build sees every document as a root: `main.py` on the collection builds one KG and then one deck per document. A
document without an outline of its own gets the headings the parser detects inside it (`structural._detect_inside`).
Identical files (same bytes) are taken once; the sidecar lists their other names.

    python -m src.utils.collection data/raw --out data/raw/_collection/raw.pdf          # merged
    python -m src.utils.collection data/raw --out data/raw/_raw_files/raw_files.pdf --files   # separate files
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

import pymupdf

SUFFIX = ".collection.json"
FILES = "files"                  # sidecar key: the documents are separate PDFs (`path`), not pages of one merged PDF
TITLE_MIN_WORDS = 2              # a document title has at least this many words
TITLE_MAX_WORDS = 25             # and at most this many (more is a paragraph set large, not a title)


def sidecar_of(pdf_path) -> Path:
    path = Path(pdf_path)
    return path.with_name(path.stem + SUFFIX)


def manifest_of(pdf_path) -> Optional[dict]:
    """The collection's sidecar next to this PDF, or None for an ordinary document."""
    if not pdf_path:
        return None
    path = sidecar_of(pdf_path)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def printed_title(doc: pymupdf.Document) -> str:
    """The largest text on the first page (its title as printed), when it reads as a title: TITLE_MIN_WORDS to
    TITLE_MAX_WORDS words."""
    if not doc.page_count:
        return ""
    spans = [(round(s["size"], 1), s["text"]) for b in doc[0].get_text("dict").get("blocks", [])
             for line in b.get("lines", []) for s in line.get("spans", []) if s.get("text", "").strip()]
    if not spans:
        return ""
    top = max(size for size, _ in spans)
    title = " ".join(" ".join(t for size, t in spans if size == top).split())
    return title if TITLE_MIN_WORDS <= len(title.split()) <= TITLE_MAX_WORDS else ""


def file_words(name: str) -> str:
    """A file name as a title: its stem in words."""
    return " ".join(re.sub(r"[_+\-]+", " ", Path(name).stem).split()).replace(";", ",")


def title_of(doc: pymupdf.Document, path: Path) -> str:
    """The document's own title: printed on its first page, else its metadata, else its file name in words."""
    meta = " ".join(str((doc.metadata or {}).get("title") or "").split())
    title = (printed_title(doc) or (meta if len(meta.split()) >= TITLE_MIN_WORDS else "") or file_words(path.name))
    return title.replace(";", ",")               # `;` separates scope headings on the command line


def retitle(pdf_path, items: list) -> dict:
    """{title in the merged outline: title to use} for the documents still named after their file (their title is
    not in the text layer): the first top-level heading read on their first page, when it reads as a title and no
    other document has it. The sidecar keeps both (`file_title`, `title`)."""
    manifest = manifest_of(pdf_path)
    if not manifest:
        return {}
    documents = manifest["documents"]
    taken = {d["title"] for d in documents}
    changed = False
    for d in documents:
        if "file_title" in d or d["title"] != file_words(d["source"]):
            continue
        heads = [it for it in items if it.get("page_idx") == d["pages"][0] and it.get("type") == "text"
                 and it.get("text_level") == 1]
        title = " ".join(str(heads[0].get("text") or "").split()).replace(";", ",") if heads else ""
        if TITLE_MIN_WORDS <= len(title.split()) <= TITLE_MAX_WORDS and title not in taken:
            d["file_title"], d["title"] = d["title"], title
            taken.add(title)
            changed = True
    if changed:
        sidecar_of(pdf_path).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return {d["file_title"]: d["title"] for d in documents if "file_title" in d}


def _unique_title(doc: pymupdf.Document, path: Path, documents: List[dict]) -> str:
    """The document's title, made distinct from the titles already taken: every document is its own root."""
    title = title_of(doc, path)
    taken = {d["title"] for d in documents}
    if title in taken:
        title = f"{title} ({file_words(path.name)})"
    n = 2
    while title in taken:
        title = f"{title_of(doc, path)} ({n})"
        n += 1
    return title


def is_files(manifest: Optional[dict]) -> bool:
    """Whether a collection keeps its documents as separate files."""
    return bool(manifest and manifest.get(FILES))


def source_pdf(pdf_path, document: dict) -> Path:
    """A separate-files collection's document: its `path`, relative to the sidecar's folder."""
    return sidecar_of(pdf_path).parent / document["path"]


def index(sources: List[Path], out: Path) -> dict:
    """A collection of the PDFs as separate files: the sidecar of `out` (a PDF path that is never written) lists each
    document's title, file and pages in the collection's numbering (one after another); nothing is merged."""
    out.parent.mkdir(parents=True, exist_ok=True)
    documents, seen, start = [], {}, 0
    for path in sources:
        data = path.read_bytes()
        key = hashlib.sha256(data).hexdigest()
        if key in seen:                                  # the same file under another name: taken once
            seen[key]["aliases"].append(path.name)
            continue
        doc = pymupdf.open(stream=data, filetype="pdf")
        row = {"title": _unique_title(doc, path, documents), "source": path.name,
               "path": os.path.relpath(path, out.parent).replace("\\", "/"),
               "pages": [start, start + doc.page_count - 1], "has_outline": bool(doc.get_toc()), "aliases": []}
        start += doc.page_count
        documents.append(row)
        seen[key] = row
    manifest = {FILES: True, "documents": documents}
    sidecar_of(out).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def outline(pdf_path, manifest: dict) -> List[Tuple[int, str, int]]:
    """A separate-files collection's outline, as a merged PDF's would be: [(level, title, page 1-based)], each document
    one top-level entry with its own outline nested below, pages in the collection's numbering."""
    entries = []
    for d in manifest["documents"]:
        start = d["pages"][0]
        entries.append((1, d["title"], start + 1))
        with pymupdf.open(str(source_pdf(pdf_path, d))) as doc:
            entries += [(int(lvl) + 1, t, int(p) + start) for lvl, t, p in doc.get_toc() if str(t).strip()]
    return entries


def page_source(pdf_path, manifest: dict, page_idx: int) -> Optional[Tuple[Path, int]]:
    """(the document's file, its own page) of a page in a separate-files collection's numbering; None outside it."""
    for d in manifest["documents"]:
        if d["pages"][0] <= page_idx <= d["pages"][1]:
            return source_pdf(pdf_path, d), page_idx - d["pages"][0]
    return None


def merge(sources: List[Path], out: Path) -> dict:
    """Merge the PDFs into `out` with one outline entry per document; write and return the sidecar manifest."""
    out.parent.mkdir(parents=True, exist_ok=True)
    merged, toc, documents, seen = pymupdf.open(), [], [], {}
    for path in sources:
        data = path.read_bytes()
        key = hashlib.sha256(data).hexdigest()
        if key in seen:                                  # the same file under another name: merged once
            seen[key]["aliases"].append(path.name)
            continue
        doc = pymupdf.open(stream=data, filetype="pdf")
        start = merged.page_count
        merged.insert_pdf(doc)
        title = _unique_title(doc, path, documents)
        inner = [[lvl + 1, t, p + start] for lvl, t, p in doc.get_toc() if str(t).strip()]
        toc.append([1, title, start + 1])
        toc += inner
        row = {"title": title, "source": path.name, "pages": [start, merged.page_count - 1],
               "has_outline": bool(inner), "aliases": []}
        documents.append(row)
        seen[key] = row
    merged.set_toc(toc)
    merged.set_metadata({"title": out.stem.replace("_", " ")})
    merged.save(out)
    manifest = {"documents": documents}
    sidecar_of(out).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def own_outline_sizes(entries, manifest: dict) -> List[int]:
    """How many outline entries each document of a collection has of its own, without the one the merge adds for it."""
    sizes = []
    for d in manifest["documents"]:
        first, last = d["pages"]
        sizes.append(sum(1 for level, _, page in entries if level > 1 and first <= int(page) - 1 <= last))
    return sizes


def report_row(doc: dict, measures: dict, deck_path: Path, status: str) -> dict:
    """One document's line of the collection report: its field of study and kind, its deck and the deck's measures
    (`deck_report.measure`)."""
    return {"title": doc["title"], "source": doc["source"], "aliases": doc.get("aliases") or [],
            "pages": doc["pages"][1] - doc["pages"][0] + 1, "domain": measures.get("domain", "-"),
            "measures": measures, "deck": str(deck_path) if deck_path.is_file() else "-", "status": status}


def write_report(pdf_path, rows: List[dict], columns: List[tuple], cells) -> Path:
    """`<stem>_report.md` beside the collection PDF; `columns` [(key, header)] and `cells(measures)` lay out the
    measures."""
    path = Path(pdf_path).with_name(Path(pdf_path).stem + "_report.md")
    head = " | ".join(h for _, h in columns)
    lines = [f"| # | Document | Source | Pages | Field | {head} | Status | Deck |",
             "|--:|---|---|--:|---|---|" + "--:|" * len(columns) + "---|---|"]
    for n, r in enumerate(rows, 1):
        source = r["source"] + (f" (= {', '.join(r['aliases'])})" if r["aliases"] else "")
        lines.append(f"| {n} | {r['title']} | {source} | {r['pages']} | {r['domain']} | "
                     f"{' | '.join(cells(r['measures']))} | {r['status']} | {r['deck']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description="The PDFs of a folder as one collection (merged, or separate files)")
    parser.add_argument("folder", help="Folder whose *.pdf files are taken (not its sub-folders)")
    parser.add_argument("--out", required=True, help="The collection's PDF path (with --files only its sidecar is written)")
    parser.add_argument("--files", action="store_true", help="Keep the documents as separate files: nothing is merged")
    args = parser.parse_args()
    sources = sorted(p for p in Path(args.folder).glob("*.pdf") if p.is_file())
    manifest = (index if args.files else merge)(sources, Path(args.out))
    docs = manifest["documents"]
    print(f"[collection] {len(docs)} documents ({sum(len(d['aliases']) for d in docs)} duplicate file(s) taken once), "
          f"{docs[-1]['pages'][1] + 1 if docs else 0} pages -> {sidecar_of(args.out)}")


if __name__ == "__main__":
    main()
