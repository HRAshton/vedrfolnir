"""Cohort-matched, prospective empirical upper ETA estimates.

Use only finished builds with first point-in-time predictions per comparable
cohort. With insufficient calibration evidence return no upper ETA.
"""
from __future__ import annotations

import math
from .features import quantile

MIN_CALIBRATION_BUILDS = 40
MAX_CALIBRATION_BUILDS = 200


def predictor_method(row):
    if row.used_model:
        return "river_residual_correction"
    return (row.features or {}).get("__prediction_method", "unknown")


def calibration_key(features, *, phase, river_used):
    metadata = features or {}
    method = ("river_residual_correction" if river_used else
              metadata.get("__prediction_method", "unknown"))
    return (phase, metadata.get("__pressure_regime", "unknown"), method)


def upper_estimate(point_minutes, *, features, phase, river_used, completed,
                   min_builds=MIN_CALIBRATION_BUILDS,
                   max_builds=MAX_CALIBRATION_BUILDS):
    """Estimate signed-overrun P90 from previous *completed*, distinct builds.

    Call with completed predictions ordered oldest to newest. Do not mix
    phases, scheduler methods or pressure regimes. A returned estimate is
    empirical and cannot guarantee future coverage under workload drift.
    """
    cohort = calibration_key(features, phase=phase, river_used=river_used)
    by_plan = {}
    for row in completed:
        if (row.actual_remaining_minutes is None or
                row.outcome_status in ("cancelled", "skipped") or
                not math.isfinite(row.actual_remaining_minutes) or
                not math.isfinite(row.predicted_minutes)):
            continue
        if calibration_key(row.features, phase=row.phase,
                           river_used=row.used_model) != cohort:
            continue
        by_plan.setdefault(row.plan_id, row)
    sample = list(by_plan.values())[-max_builds:]
    if len(sample) < min_builds:
        return None, len(sample)
    overruns = [row.actual_remaining_minutes - row.predicted_minutes
                for row in sample]
    offset = max(0.0, quantile(overruns, 0.9))
    return point_minutes + offset, len(sample)
