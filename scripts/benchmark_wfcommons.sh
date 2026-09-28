#!/usr/bin/env bash
# Backwards-compatible launcher; the Python quickstart is cross-platform.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
args=(--max-plans "${BENCHMARK_MAX_PLANS:-30}" --warmup "${BENCHMARK_WARMUP:-10}" --min-training "${BENCHMARK_MIN_TRAINING:-10}" --output "${BENCHMARK_OUTPUT_DIR:-benchmark-results/wfcommons-pegasus}")
if [[ -n "${WFCOMMONS_DATA_DIR:-}" ]]; then args+=(--data "${WFCOMMONS_DATA_DIR}"); fi
python -m vedrfolnir.benchmark_quickstart "${args[@]}" "$@"
