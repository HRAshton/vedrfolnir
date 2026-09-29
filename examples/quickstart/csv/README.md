# Synthetic CSV source example

This static, synthetic dataset demonstrates the **normalized source contract**:
four completed historical runs, one active plan, three visible jobs (completed,
running and queued) and one declared pool with two slots. Historical completion
keys are ordered by `(finished_at, run_id)`.

Run the read-only preflight from the repository root with CPython 3.15:

```sh
vedrfolnir-check-source --mode files --csv-dir examples/quickstart/csv
```

For Compose, set `SOURCE_MODE=files` and
`CSV_SOURCE_PATH=./examples/quickstart/csv` in `.env`, then run
`docker compose run --rm vedrfolnir vedrfolnir-check-source --mode files
--csv-dir /srv/examples/data` before starting the service.

The timestamps are **fixed in September 2026** to make the fixture
reproducible. This is an input-contract example, **not a live source or an
accuracy benchmark**; copy and replace it with atomically updated current
snapshots for a real deployment. Do not mix this synthetic data with a
production SQLite state volume.
