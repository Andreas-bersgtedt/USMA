-- Catalog of resolvable user tables + views in the current dedicated pool.
-- Used by the workload parser to:
--   * discard sqlglot ``Table`` nodes that are actually CTE aliases or
--     temp objects we can't migrate;
--   * resolve 1-part names (``SELECT * FROM fact_sales``) to a concrete
--     schema when exactly one schema owns that name.
--
-- Output contract:
--   schema_name  NVARCHAR
--   object_name  NVARCHAR
--   object_type  NVARCHAR  -- "table" | "view"

SELECT TABLE_SCHEMA AS schema_name,
       TABLE_NAME   AS object_name,
       CASE TABLE_TYPE WHEN 'VIEW' THEN 'view' ELSE 'table' END AS object_type
FROM   INFORMATION_SCHEMA.TABLES
WHERE  TABLE_TYPE IN ('BASE TABLE', 'VIEW')
OPTION (LABEL = 'sma:workload_catalog');
