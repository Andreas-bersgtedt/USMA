/* =============================================================================
   generate_1000_random_tables.sql
   -----------------------------------------------------------------------------
   TEST SCRIPT - Synapse Analytics dedicated SQL pool (Synapse / APS).

   Creates 1,000 tables with randomly generated names in a dedicated test
   schema. Each table:
     * lives in schema [sma_test_random]
     * uses a Synapse table-distribution policy (HASH / ROUND_ROBIN /
       REPLICATE) and CLUSTERED COLUMNSTORE INDEX
     * has 5-12 columns with mixed types
     * is created only when the name does not already exist (idempotent)

   USE FOR LOAD / SCALE TESTING ONLY. Do NOT run against production.

   Compatibility notes for dedicated SQL pool:
     * No table variables (DECLARE @x TABLE ...)         -> use #temp tables
     * No TRY/CATCH wrapping DDL inside the same batch   -> rely on existence check
     * No cursors here                                   -> simple WHILE loop
     * NEWID() is supported

   Cleanup:
       EXEC sma_test_random.usp_drop_all;     -- helper proc created at the end
   or manually:
       DECLARE @sql NVARCHAR(MAX) = N'';
       SELECT @sql = COALESCE(@sql, N'')
                   + N'DROP TABLE ' + QUOTENAME(s.name) + N'.'
                   + QUOTENAME(t.name) + N';' + CHAR(10)
       FROM sys.tables t
       JOIN sys.schemas s ON s.schema_id = t.schema_id
       WHERE s.name = N'sma_test_random';
       EXEC sp_executesql @sql;
       DROP SCHEMA sma_test_random;

   Tunables (edit below):
       @TableCount         number of tables to create (default 1000)
       @SchemaName         target schema (default sma_test_random)
       @MinCols / @MaxCols column count range per table
   =============================================================================
*/

SET NOCOUNT ON;

DECLARE @TableCount  INT     = 1000;
DECLARE @SchemaName  SYSNAME = N'sma_test_random';
DECLARE @MinCols     INT     = 5;
DECLARE @MaxCols     INT     = 12;

-- ---------------------------------------------------------------------------
-- 0. Schema (dynamic SQL: CREATE SCHEMA must be the only statement in batch).
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = @SchemaName)
BEGIN
    DECLARE @createSchema NVARCHAR(200) =
        N'CREATE SCHEMA ' + QUOTENAME(@SchemaName);
    EXEC sp_executesql @createSchema;
END;

-- ---------------------------------------------------------------------------
-- 1. Dictionaries of name fragments. ~20 each gives 20*20*20 = 8000
--    distinct base names; the numeric suffix guarantees uniqueness.
--    Use #temp tables populated via SELECT ... UNION ALL — dedicated SQL
--    pool does NOT support multi-row INSERT ... VALUES (...), (...), ...,
--    nor the "VALUES (...), (...) AS t(c1, c2)" derived-table syntax, so
--    UNION ALL is the portable way to seed small lookup sets.
-- ---------------------------------------------------------------------------
IF OBJECT_ID('tempdb..#adj')     IS NOT NULL DROP TABLE #adj;
IF OBJECT_ID('tempdb..#noun')    IS NOT NULL DROP TABLE #noun;
IF OBJECT_ID('tempdb..#kind')    IS NOT NULL DROP TABLE #kind;
IF OBJECT_ID('tempdb..#targets') IS NOT NULL DROP TABLE #targets;

CREATE TABLE #adj  (i INT NOT NULL, w NVARCHAR(40) NOT NULL)
    WITH (DISTRIBUTION = ROUND_ROBIN, HEAP);
CREATE TABLE #noun (i INT NOT NULL, w NVARCHAR(40) NOT NULL)
    WITH (DISTRIBUTION = ROUND_ROBIN, HEAP);
CREATE TABLE #kind (i INT NOT NULL, w NVARCHAR(40) NOT NULL)
    WITH (DISTRIBUTION = ROUND_ROBIN, HEAP);

