import { Building2 } from "lucide-react";

import { Panel } from "@/components/panel";
import { formatDateTime, formatPercent } from "@/lib/format";
import type { CompanyFacts } from "@/lib/types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** $302.97bn, $911.9bn, $1.2tn, $776m. */
export function bigMoney(value: number | null | undefined, currency = "USD"): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const symbol = currency === "USD" ? "$" : `${currency} `;
  const sign = value < 0 ? "−" : "";
  const size = Math.abs(value);
  const [scaled, unit] =
    size >= 1e12 ? [size / 1e12, "tn"] : size >= 1e9 ? [size / 1e9, "bn"] : size >= 1e6 ? [size / 1e6, "m"] : [size, ""];
  const digits = scaled >= 100 ? 1 : 2;
  return `${sign}${symbol}${scaled.toFixed(unit ? digits : 0)}${unit}`;
}

function signedPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${formatPercent(Math.abs(value), 1)}`;
}

function monthYear(day: string | undefined): string | null {
  if (!day) return null;
  const [year, month] = day.split("-").map(Number);
  return `${MONTHS[month - 1]} ${year}`;
}

function Figure({ label, value, detail, tone }: { label: string; value: string; detail?: string; tone?: "up" | "down" }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-xl bg-surface-2 px-3.5 py-3">
      <dt className="text-xs font-medium text-ink-3">{label}</dt>
      <dd
        className={`text-lg font-semibold tabular-nums tracking-tight ${
          tone === "up" ? "text-positive" : tone === "down" ? "text-negative" : "text-ink"
        }`}
      >
        {value}
      </dd>
      {detail ? <span className="text-[11px] leading-snug text-ink-3">{detail}</span> : null}
    </div>
  );
}

function tone(value: number | null | undefined): "up" | "down" | undefined {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) return undefined;
  return value > 0 ? "up" : "down";
}

/** Where today's close sits between the year's lowest and highest close. */
function RangeBar({ low, high, price, currency }: { low: number; high: number; price: number; currency: string }) {
  const position = high > low ? Math.min(Math.max((price - low) / (high - low), 0), 1) : 0.5;
  const fmt = (value: number) => `${value.toLocaleString("en-GB", { maximumFractionDigits: 2 })} ${currency}`;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between text-xs text-ink-3">
        <span>52-week low</span>
        <span className="font-medium text-ink-2">{Math.round(position * 100)}% of the way up</span>
        <span>52-week high</span>
      </div>
      <div className="relative h-2 rounded-full bg-gradient-to-r from-[color-mix(in_srgb,var(--series-8)_35%,var(--surface-3))] via-surface-3 to-[color-mix(in_srgb,var(--series-3)_35%,var(--surface-3))]">
        <span
          aria-hidden="true"
          className="absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface bg-ink shadow-card"
          style={{ left: `${position * 100}%` }}
        />
      </div>
      <div className="flex justify-between text-xs tabular-nums text-ink-2">
        <span>{fmt(low)}</span>
        <span className="font-semibold text-ink">{fmt(price)}</span>
        <span>{fmt(high)}</span>
      </div>
    </div>
  );
}

export function CompanyFactsPanel({ facts }: { facts: CompanyFacts }) {
  const f = facts.figures;
  const d = facts.derived;
  const period = monthYear(f.revenue_ttm_end ?? f.net_income_ttm_end ?? f.eps_ttm_end);
  const quarter = monthYear(f.revenue_quarter_end);
  const where = [facts.industry, facts.sector, facts.country].filter(Boolean).join(" · ");
  return (
    <Panel
      subtitle={
        period
          ? `Reported to the SEC, last twelve months to ${period}; value and P/E at Helios's last close.`
          : "Reported to the SEC."
      }
      title="The company"
    >
      <div className="flex flex-col gap-4">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-surface-3 text-ink-2">
            <Building2 aria-hidden="true" size={19} />
          </span>
          <div className="flex min-w-0 flex-col">
            <span className="font-semibold text-ink">{facts.name ?? facts.ticker}</span>
            {where ? <span className="text-sm text-ink-3">{where}</span> : null}
          </div>
        </div>
        <dl className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          <Figure detail="Shares × last close" label="Market value" value={bigMoney(d.market_cap)} />
          <Figure
            detail={f.eps_ttm !== undefined && f.eps_ttm <= 0 ? "Losing money: no P/E" : "Price ÷ earnings a share"}
            label="P/E"
            value={d.pe !== null && d.pe !== undefined ? d.pe.toFixed(1) : "—"}
          />
          <Figure detail="Last twelve months" label="Revenue" value={bigMoney(f.revenue_ttm)} />
          <Figure
            detail={quarter ? `Quarter to ${quarter} vs a year before` : "Latest quarter vs a year before"}
            label="Revenue growth"
            tone={tone(f.revenue_growth)}
            value={signedPercent(f.revenue_growth)}
          />
          <Figure detail="Last twelve months" label="Net profit" tone={tone(f.net_income_ttm)} value={bigMoney(f.net_income_ttm)} />
          <Figure
            detail="Profit kept from each 100 of sales"
            label="Net margin"
            value={f.net_income_margin !== undefined ? formatPercent(f.net_income_margin, 1) : "—"}
          />
          <Figure
            detail="Before interest and tax"
            label="Operating margin"
            value={f.operating_income_margin !== undefined ? formatPercent(f.operating_income_margin, 1) : "—"}
          />
          <Figure
            detail={f.eps_growth !== undefined ? `Quarter: ${signedPercent(f.eps_growth)} vs a year before` : "Diluted, twelve months"}
            label="Earnings a share"
            value={f.eps_ttm !== undefined ? `$${f.eps_ttm.toFixed(2)}` : "—"}
          />
        </dl>
        {d.low_52w !== null && d.high_52w !== null && d.price !== null ? (
          <RangeBar currency={d.price_currency ?? ""} high={d.high_52w} low={d.low_52w} price={d.price} />
        ) : null}
        <p className="text-xs text-ink-3">
          Source: the company&apos;s own SEC filings (XBRL), checked {formatDateTime(facts.fetchedAt)}. Figures as
          filed; one-off items are not adjusted out.
        </p>
      </div>
    </Panel>
  );
}
