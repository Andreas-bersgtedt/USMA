-- Total storage occupied by all user tables in the dedicated SQL pool.
-- Output is a single row with the aggregated MB values; the analyzer converts to GB.
--
-- IMPORTANT (Synapse Dedicated SQL Pool):
--   sys.dm_pdw_nodes_db_partition_stats.object_id is the *node-level* object_id
--   on each compute node, NOT sys.tables.object_id from the user database.
--   Joining `t.object_id = ps.object_id` directly therefore matches almost no
--   rows and reports "0 tables / 0 rows" even when the pool is full of data.
--   The correct path goes through:
--     sys.dm_pdw_nodes_db_partition_stats  (node-level partition stats)
--       -> sys.pdw_nodes_tables            (node-level table catalog)
--       -> sys.pdw_table_mappings          (maps physical_name -> user object_id)
--       -> sys.tables                      (user-visible table)
SELECT
    COUNT(DISTINCT t.object_id)                                                AS table_count,
    ISNULL(SUM(ps.row_count), 0)                                                AS row_count,
    CAST(ISNULL(SUM(ps.reserved_page_count), 0) * 8.0 / 1024.0 AS DECIMAL(20,2)) AS reserved_space_mb,
    CAST(ISNULL(SUM(ps.used_page_count),     0) * 8.0 / 1024.0 AS DECIMAL(20,2)) AS data_space_mb,
    CAST(ISNULL(SUM(ps.reserved_page_count - ps.used_page_count), 0)
              * 8.0 / 1024.0 AS DECIMAL(20,2))                                  AS index_or_unused_mb
FROM sys.dm_pdw_nodes_db_partition_stats ps
INNER JOIN sys.pdw_nodes_tables nt
    ON ps.object_id = nt.object_id
   AND ps.pdw_node_id = nt.pdw_node_id
INNER JOIN sys.pdw_table_mappings tm
    ON nt.name = tm.physical_name
INNER JOIN sys.tables t
    ON t.object_id = tm.object_id;
