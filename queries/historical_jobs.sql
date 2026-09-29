-- EDIT ONCE: map your MSSQL schema to these aliases. Read-only SQL login required.
-- run_id MUST be stable and sort lexically when finished_at timestamps are equal.
-- Keep TOP and cursor predicate for bounded, lossless backfill.
SELECT TOP (%(batch_limit)s)
    CAST(t.Id AS nvarchar(140)) AS run_id,
    CAST(t.PlanId AS nvarchar(140)) AS plan_id,
    CAST(t.JobName AS nvarchar(250)) AS job_id,
    CAST(t.Status AS nvarchar(40)) AS status,
    t.StartTime AS started_at,
    t.EndTime AS finished_at,
    CAST(NULL AS nvarchar(80)) AS version,
    CAST(NULL AS nvarchar(100)) AS db_type,
    CAST(NULL AS nvarchar(160)) AS vm_name
FROM dbo.YourJobs_REPLACE_ME AS t
WHERE t.EndTime IS NOT NULL
  AND (t.EndTime > %(cursor_finished_at)s
       OR (t.EndTime = %(cursor_finished_at)s
           AND CAST(t.Id AS nvarchar(140)) > %(cursor_run_id)s))
ORDER BY t.EndTime, CAST(t.Id AS nvarchar(140));
