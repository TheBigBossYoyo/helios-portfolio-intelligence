import { ActionButton } from "@/components/action-button";
import { BarList, type BarListItem } from "@/components/charts/bar-list";
import { DataTable, type Column } from "@/components/data-table";
import { Note, PageHeader, Panel, Unavailable } from "@/components/panel";
import { StatusBadge } from "@/components/status-badge";
import { syncPortfolioAction } from "@/lib/actions";
import { getQualityReport } from "@/lib/api";
import { EMPTY, formatDateTime, humanizeStatus } from "@/lib/format";
import { CARD } from "@/lib/ui";
import { statusTone } from "@/lib/viz";
import type {
  EndpointAttemptReport,
  InstrumentMappingIssueReport,
  ReconciliationIssueReport,
} from "@/lib/types";

export const dynamic = "force-dynamic";

const ENDPOINT_COLUMNS: Column<EndpointAttemptReport>[] = [
  { key: "endpoint", header: "Endpoint", render: (row) => row.endpoint },
  {
    key: "status",
    header: "Status",
    render: (row) => <StatusBadge status={row.lastStatus ?? "unknown"} />,
  },
  {
    key: "items",
    header: "Items",
    numeric: true,
    render: (row) => (row.itemCount === null ? EMPTY : String(row.itemCount)),
  },
  {
    key: "attempt",
    header: "Last attempt",
    render: (row) => <span className="text-ink-3">{formatDateTime(row.lastAttemptAt)}</span>,
  },
  {
    key: "success",
    header: "Last success",
    render: (row) => <span className="text-ink-3">{formatDateTime(row.lastSuccessAt)}</span>,
  },
  {
    key: "error",
    header: "Last error",
    render: (row) => <span className="text-ink-3">{row.lastError ?? EMPTY}</span>,
  },
];

const MAPPING_COLUMNS: Column<InstrumentMappingIssueReport>[] = [
  { key: "ticker", header: "Ticker", render: (row) => row.t212Ticker },
  {
    key: "isin",
    header: "ISIN",
    render: (row) => <span className="text-ink-3">{row.isin ?? EMPTY}</span>,
  },
  {
    key: "yahoo",
    header: "Mapped symbol",
    render: (row) => <span className="text-ink-3">{row.yahooTicker ?? EMPTY}</span>,
  },
  {
    key: "status",
    header: "Mapping",
    render: (row) => <StatusBadge status={row.mappingStatus} />,
  },
  {
    key: "source",
    header: "Source",
    render: (row) => <span className="text-ink-3">{row.mappingSource ?? EMPTY}</span>,
  },
];

const RECONCILIATION_COLUMNS: Column<ReconciliationIssueReport>[] = [
  { key: "ticker", header: "Ticker", render: (row) => row.t212Ticker },
  {
    key: "replayed",
    header: "Replayed qty",
    numeric: true,
    render: (row) => row.replayedQuantity,
  },
  { key: "live", header: "Live qty", numeric: true, render: (row) => row.liveQuantity },
  {
    key: "difference",
    header: "Difference",
    numeric: true,
    render: (row) => row.differenceQuantity,
  },
  {
    key: "tolerance",
    header: "Tolerance",
    numeric: true,
    render: (row) => <span className="text-ink-3">{row.toleranceQuantity}</span>,
  },
  { key: "status", header: "Status", render: (row) => <StatusBadge status={row.status} /> },
];

