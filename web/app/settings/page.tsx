import { AlertTriangle } from "lucide-react";
import type { ReactNode } from "react";

import { DataTable, type Column } from "@/components/data-table";
import { Note, PageHeader, Panel, Unavailable } from "@/components/panel";
import { StatusBadge } from "@/components/status-badge";
import {
  ChoiceSettingForm,
  CreateDatabaseForm,
  CredentialForm,
  Trading212ConnectForm,
  RestartButton,
  SwitchDatabaseForm,
  TextSettingForm,
  type Choice,
} from "@/components/settings-forms";
import {
  connectTrading212Action,
  createDatabaseAction,
  restartApiAction,
  saveCredentialAction,
  saveSettingAction,
  switchDatabaseAction,
} from "@/lib/actions";
import { getDatabases, getSettings } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { DatabaseInfo } from "@/lib/types";

export const dynamic = "force-dynamic";

const MARKET_DATA_PROVIDERS: Choice[] = [
  { value: "twelvedata", label: "Twelve Data" },
  { value: "alphavantage", label: "Alpha Vantage" },
  { value: "eodhd", label: "EODHD" },
  { value: "disabled", label: "Disabled" },
];

const BENCHMARKS: Choice[] = [
  { value: "vwrp", label: "FTSE All-World (VWRP)" },
  { value: "cspx", label: "S&P 500 (CSPX)" },
  { value: "swda", label: "MSCI World (SWDA)" },
];

/** Which credential fields belong under which heading, so the page reads as topics rather than a flat list. */
const T212_CREDENTIAL_FIELDS = ["t212_api_key", "t212_api_secret"];
const DATA_CREDENTIAL_FIELDS = [
  "market_data_api_key",
  "market_data_fallback_api_key",
  "openfigi_api_key",
];
const AI_CREDENTIAL_FIELDS = ["anthropic_api_key"];
const NEWS_CREDENTIAL_FIELDS = ["news_marketaux_api_key"];

