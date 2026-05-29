/**
 * Phase 3 — ``matchesScope`` pure-function coverage.
 *
 * The hook (``useScopeFilter``) needs a React renderer to exercise — the
 * project's vitest env is `node` with no jsdom + no @testing-library/react
 * installed, so we cover the pure helper here and leave the hook to manual
 * UX validation (and the smoke tests that import the page).
 */
import { describe, expect, it } from "vitest";

import type { RunScope } from "../api/loader";
import { matchesScope } from "../components/ScopeFilter";

function scope(dir: string, source: RunScope["source_type"] = "synapse_workspace"): RunScope {
  return { dir, source_type: source, slug: dir.split("__")[1] ?? dir, modules: [] };
}

describe("matchesScope", () => {
  it("returns true for the all-scopes selection", () => {
    expect(matchesScope(scope("a"), { kind: "all" })).toBe(true);
    expect(matchesScope(null, { kind: "all" })).toBe(true);
  });

  it("returns true for un-scoped payloads even when a scope is picked", () => {
    expect(matchesScope(null, { kind: "scope", dir: "x" })).toBe(true);
  });

  it("only matches the picked scope dir", () => {
    expect(
      matchesScope(scope("synapse_workspace__a"), { kind: "scope", dir: "synapse_workspace__a" }),
    ).toBe(true);
    expect(
      matchesScope(scope("adf__b", "adf"), { kind: "scope", dir: "synapse_workspace__a" }),
    ).toBe(false);
  });

  it("treats dir as the unique key (source_type doesn't override)", () => {
    const adf = scope("adf__shared-name", "adf");
    const syn = scope("synapse_workspace__shared-name", "synapse_workspace");
    expect(matchesScope(adf, { kind: "scope", dir: syn.dir })).toBe(false);
    expect(matchesScope(syn, { kind: "scope", dir: syn.dir })).toBe(true);
  });
});