INSERT INTO #adj (i, w)
SELECT  1, N'crimson'    UNION ALL SELECT  2, N'silent'    UNION ALL
SELECT  3, N'broken'     UNION ALL SELECT  4, N'gilded'    UNION ALL
SELECT  5, N'molten'     UNION ALL SELECT  6, N'frozen'    UNION ALL
SELECT  7, N'rusted'     UNION ALL SELECT  8, N'ancient'   UNION ALL
SELECT  9, N'hollow'     UNION ALL SELECT 10, N'velvet'    UNION ALL
SELECT 11, N'amber'      UNION ALL SELECT 12, N'azure'     UNION ALL
SELECT 13, N'ivory'      UNION ALL SELECT 14, N'jade'      UNION ALL
SELECT 15, N'onyx'       UNION ALL SELECT 16, N'pale'      UNION ALL
SELECT 17, N'shimmering' UNION ALL SELECT 18, N'wandering' UNION ALL
SELECT 19, N'forgotten'  UNION ALL SELECT 20, N'twilight';

INSERT INTO #noun (i, w)
SELECT  1, N'order'        UNION ALL SELECT  2, N'invoice'      UNION ALL
SELECT  3, N'ledger'       UNION ALL SELECT  4, N'session'      UNION ALL
SELECT  5, N'visit'        UNION ALL SELECT  6, N'event'        UNION ALL
SELECT  7, N'shipment'     UNION ALL SELECT  8, N'audit'        UNION ALL
SELECT  9, N'metric'       UNION ALL SELECT 10, N'snapshot'     UNION ALL
SELECT 11, N'transaction'  UNION ALL SELECT 12, N'lineage'      UNION ALL
SELECT 13, N'feature'      UNION ALL SELECT 14, N'observation'  UNION ALL
SELECT 15, N'signal'       UNION ALL SELECT 16, N'manifest'     UNION ALL
SELECT 17, N'subscription' UNION ALL SELECT 18, N'incident'     UNION ALL
SELECT 19, N'profile'      UNION ALL SELECT 20, N'cohort';

INSERT INTO #kind (i, w)
SELECT  1, N'fact'    UNION ALL SELECT  2, N'dim'      UNION ALL
SELECT  3, N'stg'     UNION ALL SELECT  4, N'raw'      UNION ALL
SELECT  5, N'curated' UNION ALL SELECT  6, N'agg'      UNION ALL
SELECT  7, N'hist'    UNION ALL SELECT  8, N'snap'     UNION ALL
SELECT  9, N'tmp'     UNION ALL SELECT 10, N'wrk'      UNION ALL
SELECT 11, N'log'     UNION ALL SELECT 12, N'src'      UNION ALL
SELECT 13, N'tgt'     UNION ALL SELECT 14, N'ext'      UNION ALL
SELECT 15, N'archive' UNION ALL SELECT 16, N'bridge'   UNION ALL
SELECT 17, N'lookup'  UNION ALL SELECT 18, N'mapping'  UNION ALL
SELECT 19, N'audit'   UNION ALL SELECT 20, N'test';

-- ---------------------------------------------------------------------------
-- 2. Build the list of target table names (suffix = sequence -> dedupe).
--    sys.all_objects can be sparse on a fresh dedicated pool, so build a
--    deterministic numbers table via UNION ALL + cross join (10 -> 100 ->
--    10,000 rows) and TOP @TableCount off it.
-- ---------------------------------------------------------------------------
IF OBJECT_ID('tempdb..#n10')  IS NOT NULL DROP TABLE #n10;
IF OBJECT_ID('tempdb..#nums') IS NOT NULL DROP TABLE #nums;

CREATE TABLE #n10 (n INT NOT NULL)
    WITH (DISTRIBUTION = ROUND_ROBIN, HEAP);
INSERT INTO #n10 (n)
SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL
SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL
SELECT 9 UNION ALL SELECT 10;

CREATE TABLE #nums (n INT NOT NULL)
    WITH (DISTRIBUTION = ROUND_ROBIN, HEAP);
INSERT INTO #nums (n)
SELECT TOP (@TableCount)
       ROW_NUMBER() OVER (ORDER BY (SELECT 1)) AS n
FROM   #n10 a CROSS JOIN #n10 b CROSS JOIN #n10 c CROSS JOIN #n10 d;

CREATE TABLE #targets
(
    seq        INT           NOT NULL,
    table_name NVARCHAR(128) NOT NULL
)
WITH (DISTRIBUTION = ROUND_ROBIN, HEAP);

