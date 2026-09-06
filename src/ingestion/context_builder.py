from pathlib import Path
from typing import List, Optional
import time
from src.models.context import DocumentContext, TextContent, TableData, ProcessingMetadata, PageInsight
from src.models.asset import AssetCollection, ImageAsset
from src.ingestion.parser import ParsedContent
from src.utils.file_utils import save_json
from src.utils.config import config
from src.ingestion.table_extraction import extract_markdown_tables

class ContextWindowBuilder:

    def __init__(self, document_id: str, source_file: str, start_time: Optional[float]=None):
        self.document_id = document_id
        self.source_file = source_file
        self.start_time = start_time if start_time is not None else time.time()

    def build_context(self, parsed_content: ParsedContent, tables_markdown: List[TableData], images: List[ImageAsset]) -> DocumentContext:
        full_text = parsed_content.full_text
        if not tables_markdown:
            tables_markdown = extract_markdown_tables(full_text)
        text_content = TextContent(markdown=full_text, page_count=parsed_content.page_count)
        asset_collection = AssetCollection(images=images)
        page_insights = [PageInsight(**item) for item in getattr(parsed_content, 'page_insights', []) or []]
        processing_time = time.time() - self.start_time
        metadata = ProcessingMetadata(total_images=len(images), total_tables=len(tables_markdown), processing_time_seconds=round(processing_time, 2))
        context = DocumentContext(
            document_id=self.document_id,
            source_file=self.source_file,
            text_content=text_content,
            tables=tables_markdown,
            assets=asset_collection,
            page_insights=page_insights,
            metadata=metadata,
        )
        return context

    def save_context(self, context: DocumentContext) -> Path:
        output_path = config.CONTEXT_DIR / f'{self.document_id}.json'
        save_json(context.model_dump(), output_path)
        return output_path
