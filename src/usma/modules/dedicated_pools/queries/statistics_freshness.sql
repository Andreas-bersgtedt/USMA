-- v2: statistics freshness for dedicated SQL pools.
-- Stale statistics (last_updated more than ~14 days old, or row-modification rate
-- above 20%) often surface during migration as bad query plans. Running
-- UPDATE STATISTICS before the cut-over is a cheap win.
SELECT
    s.name                                   AS schema_name,
    t.name                                   AS table_name,
    st.name                                  AS stat_name,
    st.user_created,
    st.auto_created,
    sp.last_updated,
    sp.rows,
    sp.rows_sampled,
    sp.modification_counter,
    sp.unfiltered_rows,
    DATEDIFF(day, sp.last_updated, SYSUTCDATETIME()) AS days_since_update
FROM sys.stats st
JOIN sys.tables  t ON t.object_id = st.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
CROSS APPLY sys.dm_db_stats_properties(st.object_id, st.stats_id) sp
WHERE sp.last_updated IS NOT NULL
ORDER BY sp.last_updated;
