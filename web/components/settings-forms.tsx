"use client";

import { useState, useTransition } from "react";
import { Check, ExternalLink } from "lucide-react";

import { ActionMessage } from "@/components/action-button";
import { BUTTON, FIELD, HELP, LABEL } from "@/lib/ui";
import type { ActionResult } from "@/lib/actions";
import type { CredentialStatus, DatabaseInfo } from "@/lib/types";

/**
 * Write controls for credentials, settings and databases.
 *
 * One rule governs this whole file: **a secret travels in exactly one direction.** Every input
 * that accepts a credential is `type="password"`, is never given a `defaultValue`, and is
 * cleared the moment the write lands. The server never sends a stored value back, so there is
 * nothing here to prefill even if we wanted to — the most this page can tell you about an
 * existing key is its last four characters.
 *
 * The second rule: **a change is not live until the process restarts.** Settings are read into
 * a frozen object at startup, so a saved key does nothing until Helios comes back. Rather than
 * imply otherwise, every write says so and the page carries a banner until the restart happens.
 */

/**
 * Shared submit plumbing. Unlike the thesis forms this *always* resets, success or failure:
 * leaving a rejected API key sitting in a DOM input is a copy of the secret nobody asked for.
 */
function useSecretForm(action: (form: FormData) => Promise<ActionResult>) {
  const [pending, startTransition] = useTransition();
  const [result, setResult] = useState<ActionResult | null>(null);

  const onSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setResult(null);
    startTransition(async () => {
      const outcome = await action(data);
      setResult(outcome);
      form.reset();
    });
  };

  return { pending, result, onSubmit };
}

/** Same plumbing, but keeps input on failure — nothing here is secret. */
function usePlainForm(action: (form: FormData) => Promise<ActionResult>) {
  const [pending, startTransition] = useTransition();
  const [result, setResult] = useState<ActionResult | null>(null);

  const onSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setResult(null);
    startTransition(async () => {
      const outcome = await action(data);
      setResult(outcome);
      if (outcome.ok) {
        form.reset();
      }
    });
  };

  return { pending, result, onSubmit };
}

const REQUIREMENT_STYLE: Record<string, string> = {
  required: "bg-warning-soft text-warning",
  recommended: "bg-accent-soft text-accent-ink",
  optional: "bg-surface-3 text-ink-3",
};

/**
 * One credential: what it unlocks, whether it is set, and a write-only input to change it.
 *
 * The status line is the honest part. "Set" plus a four-character tail is everything the
 * dashboard knows; it cannot show you the key because it never receives it.
 */
