import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from src.generator.slide_gen import output_name, run_pipeline as slide_gen
from src.multimodal.multimodal_processing import multimodal_processing
from src.preprocessor.preprocessing_context import effective_lecture_id, preprocess_context
from src.rag.context_bridge import save_context_from_kg
from src.rag.extract import configure, extract_one, forget
from src.utils.config import Config
from src.utils.content_cache import fresh
from src.utils.llm import end_llm_run, set_llm_phase, start_llm_run
from src.utils.pdf_utils import trim_pdf_to_pages

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)   # progress shows through a pipe
    sys.stderr.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s', datefmt='%H:%M:%S')


def gen_slide(document_path, lecture_title, speaker_information, institution='', root_heading=None, force=False):
    """PDF -> KG (built once, then reused) -> lecture (four agents) -> multimodal -> PPTX. Returns seconds taken.
    `force`: from scratch -- the KG and the page reading are redone and every LLM decision is asked again."""
    print('Start time:', time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()))
    start_time = time.time()
    document_id = os.path.splitext(os.path.basename(document_path))[0]
    if force:
        for path in forget(Path(document_path)):
            print(f'[force] removed {path}')
    fresh(force)
    run_output_id = effective_lecture_id(document_id, root_heading)
    run_id = start_llm_run(document_id=run_output_id, output_id=run_output_id)
    lecture_path = None
    status = 'completed'
    kg_seconds = 0.0
    try:
        print(f'[KG] {document_id}: building/reusing KG...')
        kb_dir = asyncio.run(extract_one(Path(document_path)))
        set_llm_phase('extract_file')
        context_path = save_context_from_kg(document_id, kb_dir, str(document_path))
        if not context_path:
            raise RuntimeError(f'The KG of {document_id} has no usable text: {kb_dir}')
        t1 = time.time()
        kg_seconds = t1 - start_time
        print(f'[timer] extract: {t1 - start_time:.2f}s')
        set_llm_phase('preprocess_context')
        (lecture_path, usable) = asyncio.run(preprocess_context(context_path, root_heading=root_heading))
        t2 = time.time()
        print(f'[timer] preprocess_context: {t2 - t1:.2f}s')
        if not usable:
            status = 'skipped'
            return
        set_llm_phase('multimodal')
        multimodal_processing(lecture_path)
        t3 = time.time()
        print(f'[timer] multimodal: {t3 - t2:.2f}s')
        set_llm_phase('slidegen')
        slide_gen(lecture_path, lecture_title, speaker_information, institution=institution)
        t4 = time.time()
        print(f'[timer] slidegen: {t4 - t3:.2f}s')
        print('End time:', time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()))
        return t4 - start_time
    except Exception:
        status = 'failed'
        raise
    finally:
        summary = end_llm_run(status=status, output_path=str(lecture_path) if lecture_path else None)
        if summary.get('run_id') == run_id:
            kg = [v for k, v in (summary.get('by_phase') or {}).items() if k.startswith('rag_')]
            print()
            print(f'[time] total {time.time() - start_time:.1f}s ({status})')
            print(f'[KG build] {kg_seconds:.1f}s, calls={sum(v["calls"] for v in kg)} '
                  f'tokens={sum(v["total_tokens"] for v in kg)}')
            print(f"[llm summary] calls={summary.get('total_calls', 0)} "
                  f"total_tokens={summary.get('total_tokens', 0)} "
                  f"avg_tokens_per_call={summary.get('avg_total_tokens_per_call', 0)} "
                  f"path={summary.get('summary_path', '')}")


def main():
    parser = argparse.ArgumentParser(description='Lecture slides (PPTX) from a PDF, grounded in its knowledge graph')
    parser.add_argument('--document_path', default=None, help='Input PDF (default: every PDF in data/raw/)')
    parser.add_argument('--pages', default=None, help='Trim the PDF to pages START-END (0-based, inclusive) first, '
                        'e.g. "23-189"; the trimmed PDF is its own document (own KG, lecture and deck)')
    parser.add_argument('--root_heading', default=None, help='Scope: one heading or several separated by `;` '
                        '(each is a chapter of the deck); omit for the whole document')
    parser.add_argument('--lecture_title', default=None, help='Title of the deck (default: from the scope)')
    parser.add_argument('--speaker_information', default=None, help='Speaker shown on the cover')
    parser.add_argument('--institution', default=None, help='Institution shown on the cover')
    parser.add_argument('--theme', default=None, help='THEME_LIBRARY theme of the deck (default: by document id)')
    parser.add_argument('--limit', type=int, default=None, help='Batch mode: process at most N PDFs of data/raw/')
    parser.add_argument('--force', action='store_true', help='Run from scratch: rebuild the KG (pages read again, '
                        'no LLM extraction cache) and ask every agent decision again')
    parser.add_argument('--skip-existing', action='store_true', help='Batch mode: skip PDFs that already have a deck')
    parser.add_argument('--ocr', default='cloud', choices=['auto', 'none', 'cloud'],
                        help="KG extract: how pages are read -- 'cloud' (default: the vision LLM, VLM_*), "
                             "'auto' (MinerU on a digital text layer, else the vision LLM), 'none' (MinerU)")
    parser.add_argument('--llm', default='cloud', choices=['cloud', 'local'],
                        help="KG extract: entity extraction on the cloud LLM (default) or a local Ollama server")
    args = parser.parse_args()
    if args.theme:
        os.environ['PPTX_THEME'] = args.theme
    configure(args.ocr)
    institution = args.institution or ''
    if args.document_path:
        document_path = args.document_path
        if args.pages:
            start_s, end_s = args.pages.split('-')
            trimmed_path = trim_pdf_to_pages(Path(document_path), int(start_s), int(end_s), Config.RAW_DIR / '_trimmed')
            print(f'[pages] Trimmed {document_path} pages {start_s}-{end_s} -> {trimmed_path}')
            document_path = str(trimmed_path)
        print(f'\nProcessing {document_path} \n')
        gen_slide(document_path, args.lecture_title, args.speaker_information, institution=institution,
                  root_heading=args.root_heading, force=args.force)
        return
    model_name = (Config.LLM_MODEL_NAME or 'unknown_model').replace('/', '_')
    pdfs = sorted(n for n in os.listdir(Config.RAW_DIR) if n.lower().endswith('.pdf'))[:args.limit]
    for count, document_name in enumerate(pdfs, 1):
        if args.skip_existing:
            lecture_id = effective_lecture_id(os.path.splitext(document_name)[0])
            pptx_path = Config.OUTPUT_DIR / output_name(lecture_id) / model_name / f'{output_name(lecture_id)}.pptx'
            if pptx_path.exists():
                print(f'\n[SKIP] {count}/{len(pdfs)}: {document_name} already has output at {pptx_path}')
                continue
        print(f'\nProcessing {count}/{len(pdfs)}: {document_name} \n')
        try:
            gen_slide(os.path.join(Config.RAW_DIR, document_name), args.lecture_title,
                      args.speaker_information, institution=institution, force=args.force)
        except Exception as e:
            print(f'[ERROR] Failed to process {document_name}: {e}')


if __name__ == '__main__':
    main()
