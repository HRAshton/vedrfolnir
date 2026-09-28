"""CLI for the in-memory build simulator."""
from __future__ import annotations

import argparse
import json
import os

from .engine import run_benchmark
from .scheduling import VMConfig


def parse_vms(values):
    """Override fleet with explicit NAME:SPEED[:SLOTS] entries."""
    if not values:
        return None
    result = {}
    for item in values:
        fields = item.rsplit(":", 2)
        if len(fields) not in (2, 3):
            raise argparse.ArgumentTypeError("VM must be NAME:SPEED[:SLOTS]")
        try:
            name, speed_text = fields[:2]
            speed = float(speed_text)
            slots = int(fields[2]) if len(fields) == 3 else 1
        except ValueError as exc:
            raise argparse.ArgumentTypeError("VM must be NAME:SPEED[:SLOTS]") from exc
        if not name or not 0 < speed < float("inf") or slots < 1:
            raise argparse.ArgumentTypeError("VM needs a name, positive speed and slots >= 1")
        if name in result:
            raise argparse.ArgumentTypeError("Duplicate VM: " + name)
        result[name] = VMConfig(speed=speed, slots=slots)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Virtual-clock build/job ETA simulator")
    parser.add_argument("--history-builds", type=int, default=150)
    parser.add_argument("--builds", type=int, default=300)
    parser.add_argument("--arrival-seconds", type=float, default=240)
    parser.add_argument("--min-jobs", type=int, default=2)
    parser.add_argument("--max-jobs", type=int, default=6)
    parser.add_argument("--interval-seconds", type=int, default=600)
    parser.add_argument("--min-training", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--servers", type=int, default=1)
    parser.add_argument("--vms-per-server", type=int, default=30)
    parser.add_argument("--min-slots", type=int, default=1)
    parser.add_argument("--max-slots", type=int, default=3)
    parser.add_argument("--contention", type=float, default=0.15)
    parser.add_argument("--history-contention", type=float, default=None,
                        help="Simulator-only historical contention (defaults to --contention)")
    parser.add_argument("--burst-size", type=int, default=0,
                        help="Number of builds arriving simultaneously in an initial burst")
    parser.add_argument("--vm", action="append", metavar="NAME:SPEED[:SLOTS]",
                        help="Override generated fleet with a VM name, hidden speed and slot count")
    parser.add_argument("--output", default="benchmark-results/simulator")
    initial_reconstruct = os.getenv("RECONSTRUCT_HISTORICAL_QUEUE", "false").lower() in ("1", "true", "yes")
    parser.add_argument("--reconstruct-historical-queue",
                        action=argparse.BooleanOptionalAction,
                        default=initial_reconstruct,
                        help="Replay historical queue state from created/started/finished timestamps")
    args = parser.parse_args(argv)
    try:
        vms = parse_vms(args.vm)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    summary = run_benchmark(
        output=args.output, seed=args.seed,
        history_builds=args.history_builds, builds=args.builds,
        arrival_seconds=args.arrival_seconds,
        min_jobs=args.min_jobs, max_jobs=args.max_jobs,
        interval_seconds=args.interval_seconds,
        min_training=args.min_training,
        vms=vms, servers=args.servers, vms_per_server=args.vms_per_server,
        min_slots=args.min_slots, max_slots=args.max_slots,
        contention=args.contention, history_contention=args.history_contention,
        burst_size=args.burst_size,
        reconstruct_historical_queue=args.reconstruct_historical_queue,
    )
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    main()
