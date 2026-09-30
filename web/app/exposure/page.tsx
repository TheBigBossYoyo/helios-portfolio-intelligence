import { AlertTriangle, RefreshCw } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { ActionButton } from "@/components/action-button";
import { DataTable, type Column } from "@/components/data-table";
import { Note, PageHeader, Panel, Unavailable } from "@/components/panel";
import { refreshExposureAction } from "@/lib/actions";
import { getExposure } from "@/lib/api";
import { displayTicker, formatEur, formatPercent, holdingHref } from "@/lib/format";
import type { Exposure, ExposureCompany, ExposureSlice } from "@/lib/types";
import { CARD } from "@/lib/ui";
import { SERIES } from "@/lib/viz";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "What you own" };

const SHOWN_COMPANIES = 20;
const REGIONS = new Intl.DisplayNames(["en"], { type: "region" });

function countryName(code: string): string {
  if (code.length !== 2) return code;
  try {
    return REGIONS.of(code) ?? code;
  } catch {
    return code;
  }
}

function CompanyRow({ company, scale }: { company: ExposureCompany; scale: number }) {
  const direct = company.directEur;
  const through = company.totalEur - direct;
  const width = (value: number) => `${(value / scale) * 100}%`;
  const via = company.via.map((item) => displayTicker(item.ticker)).join(", ");
  const name = company.ticker && !company.other ? (
    <Link className="truncate font-medium text-ink hover:underline" href={holdingHref(company.ticker)}>
      {company.name}
    </Link>
  ) : (
    <span className={`truncate font-medium ${company.other ? "text-ink-3" : "text-ink"}`}>{company.name}</span>
  );
  return (
    <li className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        {name}
        <span className="shrink-0 text-sm tabular-nums text-ink">
          {formatEur(company.totalEur)} <span className="text-ink-3">· {formatPercent(company.pct, 1)}</span>
        </span>
      </div>
      <div aria-hidden="true" className="flex h-2.5 overflow-hidden rounded-full bg-surface-3">
        {direct > 0 ? <span style={{ width: width(direct), background: SERIES.one }} /> : null}
        {through > 0 ? (
          <span
            style={{
              width: width(through),
              background: `color-mix(in srgb, ${SERIES.one} 40%, var(--surface-3))`,
              marginLeft: direct > 0 ? 2 : 0,
            }}
          />
        ) : null}
      </div>
      <span className="text-xs text-ink-3">
        {direct > 0 && through > 0
          ? `${formatEur(direct)} held directly · ${formatEur(through)} through ${via}`
          : through > 0
            ? `Through ${via}`
            : company.other
              ? ""
              : "Held directly"}
      </span>
    </li>
  );
}

function SliceBars({ slices, label }: { slices: ExposureSlice[]; label: (key: string) => string }) {
  const top = slices[0]?.pct ?? 1;
  return (
    <ul className="flex flex-col gap-3">
      {slices.slice(0, 12).map((slice, index) => (
        <li className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)_4.5rem] items-center gap-3" key={slice.key}>
          <span className="truncate text-sm text-ink">{label(slice.key)}</span>
          <span aria-hidden="true" className="h-2.5 rounded-full bg-surface-3">
            <span
              className="block h-full rounded-full"
              style={{ width: `${(slice.pct / top) * 100}%`, background: index === 0 ? SERIES.one : `color-mix(in srgb, ${SERIES.one} 55%, var(--surface-3))` }}
            />
          </span>
          <span className="text-right text-sm tabular-nums text-ink-2">{formatPercent(slice.pct, 1)}</span>
        </li>
      ))}
    </ul>
  );
}

const FUND_COLUMNS: Column<Exposure["funds"][number]>[] = [
  { key: "fund", header: "Your fund", render: (row) => <span className="font-medium text-ink">{row.name}</span> },
  { key: "proxy", header: "Looked through with", render: (row) => row.proxyLabel },
  { key: "report", header: "Holdings as of", render: (row) => row.reportDate ?? "Not loaded yet" },
  { key: "count", header: "Holdings", numeric: true, render: (row) => (row.holdingsCount ?? 0).toLocaleString("en-GB") },
  { key: "value", header: "Your value", numeric: true, render: (row) => formatEur(row.valueEur) },
];

