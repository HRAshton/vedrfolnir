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
  local wheel sdist expected artifact kind
  CHECK_TEMP="$(mktemp -d)"
  trap 'rm -rf "$CHECK_TEMP"' EXIT
  # Build with the development environment's explicitly installed backend tools.
  "$PYTHON" -m build --no-isolation --outdir "$CHECK_TEMP/dist"
  expected="$("$PYTHON" -c 'from vedrfolnir import __version__; print(__version__)')"
  wheel="$CHECK_TEMP/dist/vedrfolnir-$expected-py3-none-any.whl"
  sdist="$CHECK_TEMP/dist/vedrfolnir-$expected.tar.gz"
  [[ -f "$wheel" && -f "$sdist" ]] || { echo "Missing expected distributions" >&2; return 1; }
  [[ "$(find "$CHECK_TEMP/dist" -maxdepth 1 -type f | wc -l)" -eq 2 ]] || { echo "Unexpected distributions" >&2; return 1; }
  cp scripts/verify_distribution.py "$CHECK_TEMP/verify_distribution.py"
  for kind in wheel sdist; do
    if [[ "$kind" == wheel ]]; then artifact="$wheel"; else artifact="$sdist"; fi
    # Resolve real dependencies in clean environments; never inherit editable installs.
    "$PYTHON" -m venv "$CHECK_TEMP/$kind"
    "$CHECK_TEMP/$kind/bin/python" -m pip install "$artifact"
    (
      cd "$CHECK_TEMP"
      PYTHONPATH="" "$CHECK_TEMP/$kind/bin/python" verify_distribution.py "$expected"
    )
  done
  sha256sum "$wheel" "$sdist"
  echo "Full local checks passed."
}
docker_check() {
  docker info >/dev/null
  docker build --tag vedrfolnir:local-smoke .
  "$PYTHON" scripts/release_smoke.py --image vedrfolnir:local-smoke
}
case "$MODE" in
  quick) quick ;;
  full) full ;;
  docker) docker_check ;;
esac
