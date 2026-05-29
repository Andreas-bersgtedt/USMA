-- Aggregate data processed by serverless SQL over the trailing 24 hours,
-- bucketed per UTC hour. Powers the 24h companion chart on the dashboard
-- alongside the 28-day ``data_processed.sql`` rollup. Same status filter
-- and column shape, just a finer time bucket.
-- Source: sys.dm_exec_requests_history.
SELECT
    DATEADD(HOUR, DATEDIFF(HOUR, 0, start_time), 0) AS hour,
    COUNT(*)                                         AS request_count,
    SUM(CAST(data_processed_mb AS BIGINT))           AS data_processed_mb,
    SUM(
        CAST(DATEDIFF(SECOND, start_time, ISNULL(end_time, start_time)) AS BIGINT)
    )                                                AS duration_seconds,
    SUM(
        CAST(ISNULL(data_processed_mb, 0) AS BIGINT)
      * CAST(DATEDIFF(SECOND, start_time, ISNULL(end_time, start_time)) AS BIGINT)
    )                                                AS mb_seconds
FROM sys.dm_exec_requests_history
WHERE start_time > DATEADD(HOUR, -24, SYSUTCDATETIME())
  AND status = 'Completed'
GROUP BY DATEADD(HOUR, DATEDIFF(HOUR, 0, start_time), 0)
ORDER BY hour DESC;
