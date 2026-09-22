"""One-command TravisTorrent benchmark; Windows, Linux and macOS.

python -m vedrfolnir.benchmark_travistorrent [--offline | --data FILE]
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
import webbrowser

from .travistorrent import download_dataset


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fetch real TravisTorrent CI data, benchmark 10,000 distinct builds and open an HTML report")
    parser.add_argument("--data", type=Path, help="Existing local .csv, .csv.gz or .zip; skips download")
    parser.add_argument("--offline", action="store_true", help="Use cached dataset, without network access")
    parser.add_argument("--max-builds", type=int, default=10_000, help="Maximum distinct builds (default 10,000)")
    parser.add_argument("--warmup", type=int, default=500, help="First N starts excluded from scored results")
    parser.add_argument("--min-training", type=int, default=100, help="Minimum completed River training examples")
    parser.add_argument("--output", type=Path, default=Path("benchmark-results/travistorrent"))
    parser.add_argument("--no-open", action="store_true", help="Do not open report in the browser")
    args = parser.parse_args(argv)
    if args.max_builds <= args.warmup or args.warmup < 0 or args.min_training < 0:
        parser.error("Require --max-builds > --warmup >= 0 and --min-training >= 0")
    started = time.perf_counter()
    try:
        source = args.data if args.data else download_dataset(Path("datasets/travistorrent"), offline=args.offline)
        if not Path(source).is_file():
            raise ValueError(f"Dataset file not found: {source}")
        print(f"TravisTorrent source: {source}", flush=True)
        from .benchmarks import main as benchmark_main
        benchmark_main([
            "--format", "travistorrent", "--input", str(source),
            "--max-plans", str(args.max_builds), "--warmup", str(args.warmup),
            "--min-training", str(args.min_training), "--output", str(args.output),
            "--progress",
        ])
        report = (args.output / "report.html").resolve()
        print(f"\nTotal benchmark time: {time.perf_counter()-started:.1f}s\nReport: {report}", flush=True)
        if not args.no_open:
            webbrowser.open(report.as_uri())
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"TravisTorrent benchmark failed: {exc}", file=sys.stderr)
        print("You can download the archive manually and use --data PATH_TO_FILE --offline", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
