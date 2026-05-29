SELECT
    s.name AS schema_name,
    t.name AS table_name,
    i.name AS index_name,
    CASE i.type
        WHEN 0 THEN 'HEAP'
        WHEN 1 THEN 'CLUSTERED'
        WHEN 2 THEN 'NONCLUSTERED'
        WHEN 5 THEN 'CCI'
        WHEN 6 THEN 'NCCI'
        ELSE CAST(i.type AS VARCHAR(20))
    END                              AS index_type,
    CAST(i.is_unique AS INT)         AS is_unique,
    CAST(i.is_primary_key AS INT)    AS is_primary_key,
    -- v2: leading key column (used by distribution_advisor)
    (
        SELECT TOP 1 c.name
        FROM sys.index_columns ic
        JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
        WHERE ic.object_id = i.object_id
          AND ic.index_id = i.index_id
          AND ic.is_included_column = 0
        ORDER BY ic.key_ordinal
    )                                AS first_key_column
FROM sys.indexes i
JOIN sys.tables  t ON t.object_id = i.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE i.is_hypothetical = 0
ORDER BY s.name, t.name, i.index_id;
