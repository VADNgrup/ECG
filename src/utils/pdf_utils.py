from pathlib import Path

import fitz


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

    src_doc = fitz.open(src_path)
    try:
        if not (0 <= start <= end < src_doc.page_count):
            raise ValueError(
                f'Page range {start}-{end} out of bounds for {src_path.name} '
                f'({src_doc.page_count} pages, 0-based)')
        trimmed = fitz.open()
        trimmed.insert_pdf(src_doc, from_page=start, to_page=end)
        trimmed.set_metadata(dict(src_doc.metadata or {}))      # keeps the book's title for the deck
        # insert_pdf() drops the outline; re-derive it from entries within [start,end], page-shifted.
        src_toc = src_doc.get_toc()  # list of [level, title, page_1based]
        kept = [[lvl, title, page - start]
                for lvl, title, page in src_toc if start + 1 <= page <= end + 1]
        if kept:
            # set_toc requires entry 0 at level 1 and no level jumping >1 deeper than its predecessor;
            # shift every level so the minimum becomes 1, then clamp to enforce the step-down-by-1 rule.
            min_lvl = min(lvl for lvl, _, _ in kept)
            remapped, prev_lvl = [], 0
            for lvl, title, page in kept:
                norm = max(1, lvl - min_lvl + 1)
                norm = min(norm, prev_lvl + 1)
                remapped.append([norm, title, page])
                prev_lvl = norm
            trimmed.set_toc(remapped)
        trimmed.save(out_path)
        trimmed.close()
    finally:
        src_doc.close()
    return out_path