export default async function ExposurePage() {
  const result = await getExposure();
  if (!result.ok) {
    return (
      <>
        <PageHeader title="What you own" />
        <Panel title="Exposure">
          <Unavailable detail={result.error} reason="Exposure unavailable" />
        </Panel>
      </>
    );
  }
  const data = result.data;
  const companies = data.companies.filter((row) => !row.other);
  const buckets = data.companies.filter((row) => row.other);
  const shown = companies.slice(0, SHOWN_COMPANIES);
  const scale = Math.max(...shown.map((row) => row.totalEur), 1);
  const topTen = companies.slice(0, 10).reduce((sum, row) => sum + row.pct, 0);
  const inFunds = data.funds.reduce((sum, row) => sum + row.valueEur, 0);

  return (
    <>
      <PageHeader
        actions={
          <ActionButton
            action={refreshExposureAction}
            icon={<RefreshCw aria-hidden="true" size={14} />}
            label="Refresh"
            pendingLabel="Asking the SEC…"
          />
        }
        description="Every company you own, directly or through your funds, and where your money really sits by country and sector."
        title="What you own"
      />

      {data.warnings.length > 0 ? (
        <section aria-label="Concentration" className="flex flex-col gap-2">
          {data.warnings.map((warning) => (
            <p
              className="flex items-start gap-2.5 rounded-2xl border border-[color-mix(in_srgb,var(--warning)_30%,transparent)] bg-warning-soft px-4 py-3 text-sm text-ink"
              key={warning}
            >
              <AlertTriangle aria-hidden="true" className="mt-0.5 shrink-0 text-warning" size={16} />
              <span>
                {warning} <span className="text-ink-3">One company going wrong would move your whole portfolio.</span>
              </span>
            </p>
          ))}
        </section>
      ) : null}

      <section aria-label="At a glance" className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        <Stat label="Everything, valued" value={formatEur(data.totalEur)} detail="Holdings plus cash" />
        <Stat label="Companies you own" value={companies.length.toLocaleString("en-GB")} detail="Including through your funds" />
        <Stat label="Top ten companies" value={formatPercent(topTen, 0)} detail="of everything you hold" />
        <Stat label="Held through funds" value={formatPercent(data.totalEur ? inFunds / data.totalEur : 0, 0)} detail={`${data.funds.length} fund(s) looked through`} />
      </section>

      <Panel
        subtitle="Your largest positions once funds are opened up. Dark: held directly. Light: through your funds."
        title="Companies"
      >
        {shown.length > 0 ? (
          <ul className="flex flex-col gap-4">
            {shown.map((company) => (
              <CompanyRow company={company} key={company.key} scale={scale} />
            ))}
          </ul>
        ) : (
          <Unavailable reason="Nothing held yet" />
        )}
        {buckets.length > 0 ? (
          <details className="mt-5">
            <summary className="cursor-pointer text-sm font-medium text-ink-2">
              The rest ({buckets.length})
            </summary>
            <ul className="mt-3 flex flex-col gap-4">
              {buckets.map((company) => (
                <CompanyRow company={company} key={company.key} scale={Math.max(scale, company.totalEur)} />
              ))}
            </ul>
          </details>
        ) : null}
      </Panel>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2 lg:gap-6">
        <Panel subtitle="Where the companies you own are based." title="Countries">
          <SliceBars label={countryName} slices={data.countries} />
        </Panel>
        <Panel subtitle="By the SEC's industry codes; unclassified where no US filing matches." title="Sectors">
          <SliceBars label={(key) => key} slices={data.sectors} />
        </Panel>
      </div>

      {data.funds.length > 0 ? (
        <Panel subtitle="Each fund is opened up with a US fund that tracks the same index and reports its holdings every quarter." title="Funds looked through">
          <DataTable caption="Funds looked through" columns={FUND_COLUMNS} rowKey={(row) => row.ticker} rows={data.funds} />
        </Panel>
      ) : null}

      {data.notes.map((note) => (
        <Note key={note}>{note}</Note>
      ))}
    </>
  );
}

function Stat({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className={`${CARD} flex flex-col gap-1.5 p-4`}>
      <span className="text-sm font-medium text-ink-3">{label}</span>
      <span className="text-2xl font-semibold tracking-tight text-ink">{value}</span>
      <span className="text-xs text-ink-3">{detail}</span>
    </div>
  );
}