export default async function DataQualityPage() {
  const result = await getQualityReport();

  if (!result.ok) {
    return (
      <>
        <PageHeader description="Ingestion health and instrument mapping." title="Data quality" />
        <Panel actions={<SyncButton />} title="Data quality">
          <Unavailable
            detail={
              result.status === 404
                ? "No quality report exists yet. Run a sync to build one."
                : result.error
            }
            reason="Quality report unavailable"
          />
        </Panel>
      </>
    );
  }

  const report = result.data;
  const freshness = report.metadataFreshness;

  const endpointsOk = report.endpointStatuses.filter(
    (row) => statusTone(row.lastStatus ?? "unknown") === "good",
  ).length;
  const endpointsTotal = report.endpointStatuses.length;
  const mappingIssueCount =
    report.unresolvedInstruments.length +
    report.ambiguousInstruments.length +
    report.overrideRequiredInstruments.length;
  const mismatchCount = report.reconciliationMismatches.length;
  const unsupportedCount = report.unsupportedActions.length;

  const issueBars: BarListItem[] = [
    { key: "unresolved", label: "Unresolved instruments", value: report.unresolvedInstruments.length, display: String(report.unresolvedInstruments.length) },
    { key: "ambiguous", label: "Ambiguous instruments", value: report.ambiguousInstruments.length, display: String(report.ambiguousInstruments.length) },
    { key: "override", label: "Override required", value: report.overrideRequiredInstruments.length, display: String(report.overrideRequiredInstruments.length) },
    { key: "mismatch", label: "Reconciliation mismatches", value: mismatchCount, display: String(mismatchCount) },
    { key: "unsupported", label: "Unsupported actions", value: unsupportedCount, display: String(unsupportedCount) },
  ].filter((item) => item.value > 0);

  return (
    <>
      <PageHeader
        actions={<SyncButton />}
        description="Ingestion health and instrument mapping — every number here is about the data itself, not the portfolio it describes."
        title="Data quality"
      />

      <div className={`${CARD} theme-fade flex flex-wrap items-center justify-between gap-3 px-4 py-3.5 text-sm`}>
        <span className="flex items-center gap-2 text-ink-2">
          Overall <StatusBadge status={report.overallStatus} />
        </span>
        <span className="text-ink-3">
          As of <span className="tabular text-ink">{formatDateTime(report.asOf)}</span>
        </span>
      </div>

      <section aria-label="Summary" className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <SummaryTile
          label="Endpoints"
          tone={endpointsTotal === 0 ? "neutral" : endpointsOk === endpointsTotal ? "positive" : "negative"}
          value={endpointsTotal === 0 ? EMPTY : `${endpointsOk}/${endpointsTotal} ok`}
        />
        <SummaryTile
          label="Mapping issues"
          tone={mappingIssueCount === 0 ? "positive" : "negative"}
          value={String(mappingIssueCount)}
        />
        <SummaryTile
          label="Reconciliation mismatches"
          tone={mismatchCount === 0 ? "positive" : "negative"}
          value={String(mismatchCount)}
        />
        <SummaryTile
          label="Unsupported actions"
          tone={unsupportedCount === 0 ? "positive" : "neutral"}
          value={String(unsupportedCount)}
        />
      </section>

      {issueBars.length > 0 ? (
        <Panel subtitle="Every open issue across the categories below, ranked by count." title="Issues by category">
          <BarList items={issueBars} />
        </Panel>
      ) : null}

      <Panel
        actions={<SyncButton />}
        subtitle="Per-endpoint sync attempts recorded by the backend."
        title="Ingestion"
      >
        <DataTable
          caption="Endpoint sync status"
          columns={ENDPOINT_COLUMNS}
          empty="No sync has run yet"
          rowKey={(row) => row.endpoint}
          rows={report.endpointStatuses}
        />
        <div className="mt-4">
          <Note>
            Instrument metadata is {freshness.fresh ? "fresh" : "stale"} against a{" "}
            {freshness.ttlHours}h TTL — last success {formatDateTime(freshness.lastSuccessAt)}.
          </Note>
        </div>
      </Panel>

      <MappingPanel
        detail="No ISIN match was found, so these instruments cannot be priced or valued."
        rows={report.unresolvedInstruments}
        title="Unresolved instruments"
      />
      <MappingPanel
        detail="More than one candidate matched. Add a manual override to disambiguate."
        rows={report.ambiguousInstruments}
        title="Ambiguous instruments"
      />
      <MappingPanel
        detail="These require an explicit entry in config/instrument_overrides.yaml."
        rows={report.overrideRequiredInstruments}
        title="Override required"
      />

      <Panel
        subtitle="Replayed quantities compared against the live Trading 212 snapshot."
        title="Reconciliation mismatches"
      >
        <DataTable
          caption="Reconciliation mismatches"
          columns={RECONCILIATION_COLUMNS}
          empty="No mismatches — replayed quantities agree with the live snapshot"
          rowKey={(row) => row.t212Ticker}
          rows={report.reconciliationMismatches}
        />
      </Panel>

      <Panel
        subtitle="Quantity-changing events Helios will not replay, because their semantics are not documented as a TRADE fill."
        title="Unsupported actions"
      >
        <DataTable
          caption="Unsupported actions"
          columns={RECONCILIATION_COLUMNS}
          empty="No unsupported actions"
          rowKey={(row) => row.t212Ticker}
          rows={report.unsupportedActions}
        />
      </Panel>
    </>
  );
}

/**
 * A sync walks every Trading 212 history page and rewrites the ledger, so it confirms first.
 * The backend also holds a lease and answers 409 on a second call, but the UI should not
 * invite the double-click in the first place.
 */
function SyncButton() {
  return (
    <ActionButton
      action={syncPortfolioAction}
      confirmLabel="Confirm sync"
      label="Sync now"
      pendingLabel="Syncing…"
    />
  );
}

function SummaryTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "positive" | "negative" | "neutral";
}) {
  return (
    <div className={`${CARD} theme-fade flex min-w-0 flex-col gap-2 p-4`}>
      <span className="text-sm font-medium text-ink-3">{label}</span>
      <span
        className={`text-2xl font-semibold leading-none tracking-tight ${
          tone === "positive" ? "text-positive" : tone === "negative" ? "text-negative" : "text-ink"
        }`}
      >
        {value}
      </span>
    </div>
  );
}

function MappingPanel({
  title,
  detail,
  rows,
}: {
  title: string;
  detail: string;
  rows: InstrumentMappingIssueReport[];
}) {
  return (
    <Panel subtitle={detail} title={`${title} (${rows.length})`}>
      {rows.length > 0 ? (
        <DataTable caption={title} columns={MAPPING_COLUMNS} rowKey={(row) => row.t212Ticker} rows={rows} />
      ) : (
        <p className="px-1 py-4 text-center text-sm text-ink-3">{humanizeStatus("none")} — nothing to resolve</p>
      )}
    </Panel>
  );
}
