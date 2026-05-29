-- Top dedicated SQL pool requests by total elapsed time (last 14 days).
-- Source: sys.dm_pdw_exec_requests on the dedicated SQL pool database,
-- joined to sys.dm_pdw_exec_sessions for the login (login_name does not
-- exist on the requests DMV; it lives on the sessions DMV).
-- total_elapsed_time is the canonical CPU/time proxy exposed by this DMV;
-- Synapse dedicated does not expose a separate worker_time on the request,
-- so elapsed time is the de-facto "CPU cost" surfaced in the portal too.
-- The DMV is a rolling buffer (~10 000 requests) so older entries may
-- already be evicted; we still filter by submit_time so the time-window
-- claim in the UI is honest.
SELECT TOP (100)
    r.request_id                                   AS request_id,
    r.session_id                                   AS session_id,
    r.status                                       AS status,
    r.submit_time                                  AS submit_time,
    r.start_time                                   AS start_time,
    r.end_time                                     AS end_time,
    CAST(r.total_elapsed_time AS BIGINT)           AS total_elapsed_ms,
    r.resource_class                               AS resource_class,
    r.importance                                   AS importance,
    r.[label]                                      AS query_label,
    r.error_id                                     AS error_id,
    s.login_name                                   AS login_name,
    r.[command]                                    AS command_text
FROM sys.dm_pdw_exec_requests AS r
LEFT JOIN sys.dm_pdw_exec_sessions AS s
       ON s.session_id = r.session_id
WHERE r.submit_time >= DATEADD(DAY, -14, SYSUTCDATETIME())
  AND r.[command] IS NOT NULL
ORDER BY r.total_elapsed_time DESC
OPTION (LABEL = 'sma:top_queries');
