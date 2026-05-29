/**
 * Phase 2.7-C — scope-aware loader.
 *
 * Exercises ``loadModulePerScope`` + ``apiListScopes`` against a mocked
 * ``fetch`` so we cover:
 *   - control-plane multi-scope: one entry per scope, ``?scope=`` propagated
 *   - control-plane single-scope (empty `/scopes`): legacy flat shape
 *   - static mode: legacy flat shape
 *   - missing run id: legacy flat shape
 *   - modules not produced for a given scope return ``payload: null``
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// vitest runs under the ``node`` environment in this project, so the
// loader's ``window`` references aren't satisfied out of the box. A
// minimal stand-in covers the surface our tests touch:
//   - location.hash read/write
//   - sessionStorage get/set/remove
const _store = new Map<string, string>();
const _win = {
  location: { hash: "", href: "http://localhost/" },
  sessionStorage: {
    getItem: (k: string) => _store.get(k) ?? null,
    setItem: (k: string, v: string) => {
      _store.set(k, v);
    },
    removeItem: (k: string) => {
      _store.delete(k);
    },
  },
};
vi.stubGlobal("window", _win);

function resetWindow(): void {
  _win.location.hash = "";
  _store.clear();
}

type FetchMock = ReturnType<typeof vi.fn>;

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { "Content-Type": "application/json" },
  });
}

function installFetch(mock: FetchMock): void {
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    mock as unknown as typeof fetch;
}

describe("loadModulePerScope — control-plane multi-scope", () => {
  let fetchMock: FetchMock;
  let mod: typeof import("../api/loader");

  beforeEach(async () => {
    vi.resetModules();
    resetWindow();
    _win.location.hash = "#run=20260519T120000Z-abcdef12";
    fetchMock = vi.fn(async (url: RequestInfo | URL) => {
      const u = String(url);
      if (u.endsWith("/api/healthz")) {
        return jsonResponse({ status: "ok", version: "test" });
      }
      if (u.endsWith("/scopes")) {
        return jsonResponse({
          run_id: "20260519T120000Z-abcdef12",
          scopes: [
            {
              dir: "synapse_workspace__syn-1",
              source_type: "synapse_workspace",
              slug: "syn-1",
              modules: ["dedicated_pools"],
            },
            {
              dir: "adf__adf-1",
              source_type: "adf",
              slug: "adf-1",
              modules: ["pipelines"],
            },
          ],
        });
      }
      if (u.includes("/modules/dedicated_pools") && u.includes("scope=synapse_workspace__syn-1")) {
        return jsonResponse({ source: "syn" });
      }
      if (u.includes("/modules/dedicated_pools") && u.includes("scope=adf__adf-1")) {
        return jsonResponse({ source: "adf" });
      }
      return new Response("not found", { status: 404 });
    });
    installFetch(fetchMock);
    mod = await import("../api/loader");
    mod.clearLoaderCache();
  });

  afterEach(() => {
    mod.clearLoaderCache();
  });

  it("returns one entry per scope with the right payload", async () => {
    const results = await mod.loadModulePerScope<{ source: string }>("dedicated_pools");
    expect(results).toHaveLength(2);
    const bySource = Object.fromEntries(
      results.map((r) => [r.scope?.source_type, r]),
    );
    expect(bySource.synapse_workspace.scope?.dir).toBe("synapse_workspace__syn-1");
    expect(bySource.synapse_workspace.payload).toEqual({ source: "syn" });
    expect(bySource.adf.scope?.slug).toBe("adf-1");
    // adf scope did not produce ``dedicated_pools`` (not in its modules
    // list) — loader must NOT hit the network for it and must return null.
    expect(bySource.adf.payload).toBeNull();
  });

  it("propagates the scope query parameter when fetching", async () => {
    await mod.loadModulePerScope("dedicated_pools");
    const urls = fetchMock.mock.calls.map((c) => String(c[0]));
    const scopedHits = urls.filter((u) =>
      u.includes("/modules/dedicated_pools") && u.includes("scope="),
    );
    expect(scopedHits.some((u) => u.includes("scope=synapse_workspace__syn-1"))).toBe(true);
    // adf entry has dedicated_pools NOT in modules → no fetch issued.
    expect(scopedHits.some((u) => u.includes("scope=adf__adf-1"))).toBe(false);
  });

  it("apiListScopes caches the /scopes response", async () => {
    const a = await mod.apiListScopes("20260519T120000Z-abcdef12");
    const b = await mod.apiListScopes("20260519T120000Z-abcdef12");
    expect(a).toBe(b);
    const calls = fetchMock.mock.calls.filter((c) =>
      String(c[0]).endsWith("/scopes"),
    );
    expect(calls).toHaveLength(1);
  });
});

describe("loadModulePerScope — control-plane single-scope (flat)", () => {
  let fetchMock: FetchMock;
  let mod: typeof import("../api/loader");

  beforeEach(async () => {
    vi.resetModules();
    resetWindow();
    _win.location.hash = "#run=20260519T120000Z-abcdef12";
    fetchMock = vi.fn(async (url: RequestInfo | URL) => {
      const u = String(url);
      if (u.endsWith("/api/healthz")) {
        return jsonResponse({ status: "ok", version: "test" });
      }
      if (u.endsWith("/scopes")) {
        return jsonResponse({
          run_id: "20260519T120000Z-abcdef12",
          scopes: [],
        });
      }
      if (u.endsWith("/modules/dedicated_pools")) {
        return jsonResponse({ flat: true });
      }
      return new Response("not found", { status: 404 });
    });
    installFetch(fetchMock);
    mod = await import("../api/loader");
    mod.clearLoaderCache();
  });

  afterEach(() => {
    mod.clearLoaderCache();
  });

  it("falls back to the flat artefact when /scopes is empty", async () => {
    const results = await mod.loadModulePerScope<{ flat: boolean }>("dedicated_pools");
    expect(results).toEqual([{ scope: null, payload: { flat: true } }]);
  });
});

describe("loadModulePerScope — static / no-run modes", () => {
  let mod: typeof import("../api/loader");

  beforeEach(async () => {
    vi.resetModules();
    resetWindow();
    // Static mode = healthz fails. Then any `./xxx.json` URL is the static path.
    const fetchMock = vi.fn(async (url: RequestInfo | URL) => {
      const u = String(url);
      if (u.endsWith("/api/healthz")) {
        throw new Error("connection refused");
      }
      if (u.endsWith("dedicated_pools.json")) {
        return jsonResponse({ static: true });
      }
      return new Response("not found", { status: 404 });
    });
    installFetch(fetchMock);
    mod = await import("../api/loader");
    mod.clearLoaderCache();
  });

  afterEach(() => {
    mod.clearLoaderCache();
  });

  it("returns the un-scoped payload in a single entry", async () => {
    const results = await mod.loadModulePerScope<{ static: boolean }>("dedicated_pools");
    expect(results).toEqual([{ scope: null, payload: { static: true } }]);
  });
});
