# Prospective job monitoring  -  October 13, 2026

Base: `b727420f49e5748cf6ac505e9cecb136c415b359` (individual job forecasts).
Local runtime: stable CPython 3.15.0, SQLAlchemy 2.0.54, real River 0.26.1.
This change records first job/status observations during successful polls,
matches delayed terminal outcomes and supplies the built-in monitoring UI.
It does not tune duration estimates, select job River predictions or claim
job P90 coverage. Forecast duration samples now exclude future-dated history
at the prediction clock, matching the scheduler's existing capacity cutoff.

## Executed checks

`bash scripts/local_check.sh full` passed locally: Ruff, **163 tests**, four
small simulator runs, wheel/sdist build and installed-wheel API/version/static
resource verification. A writable TMPDIR and the active venv dependency path
in PYTHONPATH were supplied for this restricted runtime's nested wheel check.
Starlette emitted one TestClient/httpx deprecation warning; no failures or
skips. Tests include old-schema upgrade preserving cursor/checkpoint content,
retention, idempotency, unavailable first forecasts, late outcomes, cancelled
outcomes, invalid clocks, identity correction, future-history exclusion and
read-only CSV export with formula-safe text.

The default eight-day `simulator.monitoring_smoke` fixture exercised the real
CSVSource → import → live snapshot → capture → delayed outcome pipeline.
There were **24 completed observations** (8 queued, 16 running), no unresolved
completed observations and no label issues. Service/connection restarts and
repeated polls created no duplicates. A missing live CSV caused the expected
poll failure; restoring the file cleared the error. SQLite online backup and
an application restart/poll on the restored database preserved all 24 records
without duplicating captures or labels. Two current fixture jobs remain for UI
inspection. No original source records or database files are committed.

| Synthetic target | Scored | MAE (min) | P90 absolute error (min) | Signed bias (min) |
|---|---:|---:|---:|---:|
| Queued wait | 8 | 3.000 | 7.200 | +3.000 |
| Queued finish | 8 | 7.689 | 18.200 | +3.936 |
| Running finish | 16 | 3.188 | 10.500 | +3.062 |

This tiny one-slot FIFO fixture includes cold start and repeated job types.
These numbers verify error recording and units, **not independent accuracy**
or generalization. It deliberately contains no workflow-target training.
Existing published replay results and their limitations remain applicable.

The optional `scripts/check_dashboard.cjs` passed with jsdom against an actual
local Uvicorn API and this fixture. It exercised initial tables/SVG rendering,
target/window switching, saved-observation dialog, 15-second refresh callback,
empty-chart state, stale-source withholding, failed refresh and recovery.
There were no script/DOM errors. Failure/stale/empty responses were injected
in memory; source files were not modified by UI checks. jsdom supplies no
layout engine; this is **not** a visual/browser or accessibility audit.
Downloaded Chromium crashed at startup (including `--version`) in this
environment, so desktop/mobile rendering and native dialog behavior remain
unverified. No Node dependency was added to the application runtime.

## Reproduce

~~~bash
python -m simulator.monitoring_smoke --output benchmark-results/monitoring-smoke
SOURCE_MODE=files CSV_DATA_DIR=benchmark-results/monitoring-smoke/source \
ETA_DB_URL=sqlite:///benchmark-results/monitoring-smoke/monitor.sqlite \
python -m uvicorn vedrfolnir.api:app --host 127.0.0.1 --port 8080
~~~

In another terminal, optional DOM checks require Node and jsdom:

~~~bash
npm install --prefix benchmark-results/dashboard-tools --ignore-scripts jsdom
NODE_PATH="$PWD/benchmark-results/dashboard-tools/node_modules" \
node scripts/check_dashboard.cjs
~~~

Use the default eight-day fixture for that check. Inspect `/` manually at
desktop and mobile widths; verify the modal, keyboard navigation, scrolling,
chart readability and CSV download in a real browser before release.

## RC gates still outstanding

| Gate | Current evidence |
|---|---|
| Python regressions and packaging | Passed locally as above |
| Additive database upgrade and synthetic CSV restart/backup/restore | Passed locally |
| Live dashboard interactions | DOM/API check passed; native visual review outstanding |
| Docker clean startup/API/import/restart/shutdown | Not executed; Docker CLI unavailable here |
| Actual SQL Server integration and sustained deployment | Not executed |
| Real queued-job accuracy and job uncertainty | Not established by this fixture |

No GitHub Actions, release tag, PyPI upload or deployment was dispatched.
The historical v0.2.0 tag and package version in the old repository remained unchanged. Complete the
image lifecycle and real-browser checks before marking an experimental RC
ready. A real pilot should verify queue-policy/source assumptions and collect
prospective observations, with private records kept outside source control.