export function CredentialForm({
  credential,
  action,
}: {
  credential: CredentialStatus;
  action: (form: FormData) => Promise<ActionResult>;
}) {
  const { pending, result, onSubmit } = useSecretForm(action);
  const inputId = `credential-${credential.field}`;

  return (
    <form
      className="flex flex-col gap-3 rounded-xl border border-border bg-surface-2 p-4"
      onSubmit={onSubmit}
    >
      <input name="field" type="hidden" value={credential.field} />

      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-semibold text-ink">{credential.label}</span>
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ${
            REQUIREMENT_STYLE[credential.requirement] ?? REQUIREMENT_STYLE.optional
          }`}
        >
          {credential.requirement}
        </span>
      </div>

      <p className={HELP}>{credential.present ? credential.unlocks : credential.without}</p>

      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        {credential.present ? (
          <span className="inline-flex items-center gap-1.5 font-medium text-positive">
            <Check aria-hidden="true" size={13} strokeWidth={2.5} />
            set{credential.hint ? ` ${credential.hint}` : ""} · from {credential.source}
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-ink-4">
            <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-ink-4" />
            Not set
          </span>
        )}
        <a
          className="inline-flex items-center gap-1 font-medium text-accent hover:text-accent-hover hover:underline underline-offset-4"
          href={credential.signup}
          rel="noreferrer noopener"
          target="_blank"
        >
          Get a key <ExternalLink aria-hidden="true" size={12} />
        </a>
      </div>

      <label className={LABEL} htmlFor={inputId}>
        New value
        <input
          autoComplete="off"
          className={FIELD}
          id={inputId}
          name="value"
          placeholder={credential.present ? "Paste to replace, or submit empty to clear" : "Paste your key"}
          spellCheck={false}
          type="password"
        />
      </label>

      <div className="flex items-center justify-end gap-3">
        <ActionMessage pending={pending} result={result} />
        <button className={`${BUTTON.primary} ${BUTTON.small}`} disabled={pending} type="submit">
          {pending ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}

export interface Choice {
  value: string;
  label: string;
}

/**
 * A non-credential setting constrained to a known set of values, shown with human labels while
 * the value submitted is whatever the backend's allowlist expects.
 */
export function ChoiceSettingForm({
  field,
  label,
  description,
  current,
  choices,
  action,
}: {
  field: string;
  label: string;
  description: string;
  current: string;
  choices: readonly Choice[];
  action: (form: FormData) => Promise<ActionResult>;
}) {
  const { pending, result, onSubmit } = usePlainForm(action);
  const inputId = `setting-${field}`;

  return (
    <form className="flex flex-col gap-2" onSubmit={onSubmit}>
      <input name="field" type="hidden" value={field} />

      <label className={LABEL} htmlFor={inputId}>
        {label}
        <select className={FIELD} defaultValue={current} id={inputId} name="value">
          {choices.map((choice) => (
            <option key={choice.value} value={choice.value}>
              {choice.label}
            </option>
          ))}
        </select>
      </label>

      <p className={HELP}>{description}</p>

      <div className="flex items-center justify-end gap-3">
        <ActionMessage pending={pending} result={result} />
        <button className={`${BUTTON.secondary} ${BUTTON.small}`} disabled={pending} type="submit">
          {pending ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}

const T212_OPTIONS = [
  {
    value: "demo",
    title: "Practice (demo)",
    detail: "Trading 212's paper account. Safe to test with — no real money moves.",
  },
  {
    value: "live",
    title: "Live (real money)",
    detail: "Your real Trading 212 account. Use a key generated from the live account.",
  },
] as const;

type Environment = "demo" | "live";

const ACCOUNT_NAME: Record<Environment, string> = {
  demo: "Practice account",
  live: "Live account",
};

/**
 * Connect Trading 212: environment, API key and API secret, saved together.
 *
 * These were three separate forms, which let a live key be checked against the demo host (the
 * running process still held the old environment) and let a secret be stored without its key.
 * Now one button verifies the pair against the environment *selected here* and stores all three
 * only if Trading 212 accepts them.
 *
 * The badges say what is true, not what is clicked: "In use" is the environment the running
 * API syncs from; "Saved — restart to apply" marks a change that is on disk but not yet live.
 */
export function Trading212ConnectForm({
  action,
  running,
  pending: pendingEnvironment,
  connected,
  keyHint,
}: {
  action: (form: FormData) => Promise<ActionResult>;
  running: Environment;
  pending: Environment;
  /** Both halves of the pair are present in the running process. */
  connected: boolean;
  keyHint: string | null;
}) {
  const { pending, result, onSubmit } = usePlainForm(action);

  return (
    <form className="flex flex-col gap-5" onSubmit={onSubmit}>
      <div
        className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl px-4 py-3 text-sm ${
          connected ? "bg-positive-soft" : "bg-warning-soft"
        }`}
      >
        <span
          className={`inline-flex items-center gap-2 font-semibold ${
            connected ? "text-positive" : "text-warning"
          }`}
        >
          <span
            aria-hidden="true"
            className="h-2 w-2 rounded-full"
            style={{ background: connected ? "var(--status-good)" : "var(--status-warning)" }}
          />
          {/* "Saved", not "connected": presence is all the page knows. Whether the pair works is
              decided by Trading 212 at the next sync, or by "Verify & connect" below. */}
          {connected ? `Key and secret saved — ${ACCOUNT_NAME[running]}` : "Not connected"}
        </span>
        {connected && keyHint ? (
          <span className="text-ink-2">API key set {keyHint}</span>
        ) : (
          <span className="text-ink-2">
            Generate an API key and secret in the Trading 212 app under Settings → API.
          </span>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium text-ink">Account</span>
        <div aria-label="Environment" className="grid grid-cols-1 gap-3 sm:grid-cols-2" role="radiogroup">
          {T212_OPTIONS.map((option) => {
            const inUse = option.value === running;
            const saved = option.value === pendingEnvironment && pendingEnvironment !== running;
            return (
              <label
                className="relative flex cursor-pointer flex-col gap-1 rounded-xl border border-border bg-surface p-4 shadow-card transition-colors hover:border-border-strong has-[:checked]:border-accent has-[:checked]:ring-3 has-[:checked]:ring-accent-soft has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent"
                key={option.value}
              >
                <input
                  className="sr-only"
                  defaultChecked={option.value === pendingEnvironment}
                  name="environment"
                  type="radio"
                  value={option.value}
                />
                <span className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-ink">{option.title}</span>
                  {inUse ? (
                    <span className="rounded-full bg-positive-soft px-2 py-0.5 text-[11px] font-medium text-positive">
                      In use
                    </span>
                  ) : saved ? (
                    <span className="rounded-full bg-warning-soft px-2 py-0.5 text-[11px] font-medium text-warning">
                      Saved — restart to apply
                    </span>
                  ) : null}
                </span>
                <span className="text-xs leading-relaxed text-ink-3">{option.detail}</span>
              </label>
            );
          })}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <label className={LABEL}>
          API key
          <input
            autoComplete="off"
            className={FIELD}
            id="t212-api-key"
            name="apiKey"
            placeholder={connected ? "Paste a new key to replace" : "Paste your API key"}
            spellCheck={false}
            type="password"
          />
        </label>
        <label className={LABEL}>
          API secret
          <input
            autoComplete="off"
            className={FIELD}
            id="t212-api-secret"
            name="apiSecret"
            placeholder="Paste the matching secret"
            spellCheck={false}
            type="password"
          />
        </label>
      </div>

      <p className={HELP}>
        Helios checks the key and secret against the account you selected before saving anything,
        so a practice key pointed at Live (or the reverse) is refused with a reason instead of
        half-saved. Both are stored in your OS keyring and never shown again.
      </p>

      <div className="flex flex-wrap items-center justify-end gap-3">
        <ActionMessage pending={pending} result={result} />
        <button className={BUTTON.primary} disabled={pending} type="submit">
          {pending ? "Verifying…" : "Verify & connect"}
        </button>
      </div>
    </form>
  );
}

/** A free-text setting. Used only where there is no allowlist to enforce. */
export function TextSettingForm({
  field,
  label,
  description,
  current,
  placeholder,
  action,
}: {
  field: string;
  label: string;
  description: string;
  current: string;
  placeholder: string;
  action: (form: FormData) => Promise<ActionResult>;
}) {
  const { pending, result, onSubmit } = usePlainForm(action);
  const inputId = `setting-${field}`;

  return (
    <form className="flex flex-col gap-2" onSubmit={onSubmit}>
      <input name="field" type="hidden" value={field} />

      <label className={LABEL} htmlFor={inputId}>
        {label}
        <input
          className={FIELD}
          defaultValue={current}
          id={inputId}
          name="value"
          placeholder={placeholder}
          spellCheck={false}
          type="text"
        />
      </label>

      <p className={HELP}>{description}</p>

      <div className="flex items-center justify-end gap-3">
        <ActionMessage pending={pending} result={result} />
        <button className={`${BUTTON.secondary} ${BUTTON.small}`} disabled={pending} type="submit">
          {pending ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}

/**
 * Create a database. Deliberately separate from switching to one.
 *
 * Replaying a live account into a database holding demo history produces a portfolio that is
 * neither, so the two steps are distinct and the copy says why.
 */
export function CreateDatabaseForm({
  action,
}: {
  action: (form: FormData) => Promise<ActionResult>;
}) {
  const { pending, result, onSubmit } = usePlainForm(action);

  return (
    <form className="flex flex-col gap-2" onSubmit={onSubmit}>
      <label className={LABEL} htmlFor="database-filename">
        New database
        <input
          className={FIELD}
          id="database-filename"
          name="filename"
          placeholder="helios-live.sqlite3"
          required
          spellCheck={false}
          type="text"
        />
      </label>

      <p className={HELP}>
        Created empty at the current schema. Nothing is copied across and the database you are
        using now is untouched — switching is a separate step.
      </p>

      <div className="flex items-center justify-end gap-3">
        <ActionMessage pending={pending} result={result} />
        <button className={`${BUTTON.secondary} ${BUTTON.small}`} disabled={pending} type="submit">
          {pending ? "Creating…" : "Create"}
        </button>
      </div>
    </form>
  );
}

/**
 * Switch the active database. Confirms first, because it changes every figure on every page.
 *
 * The previous file is left on disk, so this is reversible by switching back — which the
 * confirmation says, since "irreversible" and "disruptive" deserve different warnings.
 */
export function SwitchDatabaseForm({
  databases,
  action,
}: {
  databases: DatabaseInfo[];
  action: (form: FormData) => Promise<ActionResult>;
}) {
  const { pending, result, onSubmit } = usePlainForm(action);
  const [armed, setArmed] = useState(false);
  const candidates = databases.filter((row) => !row.active);

  if (candidates.length === 0) {
    return <p className={HELP}>No other database to switch to. Create one above first.</p>;
  }

  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        setArmed(false);
        onSubmit(event);
      }}
    >
      <label className={LABEL} htmlFor="database-target">
        Switch to
        <select className={FIELD} defaultValue={candidates[0].filename} id="database-target" name="filename">
          {candidates.map((row) => (
            <option key={row.filename} value={row.filename}>
              {row.filename}
              {row.schemaVersion ? "" : " — no schema"}
            </option>
          ))}
        </select>
      </label>

      <p className={HELP}>
        Takes effect after a restart. The database you are leaving stays on disk exactly as it
        is, so switching back is another switch rather than a restore.
      </p>

      <div className="flex items-center justify-end gap-3">
        <ActionMessage pending={pending} result={result} />
        {armed ? (
          <>
            <button
              className={`${BUTTON.ghost} ${BUTTON.small}`}
              disabled={pending}
              onClick={() => setArmed(false)}
              type="button"
            >
              Cancel
            </button>
            <button className={`${BUTTON.danger} ${BUTTON.small}`} disabled={pending} type="submit">
              {pending ? "Switching…" : "Confirm — every page changes"}
            </button>
          </>
        ) : (
          <button
            className={`${BUTTON.secondary} ${BUTTON.small}`}
            disabled={pending}
            onClick={() => setArmed(true)}
            type="button"
          >
            Switch
          </button>
        )}
      </div>
    </form>
  );
}

