import argparse
import asyncio
import hashlib
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
from src.workflow.scope import EXCLUDE_MARK, TITLE_TEXT

_SLUG_CHARS = 56


def effective_lecture_id(document_id: str, root_heading: str | None = None, domain: str | None = None) -> str:
    """Id of a lecture and its deck: the document id, plus a slug of the scope when one is selected and of the field of
    study when one other than `general` is chosen."""
    scoped_id = document_id
    if root_heading:
        slug = re.sub(r'[^a-z0-9]+', '-', root_heading.lower()).strip('-')
        # two long scopes may share an opening, and two scopes may differ only by a mark: the whole name keeps them apart
        if len(slug) > _SLUG_CHARS or EXCLUDE_MARK in root_heading or TITLE_TEXT in root_heading:
            slug = f"{slug[:_SLUG_CHARS - 9].strip('-')}-{hashlib.sha1(root_heading.encode('utf-8')).hexdigest()[:8]}"
        scoped_id = f"{document_id}__{slug or 'scope'}"
    if domain and domain != 'general':
        scoped_id = f"{scoped_id}__{domain}"
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

async def preprocess_context(context, output=None, root_heading=None, domain=None):
    print(f"\n{'=' * 60}")
    print(f'Phase 2: Lecture Generation Workflow')
    print(f"{'=' * 60}\n")
    context_data = load_json(context)
    
    context_data['text_content']['markdown'] = clean_repetition(context_data['text_content']['markdown'])
    if not context_data.get('tables'):
        context_data['tables'] = [t.model_dump() for t in extract_markdown_tables(context_data['text_content']['markdown'])]
        if 'metadata' in context_data:
            context_data['metadata']['total_tables'] = len(context_data['tables'])
    
    if len(context_data['text_content']['markdown']) < Config.MIN_SOURCE_CHARS:
        print(f"[SKIP] Lecture is too short ({len(context_data['text_content']['markdown'])} chars)")
        return (None, False)
    context = DocumentContext(**context_data)
    document_language = detect_language_code(context.text_content.markdown[:Config.LANGUAGE_SAMPLE_CHARS])
    effective_id = effective_lecture_id(context.document_id, root_heading, domain)
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
                       and meta.get('root_heading') == root_heading
                       and meta.get('domain_requested') == domain)
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
    workflow = create_workflow(domain)
    initial_state = {'document_context': context, 'deck': [], 'review': None, 'lecture_title': '', 'slides': [], 'planned_specs': None,
                     'slide_specs': None, 'slide_packets': None, 'qa_report': None,
                     'root_heading': root_heading, 'domain': domain, 'scope_manifest': None, 'unit_plans': None,
                     'labels': None, 'numbered': None}
    # each Writer repair the QA Router asks for is two more steps (slide_writer, content_qa)
    result = await workflow.ainvoke(initial_state, config={"recursion_limit": Config.GRAPH_RECURSION_LIMIT})
    final_slides = result['slides']
    qa_report = result.get('qa_report') or {'status': 'skipped', 'issues': []}
    warning_count = len(qa_report.get('dropped_slides') or []) + len(qa_report.get('dropped_bullets') or [])
    quality_score = 100.0 if final_slides else 0.0
    lecture_output = {'lecture_id': effective_id, 'metadata': {'source_document_id': context.document_id, 'source_file': context.source_file, 'generated_at': datetime.now().isoformat(), 'total_slides': len(final_slides), 'quality_score': quality_score, 'iterations': 1, 'qa_status': qa_report.get('status', 'unknown'), 'qa_warning_count': warning_count}, 'lecture_title': result['lecture_title'], 'slides': [asdict(s) for s in final_slides]}
    lecture_output['metadata']['document_language'] = document_language or 'en'
    lecture_output['metadata']['labels'] = result.get('labels') or {}
    lecture_output['metadata']['numbered'] = result.get('numbered') or {}
    lecture_output['metadata']['pipeline_version'] = PIPELINE_VERSION
    lecture_output['metadata']['lecture_contract_version'] = LECTURE_CONTRACT_VERSION
    lecture_output['qa_report'] = qa_report
    lecture_output['slide_packets'] = result.get('slide_packets') or []
    lecture_output['metadata']['root_heading'] = root_heading
    lecture_output['metadata']['domain'] = result.get('domain') or 'general'
    lecture_output['metadata']['domain_requested'] = domain
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
