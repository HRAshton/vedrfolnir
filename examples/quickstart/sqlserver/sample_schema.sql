-- OPTIONAL: run manually ONLY on an isolated, disposable SQL Server test DB.
-- This fixture CREATES tables and rows. It is NEVER executed by Vedrfolnir.
-- All datetime2 timestamps are interpreted as UTC by the application.
IF OBJECT_ID(N'dbo.EtaDemoPlans', N'U') IS NULL
BEGIN
  CREATE TABLE dbo.EtaDemoPlans (
    PlanId nvarchar(140) NOT NULL PRIMARY KEY,
    Status nvarchar(40) NOT NULL,
    StartedAt datetime2 NOT NULL,
    FinishedAt datetime2 NULL,
    Priority int NOT NULL DEFAULT 0
  );
END;
IF OBJECT_ID(N'dbo.EtaDemoJobs', N'U') IS NULL
BEGIN
  CREATE TABLE dbo.EtaDemoJobs (
    RunId nvarchar(140) NOT NULL PRIMARY KEY,
    PlanId nvarchar(140) NULL,
    JobType nvarchar(250) NOT NULL,
    Status nvarchar(40) NOT NULL,
    StartedAt datetime2 NULL,
    FinishedAt datetime2 NULL,
    QueuedAt datetime2 NULL,
    Runner nvarchar(160) NULL,
    PoolId nvarchar(80) NOT NULL,
    Priority int NOT NULL DEFAULT 0
  );
END;
IF OBJECT_ID(N'dbo.EtaDemoPools', N'U') IS NULL
BEGIN
  CREATE TABLE dbo.EtaDemoPools (
    PoolId nvarchar(80) NOT NULL PRIMARY KEY,
    CapacitySlots int NULL
  );
END;
IF NOT EXISTS (SELECT 1 FROM dbo.EtaDemoPools WHERE PoolId = N'builders')
  INSERT INTO dbo.EtaDemoPools (PoolId, CapacitySlots) VALUES (N'builders', 2);
IF NOT EXISTS (SELECT 1 FROM dbo.EtaDemoPlans WHERE PlanId = N'active-001')
  INSERT INTO dbo.EtaDemoPlans (PlanId, Status, StartedAt, Priority)
  VALUES (N'active-001', N'running', '2026-09-02T10:59:00', 1);
IF NOT EXISTS (SELECT 1 FROM dbo.EtaDemoJobs WHERE RunId = N'h-001')
  INSERT INTO dbo.EtaDemoJobs (RunId, PlanId, JobType, Status, StartedAt, FinishedAt, Runner, PoolId)
  VALUES (N'h-001', N'finished-001', N'compile', N'passed',
          '2026-09-01T09:00:00', '2026-09-01T09:12:00', N'runner-a', N'builders');
IF NOT EXISTS (SELECT 1 FROM dbo.EtaDemoJobs WHERE RunId = N'h-002')
  INSERT INTO dbo.EtaDemoJobs (RunId, PlanId, JobType, Status, StartedAt, FinishedAt, Runner, PoolId)
  VALUES (N'h-002', N'finished-001', N'test', N'passed',
          '2026-09-01T09:15:00', '2026-09-01T09:21:00', N'runner-a', N'builders');
IF NOT EXISTS (SELECT 1 FROM dbo.EtaDemoJobs WHERE RunId = N'h-003')
  INSERT INTO dbo.EtaDemoJobs (RunId, PlanId, JobType, Status, StartedAt, FinishedAt, Runner, PoolId)
  VALUES (N'h-003', N'finished-002', N'compile', N'passed',
          '2026-09-01T09:30:00', '2026-09-01T09:42:00', N'runner-b', N'builders');
IF NOT EXISTS (SELECT 1 FROM dbo.EtaDemoJobs WHERE RunId = N'r-001')
  INSERT INTO dbo.EtaDemoJobs (RunId, PlanId, JobType, Status, StartedAt, FinishedAt,
                               QueuedAt, Runner, PoolId, Priority)
  VALUES (N'r-001', N'active-001', N'compile', N'passed',
          '2026-09-02T11:00:00', '2026-09-02T11:12:00',
          '2026-09-02T10:59:00', N'runner-a', N'builders', 1);
IF NOT EXISTS (SELECT 1 FROM dbo.EtaDemoJobs WHERE RunId = N'r-002')
  INSERT INTO dbo.EtaDemoJobs (RunId, PlanId, JobType, Status, StartedAt,
                               QueuedAt, Runner, PoolId, Priority)
  VALUES (N'r-002', N'active-001', N'test', N'running',
          '2026-09-02T11:13:00', '2026-09-02T11:12:00', N'runner-a', N'builders', 1);
IF NOT EXISTS (SELECT 1 FROM dbo.EtaDemoJobs WHERE RunId = N'r-003')
  INSERT INTO dbo.EtaDemoJobs (RunId, PlanId, JobType, Status, QueuedAt, Runner, PoolId, Priority)
  VALUES (N'r-003', N'active-001', N'package', N'queued',
          '2026-09-02T11:14:00', N'runner-b', N'builders', 1);
