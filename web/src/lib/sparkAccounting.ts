import type {
  SparkDailyUsage,
  SparkPoolRunStats,
  SparkRunRecord,
} from "../types";

export interface SparkWindowTotals {
  windowDays: number;
  runCount: number;
  succeeded: number;
  failed: number;
  inProgress: number;
  durationHours: number;
  vcoreHours: number;
  cuHours: number;
  avgDailyVcoreHours: number | null;
  avgDailyCuHours: number | null;
  steadyStateCu: number | null;
  knownUsageRunCount: number | null;
  unknownUsageRunCount: number | null;
}

export interface SparkDailyChartPoint {
  day: string;
  totalVcoreHours: number;
  perPool: Record<string, number>;
}

export interface SparkDailyChartData {
  points: SparkDailyChartPoint[];
  source: "canonical" | "legacy_interval_estimate";
  omittedRunCount: number;
}

export interface SparkSizingStatus {
  included: boolean;
  warning: string | null;
}

export function getSparkSizingStatus(options: {
  accountingBasis?: string | null;
  collectionComplete?: boolean | null;
  unknownUsageRunCount: number | null;
  avgDailyCuHours: number | null;
  steadyStateCu: number | null;
}): SparkSizingStatus {
  const legacy = !options.accountingBasis?.trim();
  if (options.collectionComplete === false) {
    return {
      included: false,
      warning: "Spark excluded from combined sizing because history collection is incomplete.",
    };
  }
  if (options.collectionComplete == null && !legacy) {
    return {
      included: false,
      warning: "Spark excluded from combined sizing because collection coverage is unknown.",
    };
  }
  if (options.unknownUsageRunCount != null && options.unknownUsageRunCount > 0) {
    return {
      included: false,
      warning: "Spark excluded from combined sizing because some runs have unknown usage.",
    };
  }
  if (!legacy && options.unknownUsageRunCount == null) {
    return {
      included: false,
      warning: "Spark excluded from combined sizing because usage completeness is unavailable.",
    };
  }
  if (options.avgDailyCuHours == null || options.steadyStateCu == null) {
    return {
      included: false,
      warning: "Spark excluded from combined sizing because complete producer daily averages are unavailable.",
    };
  }
  if (legacy && options.collectionComplete == null) {
    return {
      included: true,
      warning: "Legacy Spark estimate retained with unknown collection coverage.",
    };
  }
  return { included: true, warning: null };
}

export function filterSparkDailyUsageToWindow(
  dailyUsage: SparkDailyUsage[] | undefined,
  observationEnd: string | null | undefined,
  windowDays: number,
): SparkDailyUsage[] {
  const parsedEnd = observationEnd ? Date.parse(observationEnd) : Number.NaN;
  if (!Number.isFinite(parsedEnd) || !Number.isFinite(windowDays) || windowDays <= 0) {
    return [];
  }
  const endDay = Math.floor(parsedEnd / 86_400_000) * 86_400_000;
  const firstDay = endDay - Math.floor(windowDays) * 86_400_000;
  return (dailyUsage ?? []).filter((row) => {
    const day = parseUtcDay(row.day);
    return day != null && day >= firstDay && day < endDay;
  });
}

function sumRequired<K extends "run_count" | "succeeded" | "failed" | "in_progress" |
  "total_duration_hours" | "total_vcore_hours" | "est_cu_hours_fabric_spark">(
  windows: SparkPoolRunStats["windows"][],
  windowDays: number,
  key: K,
): number {
  return windows.reduce((sum, entries) => {
    const window = entries.find((entry) => entry.window_days === windowDays);
    return sum + (window?.[key] ?? 0);
  }, 0);
}

function sumOptionalWindowField(
  stats: SparkPoolRunStats[],
  windowDays: number,
  key: "avg_daily_vcore_hours" | "avg_daily_cu_hours" | "steady_state_cu" |
    "known_usage_run_count" | "unknown_usage_run_count",
  legacyFallback: number | null,
  allowLegacyDerivation: boolean,
): number | null {
  let total = 0;
  let anyMissing = false;
  let anyPresent = false;
  for (const stat of stats) {
    const value = stat.windows.find((entry) => entry.window_days === windowDays)?.[key];
    if (typeof value !== "number" || !Number.isFinite(value)) {
      if (value != null) return null;
      anyMissing = true;
      continue;
    }
    anyPresent = true;
    total += value;
  }
  if (anyMissing) {
    if (anyPresent) return null;
    return allowLegacyDerivation ? legacyFallback : null;
  }
  return total;
}

export function selectCommonSparkWindowDays(
  stats: SparkPoolRunStats[],
): number | null {
  if (stats.length === 0 || stats.some((stat) => stat.windows.length === 0)) return null;
  const sharedDays = stats[0].windows
    .map((window) => window.window_days)
    .filter((days, index, values) =>
      days > 0 &&
      values.indexOf(days) === index &&
      stats.every((stat) => stat.windows.some((window) => window.window_days === days)),
    );
  if (sharedDays.length === 0) return null;
  return sharedDays.sort((a, b) =>
    (a === 7 ? -1 : b === 7 ? 1 : a - b),
  )[0];
}

