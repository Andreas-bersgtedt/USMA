-- Snapshot usage indicators for the built-in serverless SQL pool (master DB scope).
-- Note: serverless DMVs differ from dedicated pools. This is a scaffold; expand as needed.
SELECT metric, value, unit, captured_at FROM (
    SELECT 'database_count' AS metric,
           CAST(COUNT(*) AS NVARCHAR(64)) AS value,
           'count' AS unit,
           SYSUTCDATETIME() AS captured_at
    FROM sys.databases
    WHERE name <> 'master'
) m;
