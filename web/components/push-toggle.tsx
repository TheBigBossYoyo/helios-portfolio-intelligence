"use client";

import { Bell, BellOff, Send } from "lucide-react";
import { useEffect, useState, useTransition } from "react";

import { ActionMessage } from "@/components/action-button";
import type { ActionResult } from "@/lib/actions";
import { BUTTON } from "@/lib/ui";

type Support = "checking" | "ok" | "insecure" | "install" | "unsupported" | "denied";

function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const padded = `${base64url}${"=".repeat((4 - (base64url.length % 4)) % 4)}`
    .replace(/-/g, "+")
    .replace(/_/g, "/");
  const raw = atob(padded);
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
  return bytes;
}

function deviceLabel(): string {
  const agent = navigator.userAgent;
  if (/iPhone/.test(agent)) return "iPhone";
  if (/iPad/.test(agent)) return "iPad";
  if (/Android/.test(agent)) return "Android phone";
  return "Browser";
}

function standalone(): boolean {
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  );
}

/**
 * Turns this phone's notifications on or off. Web Push needs a secure page (the Tailscale
 * https:// address) and, on an iPhone, the app added to the home screen.
 */
export function PushToggle({
  publicKey,
  subscribe,
  unsubscribe,
  test,
}: {
  publicKey: string;
  subscribe: (subscription: { endpoint: string; keys: { p256dh: string; auth: string }; label: string }) => Promise<ActionResult>;
  unsubscribe: (endpoint: string) => Promise<ActionResult>;
  test: (endpoint: string) => Promise<ActionResult>;
}) {
  const [support, setSupport] = useState<Support>("checking");
  const [endpoint, setEndpoint] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const [result, setResult] = useState<ActionResult | null>(null);

  useEffect(() => {
    const check = async () => {
      if (!window.isSecureContext) return setSupport("insecure");
      const iOS = /iPhone|iPad/.test(navigator.userAgent);
      if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
        return setSupport(iOS && !standalone() ? "install" : "unsupported");
      }
      if (Notification.permission === "denied") return setSupport("denied");
      setSupport("ok");
      const registration = await navigator.serviceWorker.ready;
      const current = await registration.pushManager.getSubscription();
      setEndpoint(current?.endpoint ?? null);
    };
    void check();
  }, []);

  const turnOn = () =>
    startTransition(async () => {
      setResult(null);
      try {
        const permission = await Notification.requestPermission();
        if (permission !== "granted") {
          setSupport(permission === "denied" ? "denied" : "ok");
          return;
        }
        const registration = await navigator.serviceWorker.ready;
        const subscription =
          (await registration.pushManager.getSubscription()) ??
          (await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: keyBytes(publicKey),
          }));
        const json = subscription.toJSON();
        const outcome = await subscribe({
          endpoint: subscription.endpoint,
          keys: { p256dh: json.keys?.p256dh ?? "", auth: json.keys?.auth ?? "" },
          label: deviceLabel(),
        });
        setResult(outcome);
        if (outcome.ok) setEndpoint(subscription.endpoint);
      } catch (error) {
        setResult({
          ok: false,
          error: error instanceof Error ? error.message : "Could not turn notifications on",
          status: null,
          timestamp: new Date().toISOString(),
        });
      }
    });

  const turnOff = () =>
    startTransition(async () => {
      setResult(null);
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      if (subscription) {
        const outcome = await unsubscribe(subscription.endpoint);
        await subscription.unsubscribe();
        setResult(outcome);
      }
      setEndpoint(null);
    });

  const message: Record<Exclude<Support, "ok">, string> = {
    checking: "Checking this phone…",
    insecure:
      "Notifications need the secure address: open Helios through its Tailscale https:// link (Settings → Phone on the computer shows it).",
    install: "On an iPhone, first add Helios to the home screen (Share → Add to Home Screen), then open it from there.",
    unsupported: "This browser cannot receive web notifications.",
    denied: "Notifications are blocked for Helios in this phone's settings. Allow them there, then come back.",
  };

  return (
    <div className="flex flex-col gap-3" data-testid="push-toggle">
      <div className="flex items-start gap-3">
        <span
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${
            endpoint ? "bg-positive-soft text-positive" : "bg-surface-3 text-ink-3"
          }`}
        >
          {endpoint ? <Bell aria-hidden="true" size={18} /> : <BellOff aria-hidden="true" size={18} />}
        </span>
        <div className="flex flex-col gap-0.5">
          <span className="font-medium text-ink">{endpoint ? "Notifications are on" : "Notifications"}</span>
          <span className="text-sm text-ink-3">
            {support === "ok"
              ? "Price alerts, results tomorrow, dividends that arrived and the evening summary, on this phone."
              : message[support]}
          </span>
        </div>
      </div>
      {support === "ok" ? (
        <div className="flex flex-wrap items-center gap-2">
          {endpoint ? (
            <>
              <button
                className={`${BUTTON.secondary} ${BUTTON.small}`}
                disabled={pending}
                onClick={() => startTransition(async () => setResult(await test(endpoint)))}
                type="button"
              >
                <Send aria-hidden="true" size={14} /> Send a test
              </button>
              <button className={`${BUTTON.ghost} ${BUTTON.small}`} disabled={pending} onClick={turnOff} type="button">
                Turn off
              </button>
            </>
          ) : (
            <button className={`${BUTTON.primary} ${BUTTON.small}`} disabled={pending} onClick={turnOn} type="button">
              <Bell aria-hidden="true" size={14} /> Turn on notifications
            </button>
          )}
          <ActionMessage pending={pending} result={result} />
        </div>
      ) : null}
    </div>
  );
}
