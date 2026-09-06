import argparse
import time
import os
from pathlib import Path
import re
from src.utils.config import config, Config
from src.ingestion.parser import DocumentParser
from src.ingestion.context_builder import ContextWindowBuilder
from src.utils.file_utils import load_json
from src.models.context import DocumentContext


def _heuristic_page_insights(markdown: str) -> list[dict]:
    matches = list(re.finditer(r"<!--\s*PAGE\s+(\d+)\s*-->", markdown, flags=re.IGNORECASE))
    pages = []
    if not matches:
        pages = [(1, markdown)]
    else:
        for idx, match in enumerate(matches):
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
            pages.append((int(match.group(1)), markdown[start:end].strip()))
    insights = []
    for page_num, page_md in pages:
        headings = re.findall(r"^#{1,4}\s+(.+)$", page_md, flags=re.MULTILINE)
        bullets = re.findall(r"^\s*(?:[-*]|\d+[.)])\s+(.+)$", page_md, flags=re.MULTILINE)
        insights.append({
            "page": page_num,
            "page_role": "content",
            "page_title": headings[0].strip()[:180] if headings else "",
            "must_have_points": [item.strip()[:180] for item in bullets[:5] if len(item.strip()) >= 12],
            "support_points": [],
            "noise_points": [],
            "confidence": 0.15,
        })
    return insights


def _page_insights_need_reparse(context_payload: dict) -> bool:
    insights = context_payload.get("page_insights") or []
    if not insights:
        return True
    avg_conf = sum(float(item.get("confidence", 0.0) or 0.0) for item in insights if isinstance(item, dict)) / max(1, len(insights))
    must_have_count = sum(len(item.get("must_have_points", []) or []) for item in insights if isinstance(item, dict))
    markdown = context_payload.get("text_content", {}).get("markdown", "")
    mismatch = _dominant_script(markdown) != _dominant_script(" ".join(str(item.get("page_title", "")) for item in insights if isinstance(item, dict)))
    return avg_conf < 0.35 or must_have_count == 0 or mismatch


def _dominant_script(text: str) -> str:
    counts = {"latin": 0, "cjk": 0, "cyrillic": 0, "arabic": 0, "other": 0}
    for ch in text or "":
        code = ord(ch)
        if "A" <= ch <= "Z" or "a" <= ch <= "z":
            counts["latin"] += 1
        elif 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
            counts["cjk"] += 1
        elif 0x0400 <= code <= 0x04FF:
            counts["cyrillic"] += 1
        elif 0x0600 <= code <= 0x06FF:
            counts["arabic"] += 1
        elif ch.isalpha():
            counts["other"] += 1
    dominant = max(counts.items(), key=lambda item: item[1])[0]
    return dominant if counts[dominant] > 0 else "unknown"


def extract_file(input_path):
    config.validate()
    processing_start = time.time()
    document_id = Path(input_path).stem
    
    print(f"\n{'=' * 60}")
    print(f'Phase 1: Hybrid VLM Extraction for {document_id}')
    print(f"{'=' * 60}\n")
    
    parsed_context_path = Path(Config.CONTEXT_DIR / f'{document_id}.json')
    if parsed_context_path.exists():
        print(f'Document {document_id} already exists in context directory. Checking extraction quality...')
        context_payload = load_json(parsed_context_path)
        if _page_insights_need_reparse(context_payload):
            print('Existing context lacks strong page insights. Re-parsing with full Phase 1...')
        else:
            print('Reusing existing context...')
            if not context_payload.get("page_insights"):
                context_payload["page_insights"] = _heuristic_page_insights(context_payload.get("text_content", {}).get("markdown", ""))
                from src.utils.file_utils import save_json
                save_json(context_payload, parsed_context_path)
            return parsed_context_path
        if not context_payload.get("page_insights"):
            context_payload["page_insights"] = _heuristic_page_insights(context_payload.get("text_content", {}).get("markdown", ""))
            from src.utils.file_utils import save_json
            save_json(context_payload, parsed_context_path)

    print('[1/3] Parsing document with Hybrid VLM logic...')
    doc_parser = DocumentParser(input_path)
    parsed_content = doc_parser.parse_document(document_id)

    print('\n[2/3] Building context JSON...')
    builder = ContextWindowBuilder(document_id, Path(input_path).name, start_time=processing_start)
    
    context = builder.build_context(
        parsed_content=parsed_content, 
        tables_markdown=[], 
        images=parsed_content.images
    )
    
    output_path = builder.save_context(context)
    print(f'Context saved to: {output_path}')

    print(f"\n{'=' * 60}")
    print('SUMMARY')
    print(f"{'=' * 60}")
    print(f'Document ID:     {document_id}')
    print(f'Pages:           {parsed_content.page_count}')
    print(f'Processing Time: {time.time() - processing_start:.2f}s')
    print(f'Output:          {output_path}')
    print(f"\n{'=' * 60}\n")
    
    return output_path

def main():
    parser = argparse.ArgumentParser(description='Phase 1: Hybrid VLM Ingestion Pipeline')
    parser.add_argument('--input', required=True, help='Path to input PDF file')
    args = parser.parse_args()
    input_path = args.input
    if not os.path.exists(input_path):
        print(f'Error: Input file not found: {input_path}')
        return
    else:
        extract_file(input_path)

if __name__ == '__main__':
    main()
