SELECT
    s.name        AS schema_name,
    COUNT(o.object_id) AS object_count
FROM sys.schemas s
LEFT JOIN sys.objects o
    ON o.schema_id = s.schema_id
   AND o.type IN ('U','V','P','FN','TF','IF','SN')
WHERE s.name NOT IN ('sys','INFORMATION_SCHEMA','db_owner','db_accessadmin','db_securityadmin',
                     'db_ddladmin','db_backupoperator','db_datareader','db_datawriter',
                     'db_denydatareader','db_denydatawriter','guest')
GROUP BY s.name
ORDER BY s.name;
