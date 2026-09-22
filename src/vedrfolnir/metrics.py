"""Bounded, one-forecast-per-plan accuracy calculations for the built-in UI."""
from collections import defaultdict
from datetime import timedelta
from sqlalchemy import func, select
from .db import Prediction, utcnow
from .features import quantile, timestamp

MAX_EVALUATED_PLANS = 2000


def evaluated_plans(session, limit=MAX_EVALUATED_PLANS):
    """Use each plan's FIRST evaluated early-phase forecast, not every refresh.

    Restrict the returned sample to recent completed plans to bound dashboard work.
    This is a recent-plan sample, NOT a lifetime population statistic.
    """
    ranked = (
        select(
            Prediction.id.label("prediction_id"),
            func.row_number().over(
                partition_by=Prediction.plan_id,
                order_by=(Prediction.predicted_at.asc(), Prediction.id.asc()),
            ).label("position"),
        )
        .where(
            Prediction.phase == "start",
            Prediction.error_minutes.is_not(None),
            Prediction.actual_finished_at.is_not(None),
            Prediction.outcome_status.not_in(["cancelled", "skipped"]),
        ).subquery()
    )
    return list(session.scalars(
        select(Prediction)
        .join(ranked, Prediction.id == ranked.c.prediction_id)
        .where(ranked.c.position == 1)
        .order_by(Prediction.actual_finished_at.desc())
        .limit(limit)
    ))


def summarize(predictions, demo=False):
    abs_errors = [abs(p.error_minutes) for p in predictions]
    eligible = [p for p in predictions if p.p90_minutes is not None]
    river = [p for p in predictions if p.used_model]
    return {
        "completed_plans_evaluated": len(predictions),
        "sample_limit": MAX_EVALUATED_PLANS,
        "mae_minutes": round(sum(abs_errors) / len(abs_errors), 2) if abs_errors else None,
        "median_absolute_error_minutes": round(quantile(abs_errors, .5), 2) if abs_errors else None,
        "p90_absolute_error_minutes": round(quantile(abs_errors, .9), 2) if abs_errors else None,
        "signed_bias_minutes": round(sum(p.error_minutes for p in predictions) / len(predictions), 2) if predictions else None,
        "baseline_mae_minutes": round(sum(abs(p.baseline_minutes - p.actual_remaining_minutes) for p in predictions) / len(predictions), 2) if predictions else None,
        "river_mae_minutes": round(sum(abs(p.error_minutes) for p in river) / len(river), 2) if river else None,
        "river_evaluated_plans": len(river),
        "p90_coverage": round(sum(p.actual_remaining_minutes <= p.p90_minutes for p in eligible) / len(eligible), 3) if eligible else None,
        "p90_eligible_plans": len(eligible),
        "demo_data": demo,
    }


def daily_errors(predictions, days=30, now=None):
    end = (now or utcnow()).date()
    start = end - timedelta(days=days - 1)
    buckets = defaultdict(list)
    for p in predictions:
        day = timestamp(p.actual_finished_at).date()
        if start <= day <= end:
            buckets[day].append(p)
    return [{
        "day": (start + timedelta(days=i)).isoformat(),
        "plans": len(buckets[start + timedelta(days=i)]),
        "mae_minutes": summarize(buckets[start + timedelta(days=i)])["mae_minutes"],
        "baseline_mae_minutes": summarize(buckets[start + timedelta(days=i)])["baseline_mae_minutes"],
    } for i in range(days)]


def by_workload(predictions):
    """Bounded, first-forecast observations grouped by load and predictor.

    Counts remain visible so users do not mistake tiny cohorts for
    representative performance. Missing legacy metadata is 'unknown'.
    """
    from .uncertainty import predictor_method
    grouped = defaultdict(list)
    for p in predictions:
        regime = (p.features or {}).get("__pressure_regime", "unknown")
        if regime not in ("idle", "busy", "saturated"):
            regime = "unknown"
        grouped[(regime, predictor_method(p))].append(p)
    result = []
    for (regime, method), rows in sorted(grouped.items()):
        scores = summarize(rows)
        result.append({
            "regime": regime, "prediction_method": method,
            "completed_plans_evaluated": len(rows),
            "mae_minutes": scores["mae_minutes"],
            "p90_absolute_error_minutes": scores["p90_absolute_error_minutes"],
            "signed_bias_minutes": scores["signed_bias_minutes"],
            "p90_coverage": scores["p90_coverage"],
            "p90_eligible_plans": scores["p90_eligible_plans"],
            "interpretation": ("descriptive_only" if len(rows) < 40
                               else "observed_sample_not_guaranteed"),
        })
    return {
        "scored_at": "first start-phase prediction per completed plan",
        "sample_limit": MAX_EVALUATED_PLANS,
        "cohorts": result,
    }
