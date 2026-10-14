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
from src.rag.extract import configure, extract_one, forget, headings_from, ocr_from_env, reading_facts, will_parse
from src.utils import collection, deck_report
from src.utils.config import Config
from src.utils.content_cache import end_run, fresh, run_decisions
from src.utils.llm import PAGE_READING_PHASE, end_llm_run, set_llm_phase, start_llm_run
from src.utils.pdf_utils import trim_pdf_to_pages
from src.workflow import reading_menu, scope_menu

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)   # progress shows through a pipe
    sys.stderr.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s', datefmt='%H:%M:%S')


def choose_scopes(document_path, force, collection):
    """Build or reuse the KG, then let the user pick what the decks cover: one `root_heading` per deck."""
    if force:
        for path in forget(Path(document_path)):
            print(f'[force] removed {path}')
    return scope_menu.ask(asyncio.run(extract_one(Path(document_path))), collection=collection)


def gen_slide(document_path, lecture_title, speaker_information, institution='', root_heading=None, force=False,
              domain=None, kept_kb=False):
    """PDF -> KG (built once, then reused) -> lecture (four agents) -> multimodal -> PPTX. Returns seconds taken.
    `force`: from scratch -- the KG and the page reading are redone and every LLM decision is asked again.
    `domain`: the field of study whose prompts and layouts the agents use; None: detected from the document.
    `kept_kb`: `force` asks the decisions again but keeps the KG, just rebuilt for this run."""
    print('Start time:', time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()))
    start_time = time.time()
    document_id = os.path.splitext(os.path.basename(document_path))[0]
    if force and not kept_kb:
        for path in forget(Path(document_path)):
            print(f'[force] removed {path}')
    fresh(force)
    run_output_id = effective_lecture_id(document_id, root_heading, domain)
    run_id = start_llm_run(document_id=run_output_id, output_id=run_output_id)
    lecture_path = None
    status = 'completed'
    kg_seconds = 0.0
    try:
        print(f'[KG] {document_id}: building/reusing KG...')
        kb_dir = asyncio.run(extract_one(Path(document_path)))
        set_llm_phase('extract_file')
        run_decisions(True)                                   # the KG step's decisions stay; the agents' are this run's
        context_path = save_context_from_kg(document_id, kb_dir, str(document_path))
        if not context_path:
            raise RuntimeError(f'The KG of {document_id} has no usable text: {kb_dir}')
        t1 = time.time()
        kg_seconds = t1 - start_time
        print(f'[timer] extract: {t1 - start_time:.2f}s')
        set_llm_phase('preprocess_context')
        (lecture_path, usable) = asyncio.run(preprocess_context(context_path, root_heading=root_heading, domain=domain))
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
    except (KeyboardInterrupt, SystemExit):
        status = 'interrupted'
        raise
    except Exception:
        status = 'failed'
        raise
    finally:
        run_decisions(False)
        summary = end_llm_run(status=status, output_path=str(lecture_path) if lecture_path else None)
        if summary.get('run_id') == run_id:
            kg = [v for k, v in (summary.get('by_phase') or {}).items()
                  if k.startswith('rag_') and k != PAGE_READING_PHASE]
            print()
            print(f'[time] total {time.time() - start_time:.1f}s ({status})')
            print(f'[KG build] {kg_seconds:.1f}s, calls={sum(v["calls"] for v in kg)} '
                  f'tokens={sum(v["total_tokens"] for v in kg)}')
            print(f"[llm summary] calls={summary.get('total_calls', 0)} "
                  f"total_tokens={summary.get('total_tokens', 0)} "
                  f"avg_tokens_per_call={summary.get('avg_total_tokens_per_call', 0)} "
                  f"path={summary.get('summary_path', '')}")


