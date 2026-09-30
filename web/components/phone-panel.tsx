import { CheckCircle2, Circle, Globe, Smartphone, Wifi } from "lucide-react";
import QRCode from "qrcode";

import { ActionButton } from "@/components/action-button";
import { Note, Panel, Unavailable } from "@/components/panel";
import { CopyButton } from "@/components/copy-button";
import { ChoiceSettingForm } from "@/components/settings-forms";
import type { ActionResult } from "@/lib/actions";
import { formatDateTime } from "@/lib/format";
import type { ApiResult, PhoneAccess } from "@/lib/types";

const ON_OFF = [
  { value: "true", label: "On" },
  { value: "false", label: "Off" },
];

/** The QR code as inline SVG, drawn in the page's own ink so it reads in both themes. */
async function qrSvg(text: string): Promise<string> {
  return QRCode.toString(text, {
    type: "svg",
    margin: 1,
    errorCorrectionLevel: "M",
    color: { dark: "#101828", light: "#ffffff" },
  });
}

function timeLeft(expiresAt: string): string {
  // A code lives ten minutes; the clamp keeps a skewed clock from promising more.
  const minutes = Math.min(10, Math.max(0, Math.round((Date.parse(expiresAt) - Date.now()) / 60_000)));
  return minutes <= 1 ? "about a minute" : `${minutes} minutes`;
}

