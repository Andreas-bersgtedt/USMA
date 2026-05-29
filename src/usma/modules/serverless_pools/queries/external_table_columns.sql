-- v2: external-table column projections for serverless SQL.
-- Captures the columns referenced through OPENROWSET / external tables so the
-- migration plan can size OneLake shortcut schemas accurately.
-- NB: serverless does not expose `sys.columns` for all external tables uniformly
-- across older preview versions; the analyzer wraps this query in try/except.
SELECT
    DB_NAME()                              AS database_name,
    s.name                                 AS schema_name,
    t.name                                 AS table_name,
    c.name                                 AS column_name,
    ty.name                                AS data_type,
    c.max_length,
    c.precision,
    c.scale,
    c.is_nullable,
    c.column_id
FROM sys.external_tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.columns c ON c.object_id = t.object_id
JOIN sys.types   ty ON ty.user_type_id = c.user_type_id
ORDER BY s.name, t.name, c.column_id;
