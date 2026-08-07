import { DataTable, type Column } from "@/components/data-table";
import { Note, Panel, Unavailable } from "@/components/panel";
import { StatusBadge } from "@/components/status-badge";
import { getQualityReport } from "@/lib/api";
import { EMPTY, formatDateTime, humanizeStatus } from "@/lib/format";
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
    render: (row) => <span className="text-neutral-500">{formatDateTime(row.lastAttemptAt)}</span>,
  },
  {
    key: "success",
    header: "Last success",
    render: (row) => <span className="text-neutral-500">{formatDateTime(row.lastSuccessAt)}</span>,
  },
  {
    key: "error",
    header: "Last error",
    render: (row) => <span className="text-neutral-600">{row.lastError ?? EMPTY}</span>,
  },
];

const MAPPING_COLUMNS: Column<InstrumentMappingIssueReport>[] = [
  { key: "ticker", header: "Ticker", render: (row) => row.t212Ticker },
  {
    key: "isin",
    header: "ISIN",
    render: (row) => <span className="text-neutral-500">{row.isin ?? EMPTY}</span>,
  },
  {
    key: "yahoo",
    header: "Mapped symbol",
    render: (row) => <span className="text-neutral-500">{row.yahooTicker ?? EMPTY}</span>,
  },
  {
    key: "status",
    header: "Mapping",
    render: (row) => <StatusBadge status={row.mappingStatus} />,
  },
  {
    key: "source",
    header: "Source",
    render: (row) => <span className="text-neutral-600">{row.mappingSource ?? EMPTY}</span>,
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
    render: (row) => <span className="text-neutral-600">{row.toleranceQuantity}</span>,
  },
  { key: "status", header: "Status", render: (row) => <StatusBadge status={row.status} /> },
];

export default async function DataQualityPage() {
  const result = await getQualityReport();

  if (!result.ok) {
    return (
      <Panel subtitle="Ingestion health and instrument mapping." title="Data quality">
        <Unavailable
          detail={
            result.status === 404
              ? "No quality report exists yet. Run `helios sync` (or POST /api/v1/portfolio/sync) first."
              : result.error
          }
          reason="Quality report unavailable"
        />
      </Panel>
    );
  }

  const report = result.data;
  const freshness = report.metadataFreshness;

  return (
    <>
      <section className="panel-raised flex flex-col gap-2 border border-border px-4 py-3 text-[11px] text-neutral-500 sm:flex-row sm:items-center sm:justify-between">
        <span className="flex items-center gap-2">
          Overall <StatusBadge status={report.overallStatus} />
        </span>
        <span>
          As of <span className="tabular-nums text-neutral-300">{formatDateTime(report.asOf)}</span>
        </span>
      </section>

      <Panel subtitle="Per-endpoint sync attempts recorded by the backend." title="Ingestion">
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
        <DataTable
          caption={title}
          columns={MAPPING_COLUMNS}
          rowKey={(row) => row.t212Ticker}
          rows={rows}
        />
      ) : (
        <p className="px-1 py-4 text-center font-mono text-xs text-neutral-600">
          {humanizeStatus("none")} — nothing to resolve
        </p>
      )}
    </Panel>
  );
}
