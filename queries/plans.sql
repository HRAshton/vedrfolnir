-- EDIT ONCE: include active plans and recently completed plans for labeling.
-- Note: finished_since can be very old if predictions are awaiting a terminal label.
SELECT
    CAST(p.Id AS nvarchar(140)) AS plan_id,
    CAST(p.Status AS nvarchar(40)) AS status,
    COALESCE(p.StartTime, p.CreatedAt) AS started_at,
    p.EndTime AS finished_at,
    CAST(0 AS int) AS priority
FROM dbo.YourPlans_REPLACE_ME AS p
WHERE p.Status IN ('queued', 'running')
   OR (p.EndTime IS NOT NULL AND p.EndTime >= %(finished_since)s);
