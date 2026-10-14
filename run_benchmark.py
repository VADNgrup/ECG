import argparse
import shutil
import subprocess

from src.utils.config import Config

def _clear_path(path):
    if path.exists():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    path.mkdir(parents=True, exist_ok=True)

def clear_benchmark_state():
    paths = [
        Config.CONTEXT_DIR,
        Config.LECTURES_DIR,
        Config.ASSETS_DIR,
        Config.OUTPUT_DIR,
        Config.BASE_DIR / 'logs',
        Config.DATA_DIR / 'media',
    ]
    print('\n[CLEAN] Removing previous benchmark artifacts...')
    for path in paths:
        _clear_path(path)
        print(f'[CLEAN] reset {path}')

def run_benchmark(mode='clean', limit=None, speaker=None, title=None, institution=None):
    print('=' * 60)
    print('STARTING BENCHMARK PIPELINE')
    print(f'MODE: {mode}')
    print('=' * 60)
    if mode == 'clean':
        clear_benchmark_state()
    else:
        print('\n[RESUME] Keeping existing artifacts, will skip documents already generated.')
    print("\n[1/2] GENERATING SLIDES FOR ALL DOCUMENTS IN 'data/raw/'...")
    gen_cmd = ['python', '-m', 'main']
    if limit is not None:
        gen_cmd.extend(['--limit', str(limit)])
    if speaker is not None:
        gen_cmd.extend(['--speaker_information', speaker])
    if title is not None:
        gen_cmd.extend(['--lecture_title', title])
    if institution is not None:
        gen_cmd.extend(['--institution', institution])
    if mode == 'resume':
        gen_cmd.append('--skip-existing')

    ret_gen = subprocess.run(gen_cmd).returncode
    if ret_gen != 0:
        print('\nWarning: Some slides failed to generate. Proceeding to evaluation for completed tasks.')
    model_name = (Config.LLM_MODEL_NAME or 'unknown_model').replace('/', '_')
    print('\n[2/2] EVALUATING WITH LLM-AS-A-JUDGE (PPTEVAL)...')
    print(f'Target Directory Model: {model_name}\n')
    ret_eval = subprocess.run(['python', '-m', 'src.evaluation.eval', '--model', model_name]).returncode
    print('\n' + '=' * 60)
    if ret_eval == 0:
        print('BENCHMARK COMPLETED SUCCESSFULLY')
    else:
        print('BENCHMARK FINISHED WITH ERRORS (EVALUATION FAILED)')
    print('=' * 60)

def _build_parser():
    parser = argparse.ArgumentParser(description='Run a benchmark from raw PDFs to evaluation metrics.')
    parser.add_argument('--mode', choices=['clean', 'resume'], default='clean',
                         help="'clean' (default): wipe all previous artifacts and start over. "
                              "'resume': keep existing artifacts and only process documents that haven't finished yet.")
    parser.add_argument('--no-clean', action='store_true', help='Deprecated alias for --mode resume.')
    parser.add_argument('--limit', type=int, default=None, help='Limit the number of PDFs processed from data/raw.')
    parser.add_argument('--speaker', type=str, default=None, help='Speaker information to pass to the slides.')
    parser.add_argument('--title', type=str, default=None, help='Override lecture title for the slides.')
    parser.add_argument('--institution', type=str, default=None, help='Institution/organization of the speaker.')
    return parser

if __name__ == '__main__':
    args = _build_parser().parse_args()
    mode = 'resume' if args.no_clean else args.mode
    run_benchmark(mode=mode, limit=args.limit, speaker=args.speaker, title=args.title, institution=args.institution)