def run_collection(document_path, manifest, args, institution):
    """A collection PDF: one KG for all of it, then one deck per document (its own scope and field of study);
    `<stem>_report.md` beside the PDF lists each document's field, slides and deck. Without `--force`, a document
    whose deck is newer than the KG keeps it, so a stopped run continues where it stopped."""
    rows = []
    if args.force:
        for path in forget(Path(document_path)):
            print(f'[force] removed {path}')
    fresh(args.force)                                         # --force: the KG step's decisions are asked again too
    kb_dir = asyncio.run(extract_one(Path(document_path)))   # the KG first: it renames documents named after their file
    graph = Path(kb_dir) / 'graph_chunk_entity_relation.graphml'
    documents = collection.manifest_of(document_path)['documents']
    stem = os.path.splitext(os.path.basename(document_path))[0]
    model_name = (Config.LLM_MODEL_NAME or 'unknown_model').replace('/', '_')
    for count, doc in enumerate(documents, 1):
        lecture_id = effective_lecture_id(stem, doc['title'], args.domain)
        deck = Config.OUTPUT_DIR / output_name(lecture_id) / model_name / f'{output_name(lecture_id)}.pptx'
        if not args.force and deck.exists() and deck.stat().st_mtime > graph.stat().st_mtime:
            print(f"[keep] {count}/{len(documents)}: {doc['title']} -- its deck is newer than the KG")
            status = 'ok'
        else:
            print(f"\nProcessing {count}/{len(documents)}: {doc['title']} ({doc['source']})\n")
            try:
                gen_slide(document_path, args.lecture_title, args.speaker_information, institution=institution,
                          root_heading=doc['title'], force=args.force, domain=args.domain, kept_kb=True)
                status = 'ok'
            except Exception as e:
                print(f"[ERROR] {doc['title']}: {e}")
                status = f'failed: {str(e)[:120]}'
        rows.append(collection.report_row(doc, deck_report.measure(Config.LECTURES_DIR / lecture_id, deck), deck, status))
    report = collection.write_report(document_path, rows, deck_report.COLUMNS, deck_report.cells)
    end_run()                                                 # every deck made: the next run asks its decisions again
    print(f'[collection] {sum(r["status"] == "ok" for r in rows)}/{len(rows)} decks -> {report}')


