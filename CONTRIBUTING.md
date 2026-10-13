# Contributing

This is an experimental, CPU-only, self-hosted forecasting service.

1. Create a Python 3.15 environment and install `python -m pip install -e ".[dev]"`.
2. Run `bash scripts/local_check.sh quick` (Ruff and real River pytest) before opening a PR; no GitHub-hosted CI is enabled.
3. New benchmark adapters must never use future outcomes as prediction features, and must document unavailable timing data.
4. Keep vendor dataset files and secrets out of Git. Add small synthetic fixtures to `examples/` instead.
5. Test changes to prediction persistence for restart/retry/idempotency behavior.

Local release validation uses **actual River**, not the local-only stub. For disconnected development without
River installed, set `VEDRFOLNIR_TEST_STUB_RIVER=1` for a limited smoke test; those
results do not establish ML integration correctness.
