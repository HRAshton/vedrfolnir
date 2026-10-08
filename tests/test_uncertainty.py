"""Prospective matched-cohort P90 calibration and public diagnostics."""
from types import SimpleNamespace

from vedrfolnir.uncertainty import upper_estimate
from vedrfolnir.metrics import by_workload


def prediction(plan_id, *, actual=40.0, predicted=30.0,
               regime="saturated", method="queue_scheduler", phase="start",
               river=False, p90=None, outcome="passed"):
    return SimpleNamespace(
        plan_id=plan_id, phase=phase, used_model=river,
        outcome_status=outcome, actual_remaining_minutes=actual,
        predicted_minutes=predicted, p90_minutes=p90,
        baseline_minutes=predicted, error_minutes=predicted-actual,
        features={"__pressure_regime": regime, "__prediction_method": method},
    )


def test_upper_bound_needs_distinct_labeled_builds_in_same_group():
    scored = [prediction(str(i)) for i in range(39)]
    features = scored[0].features
    assert upper_estimate(30, features=features, phase="start",
                          river_used=False, completed=scored) == (None, 39)
    scored.append(prediction("39"))
    assert upper_estimate(30, features=features, phase="start",
                          river_used=False, completed=scored) == (40, 40)
    # Extra refreshed snapshots of the same plan cannot boost support.
    dupes = [prediction("39") for _ in range(400)]
    assert upper_estimate(30, features=features, phase="start",
                          river_used=False, completed=scored[:20]+dupes)[0] is None
    # No borrowing observations from other regimes or model methods.
    other = [prediction(f"other-{i}", regime="idle", actual=300)
             for i in range(90)]
    assert upper_estimate(30, features=features, phase="start",
                          river_used=False, completed=scored+other) == (40, 40)
    assert upper_estimate(30, features=features, phase="late",
                          river_used=False, completed=scored+other)[0] is None
    assert upper_estimate(30, features=features, phase="start",
                          river_used=True, completed=scored+other)[0] is None


def test_upper_bound_never_precedes_point_and_uses_recent_window():
    stable = [prediction(str(i), actual=20, predicted=30) for i in range(40)]
    assert upper_estimate(30, features=stable[0].features, phase="start",
                          river_used=False, completed=stable) == (30, 40)
    varying = [prediction(str(i), actual=30+float(i), predicted=30)
               for i in range(250)]
    bound, count = upper_estimate(30, features=varying[0].features, phase="start",
                                  river_used=False, completed=varying)
    assert count == 200
    assert 250 <= bound <= 260


def test_excludes_cancelled_and_skipped_from_upper_bound():
    rows = [prediction(str(i), outcome="cancelled") for i in range(40)]
    assert upper_estimate(30, features=rows[0].features, phase="start",
                          river_used=False, completed=rows) == (None, 0)


def test_workload_metrics_are_stratified_and_do_not_claim_guarantees():
    rows = [prediction("a", actual=40, predicted=30, p90=50),
            prediction("b", actual=25, predicted=30, regime="idle", p90=None),
            prediction("c", actual=100, predicted=30, regime="saturated",
                       river=True, p90=95)]
    result = by_workload(rows)
    assert result["sample_limit"] == 2000
    assert len(result["cohorts"]) == 3
    saturated = next(x for x in result["cohorts"]
                     if x["regime"] == "saturated"
                     and x["prediction_method"] == "queue_scheduler")
    assert saturated["p90_coverage"] == 1.0
    assert saturated["completed_plans_evaluated"] == 1
    assert saturated["interpretation"] == "descriptive_only"
    idle = next(x for x in result["cohorts"] if x["regime"] == "idle")
    assert idle["p90_coverage"] is None