def main():
    parser = argparse.ArgumentParser(description='Lecture slides (PPTX) from a PDF, grounded in its knowledge graph')
    parser.add_argument('--document_path', default=None, help='Input PDF (default: every PDF in data/raw/)')
    parser.add_argument('--pages', default=None, help='Trim the PDF to pages START-END (0-based, inclusive) first, '
                        'e.g. "23-189"; the trimmed PDF is its own document (own KG, lecture and deck)')
    parser.add_argument('--root_heading', default=None, help='Scope: one heading or several separated by `;` '
                        '(each is a chapter of the deck), `!heading` leaves one out; omit for the whole document')
    parser.add_argument('--lecture_title', default=None, help='Title of the deck (default: from the scope)')
    parser.add_argument('--speaker_information', default=None, help='Speaker shown on the cover')
    parser.add_argument('--institution', default=None, help='Institution shown on the cover')
    parser.add_argument('--theme', default=None, help='THEME_LIBRARY theme of the deck (default: by document id)')
    parser.add_argument('--limit', type=int, default=None, help='Batch mode: process at most N PDFs of data/raw/')
    parser.add_argument('--force', action='store_true', help='Run from scratch: rebuild the KG (pages read again, '
                        'no LLM extraction cache) and ask every agent decision again')
    parser.add_argument('--skip-existing', action='store_true', help='Batch mode: skip PDFs that already have a deck')
    parser.add_argument('--ocr', default=None, choices=['auto', 'none', 'cloud'],
                        help="KG extract: how pages are read -- 'cloud' (the vision LLM, VLM_*), 'none' (MinerU on the "
                             "PDF's outline and text layer), 'auto' (none when the PDF has both, else cloud); without "
                             "it .env OCR_MODE decides: cloud (the default) or mineru")
    parser.add_argument('--headings', default=None, choices=['toc', 'ocr'],
                        help="KG extract: where the headings come from -- 'toc' (the PDF's embedded outline) or 'ocr' "
                             "(the page reading: its printed contents page and heading marks); without it a terminal "
                             "asks when the PDF has an outline, anything else takes toc")
    parser.add_argument('--domain', default=None, choices=list(Config.DOMAINS),
                        help='Field of study whose prompts and layouts the agents use (default: detected from the '
                             'document; a low-confidence guess takes general)')
    parser.add_argument('--llm', default='cloud', choices=['cloud', 'local'],
                        help="KG extract: entity extraction on the cloud LLM (default) or a local Ollama server")
    args = parser.parse_args()
    if args.document_path is not None and not args.document_path.strip():
        parser.error('--document_path is empty')             # never the batch over every PDF of data/raw/
    if args.theme:
        os.environ['PPTX_THEME'] = args.theme
    configure(args.ocr or ocr_from_env())
    institution = args.institution or ''
    if args.document_path:
        document_path = args.document_path
        if args.pages:
            start_s, end_s = args.pages.split('-')
            trimmed_path = trim_pdf_to_pages(Path(document_path), int(start_s), int(end_s), Config.RAW_DIR / '_trimmed')
            print(f'[pages] Trimmed {document_path} pages {start_s}-{end_s} -> {trimmed_path}')
            document_path = str(trimmed_path)
        terminal = sys.stdin.isatty() and sys.stdout.isatty()
        headings = args.headings
        if headings is None and terminal and will_parse(Path(document_path), args.force):
            facts = reading_facts(Path(document_path))
            if facts["entries"]:
                headings = reading_menu.ask(Path(document_path).name, facts)
        headings_from(headings or reading_menu.TOC)
        # at a terminal, the user picks the decks' sections once the KG is built; otherwise the whole document
        # (a collection: one deck per document)
        choose = terminal and not args.root_heading
        manifest = collection.manifest_of(document_path)
        if manifest and not args.root_heading and not choose:
            run_collection(document_path, manifest, args, institution)
            return
        print(f'\nProcessing {document_path} \n')
        scopes = choose_scopes(document_path, args.force, bool(manifest)) if choose else [args.root_heading]
        for scope in scopes:
            try:
                gen_slide(document_path, args.lecture_title, args.speaker_information, institution=institution,
                          root_heading=scope, force=args.force, domain=args.domain, kept_kb=choose)
            except Exception as e:
                if len(scopes) == 1:                  # one deck among several failing leaves the others to run
                    raise
                print(f'[ERROR] {scope}: {e}')
        end_run()
        return
    model_name = (Config.LLM_MODEL_NAME or 'unknown_model').replace('/', '_')
    pdfs = sorted(n for n in os.listdir(Config.RAW_DIR) if n.lower().endswith('.pdf'))[:args.limit]
    for count, document_name in enumerate(pdfs, 1):
        if args.skip_existing:
            lecture_id = effective_lecture_id(os.path.splitext(document_name)[0], domain=args.domain)
            pptx_path = Config.OUTPUT_DIR / output_name(lecture_id) / model_name / f'{output_name(lecture_id)}.pptx'
            if pptx_path.exists():
                print(f'\n[SKIP] {count}/{len(pdfs)}: {document_name} already has output at {pptx_path}')
                continue
        print(f'\nProcessing {count}/{len(pdfs)}: {document_name} \n')
        try:
            gen_slide(os.path.join(Config.RAW_DIR, document_name), args.lecture_title,
                      args.speaker_information, institution=institution, force=args.force, domain=args.domain)
        except Exception as e:
            print(f'[ERROR] Failed to process {document_name}: {e}')
    end_run()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        # worker threads may be waiting out a retry; leave now instead of joining them (caches are already on disk)
        print('\n[stopped] Ctrl+C', flush=True)
        os._exit(130)