export function aggregateSparkWindow(
  stats: SparkPoolRunStats[],
  requestedWindowDays?: number,
  allowLegacyDerivation = true,
): SparkWindowTotals | null {
  const windowDays = requestedWindowDays ?? selectCommonSparkWindowDays(stats);
  if (windowDays == null || windowDays <= 0 ||
      stats.some((stat) => !stat.windows.some((window) => window.window_days === windowDays))) {
    return null;
  }

  const windows = stats.map((stat) => stat.windows);
  const vcoreHours = sumRequired(windows, windowDays, "total_vcore_hours");
  const cuHours = sumRequired(windows, windowDays, "est_cu_hours_fabric_spark");
  const avgDailyVcore = sumOptionalWindowField(
    stats, windowDays, "avg_daily_vcore_hours", vcoreHours / windowDays, allowLegacyDerivation,
  );
  const avgDailyCu = sumOptionalWindowField(
    stats, windowDays, "avg_daily_cu_hours", cuHours / windowDays, allowLegacyDerivation,
  );
  const steadyState = sumOptionalWindowField(
    stats, windowDays, "steady_state_cu",
    avgDailyCu == null ? null : avgDailyCu / 24,
    allowLegacyDerivation,
  );

  return {
    windowDays,
    runCount: sumRequired(windows, windowDays, "run_count"),
    succeeded: sumRequired(windows, windowDays, "succeeded"),
    failed: sumRequired(windows, windowDays, "failed"),
    inProgress: sumRequired(windows, windowDays, "in_progress"),
    durationHours: sumRequired(windows, windowDays, "total_duration_hours"),
    vcoreHours,
    cuHours,
    avgDailyVcoreHours: avgDailyVcore,
    avgDailyCuHours: avgDailyCu,
    steadyStateCu: steadyState,
    knownUsageRunCount: sumOptionalWindowField(
      stats, windowDays, "known_usage_run_count", null, false,
    ),
    unknownUsageRunCount: sumOptionalWindowField(
      stats, windowDays, "unknown_usage_run_count", null, false,
    ),
  };
}

export function sparkRunVcoreHours(run: SparkRunRecord): number | null {
  if (typeof run.vcore_hours === "number" &&
      Number.isFinite(run.vcore_hours) && run.vcore_hours >= 0) {
    return run.vcore_hours;
  }
  if (typeof run.est_cu_hours_fabric_spark === "number" &&
      Number.isFinite(run.est_cu_hours_fabric_spark) &&
      run.est_cu_hours_fabric_spark >= 0) {
    return run.est_cu_hours_fabric_spark / 0.5;
  }
  return null;
}

export function splitSparkUsageAcrossBuckets(
  start: string | number,
  end: string | number,
  amount: number,
  bucketMilliseconds: number,
  clipStart = Number.NEGATIVE_INFINITY,
  clipEnd = Number.POSITIVE_INFINITY,
): Array<{ bucketStart: string; amount: number }> {
  const startMs = typeof start === "number" ? start : Date.parse(start);
  const endMs = typeof end === "number" ? end : Date.parse(end);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) ||
      endMs <= startMs || !Number.isFinite(amount) || amount < 0 ||
      !Number.isFinite(bucketMilliseconds) || bucketMilliseconds <= 0 ||
      clipEnd <= clipStart) {
    return [];
  }

  const clippedStart = Math.max(startMs, clipStart);
  const clippedEnd = Math.min(endMs, clipEnd);
  if (clippedEnd <= clippedStart) return [];

  const duration = endMs - startMs;
  const result: Array<{ bucketStart: string; amount: number }> = [];
  let bucketStart = Math.floor(clippedStart / bucketMilliseconds) * bucketMilliseconds;
  while (bucketStart < clippedEnd) {
    const overlap = Math.max(
      0,
      Math.min(clippedEnd, bucketStart + bucketMilliseconds) -
        Math.max(clippedStart, bucketStart),
    );
    if (overlap > 0) {
      result.push({
        bucketStart: new Date(bucketStart).toISOString(),
        amount: amount * overlap / duration,
      });
    }
    bucketStart += bucketMilliseconds;
  }
  return result;
}

function parseUtcDay(day: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const timestamp = Date.parse(`${day}T00:00:00.000Z`);
  if (!Number.isFinite(timestamp) ||
      new Date(timestamp).toISOString().slice(0, 10) !== day) return null;
  return timestamp;
}

