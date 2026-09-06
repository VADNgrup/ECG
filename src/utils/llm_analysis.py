from __future__ import annotations
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

def _iter_entries(log_path: Path, tail: int | None=None) -> Iterator[dict]:
    if not log_path.exists():
        print(f'[llm_analysis] Log file not found: {log_path}', file=sys.stderr)
        return
    lines = log_path.read_text(encoding='utf-8').splitlines()
    if tail is not None:
        lines = lines[-tail:]
    for (lineno, raw) in enumerate(lines, 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f'[llm_analysis] Skipping malformed line {lineno}: {exc}', file=sys.stderr)

def _parse_time(iso: str) -> datetime:
    return datetime.fromisoformat(iso)

def analyze(log_path: Path, tail: int | None=None, since: datetime | None=None) -> None:
    per_model: dict[str, dict] = defaultdict(lambda : {'calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'elapsed_s': 0.0})
    total_calls = 0
    skipped = 0
    for entry in _iter_entries(log_path, tail=tail):
        ts = _parse_time(entry.get('time', '1970-01-01T00:00:00+00:00'))
        if since and ts < since:
            skipped += 1
            continue
        model = entry.get('model', 'unknown')
        usage: dict = entry.get('token_usage', {})
        elapsed: float = entry.get('elapsed_s', 0.0)
        bucket = per_model[model]
        bucket['calls'] += 1
        bucket['prompt_tokens'] += usage.get('prompt_tokens', 0)
        bucket['completion_tokens'] += usage.get('completion_tokens', 0)
        bucket['total_tokens'] += usage.get('total_tokens', 0)
        bucket['elapsed_s'] += elapsed
        total_calls += 1
    if total_calls == 0:
        print('No log entries found (or all filtered out).')
        return
    sep = '─' * 80
    print(sep)
    print(f'  LLM Call Log Analysis  ·  {log_path}')
    if since:
        print(f'  Since: {since.isoformat()}  |  Skipped (before cutoff): {skipped}')
    print(sep)
    COL = '{:<40} {:>6} {:>10} {:>11} {:>10} {:>9}'
    header = COL.format('Model', 'Calls', 'Prompt T', 'Complet. T', 'Total T', 'Avg lat.')
    print(header)
    print('·' * 80)
    totals = {'calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'elapsed_s': 0.0}
    for (model, b) in sorted(per_model.items(), key=lambda kv: -kv[1]['total_tokens']):
        avg_lat = b['elapsed_s'] / b['calls'] if b['calls'] else 0.0
        print(COL.format(model[:40], b['calls'], _fmt(b['prompt_tokens']), _fmt(b['completion_tokens']), _fmt(b['total_tokens']), f'{avg_lat:.2f}s'))
        for k in totals:
            totals[k] += b[k]
    print('·' * 80)
    avg_lat_total = totals['elapsed_s'] / totals['calls'] if totals['calls'] else 0.0
    print(COL.format('TOTAL', totals['calls'], _fmt(totals['prompt_tokens']), _fmt(totals['completion_tokens']), _fmt(totals['total_tokens']), f'{avg_lat_total:.2f}s'))
    print(sep)

def _fmt(n: int) -> str:
    return f'{n:,}'

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Analyse LLM call logs (JSONL).')
    p.add_argument('--log', default='logs/llm_calls.jsonl', help='Path to the JSONL log file (default: logs/llm_calls.jsonl)')
    p.add_argument('--tail', type=int, default=None, metavar='N', help='Only analyse the last N entries.')
    p.add_argument('--since', default=None, metavar='DATE', help='Only include entries on or after this ISO date/datetime (e.g. 2026-04-05).')
    return p.parse_args()

def main() -> None:
    args = _parse_args()
    since_dt: datetime | None = None
    if args.since:
        try:
            since_dt = datetime.fromisoformat(args.since)
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            print(f'[llm_analysis] Invalid --since value: {args.since!r}', file=sys.stderr)
            sys.exit(1)
    analyze(Path(args.log), tail=args.tail, since=since_dt)

def plot(log_path: Path=Path('logs/llm_calls.jsonl'), tail: int | None=None, since: datetime | None=None) -> None:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from collections import defaultdict
    entries = list(_iter_entries(log_path, tail=tail))
    if since:
        entries = [e for e in entries if _parse_time(e.get('time', '1970-01-01T00:00:00+00:00')) >= since]
    if not entries:
        print('No entries to plot.')
        return
    per_model: dict[str, dict] = defaultdict(lambda : {'calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'elapsed_s': 0.0})
    (timestamps, cumulative_tokens) = ([], [])
    running = 0
    for e in sorted(entries, key=lambda x: x.get('time', '')):
        m = e.get('model', 'unknown')
        u = e.get('token_usage', {})
        elapsed = e.get('elapsed_s', 0.0)
        per_model[m]['calls'] += 1
        per_model[m]['prompt_tokens'] += u.get('prompt_tokens', 0)
        per_model[m]['completion_tokens'] += u.get('completion_tokens', 0)
        per_model[m]['total_tokens'] += u.get('total_tokens', 0)
        per_model[m]['elapsed_s'] += elapsed
        running += u.get('total_tokens', 0)
        timestamps.append(_parse_time(e['time']))
        cumulative_tokens.append(running)
    models = list(per_model.keys())
    calls = [per_model[m]['calls'] for m in models]
    prompt = [per_model[m]['prompt_tokens'] for m in models]
    compl = [per_model[m]['completion_tokens'] for m in models]
    avg_lat = [per_model[m]['elapsed_s'] / per_model[m]['calls'] for m in models]
    short = [m.split('/')[-1][:24] for m in models]
    x = range(len(models))
    (fig, axes) = plt.subplots(2, 2, figsize=(14, 9))
    fig.suptitle('LLM Call Log Analysis', fontsize=14, fontweight='bold')
    _COLORS = ['#4C72B0', '#DD8452', '#55A868', '#C44E52', '#8172B3', '#937860', '#DA8BC3', '#8C8C8C']
    ax = axes[0, 0]
    bars = ax.bar(x, calls, color=_COLORS[:len(models)])
    ax.bar_label(bars, fmt='%d', padding=3)
    ax.set_title('Calls per Model')
    ax.set_ylabel('# calls')
    ax.set_xticks(list(x))
    ax.set_xticklabels(short, rotation=25, ha='right')
    ax.grid(axis='y', alpha=0.3)
    ax = axes[0, 1]
    b1 = ax.bar(x, prompt, label='Prompt', color=_COLORS[0], alpha=0.85)
    b2 = ax.bar(x, compl, bottom=prompt, label='Completion', color=_COLORS[1], alpha=0.85)
    ax.set_title('Token Usage per Model')
    ax.set_ylabel('tokens')
    ax.set_xticks(list(x))
    ax.set_xticklabels(short, rotation=25, ha='right')
    ax.legend(fontsize=8)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f'{int(v):,}'))
    ax.grid(axis='y', alpha=0.3)
    ax = axes[1, 0]
    bars = ax.bar(x, avg_lat, color=_COLORS[2])
    ax.bar_label(bars, fmt='%.2fs', padding=3)
    ax.set_title('Avg Latency per Model')
    ax.set_ylabel('seconds')
    ax.set_xticks(list(x))
    ax.set_xticklabels(short, rotation=25, ha='right')
    ax.grid(axis='y', alpha=0.3)
    ax = axes[1, 1]
    ax.plot(timestamps, cumulative_tokens, color=_COLORS[0], linewidth=1.8)
    ax.fill_between(timestamps, cumulative_tokens, alpha=0.15, color=_COLORS[0])
    ax.set_title('Cumulative Tokens Over Time')
    ax.set_ylabel('total tokens')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=25)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f'{int(v):,}'))
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.show()
if __name__ == '__main__':
    main()