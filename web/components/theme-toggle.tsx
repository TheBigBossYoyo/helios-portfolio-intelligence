"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useSyncExternalStore } from "react";

export type ThemePreference = "light" | "dark" | "system";

export const THEME_STORAGE_KEY = "helios-theme";

/** The phone's status-bar colour: the top bar's surface in each theme. */
const LIGHT_BAR = "#ffffff";
const DARK_BAR = "#141820";

/**
 * Runs in <head> before first paint (see app/layout.tsx), so the page never flashes the wrong
 * theme. Kept as a string because it must execute before React exists. Light is the default.
 */
export const THEME_BOOT_SCRIPT = `(function(){try{var p=localStorage.getItem("${THEME_STORAGE_KEY}")||"light";var d=p==="dark"||(p==="system"&&window.matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.setAttribute("data-theme",d?"dark":"light");document.querySelectorAll('meta[name="theme-color"]').forEach(function(m){m.setAttribute("content",d?"${DARK_BAR}":"${LIGHT_BAR}")});}catch(e){document.documentElement.setAttribute("data-theme","light");}})();`;

export function resolveTheme(preference: ThemePreference, systemDark: boolean): "light" | "dark" {
  if (preference === "system") return systemDark ? "dark" : "light";
  return preference;
}

function readPreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return stored === "dark" || stored === "system" || stored === "light" ? stored : "light";
  } catch {
    return "light";
  }
}

function apply(preference: ThemePreference) {
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const theme = resolveTheme(preference, systemDark);
  document.documentElement.setAttribute("data-theme", theme);
  // The status bar follows Helios's theme, not the phone's.
  for (const meta of document.querySelectorAll('meta[name="theme-color"]')) {
    meta.setAttribute("content", theme === "dark" ? DARK_BAR : LIGHT_BAR);
  }
}

const OPTIONS = [
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
  { value: "system", label: "System", Icon: Monitor },
] as const;

/** Same-tab changes don't fire `storage`, so the toggle announces its own writes. */
const CHANGE_EVENT = "helios-theme-change";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  window.addEventListener(CHANGE_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGE_EVENT, onChange);
  };
}

export function ThemeToggle() {
  // The server has no localStorage; it renders "light" and the client corrects on hydration.
  const preference = useSyncExternalStore<ThemePreference>(
    subscribe,
    readPreference,
    () => "light",
  );

  // In "system" mode, follow the OS as it changes (e.g. an automatic evening switch).
  useEffect(() => {
    if (preference !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const listener = () => apply("system");
    media.addEventListener("change", listener);
    return () => media.removeEventListener("change", listener);
  }, [preference]);

  function choose(next: ThemePreference) {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Storage can be unavailable (private mode); the choice still applies for this page.
    }
    apply(next);
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }

  return (
    <div
      aria-label="Theme"
      className="flex items-center gap-0.5 rounded-lg border border-border bg-surface-2 p-0.5"
      role="radiogroup"
    >
      {OPTIONS.map(({ value, label, Icon }) => {
        const selected = preference === value;
        return (
          <button
            aria-checked={selected}
            aria-label={label}
            className={`flex h-7 flex-1 items-center justify-center rounded-md transition-colors ${
              selected ? "bg-surface text-ink shadow-card" : "text-ink-3 hover:text-ink"
            }`}
            key={value}
            onClick={() => choose(value)}
            role="radio"
            title={label}
            type="button"
          >
            <Icon size={15} strokeWidth={2} />
          </button>
        );
      })}
    </div>
  );
}