-- Materialize the random picks FIRST. Putting NEWID() / ABS(CHECKSUM(NEWID()))
-- directly into a JOIN predicate is unreliable on dedicated SQL pool: the
-- optimizer may re-evaluate the expression per row scan, so the join yields
-- 0 rows. Persist the picks in a temp table, then join on plain INT columns.
IF OBJECT_ID('tempdb..#picks') IS NOT NULL DROP TABLE #picks;
CREATE TABLE #picks
(
    seq INT NOT NULL,
    ki  INT NOT NULL,
    ai  INT NOT NULL,
    ni  INT NOT NULL
)
WITH (DISTRIBUTION = ROUND_ROBIN, HEAP);

INSERT INTO #picks (seq, ki, ai, ni)
SELECT n.n,
       (ABS(CHECKSUM(NEWID())) % 20) + 1,
       (ABS(CHECKSUM(NEWID())) % 20) + 1,
       (ABS(CHECKSUM(NEWID())) % 20) + 1
FROM   #nums n;

INSERT INTO #targets (seq, table_name)
SELECT  p.seq,
        k.w + N'_' + a.w + N'_' + nn.w + N'_'
            + RIGHT(N'0000' + CAST(p.seq AS NVARCHAR(10)), 4)
FROM    #picks p
JOIN    #kind k  ON k.i  = p.ki
JOIN    #adj  a  ON a.i  = p.ai
JOIN    #noun nn ON nn.i = p.ni;

-- ---------------------------------------------------------------------------
-- 3. Create-loop. Plain WHILE over the #targets temp table by sequence id.
--    Existence check keeps the script idempotent (no TRY/CATCH around DDL
--    in dedicated pool).
-- ---------------------------------------------------------------------------
DECLARE
    @i        INT,
    @maxI     INT,
    @name     NVARCHAR(128),
    @full     NVARCHAR(260),
    @sql      NVARCHAR(MAX),
    @colCount INT,
    @j        INT,
    @colName  NVARCHAR(128),
    @colType  NVARCHAR(64),
    @colsDdl  NVARCHAR(MAX),
    @dist     NVARCHAR(200),
    @distRoll INT,
    @typeRoll INT,
    @done     INT = 0,
    @skipped  INT = 0;

SELECT @i = MIN(seq), @maxI = MAX(seq) FROM #targets;

WHILE @i IS NOT NULL AND @i <= @maxI
BEGIN
    SET @name = NULL;
    SELECT @name = table_name FROM #targets WHERE seq = @i;
    SET @full = QUOTENAME(@SchemaName) + N'.' + QUOTENAME(@name);

    -- Skip if already exists (idempotency).
    IF OBJECT_ID(@full, 'U') IS NOT NULL
    BEGIN
        SET @skipped = @skipped + 1;
    END
    ELSE
    BEGIN
        -- Pick column count in [@MinCols, @MaxCols].
        SET @colCount = @MinCols
                      + ABS(CHECKSUM(NEWID())) % (@MaxCols - @MinCols + 1);

        SET @colsDdl = N'    [id]         BIGINT       NOT NULL,' + CHAR(10)
                     + N'    [created_at] DATETIME2(3) NOT NULL,' + CHAR(10);

        SET @j = 1;
        WHILE @j <= @colCount
        BEGIN
            SET @colName = N'col_' + RIGHT(N'00' + CAST(@j AS NVARCHAR(3)), 3);
            SET @typeRoll = ABS(CHECKSUM(NEWID())) % 8;
            SET @colType =
                CASE @typeRoll
                    WHEN 0 THEN N'INT'
                    WHEN 1 THEN N'BIGINT'
                    WHEN 2 THEN N'DECIMAL(18,4)'
                    WHEN 3 THEN N'NVARCHAR(100)'
                    WHEN 4 THEN N'VARCHAR(50)'
                    WHEN 5 THEN N'DATETIME2(3)'
                    WHEN 6 THEN N'BIT'
                    ELSE        N'FLOAT'
                END;
            SET @colsDdl = @colsDdl
                         + N'    ' + QUOTENAME(@colName) + N' ' + @colType + N' NULL';
            IF @j < @colCount
                SET @colsDdl = @colsDdl + N',';
            SET @colsDdl = @colsDdl + CHAR(10);
            SET @j = @j + 1;
        END;

        -- Distribution policy: ~60% HASH, ~30% ROUND_ROBIN, ~10% REPLICATE.
        SET @distRoll = ABS(CHECKSUM(NEWID())) % 10;
        IF @distRoll < 6
            SET @dist = N'DISTRIBUTION = HASH([id])';
        ELSE IF @distRoll < 9
            SET @dist = N'DISTRIBUTION = ROUND_ROBIN';
        ELSE
            SET @dist = N'DISTRIBUTION = REPLICATE';

        SET @sql =
            N'CREATE TABLE ' + @full + CHAR(10)
          + N'(' + CHAR(10)
          + @colsDdl
          + N')' + CHAR(10)
          + N'WITH (' + CHAR(10)
          + N'    ' + @dist + N',' + CHAR(10)
          + N'    CLUSTERED COLUMNSTORE INDEX' + CHAR(10)
          + N');';

        EXEC sp_executesql @sql;
        SET @done = @done + 1;

        IF @done % 100 = 0
            PRINT CONCAT(N'... created ', @done, N' tables');
    END;

    SET @i = @i + 1;
