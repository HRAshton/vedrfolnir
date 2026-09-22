"""One-command real workflow benchmark for Windows, Linux and macOS.

python -m vedrfolnir.benchmark_quickstart
python -m vedrfolnir.benchmark_quickstart --offline
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import time
import webbrowser

DATASET = 'https://github.com/wfcommons/WfInstances.git'


def _run(*args):
    print('>', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True)


def ensure_wfcommons(repo: Path, *, offline=False):
    """Use sparse checkout regardless of pre-existing (possibly incomplete) state."""
    repo = repo.resolve()
    if repo.exists() and not (repo / '.git').is_dir():
        # Support an unpacked dataset, including fully offline/direct downloads.
        if any(repo.rglob('*.json')):
            print('Using locally supplied JSON traces:', repo, flush=True)
            return repo / 'pegasus' if (repo / 'pegasus').exists() else repo
        raise RuntimeError(f'{repo} exists but contains no WfCommons JSON traces and is not a Git checkout.')
    if not repo.exists():
        if offline:
            raise RuntimeError('Dataset not downloaded. Run without --offline once, or pass --data PATH.')
        if not shutil.which('git'):
            raise RuntimeError('Git is needed for the initial sparse download. Install Git for Windows or pass --data with previously downloaded WfCommons JSON files.')
        repo.parent.mkdir(parents=True, exist_ok=True)
        _run('git', 'clone', '--depth', '1', '--filter=blob:none', '--sparse', DATASET, repo)
    # Explicitly enforce checkout, even if a stale/empty pegasus folder exists.
    if not offline:
        _run('git', '-C', repo, 'sparse-checkout', 'set', 'pegasus')
    data = repo / 'pegasus'
    if not data.is_dir() or not any(data.rglob('*.json')):
        raise RuntimeError(
            f'No Pegasus execution JSON found in {data}. '
            'If a partial clone was interrupted, rerun without --offline; '
            'you may need to delete an incomplete datasets/WfInstances directory.'
        )
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description='Download real WfCommons workflows and benchmark Vedrfolnir, then open an HTML report.')
    parser.add_argument('--data', type=Path, help='Existing WfCommons directory; skip downloading')
    parser.add_argument('--offline', action='store_true', help='Use locally cached WfCommons data only')
    parser.add_argument('--max-plans', type=int, default=135, help='Maximum real workflows (default 135: full Pegasus collection)')
    parser.add_argument('--warmup', type=int, default=20, help='Exclude first N plan starts from scored comparison')
    parser.add_argument('--min-training', type=int, default=10, help='Minimum River training snapshots before use')
    parser.add_argument('--output', type=Path, default=Path('benchmark-results/wfcommons-pegasus'))
    parser.add_argument('--no-open', action='store_true', help='Do not open HTML report in default browser')
    args = parser.parse_args(argv)
    if args.max_plans <= args.warmup:
        parser.error('--max-plans must be greater than --warmup')
    started = time.perf_counter()
    try:
        if args.data:
            path = args.data.resolve()
            if (path / 'pegasus').is_dir():
                path = path / 'pegasus'
        else:
            path = ensure_wfcommons(Path('datasets/WfInstances'), offline=args.offline)
        from .benchmarks import main as benchmark_main, wfcommons_planstart, wfcommons_plans
        print('Scanning WfCommons JSON traces...', flush=True)
        try:
            available = wfcommons_planstart(path)
            mode = 'wfcommons-start'
            task_count = sum(p.task_count for p in available)
            print('Dataset has workflow start and makespan, but may lack task start times; running plan-start ETA benchmark.', flush=True)
        except ValueError as summary_error:
            try:
                available = wfcommons_plans(path)
            except ValueError as timeline_error:
                raise RuntimeError(f'Neither plan-start nor task-timeline records were usable. '
                                   f'Plan-start error: {summary_error}. Task-timeline error: {timeline_error}') from timeline_error
            mode = 'wfcommons'
            task_count = sum(len(p.jobs) for p in available)
            print('Dataset has task start timestamps; running progress-aware ETA benchmark.', flush=True)
        print(f'Found {len(available)} real workflows ({task_count:,} tasks).', flush=True)
        if len(available) <= args.warmup:
            raise RuntimeError(f'Only {len(available)} valid workflows found, but warmup={args.warmup}. Pass --warmup 0.')
        args.output.mkdir(parents=True, exist_ok=True)
        print(f'Evaluating up to {args.max_plans} workflows (warmup={args.warmup}, format={mode}); no jobs are executed...', flush=True)
        benchmark_main(['--format', mode, '--input', str(path), '--output', str(args.output),
                        '--max-plans', str(args.max_plans), '--warmup', str(args.warmup),
                        '--min-training', str(args.min_training), '--progress'])
        report = (args.output / 'report.html').resolve()
        print(f'Completed in {time.perf_counter() - started:.1f} seconds.\nReport: {report}', flush=True)
        if not args.no_open:
            webbrowser.open(report.as_uri())
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'Benchmark failed: {exc}', file=sys.stderr)
        print('Try: python -m vedrfolnir.benchmark_quickstart --data PATH_TO_JSON_DIRECTORY --no-open', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
