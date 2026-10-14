# Veðrfölnir

**Self-hosted build/workflow completion ETA** for queued and concurrent jobs.
Historical durations, observed runner capacity and priority/FIFO queue replay
provide a baseline; a River model learns from completed builds. Includes an
HTTP API and a status/accuracy dashboard.

**Experimental:** production ETA accuracy is **not proven**. Predictions target
a *terminal* result, including failures. **CPython 3.15 only**; the initial
container target is **Linux/amd64**, with demo and normalized CSV sources.

## Run from source

With Docker and Compose v2, from a repository checkout:

~~~bash
cp .env.example .env
# Set a unique, long random ETA_ADMIN_TOKEN in .env
docker compose up -d --build
curl http://localhost:8080/api/status
~~~

Open [the dashboard](http://localhost:8080) or [API docs](http://localhost:8080/docs).
The default demo uses synthetic data. Compose builds locally and persists
SQLite state; use one host, one app instance and **one worker per database**.
Do not mix demo and real histories in one data volume.

For a pip installation from the checkout (CPython 3.15):

~~~bash
python -m pip install .
# Configure .env with a unique ETA_ADMIN_TOKEN
vedrfolnir doctor
vedrfolnir serve --host 127.0.0.1 --port 8080
~~~

PyPI publication and prebuilt `ghcr.io/hrashton/vedrfolnir` images are pending;
these are source-install commands. Consumer install/pull commands will be
added after the published artifacts are qualified. See the
[release contract](docs/release-contract.md) and
[GitHub Releases](https://github.com/HRAshton/vedrfolnir/releases) for support
rules and version-specific publication notes.

**Security:** read API/UI endpoints are unauthenticated. Compose binds to
`127.0.0.1`; remote access requires an authenticating TLS reverse proxy.
The admin token protects sync only. Back up SQLite before upgrades and never
load untrusted model checkpoints. See [operations](docs/operations.md).

## Connect and interpret your data

Follow the [quick start](docs/quickstart.md) for demo, CSV or optional
SQL Server source builds; use SELECT-only SQL accounts. The planned official
image omits the MSSQL driver, and SQL Server integration is unqualified until
separately tested. [Source contracts](docs/contracts.md) define required
columns and visibility assumptions.

Future arrivals, unreported dependencies and unknown scheduler policies are
not inferred. River correction is selected only after sustained measured
improvement. Empirical P90 bounds need 40 comparable completed builds and
are not guarantees. See [forecasting](docs/forecasting.md) and
[measured results and limitations](docs/observations.md).

## Develop and explore

~~~bash
python -m pip install -e ".[dev]"
bash scripts/local_check.sh quick
~~~

The [development guide](docs/development.md) covers hosted CI and clean
package checks. Public benchmarks remain opt-in. The
[documentation index](docs/README.md) is the detailed navigation map;
[roadmap](docs/roadmap.md) separates distribution gates from accuracy evidence.
