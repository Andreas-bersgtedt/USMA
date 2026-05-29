-- v2 (advisory): partition-pruning hints for serverless SQL queries.
-- Joins recent serverless DMV rows against the most expensive queries to flag
-- those that scanned >> partition_count * avg-partition-size. The query is
-- intentionally conservative; results are advisory only.
SELECT TOP 200
    qs.session_id,
    qs.distributed_statement_id    AS request_id,
    qs.start_time,
    qs.end_time,
    qs.data_processed_mb,
    qs.command,
    DATEDIFF(SECOND, qs.start_time, qs.end_time) AS duration_seconds
FROM sys.dm_exec_requests_history qs
WHERE qs.start_time >= DATEADD(DAY, -30, SYSUTCDATETIME())
  AND qs.data_processed_mb IS NOT NULL
  AND qs.data_processed_mb > 1024     -- >1 GB scans only
  AND (qs.command LIKE '%OPENROWSET%' OR qs.command LIKE '%FROM%')
ORDER BY qs.data_processed_mb DESC;