/**
 * Restart the API so saved settings take effect.
 *
 * Confirms first: a restart drops in-flight requests, and the backend rate-limits this to three
 * per five minutes precisely so it cannot be held in a reboot loop. Success means "accepted",
 * not "already back", so the message tells the reader to reload rather than implying the new
 * configuration is live the instant the button returns.
 */
export function RestartButton({
  action,
  restartRequired,
}: {
  action: () => Promise<ActionResult>;
  restartRequired: boolean;
}) {
  const [pending, startTransition] = useTransition();
  const [result, setResult] = useState<ActionResult | null>(null);
  const [armed, setArmed] = useState(false);

  const run = () => {
    setArmed(false);
    setResult(null);
    startTransition(async () => {
      setResult(await action());
    });
  };

  return (
    <div className="flex flex-wrap items-center justify-end gap-3">
      <ActionMessage pending={pending} result={result} />
      {armed ? (
        <>
          <button
            className={`${BUTTON.ghost} ${BUTTON.small}`}
            disabled={pending}
            onClick={() => setArmed(false)}
            type="button"
          >
            Cancel
          </button>
          <button className={`${BUTTON.danger} ${BUTTON.small}`} disabled={pending} onClick={run} type="button">
            {pending ? "Restarting…" : "Confirm — drops in-flight requests"}
          </button>
        </>
      ) : (
        <button
          className={`${restartRequired ? BUTTON.danger : BUTTON.secondary} ${BUTTON.small}`}
          disabled={pending}
          onClick={() => setArmed(true)}
          type="button"
        >
          Restart API
        </button>
      )}
    </div>
  );
}
