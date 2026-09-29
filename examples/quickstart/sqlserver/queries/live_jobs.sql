-- Include completed jobs of active plans and all globally queued/running jobs.
SELECT
  j.RunId AS run_id,
  j.PlanId AS plan_id,
  j.JobType AS job_id,
  j.Status AS status,
  j.StartedAt AS started_at,
  j.FinishedAt AS finished_at,
  j.QueuedAt AS queued_at,
  j.Runner AS vm_name,
  j.PoolId AS sched_pool_id,
  j.Priority AS priority
FROM dbo.EtaDemoJobs AS j
WHERE j.Status IN (N'queued', N'pending', N'running')
  OR EXISTS (
    SELECT 1 FROM dbo.EtaDemoPlans AS p
    WHERE p.PlanId = j.PlanId AND p.Status IN (N'queued', N'pending', N'running')
  );
