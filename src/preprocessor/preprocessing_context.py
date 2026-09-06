import asyncio
import argparse
import re
from pathlib import Path
from datetime import datetime
from src.workflow.graph import create_workflow
from src.utils.file_utils import load_json, save_json
from src.models.context import DocumentContext
from src.utils.config import config, Config
from dataclasses import asdict
from src.ingestion.table_extraction import extract_markdown_tables

def effective_lecture_id(document_id: str) -> str:
    """Ablation-mode-suffixed id used for Phase-2+ output/cache paths, so different ablation
    modes never collide/overwrite each other's generated lecture. Phase 1 (VLM extraction) and
    the raw source context file stay keyed by the unsuffixed document_id (shared across modes)."""
    if Config.ABLATION_MODE == 0:
        return document_id
    return f"{document_id}__abl{Config.ABLATION_MODE}"

def clean_repetition(text: str) -> str:
    """Remove pathological repeated tokens without destroying line structure."""
    if not text:
        return text
    cleaned_lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"\b(\w+)(?:\s+\1){3,}\b", r"\1", line, flags=re.IGNORECASE)
        cleaned_lines.append(cleaned)
    return "\n".join(cleaned_lines)

async def preprocess_context(context, output=None):
    print(f"\n{'=' * 60}")
    print(f'Phase 2: Lecture Generation Workflow')
    print(f"{'=' * 60}\n")
    context_data = load_json(context)
    
    # Clean repetition loops before checking length
    if Config.ABLATION_MODE != 4:
        context_data['text_content']['markdown'] = clean_repetition(context_data['text_content']['markdown'])
    
    if not context_data.get('tables'):
        context_data['tables'] = [t.model_dump() for t in extract_markdown_tables(context_data['text_content']['markdown'])]
        if 'metadata' in context_data:
            context_data['metadata']['total_tables'] = len(context_data['tables'])
    
    if len(context_data['text_content']['markdown']) < 512 or len(context_data['text_content']['markdown']) > 100000:
        print(f"[SKIP] Lecture is too short or too long ({len(context_data['text_content']['markdown'])} chars)")
        return (None, False)
    
    context = DocumentContext(**context_data)
    effective_id = effective_lecture_id(context.document_id)
    lecture_path = Path(Config.LECTURES_DIR / f'{effective_id}' / f'{effective_id}.json')
    context_path = Path(Config.CONTEXT_DIR / f'{context.document_id}.json')
    outline_path = Path(Config.LECTURES_DIR / f'{effective_id}' / f'{effective_id}_outline.md')
    if Path(lecture_path).exists() and outline_path.exists():
        lecture_mtime = min(lecture_path.stat().st_mtime, outline_path.stat().st_mtime)
        context_mtime = context_path.stat().st_mtime if context_path.exists() else 0
        if lecture_mtime >= context_mtime:
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
    initial_state = {'document_context': context, 'lecture_title': '', 'slides': [], 'slide_specs': None, 'slide_packets': None, 'qa_report': None}
    result = await workflow.ainvoke(initial_state)
    final_slides = result['slides']
    qa_report = result.get('qa_report') or {}
    warning_count = len(qa_report.get('advisory_issues', {})) + len(qa_report.get('soft_issues', {}))
    quality_score = 100.0 if final_slides else 0.0
    lecture_output = {'lecture_id': effective_id, 'metadata': {'source_document_id': context.document_id, 'source_file': context.source_file, 'generated_at': datetime.now().isoformat(), 'total_slides': len(final_slides), 'quality_score': quality_score, 'iterations': 1, 'qa_status': qa_report.get('status', 'unknown'), 'qa_warning_count': warning_count}, 'lecture_title': result['lecture_title'], 'slides': [asdict(s) for s in final_slides]}
    lecture_output['metadata']['document_language'] = 'en'
    output_save_path = Path(output) if output else config.LECTURES_DIR / f"{lecture_output['lecture_id']}"
    output_save_path.mkdir(parents=True, exist_ok=True)
    lecture_json_path = output_save_path / f"{lecture_output['lecture_id']}.json"
    save_json(lecture_output, lecture_json_path)
    final_specs  = result.get('slide_specs') or []
    _num_prefix  = re.compile(r'^\d+(?:\.\d+)*[.)]*\s*')
    outline_md   = '\n'.join(
        f"# {_num_prefix.sub('', s.slide_title).strip()}"
        for s in final_specs
    )
    outline_path = output_save_path / f"{lecture_output['lecture_id']}_outline.md"
    outline_path.write_text(outline_md, encoding='utf-8')
    if final_specs:

        def _serialize_spec(s):
            d = asdict(s)
            if hasattr(d.get('slide_type'), 'value'):
                d['slide_type'] = d['slide_type'].value
            return d
        specs_data = [_serialize_spec(s) for s in final_specs]
        specs_path = output_save_path / f"{lecture_output['lecture_id']}_plan_spec.json"
        save_json(specs_data, specs_path)
    else:
        specs_path = None
    final_packets = result.get('slide_packets')
    if final_packets:
        packets_path = output_save_path / f"{lecture_output['lecture_id']}_slide_packets.json"
        save_json(final_packets, packets_path)
    else:
        packets_path = None
    qa_report_path = output_save_path / f"{lecture_output['lecture_id']}_qa_report.json"
    save_json(qa_report, qa_report_path)
    print(f"\n{'=' * 60}")
    print(f'Lecture Generated Successfully')
    print(f"{'=' * 60}\n")
    print(f'Output:                  {output_save_path}')
    print(f'Outline:                 {outline_path}')
    print(f'Plan Spec:               {specs_path}')
    print(f'Slide Packets:           {packets_path}')
    print(f'QA Report:               {qa_report_path}')
    print(f"Slides:                  {lecture_output['metadata']['total_slides']}")
    print(f'Quality Score:           {quality_score:.1f}% slides passed')
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
