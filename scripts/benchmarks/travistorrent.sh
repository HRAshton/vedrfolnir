#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/../.."
if [ ! -x .venv/bin/python ]; then
  python3.15 -m venv .venv
fi
if ! .venv/bin/python -c 'import vedrfolnir, river, sqlalchemy' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -e .
fi
exec .venv/bin/python -m vedrfolnir.benchmark_travistorrent "$@"
