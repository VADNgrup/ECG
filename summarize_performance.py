import json
import os
import argparse
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from src.utils.config import Config

def summarize():
    parser = argparse.ArgumentParser()
    parser.add_argument('--log', type=str, default=None)
    args = parser.parse_args()

    log_path = Path(args.log) if args.log else Config.get_log_path()
    output_dir = Config.OUTPUT_DIR

    if not log_path.exists():
        print(f'Không tìm thấy file log: {log_path}')
        return

    model_filter = '*'
    if args.log:
        log_name = Path(args.log).stem
        if 'llm_calls_' in log_name:
            model_filter = log_name.replace('llm_calls_', '')

    parsed_logs = []
    with open(log_path, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                data = json.loads(line)
                ts = datetime.fromisoformat(data.get('time')).timestamp()
                parsed_logs.append((ts, data))
            except:
                continue

    if not parsed_logs:
        print('File log trống.')
        return

    log_min_ts = parsed_logs[0][0]
    log_max_ts = parsed_logs[-1][0]

    eval_files_list = list(output_dir.glob(f'**/{model_filter}/eval_*.json'))
    if not eval_files_list:
        eval_files_list = list(output_dir.glob('**/eval_*.json'))

    valid_eval_mtimes = [
        os.path.getmtime(f) for f in eval_files_list
        if log_min_ts <= os.path.getmtime(f) <= log_max_ts + 7200
    ]
    eval_start_ts = min(valid_eval_mtimes) if valid_eval_mtimes else float('inf')

    total_calls_gen = 0
    total_tokens_gen = 0
    total_time_gen = 0
    for ts, d in parsed_logs:
        if ts < eval_start_ts:
            total_calls_gen += 1
            total_tokens_gen += d.get('token_usage', {}).get('total_tokens', 0)
            total_time_gen += d.get('elapsed_s', 0)

    # A lecture is "successful" when its slide_images folder exists with at least one image.
    # (run_benchmark produces HTML/JSON/screenshots, not PDF exports)
    slide_image_dirs = [
        d for d in output_dir.glob(f'**/{model_filter}/slide_images')
        if any(d.iterdir())
    ]
    success_count = len(slide_image_dirs)

    all_lectures = [d for d in output_dir.iterdir() if d.is_dir() and d.name != '.gitkeep']
    total_attempted = len(all_lectures)
    failed_count = total_attempted - success_count

    avg_metrics = defaultdict(float)
    count_metrics = 0
    for ef in eval_files_list:
        try:
            with open(ef, 'r', encoding='utf-8') as f:
                e = json.load(f)
                avg_metrics['rouge'] += e.get('rouge_score', 0)
                avg_metrics['content'] += e.get('content_score', 0)
                avg_metrics['design'] += e.get('design_score', 0)
                c = e.get('coherence_score', {})
                avg_metrics['coherence'] += c if isinstance(c, (int, float)) else c.get('score', 0)
                count_metrics += 1
        except:
            continue

    if count_metrics > 0:
        for k in avg_metrics:
            avg_metrics[k] /= count_metrics

    total_real_duration = 0.0
    total_calls_from_runs = 0
    total_tokens_from_runs = 0
    real_duration_count = 0
    logs_dir = log_path.parent
    for img_dir in slide_image_dirs:
        lecture_id = img_dir.parent.parent.name
        run_files = sorted(logs_dir.glob(f'llm_run_{lecture_id}_*.json'), reverse=True)
        for rf_path in run_files:
            try:
                with open(rf_path, 'r', encoding='utf-8') as rf:
                    run_data = json.load(rf)
                if model_filter != '*':
                    by_model = run_data.get('by_model', {})
                    run_models = [m.replace('/', '_') for m in by_model.keys()]
                    if model_filter not in run_models:
                        continue
                        
                start_str = run_data.get('started_at')
                end_str = run_data.get('ended_at')
                if start_str and end_str:
                    start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                    end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                    total_real_duration += (end_dt - start_dt).total_seconds()
                total_calls_from_runs += run_data.get('total_calls', 0)
                total_tokens_from_runs += run_data.get('total_tokens', 0)
                real_duration_count += 1
                break 
            except:
                pass
                
    avg_call = total_calls_from_runs / real_duration_count if real_duration_count > 0 else 0
    avg_token_k = total_tokens_from_runs / real_duration_count / 1000 if real_duration_count > 0 else 0
    avg_done_time_m = total_real_duration / real_duration_count / 60 if real_duration_count > 0 else 0

    print('\n' + '=' * 40)
    print('   GENERATION PERFORMANCE')
    print('=' * 40)
    print(f'Log source: {log_path.name}')
    print(f'Eval source folder: {model_filter}')
    print(f"{'Metric':<25} | {'Value':<10}")
    print('-' * 40)
    print(f"{'Call (Avg)':<25} | {avg_call:.1f}")
    print(f"{'Total Token per output(k)':<25} | {avg_token_k:.1f}")
    print(f"{'Failed':<25} | {failed_count}")
    print(f"{'Done time (Real, m)':<25} | {avg_done_time_m:.2f}")
    print(f"{'Success Rate':<25} | {(success_count / total_attempted * 100 if total_attempted > 0 else 0):.2f}%")
    print('-' * 40)
    print(f"{'ROUGE-L':<25} | {avg_metrics['rouge'] * 100:.2f}")
    print(f"{'Content':<25} | {avg_metrics['content']:.2f}")
    print(f"{'Design':<25} | {avg_metrics['design']:.2f}")
    print(f"{'Coherence':<25} | {avg_metrics['coherence']:.2f}")
    print('=' * 40)

if __name__ == '__main__':
    summarize()