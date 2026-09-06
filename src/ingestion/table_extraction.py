import re
from typing import List
from src.models.context import TableData

def extract_markdown_tables(markdown: str) -> List[TableData]:
    tables = []
    lines = markdown.splitlines()
    idx = 0
    table_index = 1
    while idx < len(lines):
        if _is_table_start(lines, idx):
            start = idx
            idx += 2
            while idx < len(lines) and lines[idx].strip().startswith('|'):
                idx += 1
            table_lines = lines[start:idx]
            markdown_table = '\n'.join(table_lines).strip()
            caption = _nearby_caption(lines, start, idx)
            if markdown_table and len(table_lines) >= 3:
                tables.append(TableData(
                    table_id=f'table_{table_index:03d}',
                    markdown=markdown_table,
                    table_caption=caption,
                    should_visualize='No',
                    image_table_path=None,
                ))
                table_index += 1
            continue
        idx += 1
    return tables

def _is_table_start(lines: List[str], idx: int) -> bool:
    if idx + 1 >= len(lines):
        return False
    first = lines[idx].strip()
    second = lines[idx + 1].strip()
    return first.startswith('|') and first.endswith('|') and re.match(r'^\|[\s:\-|]+\|$', second) is not None

def _nearby_caption(lines: List[str], start: int, end: int) -> str:
    window = lines[max(0, start - 3):min(len(lines), end + 3)]
    for line in window:
        text = line.strip().strip('*').strip()
        if re.search(r'\b(table|bảng)\b', text, re.IGNORECASE):
            return text[:240]
    return f'Table extracted from source document'
