"""Explain the evidence behind an ETA without inventing a confidence probability.

These are diagnostic flags, not a calibrated reliability score. Historical
predictions lacking method metadata retain an explicit unknown method.
"""
from __future__ import annotations


def prediction_evidence(features, *, river_used=False, p90_available=False):
    features = features or {}
    method = ("river_residual_correction" if river_used else
              features.get("__prediction_method", "unknown"))
    if method not in {"river_residual_correction", "queue_scheduler",
                      "historical_baseline"}:
        method = "unknown"
    warnings = []
    if method in ("historical_baseline", "unknown"):
        warnings.append("scheduler_not_observable")
    if float(features.get("observed_vm_count") or 0) <= 0:
        warnings.append("vm_inventory_unobserved")
    if float(features.get("plan_active_jobs_without_matching_history") or 0) > 0:
        warnings.append("job_duration_history_scarce")
    if float(features.get("plan_pending_count") or 0) > 0:
        warnings.append("blocked_or_dependency_pending_jobs")
    if (features.get("__pressure_regime") == "saturated"
            and method != "historical_baseline"):
        warnings.append("future_job_arrivals_not_modeled")
    source = features.get("__pool_capacity_source")
    if source == "observed":
        warnings.append("pool_capacity_inferred_not_declared")
    elif source in ("unknown", "mixed") and features.get("__pool_ids"):
        warnings.append("pool_capacity_partially_unknown")
    if not p90_available:
        warnings.append("p90_not_yet_calibrated")
    # "observational" is not "accurate": this project has no production
    # validation, and an ETA is always conditional on scheduler assumptions.
    limited = any(warning != "p90_not_yet_calibrated" for warning in warnings)
    return {
        "method": method,
        "level": "limited" if limited else "observational",
        "warnings": warnings,
    }
