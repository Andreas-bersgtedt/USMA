-- Top serverless SQL queries by data processed (last 14 days).
-- Source: sys.dm_exec_requests_history (master DB on the on-demand endpoint).
-- Returns one row per request, capped to 100 most expensive in MB scanned.
-- NOTE: the request identifier on this DMV is `distributed_statement_id`.
-- (Older docs / shared T-SQL that mention `request_id` are referring to the
-- in-flight `sys.dm_exec_requests` view, not the *history* one.)
SELECT TOP (100)
    distributed_statement_id AS request_id,
    login_name               AS login_name,
    start_time               AS start_time,
    end_time                 AS end_time,
    DATEDIFF(SECOND, start_time, ISNULL(end_time, SYSUTCDATETIME())) AS duration_seconds,
    status                   AS status,
    error_code               AS error_code,
    data_processed_mb        AS data_processed_mb,
    command                  AS command_text
FROM sys.dm_exec_requests_history
WHERE start_time > DATEADD(DAY, -14, SYSUTCDATETIME())
ORDER BY data_processed_mb DESC;