export async function PhonePanel({
  phone,
  current,
  saveSetting,
  createPairing,
  revokeDevice,
}: {
  phone: ApiResult<PhoneAccess>;
  current: string;
  saveSetting: (form: FormData) => Promise<ActionResult>;
  createPairing: () => Promise<ActionResult>;
  revokeDevice: (id: number) => Promise<ActionResult>;
}) {
  const data = phone.ok ? phone.data : null;
  const pairing = data?.pairing ?? null;
  const qr = pairing?.urls[0] ? await qrSvg(pairing.urls[0]) : null;

  return (
    <Panel
      actions={
        data?.enabled && data.listening ? (
          <ActionButton
            action={createPairing}
            icon={<Smartphone aria-hidden="true" size={14} />}
            label="Pair a phone"
            pendingLabel="Making a code…"
            variant="primary"
          />
        ) : undefined
      }
      subtitle="Open Helios on your phone: over your home Wi‑Fi, or from anywhere through Tailscale. Only phones you pair here get in, and they cannot change settings."
      title="Phone"
    >
      <div className="flex flex-col gap-5">
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <ChoiceSettingForm
            action={saveSetting}
            choices={ON_OFF}
            current={current}
            description="Lets paired phones on your network reach Helios. Applies after Restart (top of this page)."
            field="phone_access_enabled"
            label="Phone access"
          />
          {data?.enabled ? (
            <div className="flex flex-col gap-2">
              <span className="text-sm font-medium text-ink">Addresses</span>
              {data.addresses.length > 0 ? (
                <ul className="flex flex-col gap-1.5">
                  {data.addresses.map((address) => (
                    <li className="flex items-center gap-2 text-sm" key={address.url}>
                      <Wifi aria-hidden="true" className="text-ink-4" size={15} />
                      <code className="rounded-md bg-surface-2 px-2 py-0.5 text-ink">{address.url}</code>
                      <span className="text-xs text-ink-3">
                        {address.kind === "tailscale" ? "Tailscale, from anywhere" : "same Wi‑Fi"}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-ink-3">This computer has no network address right now.</p>
              )}
            </div>
          ) : null}
        </div>

        {data?.enabled && !data.listening ? (
          <Note>
            Phone access is switched on but not running yet. Press <b>Restart</b> at the top of this
            page. The first time, Windows asks whether Python may use the network: allow it on
            private networks.
          </Note>
        ) : null}

        {data?.enabled ? <AwayFromHome phone={data} /> : null}

        {pairing && qr ? (
          <div className="flex flex-col items-center gap-5 rounded-2xl border border-border bg-surface-2 p-5 sm:flex-row sm:items-center">
            <div
              aria-label="QR code for pairing a phone"
              className="h-44 w-44 shrink-0 overflow-hidden rounded-xl bg-white p-1.5 shadow-card [&>svg]:h-full [&>svg]:w-full"
              dangerouslySetInnerHTML={{ __html: qr }}
              role="img"
            />
            <div className="flex flex-col gap-2 text-center sm:text-left">
              <span className="text-sm font-medium text-ink-3">Pairing code</span>
              <span
                className="font-mono text-3xl font-semibold tracking-[0.2em] text-ink"
                data-testid="pairing-code"
              >
                {pairing.code}
              </span>
              <p className="max-w-sm text-sm leading-relaxed text-ink-2">
                Scan it with the phone&apos;s camera, or open{" "}
                <code className="text-ink">{data?.addresses[0]?.url ?? "the address above"}</code>{" "}
                and type the code. Works once, for the next {timeLeft(pairing.expiresAt)}. Then use
                <b> Add to Home Screen</b> to keep Helios there like an app.
              </p>
            </div>
          </div>
        ) : null}

        {data ? (
          data.devices.length > 0 ? (
            <ul className="flex flex-col divide-y divide-border rounded-xl border border-border">
              {data.devices.map((device) => (
                <li className="flex items-center justify-between gap-3 px-4 py-3" key={device.id}>
                  <span className="flex items-center gap-3">
                    <Smartphone aria-hidden="true" className="text-ink-3" size={18} />
                    <span className="flex flex-col">
                      <span className="text-sm font-medium text-ink">{device.name}</span>
                      <span className="text-xs text-ink-3">
                        Paired {formatDateTime(device.createdAt)}
                        {device.lastSeenAt ? ` · last used ${formatDateTime(device.lastSeenAt)}` : ""}
                      </span>
                    </span>
                  </span>
                  <ActionButton
                    action={revokeDevice.bind(null, device.id)}
                    confirmLabel="Confirm unpair"
                    label="Unpair"
                    pendingLabel="Unpairing…"
                  />
                </li>
              ))}
            </ul>
          ) : data.enabled ? (
            <p className="text-sm text-ink-3">No phone paired yet.</p>
          ) : null
        ) : (
          <Unavailable detail={phone.ok ? null : phone.error} reason="Phone access status unavailable" />
        )}
      </div>
    </Panel>
  );
}

function Step({ done, title, children }: { done: boolean; title: string; children: React.ReactNode }) {
  const Icon = done ? CheckCircle2 : Circle;
  return (
    <li className="flex gap-3">
      <Icon
        aria-hidden="true"
        className={`mt-0.5 shrink-0 ${done ? "text-positive" : "text-ink-4"}`}
        size={18}
        strokeWidth={2}
      />
      <div className="flex min-w-0 flex-col gap-1.5">
        <span className="text-sm font-medium text-ink">
          {title}
          {done ? <span className="sr-only"> (done)</span> : null}
        </span>
        <div className="text-sm leading-relaxed text-ink-3">{children}</div>
      </div>
    </li>
  );
}

/**
 * Away from home, through Tailscale: a private network between this computer and the phone.
 * Nothing is opened to the internet; `tailscale serve` hands the phone an HTTPS address that
 * only devices signed in to the same Tailscale account can reach.
 */
function AwayFromHome({ phone }: { phone: PhoneAccess }) {
  const tailscale = phone.tailscale;
  const serving = Boolean(tailscale.serveUrl);
  return (
    <section
      aria-label="Away from home"
      className="flex flex-col gap-4 rounded-2xl border border-border p-4 sm:p-5"
      data-testid="away-from-home"
    >
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent-ink">
          <Globe aria-hidden="true" size={18} />
        </span>
        <div>
          <h3 className="text-sm font-semibold text-ink">Away from home</h3>
          <p className="text-sm text-ink-3">
            {serving
              ? "Ready: your phone reaches Helios from anywhere at the address below."
              : "Four steps, once. Tailscale is free and links only your own devices; nothing is opened to the internet."}
          </p>
        </div>
      </div>
      <ol className="flex flex-col gap-4">
        <Step done={tailscale.installed} title="Install Tailscale on this computer">
          {tailscale.installed ? (
            "Installed."
          ) : (
            <>
              Download it from{" "}
              <a className="font-medium text-accent hover:underline" href="https://tailscale.com/download/windows" rel="noreferrer" target="_blank">
                tailscale.com/download
              </a>{" "}
              and run the installer.
            </>
          )}
        </Step>
        <Step done={tailscale.connected} title="Sign in">
          {tailscale.connected ? (
            <>
              Connected as <code className="text-ink">{tailscale.dnsName ?? tailscale.ip}</code>.
            </>
          ) : (
            "Click the Tailscale icon in the taskbar, Log in, and use a Google, Microsoft or Apple account. Remember which: the phone signs in with the same one."
          )}
        </Step>
        <Step done={serving} title="Give Helios an HTTPS address on your Tailscale network">
          {serving ? (
            <span className="flex flex-wrap items-center gap-2">
              <code className="rounded-md bg-surface-2 px-2 py-0.5 text-ink">{tailscale.serveUrl}</code>
              <CopyButton label="Copy address" text={tailscale.serveUrl ?? ""} />
            </span>
          ) : (
            <span className="flex flex-col gap-2">
              <span>Open Terminal (or PowerShell) and run this once. It keeps working after restarts:</span>
              <span className="flex flex-wrap items-center gap-2">
                <code className="rounded-md bg-surface-2 px-2 py-1 font-mono text-ink">{tailscale.serveCommand}</code>
                <CopyButton text={tailscale.serveCommand} />
              </span>
              <span>
                If it prints a link asking to enable HTTPS, open it, press Enable, and run the command
                again. Then reload this page.
              </span>
            </span>
          )}
        </Step>
        <Step done={serving && phone.devices.length > 0} title="On your phone">
          Install the Tailscale app from the App Store or Play Store and sign in with the same
          account. Then press <b>Pair a phone</b> above and scan the code
          {serving ? (
            <>
              {" "}
              (it opens <code className="text-ink">{tailscale.serveUrl}</code>)
            </>
          ) : null}
          . Add it to your home screen; it works on Wi‑Fi and mobile data alike.
        </Step>
      </ol>
    </section>
  );
}
