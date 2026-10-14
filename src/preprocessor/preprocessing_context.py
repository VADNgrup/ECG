import argparse
import asyncio
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from src.ingestion.table_extraction import extract_markdown_tables
from src.models.context import DocumentContext
from src.utils.config import config, Config
from src.utils.content_cache import LECTURE_CONTRACT_VERSION, PIPELINE_VERSION
from src.utils.file_utils import load_json, save_json
from src.utils.language_utils import detect_language_code
from src.workflow.graph import create_workflow

def effective_lecture_id(document_id: str, root_heading: str | None = None) -> str:
    """Id of a lecture and its deck: the document id, plus a slug of the scope when one is selected."""
    scoped_id = document_id
    if root_heading:
        slug = re.sub(r'[^a-z0-9]+', '-', root_heading.lower()).strip('-')[:56]
        scoped_id = f"{document_id}__{slug or 'scope'}"
    return scoped_id

def _outline_lines(scope_manifest=None) -> list:
    """One (title, level) pair per heading of the deck's scope, levels rebased so each chapter starts at 1."""
    return [(str(row.get('title') or ''), max(1, int(row.get('rebased_level') or 1)))
            for row in (scope_manifest or {}).get('heading_tree') or [] if str(row.get('title') or '').strip()]

def clean_repetition(text: str) -> str:
    """Remove pathological repeated tokens without destroying line structure."""
    if not text:
        return text
    cleaned_lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"\b(\w+)(?:\s+\1){3,}\b", r"\1", line, flags=re.IGNORECASE)
        cleaned_lines.append(cleaned)
    return "\n".join(cleaned_lines)

