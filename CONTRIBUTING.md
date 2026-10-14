# Contributing

This is an experimental, CPU-only, self-hosted forecasting service.

1. Create a Python 3.15 environment and install `python -m pip install -e ".[dev]"`.
2. Run `bash scripts/local_check.sh quick` (Ruff and real River pytest) before opening a PR. Hosted CI runs the full gate on every PR and pushes to `master`.
3. New benchmark adapters must never use future outcomes as prediction features, and must document unavailable timing data.
4. Keep vendor dataset files and secrets out of Git. Add small synthetic fixtures to `examples/` instead.
5. Test changes to prediction persistence for restart/retry/idempotency behavior.

Use Conventional Commits for PR titles and commits, preferably a single-line
subject with no body (for example `build: define release metadata and support contract`).
See the [release contract](docs/release-contract.md); PRs never publish releases.

Local release validation uses **actual River**, not the local-only stub. For disconnected development without
River installed, set `VEDRFOLNIR_TEST_STUB_RIVER=1` for a limited smoke test; those
results do not establish ML integration correctness.
