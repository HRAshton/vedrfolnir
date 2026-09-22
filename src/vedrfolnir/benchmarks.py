"""Compatibility entry point for the benchmark API and ``python -m``."""
from .benchmarking.models import Job, Workload, WorkflowSummary, _date, _valid
from .benchmarking.loaders import (
    csv_plans, wfcommons_plans, wfcommons_planstart, wfcommons_timeline_plans,
    wfcommons_taskspan_plans,
    swf_plans,
)
from .benchmarking.scheduler_replay import evaluate_scheduler_replay
from .benchmarking.evaluation import evaluate, evaluate_planstart, _stats, _make_job
from .benchmarking.cli import main

if __name__ == "__main__":
    main()
