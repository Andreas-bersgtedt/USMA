import { describe, expect, it } from "vitest";

import {
  aggregateSparkWindow,
  buildSparkDailyChartData,
  buildSparkHourlyUsage,
  filterSparkDailyUsageToWindow,
  getSparkSizingStatus,
  selectCommonSparkWindowDays,
  sparkObservationWindowDays,
  splitSparkUsageAcrossBuckets,
} from "../lib/sparkAccounting";
import type { SparkPoolRunStats, SparkRunRecord } from "../types";

function stats(
  pool: string,
  windows: SparkPoolRunStats["windows"],
): SparkPoolRunStats {
  return { pool, kind: "scheduled", windows };
}

function run(overrides: Partial<SparkRunRecord> = {}): SparkRunRecord {
  return {
    livy_id: 1,
    kind: "scheduled",
    pool: "pool-a",
    outcome: "succeeded",
    submitted_at: "2026-09-27T23:30:00Z",
    ended_at: "2026-09-28T00:30:00Z",
    vcore_hours: 10,
    ...overrides,
  };
}

describe("Spark accounting presentation helpers", () => {
  it("chooses one shared window, preferring seven days when available", () => {
    const rows = [
      stats("a", [
        { window_days: 7, run_count: 1, succeeded: 1, failed: 0, in_progress: 0, total_duration_hours: 1, total_vcore_hours: 10, est_cu_hours_fabric_spark: 5 },
        { window_days: 14, run_count: 2, succeeded: 2, failed: 0, in_progress: 0, total_duration_hours: 2, total_vcore_hours: 20, est_cu_hours_fabric_spark: 10 },
      ]),
      stats("b", [
        { window_days: 7, run_count: 1, succeeded: 1, failed: 0, in_progress: 0, total_duration_hours: 1, total_vcore_hours: 6, est_cu_hours_fabric_spark: 3 },
        { window_days: 28, run_count: 2, succeeded: 2, failed: 0, in_progress: 0, total_duration_hours: 2, total_vcore_hours: 30, est_cu_hours_fabric_spark: 15 },
      ]),
    ];
    expect(selectCommonSparkWindowDays(rows)).toBe(7);
    expect(aggregateSparkWindow(rows)).toMatchObject({
      windowDays: 7,
      runCount: 2,
      vcoreHours: 16,
      cuHours: 8,
      avgDailyVcoreHours: 16 / 7,
      avgDailyCuHours: 8 / 7,
      steadyStateCu: 8 / 7 / 24,
    });
  });

  it("returns no aggregate instead of mixing non-overlapping windows", () => {
    const rows = [
      stats("a", [{ window_days: 7, run_count: 1, succeeded: 1, failed: 0, in_progress: 0, total_duration_hours: 1, total_vcore_hours: 10, est_cu_hours_fabric_spark: 5 }]),
      stats("b", [{ window_days: 14, run_count: 1, succeeded: 1, failed: 0, in_progress: 0, total_duration_hours: 1, total_vcore_hours: 10, est_cu_hours_fabric_spark: 5 }]),
    ];
    expect(aggregateSparkWindow(rows)).toBeNull();
  });

  it("uses the observation span when no common eligible run window exists", () => {
    expect(sparkObservationWindowDays(
      "2026-09-25T00:00:00Z",
      "2026-09-28T00:00:00Z",
    )).toBe(3);
    expect(sparkObservationWindowDays(null, null)).toBe(28);
  });

  it("does not fill a missing group's daily averages from mixed coverage", () => {
    const rows = [
      stats("a", [{
        window_days: 7, run_count: 1, succeeded: 1, failed: 0, in_progress: 0,
        total_duration_hours: 1, total_vcore_hours: 10, est_cu_hours_fabric_spark: 5,
        avg_daily_vcore_hours: 2, avg_daily_cu_hours: 1, steady_state_cu: 1 / 24,
      }]),
      stats("b", [{
        window_days: 7, run_count: 1, succeeded: 1, failed: 0, in_progress: 0,
        total_duration_hours: 1, total_vcore_hours: 10, est_cu_hours_fabric_spark: 5,
        avg_daily_vcore_hours: null, avg_daily_cu_hours: null, steady_state_cu: null,
      }]),
    ];
    const result = aggregateSparkWindow(rows);
    expect(result?.avgDailyVcoreHours).toBeNull();
    expect(result?.avgDailyCuHours).toBeNull();
    expect(result?.steadyStateCu).toBeNull();
  });

  it("excludes incomplete and unknown Spark estimates from steady-state sizing", () => {
    const base = {
      accountingBasis: "fixed_shape_runtime_v1",
      collectionComplete: true,
      unknownUsageRunCount: 0,
      avgDailyCuHours: 24,
      steadyStateCu: 1,
    };
    expect(getSparkSizingStatus({
      ...base,
      collectionComplete: false,
    })).toMatchObject({ included: false });
    expect(getSparkSizingStatus({
      ...base,
      collectionComplete: null,
    })).toMatchObject({ included: false });
    expect(getSparkSizingStatus({
      ...base,
      unknownUsageRunCount: 1,
    })).toMatchObject({ included: false });
    expect(getSparkSizingStatus({
      ...base,
      avgDailyCuHours: null,
      steadyStateCu: null,
    })).toMatchObject({ included: false });
    expect(getSparkSizingStatus(base)).toEqual({ included: true, warning: null });
  });

  it("retains legacy sizing with an explicit coverage warning", () => {
    expect(getSparkSizingStatus({
      accountingBasis: null,
      collectionComplete: null,
      unknownUsageRunCount: null,
      avgDailyCuHours: 12,
      steadyStateCu: 0.5,
    })).toEqual({
      included: true,
      warning: "Legacy Spark estimate retained with unknown collection coverage.",
    });
  });

  it("splits usage proportionally at UTC midnight and clips without losing allocation logic", () => {
    const buckets = splitSparkUsageAcrossBuckets(
      "2026-09-27T23:30:00Z",
      "2026-09-28T00:30:00Z",
      10,
      86_400_000,
    );
    expect(buckets).toEqual([
      { bucketStart: "2026-09-27T00:00:00.000Z", amount: 5 },
      { bucketStart: "2026-09-28T00:00:00.000Z", amount: 5 },
    ]);
    const clipped = splitSparkUsageAcrossBuckets(
      "2026-09-27T23:30:00Z",
      "2026-09-28T00:30:00Z",
      10,
      3_600_000,
      Date.parse("2026-09-28T00:00:00Z"),
      Date.parse("2026-09-28T00:30:00Z"),
    );
    expect(clipped.reduce((sum, bucket) => sum + bucket.amount, 0)).toBe(5);
  });

  it("uses canonical daily rows instead of attributing the whole run to submission day", () => {
    const result = buildSparkDailyChartData(
      [
        { day: "2026-09-27", total_vcore_hours: 3, est_cu_hours_fabric_spark: 1.5 },
        { day: "2026-09-28", total_vcore_hours: 7, est_cu_hours_fabric_spark: 3.5 },
        { day: "2026-09-29", total_vcore_hours: 11, est_cu_hours_fabric_spark: 5.5 },
      ],
      [run()],
      "2026-09-29T00:00:00Z",
      2,
      new Date("2026-09-29T12:00:00Z"),
    );
    expect(result.source).toBe("canonical");
    expect(result.points.map((point) => point.totalVcoreHours)).toEqual([3, 7]);
  });

  it("filters canonical daily usage to the half-open observation window", () => {
    const rows = [
      { day: "2026-09-26", total_vcore_hours: 1, est_cu_hours_fabric_spark: 0.5 },
      { day: "2026-09-27", total_vcore_hours: 2, est_cu_hours_fabric_spark: 1 },
      { day: "2026-09-28", total_vcore_hours: 3, est_cu_hours_fabric_spark: 1.5 },
      { day: "2026-09-29", total_vcore_hours: 4, est_cu_hours_fabric_spark: 2 },
    ];
    expect(filterSparkDailyUsageToWindow(
      rows,
      "2026-09-29T00:00:00Z",
      2,
    ).map((row) => row.day)).toEqual(["2026-09-27", "2026-09-28"]);
  });

  it("includes the latest canonical day when the observation end is unavailable", () => {
    const result = buildSparkDailyChartData(
      [{ day: "2026-09-28", total_vcore_hours: 3, est_cu_hours_fabric_spark: 1.5 }],
      [],
      null,
      1,
      new Date("2026-09-30T12:00:00Z"),
    );
    expect(result.points.map((point) => point.totalVcoreHours)).toEqual([3]);
  });

  it("interval-splits legacy daily and hourly values across boundaries", () => {
    const daily = buildSparkDailyChartData(
      undefined,
      [run()],
      "2026-09-29T00:00:00Z",
      2,
      new Date("2026-09-29T12:00:00Z"),
    );
    expect(daily.source).toBe("legacy_interval_estimate");
    expect(daily.points.map((point) => point.totalVcoreHours)).toEqual([5, 5]);

    const hourly = buildSparkHourlyUsage(
      [run()],
      new Date("2026-09-27T23:00:00Z"),
      new Date("2026-09-28T01:00:00Z"),
    );
    expect(hourly.map((point) => point.vcoreHours)).toEqual([5, 5]);
    expect(hourly.map((point) => point.bucketStart)).toEqual([
      "2026-09-27T23:00:00.000Z",
      "2026-09-28T00:00:00.000Z",
    ]);
  });

  it("does not invent a daily bucket for a run without a usable interval", () => {
    const result = buildSparkDailyChartData(
      undefined,
      [run({ ended_at: null, duration_seconds: null, outcome: "failed" })],
      "2026-09-28T23:59:59Z",
      1,
      new Date("2026-09-29T12:00:00Z"),
    );
    expect(result.omittedRunCount).toBe(1);
    expect(result.points[0].totalVcoreHours).toBe(0);
  });
});
