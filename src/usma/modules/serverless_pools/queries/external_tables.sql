SELECT
    s.name           AS schema_name,
    et.name          AS table_name,
    eds.name         AS data_source,
    eff.name         AS file_format,
    et.location      AS location
FROM sys.external_tables et
JOIN sys.schemas s              ON s.schema_id = et.schema_id
LEFT JOIN sys.external_data_sources eds ON eds.data_source_id = et.data_source_id
LEFT JOIN sys.external_file_formats eff ON eff.file_format_id = et.file_format_id
ORDER BY s.name, et.name;
