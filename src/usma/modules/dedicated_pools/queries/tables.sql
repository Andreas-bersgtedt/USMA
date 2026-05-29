-- Tables with distribution policy, partitioning, row counts and storage (Synapse dedicated SQL pool DMVs).
WITH dist AS (
    SELECT
        t.object_id,
        tdp.distribution_policy_desc        AS distribution_policy,
        cdp.name                            AS distribution_column
    FROM sys.tables t
    JOIN sys.pdw_table_distribution_properties tdp
        ON tdp.object_id = t.object_id
    LEFT JOIN sys.pdw_column_distribution_properties cdp_props
        ON cdp_props.object_id = t.object_id AND cdp_props.distribution_ordinal = 1
    LEFT JOIN sys.columns cdp
        ON cdp.object_id = cdp_props.object_id AND cdp.column_id = cdp_props.column_id
),
size_agg AS (
    -- Aggregate node-level partition stats and map back to the user
    -- table object_id via sys.pdw_nodes_tables + sys.pdw_table_mappings.
    -- Joining sys.dm_pdw_nodes_db_partition_stats.object_id directly
    -- to sys.tables.object_id is incorrect (the former is the node-local
    -- physical table object_id) and produces NULL row_count / sizes
    -- for every user table.
    SELECT
        tm.object_id                                          AS object_id,
        SUM(ps.reserved_page_count)  * 8.0 / 1024.0           AS reserved_space_mb,
        SUM(ps.used_page_count)      * 8.0 / 1024.0           AS data_space_mb,
        SUM(ps.reserved_page_count - ps.used_page_count) * 8.0 / 1024.0 AS index_space_mb,
        SUM(ps.row_count)                                     AS row_count
    FROM sys.dm_pdw_nodes_db_partition_stats ps
    INNER JOIN sys.pdw_nodes_tables nt
        ON ps.object_id = nt.object_id
       AND ps.pdw_node_id = nt.pdw_node_id
    INNER JOIN sys.pdw_table_mappings tm
        ON nt.name = tm.physical_name
    GROUP BY tm.object_id
),
part_agg AS (
    SELECT object_id, COUNT(DISTINCT partition_number) AS partition_count
    FROM sys.partitions
    GROUP BY object_id
),
idx_type AS (
    SELECT
        i.object_id,
        MAX(CASE WHEN i.type = 5 THEN 'CCI'
                 WHEN i.type = 6 THEN 'NCCI'
                 WHEN i.type = 1 THEN 'CLUSTERED'
                 WHEN i.type = 2 THEN 'NONCLUSTERED'
                 WHEN i.type = 0 THEN 'HEAP'
                 ELSE CAST(i.type AS VARCHAR(20)) END) AS index_type
    FROM sys.indexes i
    WHERE i.is_hypothetical = 0
    GROUP BY i.object_id
)
SELECT
    s.name                                  AS schema_name,
    t.name                                  AS table_name,
    d.distribution_policy,
    d.distribution_column,
    CASE WHEN p.partition_count > 1 THEN 1 ELSE 0 END AS is_partitioned,
    ISNULL(p.partition_count, 1)            AS partition_count,
    sa.row_count                            AS row_count,
    CAST(sa.reserved_space_mb AS DECIMAL(18,2)) AS reserved_space_mb,
    CAST(sa.data_space_mb     AS DECIMAL(18,2)) AS data_space_mb,
    CAST(sa.index_space_mb    AS DECIMAL(18,2)) AS index_space_mb,
    it.index_type
FROM sys.tables t
JOIN sys.schemas s   ON s.schema_id = t.schema_id
LEFT JOIN dist d     ON d.object_id = t.object_id
LEFT JOIN size_agg sa ON sa.object_id = t.object_id
LEFT JOIN part_agg p  ON p.object_id  = t.object_id
LEFT JOIN idx_type it ON it.object_id = t.object_id
ORDER BY s.name, t.name
OPTION (LABEL = 'sma:tables');
