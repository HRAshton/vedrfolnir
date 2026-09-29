-- Active plans plus recent terminal plans for delayed outcome labels.
SELECT
  p.PlanId AS plan_id,
  p.Status AS status,
  p.StartedAt AS started_at,
  p.FinishedAt AS finished_at,
  p.Priority AS priority
FROM dbo.EtaDemoPlans AS p
WHERE p.Status IN (N'queued', N'pending', N'running')
  OR (p.FinishedAt IS NOT NULL AND p.FinishedAt >= %(finished_since)s);
