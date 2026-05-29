-- v2: column-level collation audit for dedicated SQL pools.
-- Reports columns whose collation differs from the database default. These will
-- need explicit COLLATE clauses (or column re-creation) when migrating to Fabric
-- Warehouse, which uses Latin1_General_100_BIN2_UTF8 as the default.
SELECT
    s.name      AS schema_name,
    t.name      AS table_name,
    c.name      AS column_name,
    ty.name     AS data_type,
    c.max_length,
    c.collation_name,
    db.collation_name AS db_collation,
    CASE WHEN c.collation_name IS NOT NULL
              AND c.collation_name <> db.collation_name THEN 1 ELSE 0 END AS differs_from_db
FROM sys.columns c
JOIN sys.tables  t  ON t.object_id = c.object_id
JOIN sys.schemas s  ON s.schema_id = t.schema_id
JOIN sys.types   ty ON ty.user_type_id = c.user_type_id
CROSS JOIN sys.databases db
WHERE db.database_id = DB_ID()
  AND c.collation_name IS NOT NULL
ORDER BY differs_from_db DESC, s.name, t.name, c.column_id;
