import type { ReactNode } from "react";

export interface Column<T> {
  key: string;
  header: string;
  /** Right-align numeric columns; they also get tabular figures so digits line up. */
  numeric?: boolean;
  render: (row: T) => ReactNode;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  empty?: string;
  caption?: string;
  /**
   * Cap the table's height and scroll inside it. Use for long series tables (the chart
   * table-view twins) so a complete dataset stays reachable without the page becoming a
   * kilometre of rows.
   */
  maxHeight?: number;
}

/**
 * The table view. Every chart in this app has one of these beside it — it is the WCAG-clean
 * equivalent, so no value is reachable only by hovering a mark.
 *
 * Wide tables scroll inside their own container; the page body never scrolls sideways.
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  empty,
  caption,
  maxHeight,
}: DataTableProps<T>) {
  if (rows.length === 0) {
    return <p className="px-1 py-8 text-center text-sm text-ink-3">{empty ?? "No rows"}</p>;
  }

  return (
    <div
      className="overflow-auto rounded-xl border border-border"
      style={maxHeight ? { maxHeight } : undefined}
    >
      <table className="w-full min-w-full border-collapse text-sm">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead className="sticky top-0 z-10 bg-surface-2">
          <tr className="border-b border-border">
            {columns.map((column) => (
              <th
                className={`whitespace-nowrap px-3 py-2.5 text-xs font-medium text-ink-3 ${
                  column.numeric ? "text-right" : "text-left"
                }`}
                key={column.key}
                scope="col"
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              className="border-b border-border transition-colors last:border-b-0 hover:bg-surface-2"
              key={rowKey(row)}
            >
              {columns.map((column) => (
                <td
                  className={`whitespace-nowrap px-3 py-2.5 text-ink-2 ${
                    column.numeric ? "tabular-nums text-right text-ink" : "text-left"
                  }`}
                  key={column.key}
                >
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
