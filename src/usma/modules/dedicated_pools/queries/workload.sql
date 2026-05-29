-- Workload groups & their classifier counts (Synapse dedicated SQL pool workload management).
SELECT
    wg.name                                     AS name,
    ISNULL(c.classifier_count, 0)               AS classifier_count,
    wg.importance                               AS importance,
    wg.min_percentage_resource                  AS min_resource_pct,
    wg.cap_percentage_resource                  AS cap_resource_pct,
    wg.request_min_resource_grant_percent       AS request_min_resource_grant_pct
FROM sys.workload_management_workload_groups wg
LEFT JOIN (
    SELECT workload_group_id, COUNT(*) AS classifier_count
    FROM sys.workload_management_workload_classifiers
    GROUP BY workload_group_id
) c ON c.workload_group_id = wg.group_id
ORDER BY wg.name;
