import { ArrowDownRight, ArrowUpRight, Newspaper } from "lucide-react";
import type { ReactNode } from "react";
import { signedEur } from "@/components/period-change";
import {
  EMPTY,
  decimalToNumber,
  displayTicker,
  holdingHref,
  formatDay,
  formatEur,
  formatSignedPercent,
} from "@/lib/format";
import type { HoldingMovement, NewsItem, PeriodSummary } from "@/lib/types";
import { DIVERGING } from "@/lib/viz";

interface Mover {
  item: HoldingMovement;
  result: number;
  story: NewsItem | null;
}

/** Days after a period's last close whose news still explains it (a weekend's worth). */
const STORY_GRACE_DAYS = 3;

/**
 * The newest story that names this holding and was published during the period, or in the few
 * days after its last close (when a weekend's news lands). Only stories whose text names the
 * company are candidates, so the line under a mover is about that mover.
 */
function storyFor(ticker: string, period: PeriodSummary, news: NewsItem[]): NewsItem | null {
  const since = period.startDate ? `${period.startDate}T00:00:00` : "";
  let until = "9999";
  if (period.endDate) {
    const end = new Date(`${period.endDate}T00:00:00Z`);
    end.setUTCDate(end.getUTCDate() + STORY_GRACE_DAYS + 1);
    until = end.toISOString();
  }
  return (
    news.find(
      (item) =>
        item.t212Ticker === ticker &&
        (item.relevance === "headline" || item.relevance === "summary") &&
        item.publishedAt !== null &&
        item.publishedAt >= since &&
        item.publishedAt < until,
    ) ?? null
  );
}

/**
 * Which holdings made the period's investment result, split into the ones that rose and the
 * ones that fell, each on the same € scale. Buying or selling during the period is shown as a
 * fact beside the holding, never as part of its result. What no single holding explains
 * (interest, fees) closes the list, so the rows add up to the headline figure.
 */
export function HoldingMovers({ period, news }: { period: PeriodSummary; news: NewsItem[] }) {
  const holdings = period.holdings ?? [];
  const priced: Mover[] = [];
  const unpriced: HoldingMovement[] = [];
  for (const item of holdings) {
    const result = decimalToNumber(item.resultEur);
    if (item.status !== "ok" || result === null) {
      unpriced.push(item);
      continue;
    }
    priced.push({ item, result, story: storyFor(item.ticker, period, news) });
  }

  if (priced.length === 0 && unpriced.length === 0) {
    return (
      <p className="px-1 py-8 text-center text-sm text-ink-3">
        No per-stock split for this period: nothing was held or traded, or history has not been
        replayed since the last update.
      </p>
    );
  }

  const scale = Math.max(...priced.map((mover) => Math.abs(mover.result)), 0.01);
  const fell = priced.filter((mover) => mover.result < 0).sort((a, b) => a.result - b.result);
  const rose = priced.filter((mover) => mover.result >= 0).sort((a, b) => b.result - a.result);
  const unattributed = decimalToNumber(period.unattributedEur ?? null);
  const total = decimalToNumber(period.investmentResultEur);

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <MoverColumn
          empty="Nothing fell in this period."
          icon={<ArrowDownRight aria-hidden="true" size={16} />}
          movers={fell}
          scale={scale}
          title={`Fell · ${fell.length}`}
          tone="down"
        />
        <MoverColumn
          empty="Nothing rose in this period."
          icon={<ArrowUpRight aria-hidden="true" size={16} />}
          movers={rose}
          scale={scale}
          title={`Rose · ${rose.length}`}
          tone="up"
        />
      </div>

      {unpriced.length > 0 ? (
        <p className="text-xs text-ink-3">
          Not split for lack of a price at one end of the period:{" "}
          {unpriced.map((item) => displayTicker(item.ticker)).join(", ")}.
        </p>
      ) : null}

      <dl className="flex flex-wrap gap-x-8 gap-y-2 border-t border-border pt-4 text-sm">
        <div className="flex items-baseline gap-2">
          <dt className="text-ink-3">All holdings</dt>
          <dd className="tabular-nums font-medium text-ink">
            {signedEur(priced.reduce((sum, mover) => sum + mover.result, 0))}
          </dd>
        </div>
        {unattributed !== null ? (
          <div className="flex items-baseline gap-2">
            <dt className="text-ink-3">Interest, cashback, fees and other cash</dt>
            <dd className="tabular-nums font-medium text-ink">{signedEur(unattributed)}</dd>
          </div>
        ) : null}
        <div className="flex items-baseline gap-2">
          <dt className="text-ink-3">Investment result</dt>
          <dd
            className={`tabular-nums font-semibold ${
              total !== null && total < 0 ? "text-negative" : total ? "text-positive" : "text-ink"
            }`}
          >
            {signedEur(total)}
          </dd>
        </div>
      </dl>

      <details>
        <summary className="cursor-pointer text-sm font-medium text-ink-2">
          Show every figure
        </summary>
        <div className="mt-3">
          <HoldingTable holdings={holdings} />
        </div>
      </details>
    </div>
  );
}

