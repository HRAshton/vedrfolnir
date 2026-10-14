# Documentation map

[← Project README](../README.md)

Vedrfolnir is an **experimental single-host** build/workflow ETA service. Start with the guide matching your task; each topic has one primary home.

| I want to… | Read |
|---|---|
| Run the demo or connect CSV/SQL Server | [Quick start](quickstart.md) |
| Map a private schema with SQL aliases | [Source contract](contracts.md) |
| Set environment variables and logging | [Configuration](configuration.md) |
| Understand how data, scheduler and ML interact | [Architecture](architecture.md) |
| Interpret forecasts, P90 and metrics | [Forecasting](forecasting.md) |
| Forecast queued/running jobs, monitor errors or capture a pilot | [Job forecasts and monitoring](job-forecasts.md) |
| Examine selection/calibration internals | [Adaptive ETA internals](adaptive-eta.md) |
| Reproduce or extend synthetic tests | [Validation](validation.md), [Simulator](../simulator/README.md) |
| Replay real task timelines without future labels | [Timeline replay](timeline-replay.md) |
| Run public data traces with independent targets | [Public benchmarks](benchmarks.md) |
| See **measured** results and negative findings | [Observations](observations.md) |
| Deploy responsibly, back up or debug | [Operations](operations.md) |
| Change the code / run test suite | [Development](development.md) |
| See support rules, publication notes and release gates | [Release contract](release-contract.md), [roadmap](roadmap.md), [GitHub Releases](https://github.com/HRAshton/vedrfolnir/releases) |

**Contract stability:** Only the documented core aliases and optional pool aliases are implemented. Arbitrary `feat_num_*` / `feat_cat_*` dynamic feature decoding, dependency DAG ingestion and configurable queue policies are **not implemented**.

**Accuracy scope:** The [observations](observations.md) distinguish synthetic queue ETA from public workflow-start/build-start benchmarks. Neither establishes production-level guarantees.