export default async function SettingsPage() {
  const [settings, databases] = await Promise.all([getSettings(), getDatabases()]);

  if (!settings.ok) {
    return (
      <>
        <PageHeader description="Credentials, providers and databases." title="Settings" />
        <Panel title="Settings">
          <Unavailable
            detail={
              settings.status === 403
                ? "The settings API refused this request. It only answers callers on this machine."
                : settings.error
            }
            reason="Settings unavailable"
          />
        </Panel>
      </>
    );
  }

  const snapshot = settings.data;
  const t212Key = snapshot.credentials.find((row) => row.field === "t212_api_key");
  const t212Secret = snapshot.credentials.find((row) => row.field === "t212_api_secret");
  const t212Connected = Boolean(t212Key?.present && t212Secret?.present);
  const runningEnvironment =
    snapshot.t212Environment ??
    ((snapshot.editable.t212_base_url ?? "").includes("demo.") ? "demo" : "live");
  const dataCredentials = snapshot.credentials.filter((row) => DATA_CREDENTIAL_FIELDS.includes(row.field));
  const aiCredentials = snapshot.credentials.filter((row) => AI_CREDENTIAL_FIELDS.includes(row.field));
  const newsCredentials = snapshot.credentials.filter((row) => NEWS_CREDENTIAL_FIELDS.includes(row.field));
  const knownFields = new Set([
    ...T212_CREDENTIAL_FIELDS,
    ...DATA_CREDENTIAL_FIELDS,
    ...AI_CREDENTIAL_FIELDS,
    ...NEWS_CREDENTIAL_FIELDS,
  ]);
  const otherCredentials = snapshot.credentials.filter((row) => !knownFields.has(row.field));

  const missingRequired = snapshot.credentials.filter(
    (row) => row.requirement === "required" && !row.present,
  );
  const missingRecommended = snapshot.credentials.filter(
    (row) => row.requirement === "recommended" && !row.present,
  );
  // Under Docker Compose nothing saved here could ever be read, so every control renders
  // disabled rather than letting the operator submit a change that silently never applies.
  // The API refuses these writes too; this just says so before anyone types a key.
  const readOnly = !snapshot.writable;

  return (
    <>
      <PageHeader
        actions={readOnly ? undefined : <RestartButton action={restartApiAction} restartRequired={false} />}
        description="Credentials, providers, benchmarks and databases — nothing here is ever displayed back to you."
        title="Settings"
      />

      {readOnly ? (
        <Banner detail={snapshot.readOnlyReason ?? ""} eyebrow="Read-only" testId="settings-read-only" />
      ) : null}

      {snapshot.restartRequired && !readOnly ? (
        <Banner
          detail="Settings are read once at startup, so what you saved is on disk but not yet in use. Nothing here is live until Helios comes back."
          eyebrow="Restart pending"
          action={<RestartButton action={restartApiAction} restartRequired />}
        />
      ) : null}

      <fieldset className="contents" disabled={readOnly}>
        <Panel
          subtitle="The Trading 212 account Helios syncs from — a key from one environment never works against the other."
          title="Trading 212 account"
        >
          <div className="flex flex-col gap-5">
            {missingRequired.length > 0 ? (
              <Note>
                Helios has no data source until the Trading 212 pair is set — every page will
                read as empty rather than wrong, but it will read as empty.
              </Note>
            ) : null}

            <Trading212ConnectForm
              action={connectTrading212Action}
              connected={t212Connected}
              keyHint={t212Key?.hint ?? null}
              pending={snapshot.t212PendingEnvironment ?? runningEnvironment}
              running={runningEnvironment}
            />
          </div>
        </Panel>

        <Panel
          subtitle="Daily prices and the passive counterfactual benchmark."
          title="Data providers"
        >
          <div className="flex flex-col gap-5">
            {missingRecommended.length > 0 ? (
              <Note>
                Without a prices key, every price-dependent metric reports{" "}
                <code className="rounded bg-surface-3 px-1 py-0.5 font-mono text-xs text-ink-2">
                  unavailable
                </code>
                : no TWR, Sharpe, VaR, drawdown, beta or NAV chart. The ledger and holdings still
                work.
              </Note>
            ) : null}

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <ChoiceSettingForm
                action={saveSettingAction}
                choices={MARKET_DATA_PROVIDERS}
                current={snapshot.editable.market_data_provider ?? ""}
                description="Daily prices. Twelve Data's free tier allows 800 calls a day for US listings. With a paid EODHD plan, pick EODHD here: it prices US and international listings, with full history, in one call per holding. Set to Disabled and every price-dependent metric reports unavailable rather than being estimated."
                field="market_data_provider"
                label="Price provider"
              />
              <ChoiceSettingForm
                action={saveSettingAction}
                choices={MARKET_DATA_PROVIDERS}
                current={snapshot.editable.market_data_fallback_provider ?? "disabled"}
                description="Asked only for holdings the main provider cannot price. Twelve Data's free tier covers US listings only. For London-listed ETFs like VUAG or VWRP pick EODHD (full history) or Alpha Vantage (free, 25 requests a day, only the last ~100 trading days), and add its key below."
                field="market_data_fallback_provider"
                label="Second price source (London listings)"
              />
              <ChoiceSettingForm
                action={saveSettingAction}
                choices={BENCHMARKS}
                current={snapshot.editable.analytics_passive_benchmark_key ?? ""}
                description="The proxy behind the you-but-passive counterfactual. These are labelled ETF proxies, never licensed index levels."
                field="analytics_passive_benchmark_key"
                label="Passive benchmark"
              />
            </div>

            {dataCredentials.length > 0 ? (
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {dataCredentials.map((credential) => (
                  <CredentialForm action={saveCredentialAction} credential={credential} key={credential.field} />
                ))}
              </div>
            ) : null}
          </div>
        </Panel>

        <Panel subtitle="Claude reading your own analytics. Never advice." title="AI">
          <div className="flex flex-col gap-5">
            {aiCredentials.length > 0 ? (
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {aiCredentials.map((credential) => (
                  <CredentialForm action={saveCredentialAction} credential={credential} key={credential.field} />
                ))}
              </div>
            ) : null}
            {snapshot.editable.anthropic_model !== undefined ? (
              <TextSettingForm
                action={saveSettingAction}
                current={snapshot.editable.anthropic_model ?? ""}
                description="The Claude model Helios calls for the Insights page."
                field="anthropic_model"
                label="Claude model"
                placeholder="claude-opus-5"
              />
            ) : null}
          </div>
        </Panel>

        <Panel subtitle="Headline feeds, and the identity Helios gives the SEC's own feed." title="News">
          <div className="flex flex-col gap-5">
            {newsCredentials.length > 0 ? (
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {newsCredentials.map((credential) => (
                  <CredentialForm action={saveCredentialAction} credential={credential} key={credential.field} />
                ))}
              </div>
            ) : null}
            <TextSettingForm
              action={saveSettingAction}
              current={snapshot.editable.news_sec_user_agent ?? ""}
              description="The SEC requires a User-Agent naming a real contact. The EDGAR feed is enabled but stays skipped until this is set, rather than being sent with a fake identity."
              field="news_sec_user_agent"
              label="SEC contact"
              placeholder="Your Name you@example.com"
            />
          </div>
        </Panel>

        {otherCredentials.length > 0 ? (
          <Panel subtitle="Everything else Helios can use a key for." title="Other credentials">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {otherCredentials.map((credential) => (
                <CredentialForm action={saveCredentialAction} credential={credential} key={credential.field} />
              ))}
            </div>
          </Panel>
        ) : null}

        <Panel subtitle={`Active: ${snapshot.activeDatabase}`} title="Databases">
          {databases.ok ? (
            <div className="flex flex-col gap-5">
              <Note>
                Keep live and demo history in separate files. Replaying a live account into a
                database holding demo trades produces a portfolio that is neither, and no figure
                on any page would tell you which one you were reading.
              </Note>

              <DatabaseTable rows={databases.data.databases} />

              <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
                <CreateDatabaseForm action={createDatabaseAction} />
                <SwitchDatabaseForm action={switchDatabaseAction} databases={databases.data.databases} />
              </div>
            </div>
          ) : (
            <Unavailable detail={databases.error} reason="Database list unavailable" />
          )}
        </Panel>
      </fieldset>

      <Panel
        subtitle="Where a saved credential goes, and what that does and does not protect."
        title="Security notes"
      >
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-surface-2 px-3.5 py-2.5 text-sm">
            <span className="text-ink-3">Keyring backend</span>
            <span className="font-medium text-ink">{snapshot.keyringBackend}</span>
            <StatusBadge
              label={snapshot.keyringAvailable ? "Usable" : "Unusable"}
              status={snapshot.keyringAvailable ? "ok" : "warning"}
            />
            <span className="text-ink-3">{snapshot.keyringDetail}</span>
          </div>

          {readOnly ? (
            <Note>
              Credentials here come from the environment Helios was started with, so this page
              can report which are present but cannot change them.
            </Note>
          ) : snapshot.keyringAvailable ? (
            <Note>
              Credentials go to your OS keyring, not to a file. The backend was verified by
              writing a throwaway value and reading it back, because a keyring library reports a
              backend even when that backend silently discards writes.
            </Note>
          ) : (
            <Note>
              No usable keyring, so the dashboard will refuse to store a credential rather than
              write one to a plaintext file behind your back. Set them in{" "}
              <code className="rounded bg-surface-3 px-1 py-0.5 font-mono text-xs text-ink-2">
                {snapshot.envPath}
              </code>{" "}
              instead.
            </Note>
          )}

          <Note>
            A saved value is never sent back. The most this page knows about an existing key is
            its last four characters, which is enough to recognise one you pasted and useless to
            anyone who did not already have it.
          </Note>

          <Note>
            An environment variable still wins over the keyring, so exporting one to override a
            stored key works as you would expect.
          </Note>

          <Note>
            These routes accept requests only from this machine, refuse a browser-initiated
            cross-origin request, and are rate limited. That is defence in depth for a
            single-user loopback install — <span className="font-medium text-ink-2">not</span>{" "}
            authentication. A Helios reachable from the network needs real auth and CSRF
            protection instead.
          </Note>
        </div>
      </Panel>
    </>
  );
}

