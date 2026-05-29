import { describe, expect, it } from "vitest";

import { moduleProvenance } from "../components/Provenance";
import type { RunMeta } from "../api/loader";

function meta(modules: RunMeta["modules"], extra: Partial<RunMeta> = {}): RunMeta {
  return {
    id: "20260512T100000Z-deadbeef",
    label: null,
    status: "ok",
    started_at: "2026-05-12T10:00:00Z",
    finished_at: "2026-05-12T10:00:05Z",
    config_hash: "h",
    modules,
    readiness_score: null,
    errors_count: 0,
    ...extra,
  };
}

describe("moduleProvenance", () => {
  it("returns carried=false when meta is null", () => {
    expect(moduleProvenance(null, "storage")).toEqual({
      carried: false, sourceStartedAt: null, sourceRunId: null,
    });
  });

  it("returns carried=false when the module ran in this run", () => {
    const m = meta([{ name: "storage", state: "ok" }]);
    expect(moduleProvenance(m, "storage").carried).toBe(false);
  });

  it("returns carried=false for unknown modules", () => {
    const m = meta([{ name: "storage", state: "ok" }]);
    expect(moduleProvenance(m, "not_a_module").carried).toBe(false);
  });

  it("returns carried provenance when state == carried", () => {
    const m = meta([
      { name: "storage", state: "ok" },
      {
        name: "spark_pools",
        state: "carried",
        carried_from_run_id: "20260511T100000Z-aaaaaaaa",
        carried_from_started_at: "2026-05-11T10:00:00Z",
      },
    ]);
    const prov = moduleProvenance(m, "spark_pools");
    expect(prov.carried).toBe(true);
    expect(prov.sourceRunId).toBe("20260511T100000Z-aaaaaaaa");
    expect(prov.sourceStartedAt).toBe("2026-05-11T10:00:00Z");
  });
});