async def preprocess_context(context, output=None, root_heading=None):
    print(f"\n{'=' * 60}")
    print(f'Phase 2: Lecture Generation Workflow')
    print(f"{'=' * 60}\n")
    context_data = load_json(context)
    
    context_data['text_content']['markdown'] = clean_repetition(context_data['text_content']['markdown'])
    if not context_data.get('tables'):
        context_data['tables'] = [t.model_dump() for t in extract_markdown_tables(context_data['text_content']['markdown'])]
        if 'metadata' in context_data:
            context_data['metadata']['total_tables'] = len(context_data['tables'])
    
    # No compact-context step: the 100k upper bound existed only to protect the old
    # compact/summarize step from oversized input. Keep the lower floor (filters
    # empty/broken extraction). Very large documents may hit a real LLM context-window
    # error further downstream — that's an inherent cost of skipping compact context.
    if len(context_data['text_content']['markdown']) < 512:
        print(f"[SKIP] Lecture is too short ({len(context_data['text_content']['markdown'])} chars)")
        return (None, False)
    print(f"Processing {len(context_data['text_content']['markdown'])} raw chars — no upper limit, cost may be significant for large documents.")
    context = DocumentContext(**context_data)
    # document language: detect directly from the raw markdown (replaces the removed compact-context
    # step, whose only remaining consumer of language was this metadata field).
    document_language = detect_language_code(context.text_content.markdown[:4000])
    effective_id = effective_lecture_id(context.document_id, root_heading)
    lecture_path = Path(Config.LECTURES_DIR / f'{effective_id}' / f'{effective_id}.json')
    context_path = Path(Config.CONTEXT_DIR / f'{context.document_id}.json')
    outline_path = Path(Config.LECTURES_DIR / f'{effective_id}' / f'{effective_id}_outline.md')
    if Path(lecture_path).exists() and outline_path.exists():
        lecture_mtime = min(lecture_path.stat().st_mtime, outline_path.stat().st_mtime)
        context_mtime = context_path.stat().st_mtime if context_path.exists() else 0
        cached_lecture = load_json(lecture_path)
        meta = cached_lecture.get('metadata', {})
        cache_valid = (meta.get('pipeline_version') == PIPELINE_VERSION
                       and meta.get('lecture_contract_version') == LECTURE_CONTRACT_VERSION
                       and meta.get('root_heading') == root_heading)
        if lecture_mtime >= context_mtime and cache_valid:
            print(f'[SKIP] Lecture has id {effective_id} already exists in lecture directory')
            print(f"\n{'=' * 60}")
            print(f'End Phase 2: Generated Lecture')
            print(f"\n{'=' * 60}")
            return (lecture_path, True)
    if Path(lecture_path).exists():
        print(f'[REBUILD] Lecture exists but source context is newer. Rebuilding...')
    else:
        print(f'Source: {context.source_file}')
        print(f'Pages: {context.text_content.page_count}')
        print(f'Images: {context.metadata.total_images}')
        print(f'Tables: {context.metadata.total_tables}\n')
    config.validate()
    workflow = create_workflow()
    initial_state = {'document_context': context, 'lecture_title': '', 'slides': [], 'planned_specs': None,
                     'slide_specs': None, 'slide_packets': None, 'qa_report': None,
                     'root_heading': root_heading, 'scope_manifest': None, 'unit_plans': None, 'deck_plan': None}
    result = await workflow.ainvoke(initial_state, config={"recursion_limit": 32})
    final_slides = result['slides']
    qa_report = result.get('qa_report') or {'status': 'skipped', 'issues': []}
    warning_count = len(qa_report.get('dropped_slides') or []) + len(qa_report.get('dropped_bullets') or [])
    quality_score = 100.0 if final_slides else 0.0
    lecture_output = {'lecture_id': effective_id, 'metadata': {'source_document_id': context.document_id, 'source_file': context.source_file, 'generated_at': datetime.now().isoformat(), 'total_slides': len(final_slides), 'quality_score': quality_score, 'iterations': 1, 'qa_status': qa_report.get('status', 'unknown'), 'qa_warning_count': warning_count}, 'lecture_title': result['lecture_title'], 'slides': [asdict(s) for s in final_slides]}
    lecture_output['metadata']['document_language'] = document_language or 'en'
    lecture_output['metadata']['pipeline_version'] = PIPELINE_VERSION
    lecture_output['metadata']['lecture_contract_version'] = LECTURE_CONTRACT_VERSION
    lecture_output['metadata']['evidence_schema_version'] = 'chapters-v2'
    lecture_output['qa_report'] = qa_report
    lecture_output['slide_packets'] = result.get('slide_packets') or []
    lecture_output['metadata']['root_heading'] = root_heading
    output_save_path = Path(output) if output else config.LECTURES_DIR / f"{lecture_output['lecture_id']}"
    output_save_path.mkdir(parents=True, exist_ok=True)
    lecture_json_path = output_save_path / f"{lecture_output['lecture_id']}.json"
    save_json(lecture_output, lecture_json_path)
    final_specs  = result.get('slide_specs') or []
    _num_prefix  = re.compile(r'^\d+(?:\.\d+)*[.)]*\s*')
    _lines       = _outline_lines(result.get('scope_manifest'))
    outline_md   = '\n'.join(
        f"{'#' * lvl} {_num_prefix.sub('', title).strip()}"
        for title, lvl in _lines
    )
    outline_path = output_save_path / f"{lecture_output['lecture_id']}_outline.md"
    outline_path.write_text(outline_md, encoding='utf-8')
    final_packets = result.get('slide_packets')
    if final_packets:
        packets_path = output_save_path / f"{lecture_output['lecture_id']}_slide_packets.json"
        save_json(final_packets, packets_path)
    else:
        packets_path = None
    # for review: where every paragraph and KG node ended up (ledger), and the plan per chapter
    save_json(result.get('scope_manifest') or {}, output_save_path / f"{lecture_output['lecture_id']}_scope_manifest.json")
    save_json(result.get('unit_plans') or [], output_save_path / f"{lecture_output['lecture_id']}_unit_plans.json")
    qa_report_path = output_save_path / f"{lecture_output['lecture_id']}_qa_report.json"
    save_json(qa_report, qa_report_path)
    print(f"\n{'=' * 60}")
    print(f'Lecture Generated Successfully')
    print(f"{'=' * 60}\n")
    print(f'Output:                  {output_save_path}')
    print(f'Outline:                 {outline_path}')
    print(f'Slide Packets:           {packets_path}')
    print(f'QA Report:               {qa_report_path}')
    print(f"Slides:                  {lecture_output['metadata']['total_slides']}")
    print(f"QA:                      {qa_report.get('status')} ({warning_count} slide(s)/bullet(s) dropped)")
    print(f"\n{'=' * 60}")
    print(f'End Phase 2: Generated Lecture')
    print(f"\n{'=' * 60}")
    return (lecture_json_path, True)

async def main():
    parser = argparse.ArgumentParser(description='Phase 2: LangGraph Workflow')
    parser.add_argument('--context', required=True, help='Path to Phase 1 context JSON')
    parser.add_argument('--output', default=None, help='Output lecture JSON path')
    args = parser.parse_args()
    (context, output) = (args.context, args.output)
    await preprocess_context(context, output)
if __name__ == '__main__':
    asyncio.run(main())
