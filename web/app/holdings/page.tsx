import { DataTable, type Column } from "@/components/data-table";
import { Note, Panel, Unavailable } from "@/components/panel";
import { getPositions } from "@/lib/api";
import { EMPTY, formatEur, formatQuantity } from "@/lib/format";
import type { Position } from "@/lib/types";
import { DELTA } from "@/lib/viz";

export const dynamic = "force-dynamic";

/**
 * Live positions, straight from Trading 212 via the backend.
 *
 * Per-share prices stay in the instrument's own currency (that is what the API returns); only
 * the wallet-impact columns are account-currency EUR. Mixing the two into one "value" column
 * would silently misstate non-EUR holdings, so the currency is shown next to the price.
 */
const COLUMNS: Column<Position>[] = [
  { key: "ticker", header: "Ticker", render: (row) => row.instrument.ticker },
  {
    key: "name",
    header: "Instrument",
    render: (row) => <span className="text-neutral-500">{row.instrument.name ?? EMPTY}</span>,
  },
  {
    key: "isin",
    header: "ISIN",
    render: (row) => <span className="text-neutral-600">{row.instrument.isin ?? EMPTY}</span>,
  },
  {
    key: "quantity",
    header: "Quantity",
    numeric: true,
    render: (row) => formatQuantity(row.quantity),
  },
  {
    key: "avg",
    header: "Avg price",
    numeric: true,
    render: (row) => priceCell(row.averagePricePaid, row.instrument.currency),
  },
  {
    key: "price",
    header: "Current price",
    numeric: true,
    render: (row) => priceCell(row.currentPrice, row.instrument.currency),
  },
  {
    key: "value",
    header: "Account value",
    numeric: true,
    render: (row) => formatEur(row.walletImpact?.currentValue),
  },
  {
    key: "pnl",
    header: "Unrealized P/L",
    numeric: true,
    render: (row) => (
      <span style={deltaStyle(row.walletImpact?.unrealizedProfitLoss)}>
        {formatEur(row.walletImpact?.unrealizedProfitLoss)}
      </span>
    ),
  },
  {
    key: "fx",
    header: "FX impact",
    numeric: true,
    render: (row) => formatEur(row.walletImpact?.fxImpact),
  },
];

export default async function HoldingsPage() {
  const positions = await getPositions();

  return (
    <Panel
      subtitle="Live snapshot from Trading 212. Per-share prices are in the instrument currency; account value, P/L and FX impact are in EUR."
      title="Holdings"
    >
      {positions.ok ? (
        <>
          <DataTable
            caption="Open positions"
            columns={COLUMNS}
            empty="No open positions"
            rowKey={(row) => row.instrument.ticker}
            rows={positions.data}
          />
          <div className="mt-4">
            <Note>
              This table is a live read, not the replayed history. Reconstructed daily valuation
              lives on the performance page, and the two can differ while a sync is pending.
            </Note>
          </div>
        </>
      ) : (
        <Unavailable
          detail={
            positions.status === 503
              ? "Trading 212 credentials are not configured on the backend."
              : positions.error
          }
          reason="Positions unavailable"
        />
      )}
    </Panel>
  );
}

function priceCell(value: string | null | undefined, currency: string | null) {
  if (!value) return EMPTY;
  return (
    <span>
      {value}
      {/* A real space, not just a CSS margin: otherwise the accessible name reads "182.40USD". */}
      {currency ? <> <span className="text-neutral-600">{currency}</span></> : null}
    </span>
  );
}

/** Direction is already in the number's sign; the color only reinforces it. */
function deltaStyle(value: string | null | undefined) {
  if (!value) return undefined;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed === 0) return undefined;
  return { color: parsed > 0 ? DELTA.up : DELTA.down };
}
