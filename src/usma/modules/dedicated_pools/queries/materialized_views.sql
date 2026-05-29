-- v2: materialized view inventory for dedicated SQL pools.
-- Materialized views in Synapse are auto-maintained; in Fabric Warehouse they are
-- not yet supported. Each entry below typically becomes either a regular view +
-- scheduled refresh or a Lakehouse-backed materialized table.
SELECT
    s.name        AS schema_name,
    v.name        AS view_name,
    v.create_date,
    v.modify_date,
    OBJECT_DEFINITION(v.object_id) AS definition
FROM sys.views v
JOIN sys.schemas s ON s.schema_id = v.schema_id
WHERE EXISTS (
    SELECT 1
    FROM sys.indexes i
    WHERE i.object_id = v.object_id
      AND i.type IN (5, 6)         -- 5 = clustered columnstore, 6 = nonclustered columnstore
)
ORDER BY s.name, v.name;
