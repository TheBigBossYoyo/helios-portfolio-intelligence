import { ArrowDownRight, ArrowLeft, ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { ActionButton } from "@/components/action-button";
import { AlertForm } from "@/components/alert-form";
import { NavChart } from "@/components/charts/nav-chart";
import { PriceChart, type PriceRow } from "@/components/charts/price-chart";
import { DataTable, type Column } from "@/components/data-table";
import { NewsFeed } from "@/components/news-feed";
import { signedEur } from "@/components/period-change";
import { Note, PageHeader, Panel, Unavailable } from "@/components/panel";
import {
  getAccountSummary,
  getInstrumentDetail,
  getNews,
  getAlerts,
  getPositions,
  getTheses,
} from "@/lib/api";
import {
  EMPTY,
  decimalToNumber,
  displayTicker,
  formatDateTime,
  formatDay,
  formatEur,
  formatPercent,
  formatQuantity,
  formatSignedPercent,
} from "@/lib/format";
import { createAlertAction, deleteAlertAction } from "@/lib/actions";
import type { InstrumentDetail } from "@/lib/types";
import { CARD, LINK } from "@/lib/ui";

export const dynamic = "force-dynamic";

const RANGES = [
  { key: "1M", label: "1M", months: 1 },
  { key: "3M", label: "3M", months: 3 },
  { key: "6M", label: "6M", months: 6 },
  { key: "1Y", label: "1Y", months: 12 },
  { key: "ALL", label: "All", months: null },
] as const;
type RangeKey = (typeof RANGES)[number]["key"];

type Trade = InstrumentDetail["trades"][number];
type Dividend = InstrumentDetail["dividends"][number];
type PeriodRow = InstrumentDetail["periods"][number];

function money(value: string | null | undefined): number | null {
  return decimalToNumber(value ?? null);
}

function priceText(value: number | null, currency: string | null): string {
  if (value === null) return EMPTY;
  const digits = Math.abs(value) >= 1000 ? 0 : 2;
  return `${value.toLocaleString("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits })} ${currency ?? ""}`.trim();
}

function toneOf(value: number | null): string {
  if (value === null || value === 0) return "text-ink-2";
  return value > 0 ? "text-positive" : "text-negative";
}

/** Rows from the range start to the last close, with each trade placed on its day's row. */
function priceRows(detail: InstrumentDetail, range: RangeKey): PriceRow[] {
  const points = detail.prices;
  if (points.length === 0) return [];
  const months = RANGES.find((item) => item.key === range)?.months ?? null;
  let startDate = points[0].asOfDate;
  if (months !== null) {
    const last = new Date(`${points.at(-1)?.asOfDate}T00:00:00Z`);
    last.setUTCMonth(last.getUTCMonth() - months);
    startDate = last.toISOString().slice(0, 10);
  }
  const rows: PriceRow[] = points
    .filter((point) => point.asOfDate >= startDate)
    .map((point) => ({ date: point.asOfDate, close: money(point.close), bought: null, sold: null }));
  for (const trade of detail.trades) {
    const day = trade.ts.slice(0, 10);
    // The trade sits on its own day's close, or the last close before it (a weekend fill).
    const row = [...rows].reverse().find((candidate) => candidate.date <= day);
    const price = money(trade.price);
    if (!row || price === null) continue;
    if (trade.side === "BUY") row.bought = price;
    else if (trade.side === "SELL") row.sold = price;
  }
  return rows;
}

const PERIOD_COLUMNS: Column<PeriodRow>[] = [
  { key: "period", header: "Period", render: (row) => row.label },
  {
    key: "result",
    header: "Your result",
    numeric: true,
    render: (row) => (
      <span className={`font-medium ${toneOf(money(row.resultEur))}`}>{signedEur(money(row.resultEur))}</span>
    ),
  },
  {
    key: "return",
    header: "Return",
    numeric: true,
    render: (row) => (
      <span className={toneOf(row.returnPct)}>
        {row.returnPct !== null ? formatSignedPercent(row.returnPct, 1) : EMPTY}
      </span>
    ),
  },
  {
    key: "price",
    header: "Price move (€)",
    numeric: true,
    render: (row) => (
      <span className="text-ink-2">
        {row.priceChangePct !== null ? formatSignedPercent(row.priceChangePct, 1) : EMPTY}
      </span>
    ),
  },
];

function tradeColumns(currency: string | null): Column<Trade>[] {
  return [
    {
      key: "date",
      header: "Date",
      render: (row) => <span className="whitespace-nowrap text-ink-2">{formatDateTime(row.ts)}</span>,
    },
    {
      key: "side",
      header: "Trade",
      render: (row) => (
        <span
          className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
            row.side === "BUY" ? "bg-accent-soft text-accent-ink" : "bg-surface-3 text-ink-2"
          }`}
        >
          {row.side === "BUY" ? "Bought" : row.side === "SELL" ? "Sold" : row.side}
        </span>
      ),
    },
    {
      key: "shares",
      header: "Shares",
      numeric: true,
      render: (row) => (row.quantity ? formatQuantity(row.quantity) : EMPTY),
    },
    {
      key: "price",
      header: "Price",
      numeric: true,
      render: (row) => priceText(money(row.price), currency),
    },
    { key: "amount", header: "Amount", numeric: true, render: (row) => formatEur(row.valueEur) },
    {
      key: "realised",
      header: "Realised",
      numeric: true,
      render: (row) => {
        const value = money(row.realisedEur);
        return value === null || value === 0 ? (
          <span className="text-ink-4">{EMPTY}</span>
        ) : (
          <span className={toneOf(value)}>{signedEur(value)}</span>
        );
      },
    },
  ];
}

const DIVIDEND_COLUMNS: Column<Dividend>[] = [
  { key: "date", header: "Paid", render: (row) => formatDay(row.paidOn) },
  {
    key: "shares",
    header: "Shares",
    numeric: true,
    render: (row) => (row.quantity ? formatQuantity(row.quantity) : EMPTY),
  },
  { key: "amount", header: "Amount", numeric: true, render: (row) => formatEur(row.amountEur) },
];

export default async function HoldingDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ ticker: string }>;
  searchParams: Promise<{ range?: string }>;
}) {
  const [{ ticker: rawTicker }, { range: rawRange }] = await Promise.all([params, searchParams]);
  const ticker = decodeURIComponent(rawTicker);
  const range: RangeKey = RANGES.some((item) => item.key === rawRange)
    ? (rawRange as RangeKey)
    : "6M";
  const [result, positions, account, news, theses, alerts] = await Promise.all([
    getInstrumentDetail(ticker),
    getPositions(),
    getAccountSummary(),
    getNews({ ticker, limit: 8, mentionsOnly: true }),
    getTheses(),
    getAlerts(ticker),
  ]);

  const back = (
    <Link className={`${LINK} inline-flex items-center gap-1 text-sm`} href="/holdings">
      <ArrowLeft aria-hidden="true" size={15} /> Holdings
    </Link>
  );

  if (!result.ok) {
    return (
      <>
        <PageHeader actions={back} title={displayTicker(ticker)} />
        <Panel title="Details">
          <Unavailable
            detail={
              result.status === 404
                ? "Helios has no trade, price or position for this ticker."
                : result.error
            }
            reason={result.status === 404 ? "Unknown instrument" : "Details unavailable"}
          />
        </Panel>
      </>
    );
  }

  const detail = result.data;
  const live = positions.ok
    ? (positions.data.find((position) => position.instrument.ticker === ticker) ?? null)
    : null;
  const currency = live?.instrument.currency ?? detail.currency;
  const closes = detail.prices;
  const lastClose = money(closes.at(-1)?.close);
  const previousClose = money(closes.at(-2)?.close);
  const currentPrice = money(live?.currentPrice) ?? lastClose;
  const dayChange =
    lastClose !== null && previousClose ? lastClose / previousClose - 1 : null;
  const liveValue = money(live?.walletImpact?.currentValue) ?? money(detail.valueEur);
  const unrealised = money(live?.walletImpact?.unrealizedProfitLoss);
  const averageCost = money(live?.averagePricePaid);
  const quantity = money(live?.quantity) ?? money(detail.quantity) ?? 0;
  const total = account.ok ? money(account.data.totalValue) : null;
  const weight = liveValue !== null && total ? liveValue / total : null;
  const lifetime = money(detail.resultEur);
  const rows = priceRows(detail, range);
  const positionRows = detail.positions
    .filter((point) => rows.length === 0 || point.asOfDate >= rows[0].date)
    .map((point) => ({
      date: point.asOfDate,
      nav: money(point.valueEur),
      passive: null,
      invested: money(point.investedEur),
    }));
  const myTheses = theses.ok ? theses.data.filter((item) => item.t212Ticker === ticker) : [];
  const title = detail.name ?? displayTicker(ticker);

  return (
    <>
      <PageHeader
        actions={back}
        description={
          <>
            <span className="font-medium text-ink-2">{displayTicker(ticker)}</span>
            {[detail.isin, detail.exchange, currency, detail.instrumentType, detail.sector]
              .filter(Boolean)
              .map((part) => ` · ${part}`)
              .join("")}
          </>
        }
        title={title}
      />

      <section className={`${CARD} hero-wash grid grid-cols-1 gap-6 p-5 sm:p-6 lg:grid-cols-3`}>
        <div className="flex flex-col gap-2">
          <span className="text-sm font-medium text-ink-3">Price</span>
          <span className="text-4xl font-semibold leading-none tracking-tight text-ink">
            {priceText(currentPrice, currency)}
          </span>
          <span className={`flex items-center gap-1 text-sm ${toneOf(dayChange)}`}>
            {dayChange !== null && dayChange > 0 ? <ArrowUpRight aria-hidden="true" size={15} /> : null}
            {dayChange !== null && dayChange < 0 ? <ArrowDownRight aria-hidden="true" size={15} /> : null}
            {dayChange !== null
              ? `${formatSignedPercent(dayChange)} on the last close (${formatDay(closes.at(-1)?.asOfDate)})`
              : "No price history yet"}
          </span>
        </div>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:col-span-2">
          <Figure label="You own" value={quantity > 0 ? `${formatQuantity(String(quantity))} shares` : "None now"} />
          <Figure
            detail={weight !== null ? `${formatPercent(weight, 1)} of your account` : undefined}
            label="Worth"
            value={formatEur(liveValue)}
          />
          <Figure label="Average price paid" value={priceText(averageCost, currency)} />
          <Figure
            label="Unrealised P/L"
            tone={unrealised}
            value={unrealised !== null ? signedEur(unrealised) : EMPTY}
          />
          <Figure
            detail={`Bought ${formatEur(detail.boughtEur)} · sold ${formatEur(detail.soldEur)}${
              (money(detail.dividendsEur) ?? 0) > 0 ? ` · dividends ${formatEur(detail.dividendsEur)}` : ""
            }`}
            label="Made in total"
            tone={lifetime}
            value={lifetime !== null ? signedEur(lifetime) : EMPTY}
          />
          <Figure
            label="First bought"
            value={detail.firstBought ? formatDay(detail.firstBought) : EMPTY}
          />
        </dl>
      </section>

      <Panel
        actions={
          <nav aria-label="Price range" className="flex gap-1 rounded-xl bg-surface-3 p-1">
            {RANGES.map((item) => (
              <a
                aria-current={item.key === range ? "true" : undefined}
                className={`rounded-lg px-3 py-1 text-xs font-medium transition-colors ${
                  item.key === range ? "bg-surface text-ink shadow-card" : "text-ink-3 hover:text-ink"
                }`}
                href={`?range=${item.key}`}
                key={item.key}
              >
                {item.label}
              </a>
            ))}
          </nav>
        }
        subtitle="Daily closes in the instrument's currency, with your own trades marked."
        title="Price"
      >
        {rows.length > 1 ? (
          <PriceChart averageCost={averageCost} currency={currency ?? ""} data={rows} />
        ) : (
          <Unavailable
            detail="Helios stores the daily closes it fetches when it replays your history. Replay once a price source is set up."
            reason="No price history"
          />
        )}
        <ul aria-label="Price change" className="mt-5 grid grid-cols-3 gap-3 sm:grid-cols-4 lg:grid-cols-7">
          {detail.priceReturns.map((item) => (
            <li className="flex flex-col gap-0.5 rounded-xl bg-surface-2 px-3 py-2" key={item.key}>
              <span className="text-xs text-ink-3">{item.label}</span>
              <span className={`tabular-nums text-sm font-semibold ${toneOf(item.changePct)}`}>
                {item.changePct !== null ? formatSignedPercent(item.changePct, 1) : EMPTY}
              </span>
            </li>
          ))}
        </ul>
        {detail.high && detail.low ? (
          <p className="mt-3 text-xs text-ink-3">
            Highest close {priceText(money(detail.high.close), currency)} on {formatDay(detail.high.asOfDate)} ·
            lowest {priceText(money(detail.low.close), currency)} on {formatDay(detail.low.asOfDate)} · history from{" "}
            {formatDay(closes[0]?.asOfDate)}
          </p>
        ) : null}
      </Panel>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Panel
          subtitle="What your shares were worth each day, against the money you had in them. The gap is your result."
          title="Your position over time"
        >
          {positionRows.length > 1 ? (
            <NavChart
              data={positionRows}
              height={260}
              investedLabel="Money in it"
              passiveLabel={null}
              showInvested
              valueLabel="Value"
            />
          ) : (
            <Unavailable detail="Replay your history to see it." reason="No position history" />
          )}
        </Panel>
        <Panel
          subtitle="This holding's line of the Overview's stock-by-stock split, for every period."
          title="Your result by period"
        >
          <DataTable
            caption={`${displayTicker(ticker)}: result by period`}
            columns={PERIOD_COLUMNS}
            rowKey={(row) => row.key}
            rows={detail.periods}
          />
        </Panel>
      </div>

      <Panel subtitle="Every fill, newest first. Amounts are what moved in your account, in EUR." title="Your trades">
        <DataTable
          caption={`${displayTicker(ticker)} trades`}
          columns={tradeColumns(currency)}
          empty="No trades recorded."
          rowKey={(row) => `${row.ts}-${row.side}-${row.quantity}`}
          rows={[...detail.trades].reverse()}
        />
      </Panel>

      {detail.dividends.length > 0 ? (
        <Panel subtitle="Paid into your account." title="Dividends">
          <DataTable
            caption={`${displayTicker(ticker)} dividends`}
            columns={DIVIDEND_COLUMNS}
            rowKey={(row) => row.paidOn}
            rows={[...detail.dividends].reverse()}
          />
        </Panel>
      ) : null}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <Panel
          actions={
            <a className={`${LINK} text-sm`} href={`/news?ticker=${encodeURIComponent(ticker)}`}>
              All news
            </a>
          }
          className="xl:col-span-2"
          subtitle="Stories that name this holding."
          title="News"
        >
          {news.ok ? (
            news.data.length > 0 ? (
              <NewsFeed compact items={news.data} />
            ) : (
              <p className="py-6 text-center text-sm text-ink-3">No stored story names this holding yet.</p>
            )
          ) : (
            <Unavailable detail={news.error} reason="News unavailable" />
          )}
        </Panel>
        <div className="flex flex-col gap-6">
        <Panel
          subtitle="A Windows notification when a price, or your gain or loss, is reached. Checked every few minutes; each alert fires once."
          title="Alerts"
        >
          {alerts.ok && alerts.data.length > 0 ? (
            <ul className="mb-4 flex flex-col divide-y divide-border">
              {alerts.data.map((alert) => (
                <li className="flex items-start justify-between gap-3 py-2.5 first:pt-0" key={alert.id}>
                  <div className="flex min-w-0 flex-col gap-0.5">
                    <span className="text-sm font-medium text-ink">
                      {alertText(alert.kind, money(alert.threshold), currency)}
                    </span>
                    <span className="text-xs text-ink-3">
                      {alert.active
                        ? "Waiting"
                        : `Fired ${alert.triggeredAt ? formatDateTime(alert.triggeredAt) : ""} at ${priceText(money(alert.triggeredPrice), currency)}`}
                      {alert.note ? ` · ${alert.note}` : ""}
                    </span>
                  </div>
                  <ActionButton
                    action={deleteAlertAction.bind(null, alert.id, ticker)}
                    label="Remove"
                    pendingLabel="Removing…"
                    variant="secondary"
                  />
                </li>
              ))}
            </ul>
          ) : null}
          <AlertForm
            action={createAlertAction}
            currency={currency}
            currentPrice={currentPrice}
            held={quantity > 0 && averageCost !== null}
            ticker={ticker}
          />
        </Panel>
        <Panel subtitle="Why you bought it, in your own words." title="Your theses">
          {myTheses.length > 0 ? (
            <ul className="flex flex-col gap-3">
              {myTheses.map((thesis) => (
                <li key={thesis.id}>
                  <a className={`${LINK} text-sm`} href={`/journal/${thesis.id}`}>
                    {thesis.title}
                  </a>
                  <p className="text-xs text-ink-3">
                    {thesis.status} · {thesis.conviction} conviction · since {formatDay(thesis.openedOn)}
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <Note>
              No thesis for this holding yet. Writing down why you bought it, before you know how it
              turns out, is what the Journal is for.
            </Note>
          )}
        </Panel>
        </div>
      </div>
    </>
  );
}

function alertText(kind: string, threshold: number | null, currency: string | null): string {
  if (kind === "above") return `Price rises to ${priceText(threshold, currency)}`;
  if (kind === "below") return `Price falls to ${priceText(threshold, currency)}`;
  if (kind === "gain_pct") return `Gain on your average reaches ${threshold ?? EMPTY}%`;
  return `Loss on your average reaches ${threshold ?? EMPTY}%`;
}

function Figure({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail?: string;
  tone?: number | null;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs font-medium text-ink-3">{label}</dt>
      <dd className={`tabular-nums text-lg font-semibold ${tone !== undefined ? toneOf(tone) : "text-ink"}`}>
        {value}
      </dd>
      {detail ? <dd className="text-xs text-ink-3">{detail}</dd> : null}
    </div>
  );
}
