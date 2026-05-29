-- Parameter signatures for stored procedures and functions.
-- Used by the dedicated_pools module to enrich each CodeObject with its
-- input/output parameter list. View parameters are intentionally excluded.
SELECT
    s.name                         AS schema_name,
    o.name                         AS object_name,
    o.type_desc                    AS object_type,
    COALESCE(p.name, '')           AS parameter_name,
    TYPE_NAME(p.user_type_id)      AS data_type,
    p.max_length                   AS max_length,
    p.is_output                    AS is_output,
    p.has_default_value            AS has_default,
    p.parameter_id                 AS ordinal
FROM sys.parameters p
JOIN sys.objects o ON o.object_id = p.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE RTRIM(o.type) IN ('P', 'FN', 'IF', 'TF')
  AND o.is_ms_shipped = 0
  AND p.parameter_id > 0  -- skip the implicit return-value row
ORDER BY s.name, o.name, p.parameter_id;