function MoverColumn({
  title,
  icon,
  tone,
  movers,
  scale,
  empty,
}: {
  title: string;
  icon: ReactNode;
  tone: "up" | "down";
  movers: Mover[];
  scale: number;
  empty: string;
}) {
  return (
    <section aria-label={title} className="flex min-w-0 flex-col gap-3">
      <h3
        className={`flex items-center gap-1.5 text-sm font-semibold ${
          tone === "up" ? "text-positive" : "text-negative"
        }`}
      >
        {icon}
        {title}
      </h3>
      {movers.length === 0 ? (
        <p className="rounded-xl bg-surface-2 px-3 py-4 text-sm text-ink-3">{empty}</p>
      ) : (
        <ul className="flex flex-col gap-4">
          {movers.map((mover) => (
            <MoverRow key={mover.item.ticker} mover={mover} scale={scale} />
          ))}
        </ul>
      )}
    </section>
  );
}

function MoverRow({ mover, scale }: { mover: Mover; scale: number }) {
  const { item, result, story } = mover;
  const bought = decimalToNumber(item.boughtEur) ?? 0;
  const sold = decimalToNumber(item.soldEur) ?? 0;
  const dividends = decimalToNumber(item.dividendsEur) ?? 0;
  const endQuantity = decimalToNumber(item.endQuantity) ?? 0;
  const startQuantity = decimalToNumber(item.startQuantity) ?? 0;
  const notes: string[] = [];
  if (startQuantity === 0 && bought > 0) notes.push(`bought ${formatEur(bought)} in this period`);
  else if (bought > 0) notes.push(`added ${formatEur(bought)}`);
  if (endQuantity === 0 && sold > 0) notes.push(`sold all for ${formatEur(sold)}`);
  else if (sold > 0) notes.push(`sold ${formatEur(sold)}`);
  if (dividends > 0) notes.push(`${formatEur(dividends)} dividends`);
  const width = Math.max((Math.abs(result) / scale) * 100, result === 0 ? 0 : 1.5);

  return (
    <li className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 truncate text-sm">
          <a className="font-semibold text-ink hover:text-accent hover:underline" href={holdingHref(item.ticker)}>
            {displayTicker(item.ticker)}
          </a>
          {item.name ? <span className="ml-1.5 text-ink-3">{item.name}</span> : null}
        </span>
        <span className="flex shrink-0 items-baseline gap-2 tabular-nums">
          <span
            className={`text-sm font-semibold ${result < 0 ? "text-negative" : result > 0 ? "text-positive" : "text-ink"}`}
          >
            {signedEur(result)}
          </span>
          <span className="w-16 text-right text-xs text-ink-3">
            {item.returnPct !== null ? formatSignedPercent(item.returnPct, 1) : EMPTY}
          </span>
        </span>
      </div>
      <div aria-hidden="true" className="h-1.5 w-full rounded-full bg-surface-3">
        <div
          className="h-full rounded-full"
          style={{
            width: `${width}%`,
            background: result < 0 ? DIVERGING.negative : DIVERGING.positive,
          }}
        />
      </div>
      {notes.length > 0 || item.priceChangePct !== null ? (
        <p className="text-xs text-ink-3">
          {item.priceChangePct !== null
            ? `Price ${formatSignedPercent(item.priceChangePct, 1)} in €`
            : null}
          {item.priceChangePct !== null && notes.length > 0 ? " · " : null}
          {notes.join(" · ")}
        </p>
      ) : null}
      {story ? (
        <a
          className="group flex items-start gap-1.5 rounded-lg bg-surface-2 px-2.5 py-1.5 text-xs leading-snug text-ink-2 hover:text-accent"
          href={story.url}
          rel="noopener noreferrer nofollow"
          target="_blank"
        >
          <Newspaper aria-hidden="true" className="mt-px shrink-0 text-ink-4" size={13} />
          <span className="min-w-0">
            <span className="line-clamp-2 group-hover:underline">{story.headline}</span>
            <span className="text-ink-4">
              {story.sourceLabel}
              {story.publishedAt ? ` · ${formatDay(story.publishedAt)}` : ""}
            </span>
          </span>
        </a>
      ) : null}
    </li>
  );
}