/** A warning-toned strip for the read-only and restart-pending states. */
function Banner({
  eyebrow,
  detail,
  action,
  testId,
}: {
  eyebrow: string;
  detail: string;
  action?: ReactNode;
  testId?: string;
}) {
  return (
    <section
      className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-warning/30 bg-warning-soft px-4 py-3.5"
      data-testid={testId}
    >
      <div className="flex items-start gap-3">
        <span aria-hidden="true" className="mt-0.5 text-warning">
          <AlertTriangle size={18} strokeWidth={2} />
        </span>
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-semibold text-warning">{eyebrow}</span>
          <span className="max-w-2xl text-sm leading-relaxed text-ink-2">{detail}</span>
        </div>
      </div>
      {action}
    </section>
  );
}

const DATABASE_COLUMNS: Column<DatabaseInfo>[] = [
  {
    key: "filename",
    header: "File",
    render: (row) => <span className="font-mono text-xs text-ink">{row.filename}</span>,
  },
  { key: "size", header: "Size", numeric: true, render: (row) => formatBytes(row.sizeBytes) },
  {
    key: "modified",
    header: "Modified",
    render: (row) => (row.modifiedAt ? formatDateTime(row.modifiedAt) : "—"),
  },
  {
    key: "schema",
    header: "Schema",
    render: (row) => (
      <span className="font-mono text-xs text-ink-3">{row.schemaVersion ?? "none"}</span>
    ),
  },
  {
    key: "active",
    header: "In use",
    render: (row) =>
      row.active ? (
        <StatusBadge label="Active" status="ok" />
      ) : (
        <span className="text-ink-4">—</span>
      ),
  },
];

function DatabaseTable({ rows }: { rows: DatabaseInfo[] }) {
  if (rows.length === 0) {
    return <Unavailable detail="No SQLite files in the data directory yet." reason="No databases" />;
  }

  return (
    <DataTable
      caption="Databases"
      columns={DATABASE_COLUMNS}
      rowKey={(row) => row.filename}
      rows={rows}
    />
  );
}

/** Binary units, because this is a file on disk and that is how the OS reports it. */
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(1)} ${units[unit]}`;
}
