"""Conservative bounded cleanup of aging operational evidence.

A SQLite backup is the long-term archival format. Marked training samples,
active plans/jobs and outstanding job labels are never silently deleted.
"""
from datetime import timedelta

from sqlalchemy import and_, delete, or_, select

from .db import CurrentJob, CurrentPlan, HistoricalRun, JobPrediction, Prediction, TrainingMarker
from .features import TERMINAL


def prune_storage(session, config, now, *, imported_history=0):
    """Prune eligible rows after a successful poll, preserving label/training links.

    History deletions keep pace with this poll's successful imports so initial
    backfills cannot accumulate expired, unprotected rows. Plan predictions
    retain their independent fixed-size deletion limit.
    """
    batch = config.retention_batch
    history_batch = max(batch, imported_history)

    # Keep current work and unresolved job labels.
    protected_jobs = select(CurrentJob.run_id)
    active_plans = select(CurrentPlan.plan_id).where(
        CurrentPlan.status.not_in(list(TERMINAL)))
    unlabeled_jobs = select(JobPrediction.run_id).where(JobPrediction.outcome_status.is_(None))
    history_keys = select(HistoricalRun.run_id).where(
        HistoricalRun.finished_at < now - timedelta(days=config.history_retention_days),
        HistoricalRun.run_id.not_in(protected_jobs),
        or_(HistoricalRun.plan_id.is_(None),
            HistoricalRun.plan_id.not_in(active_plans)),
        HistoricalRun.run_id.not_in(unlabeled_jobs),
    ).order_by(HistoricalRun.finished_at, HistoricalRun.run_id).limit(history_batch)
    history_pruned = session.execute(
        delete(HistoricalRun).where(HistoricalRun.run_id.in_(history_keys))
    ).rowcount or 0

    # Keep training witnesses to avoid training the same phase twice.
    training_samples = select(TrainingMarker.prediction_id)
    stale_predictions = or_(
        and_(Prediction.actual_finished_at.is_not(None),
             Prediction.actual_finished_at < now - timedelta(days=config.plan_retention_days)),
        and_(Prediction.actual_finished_at.is_(None),
             Prediction.predicted_at < now - timedelta(
                 days=config.plan_unresolved_retention_days)),
    )
    prediction_ids = select(Prediction.id).where(
        stale_predictions,
        Prediction.id.not_in(training_samples),
        Prediction.plan_id.not_in(active_plans),
    ).order_by(Prediction.predicted_at, Prediction.id).limit(batch)
    plans_pruned = session.execute(
        delete(Prediction).where(Prediction.id.in_(prediction_ids))
    ).rowcount or 0
    return {"historical_runs": history_pruned, "plan_predictions": plans_pruned}
