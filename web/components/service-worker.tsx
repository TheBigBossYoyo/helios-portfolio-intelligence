"use client";

import { CloudOff, RotateCw } from "lucide-react";
import { useEffect, useState } from "react";

const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]"]);

/**
 * Registers the service worker on a phone (not in the computer's own window, which never loses
 * Helios) and asks it to save the main pages for offline use once the page has loaded.
 */
export function ServiceWorker() {
  useEffect(() => {
    if (!("serviceWorker" in navigator) || LOOPBACK.has(window.location.hostname)) return;
    let cancelled = false;
    navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .then(async () => {
        const ready = await navigator.serviceWorker.ready;
        if (!cancelled && navigator.onLine) ready.active?.postMessage({ type: "warm" });
      })
      .catch(() => {
        // An insecure origin (plain http on the Wi-Fi) cannot register: pages simply load live.
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return null;
}

function savedAt(): string | null {
  const meta = document.querySelector('meta[name="helios-offline"]');
  return meta ? meta.getAttribute("content") : null;
}

/** Says when the page is a saved copy (the computer could not be reached), and since when. */
export function OfflineBanner() {
  const [state, setState] = useState<{ saved: string | null; offline: boolean }>({ saved: null, offline: false });

  useEffect(() => {
    const update = () => setState({ saved: savedAt(), offline: !navigator.onLine });
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  if (state.saved === null && !state.offline) return null;
  const when = state.saved ? new Date(state.saved) : null;
  const label =
    when && !Number.isNaN(when.getTime())
      ? when.toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit", day: "numeric", month: "short" })
      : null;
  return (
    <div
      className="flex items-center gap-3 rounded-2xl border border-[color-mix(in_srgb,var(--warning)_30%,transparent)] bg-warning-soft px-4 py-3 text-sm text-ink"
      role="status"
    >
      <CloudOff aria-hidden="true" className="shrink-0 text-warning" size={18} />
      <span className="flex-1">
        <span className="font-semibold">Offline.</span>{" "}
        {label ? `Showing what Helios had on ${label}.` : "Showing the last saved pages."} The computer running
        Helios may be off or asleep.
      </span>
      <button
        className="flex h-9 shrink-0 items-center gap-1.5 rounded-full px-3 text-sm font-medium text-ink-2 active:bg-surface-3"
        onClick={() => window.location.reload()}
        type="button"
      >
        <RotateCw aria-hidden="true" size={15} /> Retry
      </button>
    </div>
  );
}
