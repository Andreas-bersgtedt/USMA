/**
 * Theme system.
 *
 * Implements:
 *  1. Preset map — every preset defines the same set of CSS variables.
 *  2. Runtime application — sets `data-theme="<id>"` on <html> (matching
 *     CSS blocks in styles.css), AND optionally writes per-variable
 *     overrides via `document.documentElement.style.setProperty()` so an
 *     "Advanced" picker can edit any variable without touching CSS.
 *  3. Persistence — `sma.theme` (preset id) and `sma.theme.overrides`
 *     (JSON of per-variable hex overrides) in `localStorage`.
 *  4. OS follow — the `auto` preset resolves to `light`/`dark` from
 *     `prefers-color-scheme` and re-resolves when the OS preference
 *     changes.
 */

export const VARIABLE_KEYS = [
  "--bg",
  "--panel",
  "--panel-2",
  "--border",
  "--text",
  "--muted",
  "--accent",
  "--ok",
  "--warn",
  "--err",
  "--info",
  "--on-accent",
] as const;

export type VarKey = (typeof VARIABLE_KEYS)[number];
export type Palette = Record<VarKey, string>;
export type Overrides = Partial<Palette>;

export type PresetId =
  | "dark"
  | "light"
  | "high-contrast"
  | "solarized-dark"
  | "nord";

export type ThemeId = PresetId | "auto";

export interface ThemeMeta {
  id: ThemeId;
  label: string;
}

export const THEMES: readonly ThemeMeta[] = [
  { id: "auto", label: "Auto (follow OS)" },
  { id: "dark", label: "Dark" },
  { id: "light", label: "Light" },
  { id: "high-contrast", label: "High contrast" },
  { id: "solarized-dark", label: "Solarized dark" },
  { id: "nord", label: "Nord" },
];

/**
 * Display labels for each variable in the Advanced editor. Order matches
 * VARIABLE_KEYS for stable rendering.
 */
export const VARIABLE_LABELS: Record<VarKey, string> = {
  "--bg": "Background",
  "--panel": "Panel",
  "--panel-2": "Panel (alt)",
  "--border": "Border",
  "--text": "Text",
  "--muted": "Muted text",
  "--accent": "Accent",
  "--ok": "Success",
  "--warn": "Warning",
  "--err": "Error",
  "--info": "Info",
  "--on-accent": "On-accent text",
};

export const PRESETS: Record<PresetId, Palette> = {
  dark: {
    "--bg": "#0e1116",
    "--panel": "#151a21",
    "--panel-2": "#1c232c",
    "--border": "#2a323d",
    "--text": "#e6edf3",
    "--muted": "#8b949e",
    "--accent": "#58a6ff",
    "--ok": "#3fb950",
    "--warn": "#d29922",
    "--err": "#f85149",
    "--info": "#58a6ff",
    "--on-accent": "#0e1116",
  },
  light: {
    "--bg": "#ffffff",
    "--panel": "#f6f8fa",
    "--panel-2": "#eaeef2",
    "--border": "#d0d7de",
    "--text": "#1f2328",
    "--muted": "#57606a",
    "--accent": "#0969da",
    "--ok": "#1a7f37",
    "--warn": "#9a6700",
    "--err": "#cf222e",
    "--info": "#0969da",
    "--on-accent": "#ffffff",
  },
  "high-contrast": {
    "--bg": "#000000",
    "--panel": "#0a0a0a",
    "--panel-2": "#141414",
    "--border": "#ffffff",
    "--text": "#ffffff",
    "--muted": "#d4d4d4",
    "--accent": "#ffd400",
    "--ok": "#00ff66",
    "--warn": "#ffbf00",
    "--err": "#ff5252",
    "--info": "#66d9ff",
    "--on-accent": "#000000",
  },
  "solarized-dark": {
    "--bg": "#002b36",
    "--panel": "#073642",
    "--panel-2": "#0a4250",
    "--border": "#194e5a",
    "--text": "#eee8d5",
    "--muted": "#93a1a1",
    "--accent": "#268bd2",
    "--ok": "#859900",
    "--warn": "#b58900",
    "--err": "#dc322f",
    "--info": "#2aa198",
    "--on-accent": "#002b36",
  },
  nord: {
    "--bg": "#2e3440",
    "--panel": "#3b4252",
    "--panel-2": "#434c5e",
    "--border": "#4c566a",
    "--text": "#eceff4",
    "--muted": "#d8dee9",
    "--accent": "#88c0d0",
    "--ok": "#a3be8c",
    "--warn": "#ebcb8b",
    "--err": "#bf616a",
    "--info": "#81a1c1",
    "--on-accent": "#2e3440",
  },
};

