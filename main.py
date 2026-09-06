import time
import logging
from src.extractor.extract_file import extract_file
from src.preprocessor.preprocessing_context import preprocess_context, effective_lecture_id
from src.multimodal.multimodal_processing import multimodal_processing
from src.generator.slide_gen import run_pipeline as slide_gen
from src.utils.config import Config
from src.utils.llm import end_llm_run, set_llm_phase, start_llm_run
import os
import argparse
import asyncio
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s', datefmt='%H:%M:%S')

def gen_slide(document_path, lecture_title, speaker_information, institution=''):
    print('Start time:', time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()))
    start_time = time.time()
    document_id = os.path.splitext(os.path.basename(document_path))[0]
    # Use the ablation-suffixed id (matches the actual output/lecture directory name) so
    # logs/llm_run_<id>_*.json can be found by lecture_id later (summarize_performance.py).
    run_output_id = effective_lecture_id(document_id)
    run_id = start_llm_run(document_id=run_output_id, output_id=run_output_id)
    lecture_path = None
    status = 'completed'
    try:
        set_llm_phase('extract_file')
        parsed_context_path = extract_file(document_path)
        t1 = time.time()
        print(f'[timer] extract_file: {t1 - start_time:.2f}s')
        set_llm_phase('preprocess_context')
        (lecture_path, len_condition) = asyncio.run(preprocess_context(parsed_context_path))
        t2 = time.time()
        print(f'[timer] preprocess_context: {t2 - t1:.2f}s')
        if len_condition == False:
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
        end_time = t4
        print('End time:', time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()))
        return end_time - start_time
    except Exception:
        status = 'failed'
        raise
    finally:
        summary = end_llm_run(status=status, output_path=str(lecture_path) if lecture_path else None)
        if summary.get('run_id') == run_id:
            print(
                '[llm summary] '
                f"calls={summary.get('total_calls', 0)} "
                f"total_tokens={summary.get('total_tokens', 0)} "
                f"avg_tokens_per_call={summary.get('avg_total_tokens_per_call', 0)} "
                f"path={summary.get('summary_path', '')}"
            )

def main():
    parser = argparse.ArgumentParser(description='End-to-End Lecture Slide Generation Slide from a document/old lecture slides')
    parser.add_argument('--document_path', default=None, help='Path to input PDF/Docx file')
    parser.add_argument('--lecture_title', default=None, help='Customized title of the lecture')
    parser.add_argument('--speaker_information', default=None, help='Speaker information')
    parser.add_argument('--institution', default=None, help='Institution/organization of the speaker')
    parser.add_argument('--limit', help='Limit the number of documents to process')
    parser.add_argument('--skip-existing', action='store_true', help='Skip documents whose final PPTX output already exists (resume mode).')
    args = parser.parse_args()
    default_document_folder = Config.RAW_DIR
    document_path = args.document_path
    lecture_title = args.lecture_title
    speaker_information = args.speaker_information
    institution = args.institution or ''
    pdf_files = [f for f in os.listdir(default_document_folder) if f.endswith('.pdf')]
    limit = int(args.limit) if args.limit is not None else len(pdf_files)
    model_name = (Config.LLM_MODEL_NAME or 'unknown_model').replace('/', '_')
    if document_path is None and lecture_title is None:
        document_list = pdf_files
        count = 1
        for document_name in document_list:
            if document_name.endswith('.pdf') and count <= limit:
                document_id = os.path.splitext(document_name)[0]
                if args.skip_existing:
                    # Ablation modes write output under an ablation-suffixed id (see
                    # preprocessing_context.effective_lecture_id) — check that path, not the raw document_id.
                    lecture_id = effective_lecture_id(document_id)
                    pptx_path = Config.OUTPUT_DIR / lecture_id / model_name / f'{lecture_id}.pptx'
                    if pptx_path.exists():
                        print(f'\n[SKIP] {count}/{limit}: {document_name} already has output at {pptx_path}')
                        count += 1
                        continue
                print(f'\nProcessing {count}/{limit}: {document_name} \n')
                a_document_path = os.path.join(default_document_folder, document_name)
                try:
                    time_taken = gen_slide(a_document_path, lecture_title, speaker_information, institution=institution)
                    print(f'[full time taken] {time_taken}s')
                except Exception as e:
                    print(f'[ERROR] Failed to process {document_name}: {e}')
                    time_taken = None
                count += 1
                if not time_taken:
                    continue
    elif document_path:
        print(f'\nProcessing {document_path} \n')
        time_taken = gen_slide(document_path, lecture_title, speaker_information, institution=institution)
        print(f'[full time taken] {time_taken}s')
    else:
        print("Your arguments is FAILED, let's read my README.md")
if __name__ == '__main__':
    main()
