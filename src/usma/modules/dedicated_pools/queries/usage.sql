-- Snapshot usage indicators sourced from Synapse dedicated SQL pool DMVs.
-- Returns a flat metric/value table consumed by the usage collector.
SELECT metric, value, unit, captured_at FROM (
    SELECT 'active_requests'                AS metric,
           CAST(COUNT(*) AS NVARCHAR(64))   AS value,
           'count'                          AS unit,
           SYSUTCDATETIME()                 AS captured_at
    FROM sys.dm_pdw_exec_requests
    WHERE status NOT IN ('Completed','Failed','Cancelled')
    UNION ALL
    SELECT 'completed_requests_24h',
           CAST(COUNT(*) AS NVARCHAR(64)),
           'count',
           SYSUTCDATETIME()
    FROM sys.dm_pdw_exec_requests
    WHERE submit_time >= DATEADD(HOUR, -24, SYSUTCDATETIME())
    UNION ALL
    SELECT 'failed_requests_24h',
           CAST(COUNT(*) AS NVARCHAR(64)),
           'count',
           SYSUTCDATETIME()
    FROM sys.dm_pdw_exec_requests
    WHERE submit_time >= DATEADD(HOUR, -24, SYSUTCDATETIME())
      AND status = 'Failed'
    UNION ALL
    SELECT 'avg_query_duration_ms_24h',
           CAST(AVG(CAST(total_elapsed_time AS BIGINT)) AS NVARCHAR(64)),
           'ms',
           SYSUTCDATETIME()
    FROM sys.dm_pdw_exec_requests
    WHERE submit_time >= DATEADD(HOUR, -24, SYSUTCDATETIME())
    UNION ALL
    SELECT 'open_sessions',
           CAST(COUNT(*) AS NVARCHAR(64)),
           'count',
           SYSUTCDATETIME()
    FROM sys.dm_pdw_exec_sessions
    WHERE status = 'Active'
) m
OPTION (LABEL = 'sma:usage');
