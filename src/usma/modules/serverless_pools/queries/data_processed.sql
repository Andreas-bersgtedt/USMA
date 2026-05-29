-- Aggregate data processed by serverless SQL (last 30 days), per UTC day.
-- Used to estimate cost: serverless is billed per TB of data processed.
-- ``mb_seconds`` (= sum of data_processed_mb * duration_seconds) powers the
-- Fabric SQL Analytics Endpoint CU projection in
-- ``serverless_pools/cu_estimate.py``: it captures full-coverage data x time
-- without shipping per-query rows.
-- Source: sys.dm_exec_requests_history.
SELECT
    CAST(start_time AS DATE)      AS day,
    COUNT(*)                       AS request_count,
    SUM(CAST(data_processed_mb AS BIGINT)) AS data_processed_mb,
    SUM(
        CAST(DATEDIFF(SECOND, start_time, ISNULL(end_time, start_time)) AS BIGINT)
    )                              AS duration_seconds,
    SUM(
        CAST(ISNULL(data_processed_mb, 0) AS BIGINT)
      * CAST(DATEDIFF(SECOND, start_time, ISNULL(end_time, start_time)) AS BIGINT)
    )                              AS mb_seconds
FROM sys.dm_exec_requests_history
WHERE start_time > DATEADD(DAY, -30, SYSUTCDATETIME())
  AND status = 'Completed'
GROUP BY CAST(start_time AS DATE)
ORDER BY day DESC;
