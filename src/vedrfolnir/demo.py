"""Deterministic SYNTHETIC data for first-run demonstrations only."""
import math
import random
from datetime import timedelta
from sqlalchemy import select, func
from .db import HistoricalRun, CurrentJob, CurrentPlan, Prediction, TrainingMarker, utcnow
from .learning import PlanRegressor, MODEL_ID
from .features import quantile

JOB_TYPES = ["checkout", "dependencies", "compile", "lint", "package", "artifact_upload",
             "container_build", "publish", "deploy", "security_scan", "index", "cleanup"]
VERSIONS = ["4.2", "4.3", "5.0", "5.1"]
DATABASES = ["postgresql", "mssql", "oracle"]


def seed_demo(session_factory):
    with session_factory() as s:
        if (s.scalar(select(func.count(HistoricalRun.run_id))) or 0) > 0:
            return False
    rng = random.Random(123456)
    now = utcnow()
    with session_factory.begin() as s:
        rows = []
        for n in range(2800):
            job = rng.choice(JOB_TYPES)
            version = rng.choice(VERSIONS)
            db = rng.choice(DATABASES)
            job_weight = JOB_TYPES.index(job) + 1
            version_factor = 1.3 if version == "5.1" and job in {"dependencies", "container_build"} else 1.0
            db_factor = 1.25 if db == "oracle" else 1.0
            duration = rng.lognormvariate(math.log(5 + job_weight * 3), 0.36) * db_factor * version_factor
            ended = now - timedelta(minutes=rng.uniform(70, 24 * 60 * 180))
            rows.append(HistoricalRun(
                run_id=f"historical-{n:06d}", plan_id=f"hist-plan-{n // 15:04d}",
                job_id=job, status=rng.choices(["passed", "failed", "timed_out"], [90, 8, 2])[0],
                started_at=ended-timedelta(minutes=duration), finished_at=ended,
                duration_seconds=duration*60, version=version, db_type=db,
                vm_name=f"vm-{rng.randint(1, 10):02d}",
            ))
        s.add_all(rows)

    # Synthetic completed plan snapshots to demonstrate delayed online learning.
    with session_factory.begin() as s:
        reg = PlanRegressor(s)
        previous_residuals = []
        for n in range(110):
            waiting = rng.randint(1, 45)
            running = rng.randint(1, 16)
            total = running + waiting + rng.randint(0, 60)
            work_h = (waiting * rng.uniform(0.25, 0.8) + running * 0.22)
            qh = waiting * rng.uniform(0.8, 1.9)
            features = {
                "plan_elapsed_hours": rng.uniform(0, 1.1),
                "plan_total_jobs": float(total),
                "plan_completed_count": float(total - running - waiting),
                "plan_running_count": float(running),
                "plan_queued_count": float(waiting),
                "plan_completed_fraction": (total - running - waiting) / total,
                "plan_queued_work_hours": work_h,
                "plan_running_work_hours": running * 0.2,
                "plan_longest_remaining_hours": 0.6,
                "global_queue_count": rng.uniform(30, 300),
                "global_running_count": rng.uniform(100, 420),
                "global_queue_work_hours": qh,
                "global_running_work_hours": rng.uniform(8, 60),
                "higher_priority_queued_work_hours": rng.uniform(0, 12),
                "global_vm_count": 10.0,
                "global_oldest_wait_hours": rng.uniform(.1, 2),
                "recent_finished_jobs_hour": rng.uniform(70, 290),
                "plan_priority": 0.0,
                "plan_active_jobs_without_matching_history": 0.0,
            }
            baseline = max(25., work_h*60 / 11 + qh * .3)
            actual = max(5., baseline * rng.uniform(.7, 1.35) + 0.12 * qh * 60)
            predicted, used_ml = reg.estimate(features, baseline, min_training=30)
            interval = quantile(previous_residuals, .9) if len(previous_residuals) >= 30 else None
            p90 = max(predicted, predicted + interval) if interval is not None else None
            issued = now - timedelta(days=(110-n) / 3, minutes=actual)
            p = Prediction(
                plan_id=f"demo-finished-{n:04d}", predicted_at=issued,
                phase="start", model_version=MODEL_ID, used_model=used_ml,
                baseline_minutes=baseline, predicted_minutes=predicted,
                p90_minutes=p90, features=features,
                actual_finished_at=issued + timedelta(minutes=actual),
                actual_remaining_minutes=actual, error_minutes=predicted-actual,
                outcome_status="passed",
            )
            s.add(p)
            s.flush()
            reg.learn(features, actual)
            previous_residuals.append(actual-predicted)
            s.add(TrainingMarker(plan_id=p.plan_id, phase="start", prediction_id=p.id))
        reg.save(s)

    # Live plans and global contention. Static demo by design (no real clock/scheduler).
    with session_factory.begin() as s:
        for plan_index in range(1, 7):
            plan_id = f"demo-plan-{plan_index:02d}"
            s.add(CurrentPlan(
                plan_id=plan_id, status="running",
                started_at=now - timedelta(minutes=plan_index*12), finished_at=None,
                priority=plan_index % 3,
            ))
            for n in range(27):
                status = rng.choices(["passed", "running", "queued"], [24, 33, 43])[0]
                launched = now - timedelta(minutes=rng.randint(2, 65)) if status != "queued" else None
                ended = now - timedelta(minutes=rng.randint(1, 5)) if status == "passed" else None
                if ended and launched >= ended:
                    launched = ended - timedelta(minutes=3)
                s.add(CurrentJob(
                    run_id=f"demo-p{plan_index}-j{n}", plan_id=plan_id,
                    job_id=rng.choice(JOB_TYPES), version=rng.choice(VERSIONS),
                    db_type=rng.choice(DATABASES), vm_name=f"vm-{rng.randint(1, 10):02d}",
                    status=status, priority=plan_index % 3,
                    queued_at=now-timedelta(minutes=rng.randint(10, 60)),
                    started_at=launched, finished_at=ended,
                ))
        for n in range(180):
            status = "running" if n < 90 else "queued"
            s.add(CurrentJob(
                run_id=f"demo-other-{n}", plan_id="other-plan",
                job_id=rng.choice(JOB_TYPES), status=status,
                version=rng.choice(VERSIONS), db_type=rng.choice(DATABASES),
                vm_name=f"vm-{rng.randint(1, 10):02d}" if status == "running" else "unknown",
                priority=rng.randint(0, 3),
                queued_at=now-timedelta(minutes=rng.randint(2, 95)),
                started_at=now-timedelta(minutes=rng.randint(2, 25)) if status == "running" else None,
            ))
    return True
