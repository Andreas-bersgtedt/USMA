-- Stored procedures, views, and user-defined functions in the dedicated pool.
-- Used by the fabric_mapping module to detect T-SQL surface gaps (MERGE, CURSOR, etc.)
-- and by the dedicated_pools module to surface the SQL-plane inventory.
--
-- We drive from sys.objects (the source of truth for object type) and LEFT
-- JOIN sys.sql_modules so we still surface procedures / functions whose
-- module body is unavailable (encrypted definition, restricted permission,
-- or Synapse catalog edge cases). Without the LEFT JOIN, an INNER JOIN to
-- sys.sql_modules silently drops rows -- producing reports that contain
-- only views even when the pool clearly has procedures.
SELECT
    s.name                AS schema_name,
    o.name                AS object_name,
    o.type_desc           AS object_type,
    sm.definition         AS definition,
    o.create_date         AS create_date,
    o.modify_date         AS modify_date,
    sm.uses_ansi_nulls    AS uses_ansi_nulls,
    sm.uses_quoted_identifier AS uses_quoted_identifier,
    CASE
        WHEN sm.definition IS NULL THEN NULL
        ELSE DATALENGTH(sm.definition) / 2
    END                   AS definition_length
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
LEFT JOIN sys.sql_modules sm ON sm.object_id = o.object_id
WHERE RTRIM(o.type) IN ('P', 'V', 'FN', 'IF', 'TF')
  AND o.is_ms_shipped = 0
ORDER BY s.name, o.name;

