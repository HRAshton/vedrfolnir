# Vedrfolnir

**Self-hosted build/workflow completion ETA** for queued and concurrent jobs. Uses historical durations, observed VM or runner-pool capacity, priority/FIFO queue replay, and a [River](https://riverml.xyz/) model that learns from completed builds. Includes an HTTP API and a built-in status/accuracy page.

**0.3.0rc1 development — unreleased.** Historical v0.2.0 notes refer to the old repository. Production ETA accuracy is **not proven**. Predicts time to a *terminal* result, including failures. **CPython 3.15 only**.

## Try it

Requirements: Docker with Compose v2.

~~~bash
cp .env.example .env
# Set a long random ETA_ADMIN_TOKEN in .env
docker compose up -d --build
curl http://localhost:8080/api/status
~~~

Open [http://localhost:8080](http://localhost:8080) or the [API documentation](http://localhost:8080/docs). The default **demo** mode uses synthetic data.

The source checkout and Docker Compose are the current deployment path (Compose builds locally).
The [release contract](docs/release-contract.md) defines future PyPI and
`ghcr.io/hrashton/vedrfolnir` distribution; those artifacts are not yet published.
The initial official image target is Linux/amd64 with demo/CSV support; MSSQL
requires the optional extra in a source build and separate integration validation.
For local installs, `vedrfolnir doctor` checks configuration and
`vedrfolnir serve` starts one worker. See the
[CSV](examples/quickstart/csv/README.md) and
[SQL Server](examples/quickstart/sqlserver/README.md) examples.

## Connect your own jobs

Choose `SOURCE_MODE=files` for normalized CSV exports or `SOURCE_MODE=mssql` for SQL Server using **read-only SELECT queries**. Validate your mappings before starting:

~~~bash
python -m pip install -e ".[dev]"           # CPython 3.15
python -m vedrfolnir.source_validation --mode files --csv-dir examples/data
# For SQL Server: --mode mssql --query-dir queries
~~~

Required sources: historical jobs, current jobs, and plans. Optional `sched_pool_id` distinguishes runners such as `mssql` and `android`. Vedrfolnir infers observed concurrency every 10 minutes; an optional `scheduler_pools.sql` / CSV supplies authoritative current limits (`0` = paused; `NULL` = unknown). Pool constraints never require per-pool ML models.

See [setup](docs/quickstart.md), [SQL/CSV contracts](docs/contracts.md), and [configuration](docs/configuration.md).

## What the ETA means

1. Historical per-job durations provide a baseline. Unknown VM capacity or blocked work can require a fallback.
2. The queue scheduler projects visible runnable jobs using observed or declared parallelism. **Future arrivals and unreported dependencies are not known.**
3. River learns a bounded correction in the background and is selected only if earlier completed-build forecasts show sustained improvement.
4. An empirical P90 upper ETA appears only after **40 comparable completed builds**; it is not a guaranteed probability.

Inspect `GET /api/plans`, `/api/pools`, and `/api/metrics/by-workload`. Forecast warnings distinguish inferred capacity, sparse history, and absent calibration.

`GET /api/jobs` and `/api/jobs/{run_id}` expose queue start/finish estimates,
source freshness and unsupported cases. The dashboard shows source health,
wait/finish errors and daily trends by pool, job type and load; observations
can be exported as CSV. [Job API and monitoring](docs/job-forecasts.md).

## Validate and develop

~~~bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m simulator.validation --quick
~~~

Run `bash scripts/local_check.sh quick` (Python 3.15); hosted CI is planned next. [Development guide](docs/development.md). Public traces remain opt-in. [Candidate release notes](docs/releases/v0.3.0.md) · [Historical preview notes](docs/releases/v0.2.0.md) · [Release roadmap](docs/roadmap.md). [Evaluation protocol](docs/validation.md) · [Measured results and limits](docs/observations.md) · [Independent public benchmarks](docs/benchmarks.md).

## Documentation

| Goal | Guide |
|---|---|
| First run, CSV or SQL Server | [Quick start](docs/quickstart.md) |
| Precise columns, statuses, pool cases | [Source contract](docs/contracts.md) |
| Environment, Docker, logging | [Configuration](docs/configuration.md) |
| Runtime flow and scheduler/ML | [Architecture](docs/architecture.md) |
| ETAs, uncertainty, API and evaluation | [Forecasting](docs/forecasting.md) |
| Recorded experiments and parameter observations | [Observations](docs/observations.md) |
| Benchmarks and reproducibility | [Validation](docs/validation.md) |
| Upgrades, security and troubleshooting | [Operations](docs/operations.md) |
| Contributor commands | [Development](docs/development.md) |

**Security:** Compose binds the unauthenticated read API to `127.0.0.1`; protect it with an authenticated proxy before external access. Do not mix demo and real histories in one data volume.