export function sparkObservationWindowDays(
  observationStart: string | null | undefined,
  observationEnd: string | null | undefined,
  fallback = 28,
): number {
  if (!observationStart || !observationEnd) return fallback;
  const start = Date.parse(observationStart);
  const end = Date.parse(observationEnd);
  const dayMilliseconds = 86_400_000;
  const days = (end - start) / dayMilliseconds;
  if (!Number.isFinite(days) || days <= 0 || !Number.isInteger(days)) return fallback;
  return days;
}

function intervalForRun(
  run: SparkRunRecord,
  now: Date,
): { start: string; end: string } | null {
  const start = run.accounting_start_at ?? run.submitted_at;
  if (!start) return null;
  const startMs = Date.parse(start);
  if (!Number.isFinite(startMs)) return null;

  let endMs = run.ended_at ? Date.parse(run.ended_at) : Number.NaN;
  if (!Number.isFinite(endMs) || endMs <= startMs) {
    if (typeof run.duration_seconds === "number" &&
        Number.isFinite(run.duration_seconds) && run.duration_seconds > 0) {
      endMs = startMs + run.duration_seconds * 1000;
    } else if (run.outcome === "in_progress") {
      endMs = now.getTime();
    }
  }
  if (!Number.isFinite(endMs) || endMs <= startMs) return null;
  return { start: new Date(startMs).toISOString(), end: new Date(endMs).toISOString() };
}

export function buildSparkDailyChartData(
  dailyUsage: SparkDailyUsage[] | undefined,
  runs: SparkRunRecord[],
  observationEnd: string | null | undefined,
  windowDays = 28,
  now = new Date(),
): SparkDailyChartData {
  const canonical = Array.isArray(dailyUsage);
  const validDays = (dailyUsage ?? [])
    .map((row) => parseUtcDay(row.day))
    .filter((day): day is number => day !== null);
  const parsedEnd = observationEnd ? Date.parse(observationEnd) : Number.NaN;
  const endReference = Number.isFinite(parsedEnd)
    ? parsedEnd
    : validDays.length > 0
      ? Math.max(...validDays) + 86_400_000
      : now.getTime();
  const endDay = Math.floor(endReference / 86_400_000) * 86_400_000;
  const firstDay = endDay - Math.max(1, windowDays) * 86_400_000;
  const clipEnd = endDay;
  const points = new Map<string, SparkDailyChartPoint>();

  for (let dayStart = firstDay; dayStart < clipEnd; dayStart += 86_400_000) {
    const day = new Date(dayStart).toISOString().slice(0, 10);
    points.set(day, { day, totalVcoreHours: 0, perPool: {} });
  }

  let omittedRunCount = 0;
  if (canonical) {
    for (const row of dailyUsage) {
      const dayStart = parseUtcDay(row.day);
      const amount = row.total_vcore_hours;
      if (dayStart == null || typeof amount !== "number" ||
          !Number.isFinite(amount) || amount < 0) continue;
      const point = points.get(new Date(dayStart).toISOString().slice(0, 10));
      if (!point) continue;
      point.totalVcoreHours += amount;
      point.perPool["All pools"] = (point.perPool["All pools"] ?? 0) + amount;
    }
  } else {
    for (const run of runs) {
      const amount = sparkRunVcoreHours(run);
      const interval = intervalForRun(run, now);
      if (amount == null || !interval) {
        omittedRunCount += 1;
        continue;
      }
      for (const bucket of splitSparkUsageAcrossBuckets(
        interval.start, interval.end, amount, 86_400_000, firstDay, clipEnd,
      )) {
        const day = bucket.bucketStart.slice(0, 10);
        const point = points.get(day);
        if (!point) continue;
        point.totalVcoreHours += bucket.amount;
        point.perPool[run.pool] = (point.perPool[run.pool] ?? 0) + bucket.amount;
      }
    }
  }

  return {
    points: Array.from(points.values()),
    source: canonical ? "canonical" : "legacy_interval_estimate",
    omittedRunCount,
  };
}

export function buildSparkHourlyUsage(
  runs: SparkRunRecord[],
  start: Date,
  end: Date,
): Array<{ bucketStart: string; pool: string; vcoreHours: number }> {
  const hourly = new Map<string, number>();
  const now = end;
  for (const run of runs) {
    const amount = sparkRunVcoreHours(run);
    const interval = intervalForRun(run, now);
    if (amount == null || !interval) continue;
    for (const bucket of splitSparkUsageAcrossBuckets(
      interval.start, interval.end, amount, 3_600_000,
      start.getTime(), end.getTime(),
    )) {
      const key = `${bucket.bucketStart}|${run.pool}`;
      hourly.set(key, (hourly.get(key) ?? 0) + bucket.amount);
    }
  }
  return Array.from(hourly.entries()).map(([key, vcoreHours]) => {
    const separator = key.lastIndexOf("|");
    return {
      bucketStart: key.slice(0, separator),
      pool: key.slice(separator + 1),
      vcoreHours,
    };
  });
}
