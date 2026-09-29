-- EDIT ONCE: include all jobs of active plans AND all globally queued/running jobs.
-- If an active plan's completed jobs are missing, plan_total_jobs will be incorrect.
SELECT
    CAST(t.Id AS nvarchar(140)) AS run_id,
    CAST(t.PlanId AS nvarchar(140)) AS plan_id,
    CAST(t.JobName AS nvarchar(250)) AS job_id,
    CAST(t.Status AS nvarchar(40)) AS status,
    t.StartTime AS started_at,
    t.EndTime AS finished_at,
    CAST(NULL AS datetime2) AS queued_at,
    CAST(NULL AS nvarchar(80)) AS version,
    CAST(NULL AS nvarchar(100)) AS db_type,
    CAST(NULL AS nvarchar(160)) AS vm_name,
    CAST(0 AS int) AS priority
FROM dbo.YourJobs_REPLACE_ME AS t
WHERE t.Status IN ('queued', 'running')
   OR EXISTS (
       SELECT 1 FROM dbo.YourPlans_REPLACE_ME AS p
       WHERE p.Id = t.PlanId AND p.Status IN ('queued', 'running')
   );