/** The table twin: every number behind each row, readable without the bars. */
function HoldingTable({ holdings }: { holdings: HoldingMovement[] }) {
  const money = (value: string | null) => {
    const number = decimalToNumber(value);
    return number === null || number === 0 ? EMPTY : formatEur(number);
  };
  return (
    <div className="overflow-x-auto rounded-xl border border-border">
      <table className="w-full border-collapse whitespace-nowrap text-sm">
        <caption className="sr-only">Each holding&apos;s result over the period</caption>
        <thead className="bg-surface-2">
          <tr className="border-b border-border text-xs text-ink-3">
            {[
              "Holding",
              "Value at start",
              "Bought",
              "Sold",
              "Dividends",
              "Value at end",
              "Result",
              "Return",
              "Price move",
            ].map((header, index) => (
              <th
                className={`px-3 py-2 font-medium ${index === 0 ? "text-left" : "text-right"}`}
                key={header}
                scope="col"
              >
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {holdings.map((item) => {
            const result = decimalToNumber(item.resultEur);
            return (
              <tr className="border-b border-border last:border-b-0" key={item.ticker}>
                <td className="px-3 py-2">
                  <a
                    className="font-medium text-ink hover:text-accent hover:underline"
                    href={holdingHref(item.ticker)}
                  >
                    {displayTicker(item.ticker)}
                  </a>
                  {item.name ? <span className="ml-1.5 text-ink-3">{item.name}</span> : null}
                </td>
                <td className="tabular-nums px-3 py-2 text-right text-ink-2">
                  {money(item.startValueEur)}
                </td>
                <td className="tabular-nums px-3 py-2 text-right text-ink-2">{money(item.boughtEur)}</td>
                <td className="tabular-nums px-3 py-2 text-right text-ink-2">{money(item.soldEur)}</td>
                <td className="tabular-nums px-3 py-2 text-right text-ink-2">
                  {money(item.dividendsEur)}
                </td>
                <td className="tabular-nums px-3 py-2 text-right text-ink-2">
                  {money(item.endValueEur)}
                </td>
                <td
                  className={`tabular-nums px-3 py-2 text-right font-medium ${
                    result === null || result === 0
                      ? "text-ink-2"
                      : result > 0
                        ? "text-positive"
                        : "text-negative"
                  }`}
                >
                  {signedEur(result)}
                </td>
                <td className="tabular-nums px-3 py-2 text-right text-ink-2">
                  {item.returnPct !== null ? formatSignedPercent(item.returnPct, 1) : EMPTY}
                </td>
                <td className="tabular-nums px-3 py-2 text-right text-ink-2">
                  {item.priceChangePct !== null ? formatSignedPercent(item.priceChangePct, 1) : EMPTY}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
