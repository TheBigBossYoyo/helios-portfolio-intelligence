import { Search } from "lucide-react";
import { ActionButton } from "@/components/action-button";
import { DataTable, type Column } from "@/components/data-table";
import { Note, PageHeader, Panel, Unavailable } from "@/components/panel";
import { unwatchTickerAction, watchTickerAction } from "@/lib/actions";
import { getWatchlist, searchInstruments } from "@/lib/api";
import {
  EMPTY,
  decimalToNumber,
  displayTicker,
  formatDay,
  formatSignedPercent,
  holdingHref,
} from "@/lib/format";
import type { InstrumentMatch, WatchEntry } from "@/lib/types";
import { BUTTON, FIELD } from "@/lib/ui";

export const dynamic = "force-dynamic";

function priceText(value: string | null, currency: string | null): string {
  const number = decimalToNumber(value);
  if (number === null) return EMPTY;
  const digits = Math.abs(number) >= 1000 ? 0 : 2;
  return `${number.toLocaleString("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits })} ${currency ?? ""}`.trim();
}

function Change({ value }: { value: number | null }) {
  if (value === null) return <span className="text-ink-4">{EMPTY}</span>;
  return (
    <span className={value > 0 ? "text-positive" : value < 0 ? "text-negative" : "text-ink-2"}>
      {formatSignedPercent(value, 1)}
    </span>
  );
}

const WATCH_COLUMNS: Column<WatchEntry>[] = [
  {
    key: "instrument",
    header: "Instrument",
    render: (row) => (
      <div className="flex flex-col gap-0.5 whitespace-normal">
        <a className="font-medium text-ink hover:text-accent hover:underline" href={holdingHref(row.ticker)}>
          {row.name ?? displayTicker(row.ticker)}
        </a>
        <span className="text-xs text-ink-3">
          {displayTicker(row.ticker)}
          {row.held ? " · you own it" : ""}
          {row.note ? ` · ${row.note}` : ""}
        </span>
      </div>
    ),
  },
  {
    key: "price",
    header: "Last close",
    numeric: true,
    render: (row) =>
      row.priced ? (
        <span className="flex flex-col items-end">
          <span>{priceText(row.lastClose, row.currency)}</span>
          {row.lastDate ? <span className="text-xs text-ink-3">{formatDay(row.lastDate)}</span> : null}
        </span>
      ) : (
        <span className="text-xs text-ink-3">No price source for this listing</span>
      ),
  },
  { key: "day", header: "Day", numeric: true, render: (row) => <Change value={row.dayChangePct} /> },
  { key: "month", header: "1 month", numeric: true, render: (row) => <Change value={row.monthChangePct} /> },
  {
    key: "alerts",
    header: "Alerts",
    numeric: true,
    render: (row) => (
      <a className="text-ink-2 hover:text-accent hover:underline" href={holdingHref(row.ticker)}>
        {row.activeAlerts > 0 ? `${row.activeAlerts} waiting` : "Set one"}
      </a>
    ),
  },
  {
    key: "remove",
    header: "",
    render: (row) => (
      <ActionButton
        action={unwatchTickerAction.bind(null, row.ticker)}
        label="Remove"
        pendingLabel="Removing…"
        variant="secondary"
      />
    ),
  },
];

const MATCH_COLUMNS: Column<InstrumentMatch>[] = [
  {
    key: "instrument",
    header: "Instrument",
    render: (row) => (
      <div className="flex flex-col gap-0.5 whitespace-normal">
        <span className="font-medium text-ink">{row.name ?? displayTicker(row.ticker)}</span>
        <span className="text-xs text-ink-3">
          {displayTicker(row.ticker)}
          {row.isin ? ` · ${row.isin}` : ""}
        </span>
      </div>
    ),
  },
  { key: "type", header: "Type", render: (row) => <span className="text-ink-3">{row.instrumentType ?? EMPTY}</span> },
  { key: "currency", header: "Currency", render: (row) => <span className="text-ink-3">{row.currency ?? EMPTY}</span> },
  {
    key: "action",
    header: "",
    render: (row) =>
      row.watched ? (
        <span className="text-xs text-ink-3">Watching</span>
      ) : (
        <ActionButton
          action={watchTickerAction.bind(null, row.ticker)}
          label={row.held ? "Watch (you own it)" : "Watch"}
          pendingLabel="Adding…"
          variant="primary"
        />
      ),
  },
];

export default async function WatchlistPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { q } = await searchParams;
  const query = (q ?? "").trim();
  const [watchlist, matches] = await Promise.all([
    getWatchlist(),
    query ? searchInstruments(query) : Promise.resolve(null),
  ]);

  return (
    <>
      <PageHeader
        description="Stocks and ETFs you follow without owning them: daily prices, news that names them, and price alerts that check live quotes during the day."
        title="Watchlist"
      />

      <Panel subtitle="Search Trading 212's own instrument list by name, ticker or ISIN." title="Add">
        <form action="/watchlist" className="flex flex-wrap items-center gap-2" method="get">
          <label className="sr-only" htmlFor="watch-search">
            Search instruments
          </label>
          <input
            className={`${FIELD} min-w-0 flex-1`}
            defaultValue={query}
            id="watch-search"
            name="q"
            placeholder="e.g. Apple, ASML, IE00B4L5Y983"
          />
          <button className={`${BUTTON.primary} ${BUTTON.small}`} type="submit">
            <Search aria-hidden="true" size={15} />
            Search
          </button>
        </form>
        {matches ? (
          <div className="mt-4">
            {matches.ok ? (
              <DataTable
                caption={`Instruments matching ${query}`}
                columns={MATCH_COLUMNS}
                empty={`Nothing in Trading 212's list matches "${query}".`}
                rowKey={(row) => row.ticker}
                rows={matches.data}
              />
            ) : (
              <Unavailable detail={matches.error} reason="Search unavailable" />
            )}
          </div>
        ) : null}
      </Panel>

      <Panel
        subtitle="Each opens the same page as a holding: price chart, news and alerts."
        title="Following"
      >
        {watchlist.ok ? (
          watchlist.data.length > 0 ? (
            <DataTable
              caption="Watched instruments"
              columns={WATCH_COLUMNS}
              rowKey={(row) => row.ticker}
              rows={watchlist.data}
            />
          ) : (
            <p className="py-8 text-center text-sm text-ink-3">
              Nothing followed yet. Search above and press Watch.
            </p>
          )
        ) : (
          <Unavailable detail={watchlist.error} reason="Watchlist unavailable" />
        )}
        <div className="mt-4">
          <Note>
            Prices come from the same sources as your holdings (set in Settings). A listing no
            source covers still gets news and its page, but no prices or alerts. Alerts on a stock
            you don&apos;t own use the price source&apos;s live quote when it offers one, otherwise
            the last daily close.
          </Note>
        </div>
      </Panel>
    </>
  );
}
