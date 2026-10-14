"""Builds the document's `DocumentContext` (`data/context/{id}.json`: text, images, tables) from the artifacts its KG
build already wrote (`kv_store_text_chunks.json`, `asset_headings.json`), so the PDF is read only once, by the KG
extract step."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Optional

from PIL import Image

from src.models.asset import AssetCollection, AssetMetadata, ImageAsset
from src.models.context import DocumentContext, ProcessingMetadata, TableData, TextContent
from src.rag.kb_files import load_asset_rows
from src.utils.config import config
from src.utils.file_utils import save_json


def _image_dims_kb(path: Path) -> tuple[int, int, str]:
    """(width, height, format) via PIL; falls back to (0, 0, suffix) if the file can't be opened."""
    try:
        with Image.open(path) as im:
            return im.width, im.height, (im.format or path.suffix.lstrip('.')).lower()
    except Exception:
        return 0, 0, path.suffix.lstrip('.').lower()


def _load_asset_headings(kb_dir: Path) -> list[dict]:
    try:
        return load_asset_rows(kb_dir)
    except Exception:
        return []


def _reconstruct_markdown(kb_dir: Path) -> str:
    """Concatenate every chunk's `content` in original document order (`chunk_order_index`) to rebuild the
    full document markdown -- content is already plain text (no `[[p|n]]` structural markers inline; those
    live in the separate `__struct__` field), so no stripping is needed."""
    p = kb_dir / 'kv_store_text_chunks.json'
    if not p.exists():
        return ''
    try:
        chunks = json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return ''
    ordered = sorted(chunks.values(), key=lambda c: c.get('chunk_order_index', 0))
    return '\n\n'.join(c.get('content', '') for c in ordered)


def document_context_from_kg(document_id: str, kb_dir: Path, source_file: str) -> Optional[DocumentContext]:
    """Build a `DocumentContext` from an already-built KG's artifacts; None when they are missing."""
    kb_dir = Path(kb_dir)
    start = time.time()
    markdown = _reconstruct_markdown(kb_dir)
    if not markdown:
        return None

    headings = _load_asset_headings(kb_dir)
    img_asset_dir = config.ASSETS_DIR / document_id / 'images'
    img_asset_dir.mkdir(parents=True, exist_ok=True)

    images: list[ImageAsset] = []
    tables: list[TableData] = []
    table_idx = 0
    for h in headings:
        kind = h.get('type')
        if kind == 'image':
            src = Path(h.get('img_path', ''))
            if not src.exists():
                continue
            # Copy into the canonical image dir so downstream consumers (image_distribution, pptx
            # packaging) find it.
            dest = img_asset_dir / src.name
            if not dest.exists():
                shutil.copy2(src, dest)
            w, ht, fmt = _image_dims_kb(dest)
            images.append(ImageAsset(
                image_id=src.stem,
                file_path=f'assets/{document_id}/images/{src.name}',
                caption=h.get('caption', '') or '',
                reference_context=None,
                metadata=AssetMetadata(width=w, height=ht, format=fmt,
                                       file_size_kb=round(dest.stat().st_size / 1024, 1)),
            ))
        elif kind == 'table':
            table_idx += 1
            tables.append(TableData(
                table_id=f'table_{table_idx:03d}',
                markdown=h.get('table_body', '') or '',
                table_caption=h.get('caption', '') or 'Table extracted from source document',
                should_visualize='No',
                image_table_path=None,
            ))

    text_content = TextContent(markdown=markdown, page_count=_page_count(kb_dir))
    metadata = ProcessingMetadata(
        total_images=len(images), total_tables=len(tables),
        processing_time_seconds=round(time.time() - start, 2),
    )
    return DocumentContext(
        document_id=document_id,
        source_file=source_file,
        text_content=text_content,
        tables=tables,
        assets=AssetCollection(images=images),
        page_insights=[],
        metadata=metadata,
    )


def _page_count(kb_dir: Path) -> int:
    """Best-effort page count from asset_headings' max page (falls back to 0, only used for logging/
    metadata -- no consumer treats it as authoritative)."""
    headings = _load_asset_headings(kb_dir)
    pages = [h.get('page') for h in headings if isinstance(h.get('page'), int)]
    return (max(pages) + 1) if pages else 0


def save_context_from_kg(document_id: str, kb_dir: Path, source_file: str) -> Optional[Path]:
    """Build + write `data/context/{document_id}.json` from KG artifacts. Returns the output path, or None
    when the KG holds no usable text."""
    context = document_context_from_kg(document_id, kb_dir, source_file)
    if context is None:
        return None
    output_path = config.CONTEXT_DIR / f'{document_id}.json'
    save_json(context.model_dump(), output_path)
    return output_path