const STORAGE_THEME = "sma.theme";
const STORAGE_OVERRIDES = "sma.theme.overrides";
const DEFAULT_THEME: ThemeId = "dark";

function isThemeId(value: string | null): value is ThemeId {
  return !!value && THEMES.some((t) => t.id === value);
}

function isVarKey(k: string): k is VarKey {
  return (VARIABLE_KEYS as readonly string[]).includes(k);
}

/** Resolve `auto` to a concrete preset using the OS color-scheme. */
function resolveAuto(): PresetId {
  try {
    if (
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: light)").matches
    ) {
      return "light";
    }
  } catch {
    // matchMedia unavailable; fall through to dark.
  }
  return "dark";
}

export function getStoredTheme(): ThemeId {
  try {
    const raw = window.localStorage.getItem(STORAGE_THEME);
    if (isThemeId(raw)) return raw;
  } catch {
    // localStorage unavailable; use default.
  }
  return DEFAULT_THEME;
}

export function getStoredOverrides(): Overrides {
  try {
    const raw = window.localStorage.getItem(STORAGE_OVERRIDES);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return {};
    const out: Overrides = {};
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (isVarKey(k) && typeof v === "string" && v.trim()) {
        out[k] = v;
      }
    }
    return out;
  } catch {
    return {};
  }
}

/**
 * Apply a theme + overrides to <html>. Removes any previous inline
 * overrides so toggling presets fully replaces the previous palette.
 */
export function applyTheme(id: ThemeId, overrides: Overrides = {}): void {
  const root = document.documentElement;
  const effective: PresetId = id === "auto" ? resolveAuto() : id;

  if (effective === "dark") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", effective);
  }

  // Clear any prior inline overrides before applying the new set.
  for (const k of VARIABLE_KEYS) {
    root.style.removeProperty(k);
  }
  // Write per-variable overrides on top of the preset's CSS values.
  for (const k of VARIABLE_KEYS) {
    const v = overrides[k];
    if (v) root.style.setProperty(k, v);
  }
}

export function setStoredTheme(id: ThemeId): void {
  try {
    window.localStorage.setItem(STORAGE_THEME, id);
  } catch {
    // Ignore; in-memory state still applies for the session.
  }
}

export function setStoredOverrides(overrides: Overrides): void {
  try {
    if (Object.keys(overrides).length === 0) {
      window.localStorage.removeItem(STORAGE_OVERRIDES);
    } else {
      window.localStorage.setItem(STORAGE_OVERRIDES, JSON.stringify(overrides));
    }
  } catch {
    // Ignore.
  }
}

/** Effective palette = preset palette merged with overrides. */
export function effectivePalette(id: ThemeId, overrides: Overrides): Palette {
  const base = PRESETS[id === "auto" ? resolveAuto() : id];
  return { ...base, ...overrides } as Palette;
}

let autoMediaQuery: MediaQueryList | null = null;
let autoListener: ((e: MediaQueryListEvent) => void) | null = null;

/**
 * Subscribe to OS color-scheme changes. Returns an unsubscribe function.
 * Replaces any previous subscription to avoid leaks.
 */
export function watchAuto(onChange: () => void): () => void {
  if (typeof window === "undefined" || !window.matchMedia) return () => {};
  if (autoMediaQuery && autoListener) {
    autoMediaQuery.removeEventListener("change", autoListener);
  }
  autoMediaQuery = window.matchMedia("(prefers-color-scheme: light)");
  autoListener = () => onChange();
  autoMediaQuery.addEventListener("change", autoListener);
  return () => {
    if (autoMediaQuery && autoListener) {
      autoMediaQuery.removeEventListener("change", autoListener);
    }
    autoMediaQuery = null;
    autoListener = null;
  };
}

/** Read + apply on app boot. Call before first React render to avoid flash. */
export function initTheme(): { theme: ThemeId; overrides: Overrides } {
  const theme = getStoredTheme();
  const overrides = getStoredOverrides();
  applyTheme(theme, overrides);
  if (theme === "auto") {
    watchAuto(() => applyTheme(getStoredTheme(), getStoredOverrides()));
  }
  return { theme, overrides };
}