END;

PRINT CONCAT(
    N'done. created=', @done,
    N' skipped(existed)=', @skipped,
    N' total_targeted=', @TableCount,
    N' schema=', @SchemaName);

-- ---------------------------------------------------------------------------
-- 4. Cleanup helper proc. Drop-and-recreate (CREATE OR ALTER PROCEDURE is
--    not available on all dedicated pool versions).
-- ---------------------------------------------------------------------------
IF OBJECT_ID(QUOTENAME(@SchemaName) + N'.usp_drop_all', N'P') IS NOT NULL
BEGIN
    DECLARE @dropProc NVARCHAR(400) =
        N'DROP PROCEDURE ' + QUOTENAME(@SchemaName) + N'.usp_drop_all';
    EXEC sp_executesql @dropProc;
END;

-- Dedicated pool does NOT support:
--   * the variable-aggregation concat pattern (SELECT @v = @v + col FROM t)
--   * combining variable assignment with an aggregate in the same SELECT
--     (SELECT @v = STRING_AGG(...) FROM t)  -> "Incorrect syntax near '@v'"
-- Workaround: SET @v = (SELECT STRING_AGG(...) FROM t).
DECLARE @procDdl NVARCHAR(MAX) =
    N'CREATE PROCEDURE ' + QUOTENAME(@SchemaName) + N'.usp_drop_all AS' + CHAR(10)
  + N'BEGIN' + CHAR(10)
  + N'    DECLARE @s NVARCHAR(MAX);' + CHAR(10)
  + N'    SET @s = (' + CHAR(10)
  + N'        SELECT STRING_AGG(' + CHAR(10)
  + N'            CAST(N''DROP TABLE '' + QUOTENAME(sc.name) + N''.''' + CHAR(10)
  + N'                 + QUOTENAME(t.name) + N'';'' AS NVARCHAR(MAX)),' + CHAR(10)
  + N'            CHAR(10))' + CHAR(10)
  + N'        FROM sys.tables t' + CHAR(10)
  + N'        JOIN sys.schemas sc ON sc.schema_id = t.schema_id' + CHAR(10)
  + N'        WHERE sc.name = ''' + @SchemaName + N'''' + CHAR(10)
  + N'    );' + CHAR(10)
  + N'    IF @s IS NOT NULL EXEC sp_executesql @s;' + CHAR(10)
  + N'END;';
EXEC sp_executesql @procDdl;

PRINT N'helper proc ready: EXEC ' + QUOTENAME(@SchemaName) + N'.usp_drop_all;';

-- Cleanup #temp tables (script can be re-run safely).
IF OBJECT_ID('tempdb..#adj')     IS NOT NULL DROP TABLE #adj;
IF OBJECT_ID('tempdb..#noun')    IS NOT NULL DROP TABLE #noun;
IF OBJECT_ID('tempdb..#kind')    IS NOT NULL DROP TABLE #kind;
IF OBJECT_ID('tempdb..#targets') IS NOT NULL DROP TABLE #targets;
IF OBJECT_ID('tempdb..#n10')     IS NOT NULL DROP TABLE #n10;
IF OBJECT_ID('tempdb..#nums')    IS NOT NULL DROP TABLE #nums;
IF OBJECT_ID('tempdb..#picks')   IS NOT NULL DROP TABLE #picks;
GO
