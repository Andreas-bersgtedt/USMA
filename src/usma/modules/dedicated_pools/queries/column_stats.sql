-- v2: per-column statistics for the distribution-key advisor.
-- Pulls cardinality / null counts / max-frequency from sys.dm_db_stats_histogram
-- for the leading column of each statistics object on tables that are *not*
-- already on a HASH distribution. Heavy DMV — we accept its cost in v2 because the
-- advisor needs it.
SELECT
    s.name                                AS schema_name,
    t.name                                AS table_name,
    c.name                                AS column_name,
    ty.name                               AS data_type,
    c.max_length,
    c.is_nullable,
    sp.rows                               AS row_count,
    NULLIF(sp.unfiltered_rows, 0)         AS unfiltered_rows,
    sp.rows_sampled,
    sp.modification_counter,
    -- Approximate distinct count: sum of distinct_range_rows + RANGE_HI_KEY rows
    (
        SELECT SUM(CONVERT(BIGINT, h.distinct_range_rows)) + COUNT(*)
        FROM sys.dm_db_stats_histogram(st.object_id, st.stats_id) h
    )                                     AS distinct_count,
    (
        SELECT MAX(CONVERT(BIGINT, h.equal_rows))
        FROM sys.dm_db_stats_histogram(st.object_id, st.stats_id) h
    )                                     AS max_frequency,
    (
        SELECT SUM(CONVERT(BIGINT, h.equal_rows))
        FROM sys.dm_db_stats_histogram(st.object_id, st.stats_id) h
        WHERE h.range_high_key IS NULL
    )                                     AS null_count
FROM sys.stats st
JOIN sys.stats_columns sc ON sc.object_id = st.object_id AND sc.stats_id = st.stats_id AND sc.stats_column_id = 1
JOIN sys.columns       c  ON c.object_id  = sc.object_id AND c.column_id = sc.column_id
JOIN sys.types         ty ON ty.user_type_id = c.user_type_id
JOIN sys.tables        t  ON t.object_id = st.object_id
JOIN sys.schemas       s  ON s.schema_id = t.schema_id
CROSS APPLY sys.dm_db_stats_properties(st.object_id, st.stats_id) sp
WHERE sp.rows IS NOT NULL AND sp.rows > 0
ORDER BY s.name, t.name, c.column_id
OPTION (LABEL = 'sma:column_stats');
