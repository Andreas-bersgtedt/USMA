import { useEffect, useRef, useState } from "react";
import {
  applyTheme,
  effectivePalette,
  getStoredOverrides,
  getStoredTheme,
  PRESETS,
  setStoredOverrides,
  setStoredTheme,
  THEMES,
  VARIABLE_KEYS,
  VARIABLE_LABELS,
  watchAuto,
  type Overrides,
  type ThemeId,
  type VarKey,
} from "../lib/theme";

/**
 * Normalise a CSS color string to a 7-char `#rrggbb` value the native
 * `<input type="color">` will accept. Anything we can't parse falls back
 * to the supplied default so the swatch stays usable.
 */
function toHex(value: string, fallback: string): string {
  const v = value.trim().toLowerCase();
  if (/^#[0-9a-f]{6}$/.test(v)) return v;
  if (/^#[0-9a-f]{3}$/.test(v)) {
    return `#${v[1]}${v[1]}${v[2]}${v[2]}${v[3]}${v[3]}`;
  }
  return fallback;
}

export function ThemePicker() {
  const [theme, setTheme] = useState<ThemeId>(() => getStoredTheme());
  const [overrides, setOverrides] = useState<Overrides>(() => getStoredOverrides());
  const [open, setOpen] = useState(false);
  const popoverRef = useRef<HTMLDivElement | null>(null);

  // Re-apply when theme/overrides change so external state edits also stick.
  useEffect(() => {
    applyTheme(theme, overrides);
  }, [theme, overrides]);

  // Listen for OS color-scheme changes while `auto` is selected.
  useEffect(() => {
    if (theme !== "auto") return;
    const unsub = watchAuto(() => applyTheme("auto", overrides));
    return unsub;
  }, [theme, overrides]);

  // Close the Advanced popover when clicking outside.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (
        popoverRef.current &&
        !popoverRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const onPresetChange = (next: ThemeId) => {
    setTheme(next);
    setStoredTheme(next);
  };

  const onVarChange = (key: VarKey, value: string) => {
    const next: Overrides = { ...overrides, [key]: value };
    setOverrides(next);
    setStoredOverrides(next);
  };

  const onVarReset = (key: VarKey) => {
    const next: Overrides = { ...overrides };
    delete next[key];
    setOverrides(next);
    setStoredOverrides(next);
  };

  const onResetAll = () => {
    setOverrides({});
    setStoredOverrides({});
  };

  // Compute swatch values from the effective palette so the inputs reflect
  // both the active preset and any overrides applied on top.
  const palette = effectivePalette(theme, overrides);

  return (
    <div className="theme-picker" ref={popoverRef}>
      <label className="theme-picker-row" title="Color theme">
        <span className="theme-picker-label">Theme</span>
        <select
          className="run-picker"
          value={theme}
          onChange={(e) => onPresetChange(e.target.value as ThemeId)}
          aria-label="Color theme"
        >
          {THEMES.map((t) => (
            <option key={t.id} value={t.id}>
              {t.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="theme-advanced-toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-label="Customize theme colors"
          title="Customize theme colors"
        >
          ⚙
        </button>
      </label>

      {open && (
        <div className="theme-advanced" role="dialog" aria-label="Theme customization">
          <div className="theme-advanced-header">
            <strong>Customize colors</strong>
            <button
              type="button"
              className="theme-reset-all"
              onClick={onResetAll}
              disabled={Object.keys(overrides).length === 0}
              title="Reset all custom colors"
            >
              Reset all
            </button>
          </div>
          <p className="theme-advanced-hint">
            Overrides apply on top of the selected preset and persist locally.
          </p>
          <div className="theme-vars">
            {VARIABLE_KEYS.map((key) => {
              const presetId = theme === "auto" ? "dark" : theme;
              const fallback = PRESETS[presetId]?.[key] ?? "#000000";
              const current = palette[key] ?? fallback;
              const hex = toHex(current, toHex(fallback, "#000000"));
              const isOverridden = overrides[key] !== undefined;
              return (
                <div key={key} className="theme-var-row">
                  <label className="theme-var-label" htmlFor={`theme-${key}`}>
                    {VARIABLE_LABELS[key]}
                    <code className="theme-var-key">{key}</code>
                  </label>
                  <input
                    id={`theme-${key}`}
                    type="color"
                    value={hex}
                    onChange={(e) => onVarChange(key, e.target.value)}
                    aria-label={`${VARIABLE_LABELS[key]} color`}
                  />
                  <input
                    type="text"
                    className="theme-var-text"
                    value={current}
                    onChange={(e) => onVarChange(key, e.target.value)}
                    spellCheck={false}
                    aria-label={`${VARIABLE_LABELS[key]} value`}
                  />
                  <button
                    type="button"
                    className="theme-var-reset"
                    onClick={() => onVarReset(key)}
                    disabled={!isOverridden}
                    title="Reset to preset value"
                  >
                    ↺
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
