-- Read-only pagination, stable run IDs and deterministic timestamp/run-ID order.
SELECT TOP (%(batch_limit)s)
  j.RunId AS run_id,
  j.PlanId AS plan_id,
  j.JobType AS job_id,
  j.Status AS status,
  j.StartedAt AS started_at,
  j.FinishedAt AS finished_at,
  j.Runner AS vm_name,
  j.PoolId AS sched_pool_id
FROM dbo.EtaDemoJobs AS j
WHERE j.FinishedAt IS NOT NULL
  AND j.Status IN (N'passed', N'failed', N'timed_out')
  AND (j.FinishedAt > %(cursor_finished_at)s
       OR (j.FinishedAt = %(cursor_finished_at)s
           AND j.RunId > %(cursor_run_id)s))
ORDER BY j.FinishedAt, j.RunId;
