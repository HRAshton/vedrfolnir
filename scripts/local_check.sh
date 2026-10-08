#!/usr/bin/env bash
# Run regression, package and container checks locally.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
MODE="$*"
case "$MODE" in quick|full|docker) ;; *)
  echo "Usage: bash scripts/local_check.sh quick|full|docker" >&2
  exit 2 ;;
esac
PYTHON=python3.15
if [[ -n "$(printenv VIRTUAL_ENV || true)" ]]; then PYTHON=python; fi
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "CPython 3.15 required: create and activate a local .venv" >&2
  exit 2
fi
"$PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 15), sys.version'
quick() {
  "$PYTHON" -m ruff check src tests
  VEDRFOLNIR_TEST_STUB_RIVER=0 "$PYTHON" -m pytest -q
}
full() {
  quick
  "$PYTHON" -m simulator.validation --quick --output benchmark-results/local-smoke
  local wheel
  CHECK_TEMP="$(mktemp -d)"
  trap 'rm -rf "$CHECK_TEMP"' EXIT
  # Keep builds offline by using preinstalled build dependencies.
  "$PYTHON" -m build --no-isolation --outdir "$CHECK_TEMP/dist"
  wheel="$(find "$CHECK_TEMP/dist" -maxdepth 1 -name '*.whl' -print -quit)"
  if [[ -z "$wheel" ]]; then echo "No wheel built" >&2; return 1; fi
  "$PYTHON" -m venv --system-site-packages "$CHECK_TEMP/verify"
  "$CHECK_TEMP/verify/bin/python" -m pip install --no-index --no-deps "$wheel"
  (
    cd "$CHECK_TEMP"
    "$CHECK_TEMP/verify/bin/python" - <<'PY'
from importlib.metadata import version
from importlib.resources import files
from vedrfolnir import __version__
from vedrfolnir.api import app
from importlib.metadata import entry_points
assert version("vedrfolnir") == __version__ == app.version
assert (files("vedrfolnir") / "static" / "dashboard.html").is_file()
assert any(ep.name == "vedrfolnir" for ep in entry_points(group="console_scripts"))
print("Installed wheel verified:", __version__)
PY
  )
  echo "Full local checks passed."
}
docker() {
  docker info >/dev/null
  docker build --tag vedrfolnir:local-smoke .
  "$PYTHON" scripts/release_smoke.py --image vedrfolnir:local-smoke
}
case "$MODE" in
  quick) quick ;;
  full) full ;;
  docker) docker ;;
esac
