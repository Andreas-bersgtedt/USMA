-- Database principals and their database-role memberships.
SELECT
    dp.name                  AS principal_name,
    dp.type_desc             AS principal_type,
    role_p.name              AS role_name
FROM sys.database_principals dp
LEFT JOIN sys.database_role_members rm
    ON rm.member_principal_id = dp.principal_id
LEFT JOIN sys.database_principals role_p
    ON role_p.principal_id = rm.role_principal_id
WHERE dp.type IN ('S','U','G','E','X','R')          -- SQL/AAD users, groups, roles
  AND dp.name NOT LIKE '##%'
  AND dp.name NOT IN ('public','guest','INFORMATION_SCHEMA','sys')
ORDER BY dp.name;
