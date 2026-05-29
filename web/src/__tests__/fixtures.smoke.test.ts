/**
 * Schema smoke test (Phase 2 DoD).
 *
 * Loads the committed fixtures under `tests/fixtures/web/` and asserts that
 * the documented top-level fields exist with the expected primitive types.
 * The aim is to fail loud if the analyzer's JSON output drifts in a way
 * the SPA would silently render as blank panels.
 *
 * Fixtures are intentionally minimal and hand-curated (not full analyzer
 * output) so they stay readable as a contract reference.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type {
  DedicatedPoolsReport,
  FabricMappingReport,
  RunDelta,
} from "../types";

const FIXTURES = resolve(__dirname, "../../../tests/fixtures/web");

function load<T>(name: string): T {
  const raw = readFileSync(resolve(FIXTURES, name), "utf-8");
  return JSON.parse(raw) as T;
}

describe("fabric_mapping.json contract", () => {
  const data = load<FabricMappingReport>("fabric_mapping.json");

  it("has the required top-level keys", () => {
    expect(Object.keys(data).length).toBeGreaterThan(0);
    expect(data.generated_at).toBeTypeOf("string");
    expect(Array.isArray(data.inputs)).toBe(true);
    expect(Array.isArray(data.recommendations)).toBe(true);
    expect(Array.isArray(data.runbook)).toBe(true);
  });

  it("readiness summary carries score + bucket + counts", () => {
    expect(data.readiness).toBeTruthy();
    expect(data.readiness?.score).toBeTypeOf("number");
    expect(["ready", "ready-with-effort", "blocked"]).toContain(
      data.readiness?.bucket,
    );
    expect(data.readiness?.counts).toBeTypeOf("object");
  });

  it("recommendations carry severity and effort", () => {
    expect(data.recommendations.length).toBeGreaterThan(0);
    for (const r of data.recommendations) {
      expect(r.id).toBeTypeOf("string");
      expect(["blocker", "warning", "info"]).toContain(r.severity);
      expect(["high", "medium", "low"]).toContain(r.effort);
    }
  });

  it("capacity_projection (when present) has SKU + CU", () => {
    if (data.capacity_projection) {
      expect(data.capacity_projection.recommended_sku).toBeTypeOf("string");
      expect(data.capacity_projection.estimated_cu).toBeTypeOf("number");
    }
  });
});

describe("dedicated_pools.json contract", () => {
  const data = load<DedicatedPoolsReport>("dedicated_pools.json");

  it("has at least one pool with inventory + code_objects", () => {
    expect(Object.keys(data).length).toBeGreaterThan(0);
    expect(data.pools.length).toBeGreaterThan(0);
    expect(data.pools[0].inventory.name).toBeTypeOf("string");
    expect(Array.isArray(data.pools[0].code_objects)).toBe(true);
  });

  it("code objects expose schema/object/type", () => {
    const co = data.pools[0].code_objects?.[0];
    expect(co).toBeTruthy();
    expect(co?.schema_name).toBeTypeOf("string");
    expect(co?.object_name).toBeTypeOf("string");
    expect(co?.object_type).toBeTypeOf("string");
  });

  it("code_objects covers procedure / view / function (not just views)", () => {
    // Regression guard: the analyzer's SQL query previously dropped
    // procedures and functions when sys.sql_modules was incomplete,
    // producing reports that contained only views. The committed
    // fixture exercises every bucket so this test fails loud if the
    // fixture is ever flattened back to a views-only shape.
    const types = new Set(
      (data.pools[0].code_objects ?? []).map((o) =>
        (o.object_type ?? "").toUpperCase(),
      ),
    );
    expect(types.has("SQL_STORED_PROCEDURE")).toBe(true);
    expect(types.has("VIEW")).toBe(true);
    expect(
      types.has("SQL_SCALAR_FUNCTION") ||
        types.has("SQL_INLINE_TABLE_VALUED_FUNCTION") ||
        types.has("SQL_TABLE_VALUED_FUNCTION"),
    ).toBe(true);
  });
});

describe("run_delta.json contract", () => {
  const data = load<RunDelta>("run_delta.json");

  it("has current_run + artifacts array", () => {
    expect(data.current_run).toBeTypeOf("string");
    expect(Array.isArray(data.artifacts)).toBe(true);
  });

  it("artifacts carry status from the documented enum", () => {
    for (const a of data.artifacts) {
      expect(["added", "removed", "changed", "unchanged"]).toContain(a.status);
      expect(a.path).toBeTypeOf("string");
      expect(a.module).toBeTypeOf("string");
    }
  });
});
