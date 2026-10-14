from pathlib import Path

import pymupdf


def trimmed_toc(toc: list, start: int, end: int) -> list:
    """The outline entries on pages [start, end] (0-based), page-shifted, each one level below its nearest kept
    ancestor: an entry whose ancestors fall outside the range becomes top-level, and its siblings stay its siblings."""
    out, stack = [], []                      # stack: (source level, kept?, new level) of the current ancestors
    for lvl, title, page in toc:
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        kept = start + 1 <= page <= end + 1
        parent = next((new for _, k, new in reversed(stack) if k), 0)
        new = parent + 1
        stack.append((lvl, kept, new))
        if kept:
            out.append([new, title, page - start])
    return out


def trim_pdf_to_pages(src_path: Path, start: int, end: int, out_dir: Path) -> Path:
    """Physically cut `src_path` down to pages [start, end] (0-based, inclusive) and save as a new PDF
    under `out_dir`, named `{stem}_p{start}-{end}.pdf`. Cached: if that file already exists, returns it
    without re-cutting. Feeding the returned path through the normal gen_slide() pipeline makes every
    downstream stage (KG build, Phase 1 VLM extraction, Phase 2 plan/packet/bullet-writing) see a small
    self-contained document and process it whole -- no page-range plumbing needed anywhere else.
    """
    src_path = Path(src_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'{src_path.stem}_p{start}-{end}.pdf'
    if out_path.exists():
        return out_path

    src_doc = pymupdf.open(src_path)
    try:
        if not (0 <= start <= end < src_doc.page_count):
            raise ValueError(
                f'Page range {start}-{end} out of bounds for {src_path.name} '
                f'({src_doc.page_count} pages, 0-based)')
        trimmed = pymupdf.open()
        trimmed.insert_pdf(src_doc, from_page=start, to_page=end)
        trimmed.set_metadata(dict(src_doc.metadata or {}))      # keeps the book's title for the deck
        # insert_pdf() drops the outline; re-derive it from entries within [start,end], page-shifted.
        remapped = trimmed_toc(src_doc.get_toc(), start, end)
        if remapped:
            trimmed.set_toc(remapped)
        trimmed.save(out_path)
        trimmed.close()
    finally:
        src_doc.close()
    return out_path
