SELECT name, collation_name, create_date
FROM sys.databases
WHERE name NOT IN ('master')
ORDER BY name;
