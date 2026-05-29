-- Dump recent workload commands from the dedicated-pool exec-requests
-- DMV for in-process parsing by the analyzer.
--
-- We deliberately do **not** try to identify referenced tables in T-SQL
-- here. The old LIKE-based approach against ``sys.dm_pdw_sql_requests``
-- was both unreliable (it sees the distributed DMS step text, not the
-- user's submitted statement) and quadratic. Instead we pull the raw
-- submitted text from ``sys.dm_pdw_exec_requests`` and parse it with
-- sqlglot on the client. That:
--   * sees the full user statement (incl. stored-procedure bodies that
--     were executed from EXEC ... calls);
--   * recognises 1-/2-/3-part names, quoted identifiers, comments and
--     CTEs without bespoke string heuristics;
--   * scales linearly in client memory rather than O(commands x tables)
--     on the SQL side.
--
-- The DMV is a rolling buffer (minutes-to-hours on a busy pool), so even
-- though we time-bound at 14d here, the effective lookback is whatever
-- the buffer happens to hold. The collector layers a per-pool on-disk
-- cache on top to extend visibility across runs.
--
-- Output contract:
--   request_id          NVARCHAR
--   session_id          NVARCHAR
--   submit_time         ISO-8601 UTC string (parsable in Python)
--   total_elapsed_time  BIGINT (milliseconds)
--   status              NVARCHAR
--   command             NVARCHAR(MAX)  -- the user-submitted text

SELECT TOP (5000)
       r.request_id,
       r.session_id,
       CONVERT(varchar(33), r.submit_time, 126) AS submit_time,
       r.total_elapsed_time,
       r.status,
       r.command
FROM   sys.dm_pdw_exec_requests AS r
WHERE  r.command IS NOT NULL
  AND  LEN(r.command) > 0
  AND  r.submit_time >= DATEADD(DAY, -14, SYSUTCDATETIME())
  -- Filter obvious tool / system noise. The Python parser handles the rest.
  AND  r.command NOT LIKE 'SELECT @@%'
  AND  r.command NOT LIKE '%sys.dm[_]pdw[_]%'
  AND  r.command NOT LIKE 'EXEC %sp[_]help%'
  AND  r.command NOT LIKE '%INFORMATION[_]SCHEMA.%'
ORDER BY r.submit_time DESC
OPTION (LABEL = 'sma:workload_commands');
