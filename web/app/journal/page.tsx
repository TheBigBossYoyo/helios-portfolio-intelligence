import { DataTable, type Column } from "@/components/data-table";
import { Note, Panel, Unavailable } from "@/components/panel";
import { StatusBadge } from "@/components/status-badge";
import { getJournal, getTheses } from "@/lib/api";
import { EMPTY, formatDate, formatDateTime, formatPercent } from "@/lib/format";
import type { JournalEntry, Thesis } from "@/lib/types";

export const dynamic = "force-dynamic";

const THESIS_COLUMNS: Column<Thesis>[] = [
  { key: "id", header: "#", numeric: true, render: (row) => String(row.id) },
  {
    key: "status",
    header: "Status",
    render: (row) => <StatusBadge status={row.status} />,
  },
  {
    key: "scope",
    header: "Scope",
    render: (row) => row.t212Ticker ?? <span className="text-neutral-600">portfolio</span>,
  },
  { key: "title", header: "Thesis", render: (row) => row.title },
  {
    key: "conviction",
    header: "Conviction",
    render: (row) => <span className="text-neutral-500">{row.conviction}</span>,
  },
  {
    key: "opened",
    header: "Opened",
    render: (row) => <span className="text-neutral-500">{formatDate(row.openedOn)}</span>,
  },
];

const JOURNAL_COLUMNS: Column<JournalEntry>[] = [
  {
    key: "created",
    header: "When",
    render: (row) => <span className="text-neutral-500">{formatDateTime(row.createdAt)}</span>,
  },
  {
    key: "scope",
    header: "Thesis",
    render: (row) =>
      row.thesisId ? `#${row.thesisId}` : <span className="text-neutral-600">general</span>,
  },
  { key: "note", header: "Note", render: (row) => row.note },
  {
    key: "tags",
    header: "Tags",
    render: (row) => <span className="text-neutral-600">{row.tags ?? EMPTY}</span>,
  },
];

export default async function JournalPage() {
  const [theses, journal] = await Promise.all([getTheses(), getJournal(100)]);

  const open = theses.ok
    ? theses.data.filter((row) => row.status === "draft" || row.status === "active")
    : [];
  const settled = theses.ok
    ? theses.data.filter((row) => row.status !== "draft" && row.status !== "active")
    : [];

  return (
    <>
      <Panel
        subtitle="Why you hold what you hold, written before the outcome is known."
        title={`Open theses (${open.length})`}
      >
        {theses.ok ? (
          <DataTable
            caption="Open theses"
            columns={THESIS_COLUMNS}
            empty="No open theses. Create one with `helios thesis create`."
            rowKey={(row) => String(row.id)}
            rows={open}
          />
        ) : (
          <Unavailable detail={theses.error} reason="Theses unavailable" />
        )}
        <div className="mt-4">
          <Note>
            A thesis is editable only while it is a draft. Once you activate it, the original
            reasoning is frozen — later thinking goes in the journal, and the outcome goes in the
            note you write when you close it. That is what makes reviewing them honest.
          </Note>
        </div>
      </Panel>

      {settled.length > 0 ? (
        <Panel
          subtitle="Closed positions on your own reasoning — the part worth re-reading."
          title={`Settled theses (${settled.length})`}
        >
          <DataTable
            caption="Settled theses"
            columns={[
              ...THESIS_COLUMNS,
              {
                key: "outcome",
                header: "Outcome",
                render: (row: Thesis) => (
                  <span className="text-neutral-500">{row.outcomeNote ?? EMPTY}</span>
                ),
              },
            ]}
            rowKey={(row) => String(row.id)}
            rows={settled}
          />
        </Panel>
      ) : null}

      <Panel subtitle="Dated notes, attached to a thesis or standalone." title="Journal">
        {journal.ok ? (
          <DataTable
            caption="Journal entries"
            columns={JOURNAL_COLUMNS}
            empty="No journal entries yet. Add one with `helios journal add --note '...'`."
            maxHeight={480}
            rowKey={(row) => String(row.id)}
            rows={journal.data}
          />
        ) : (
          <Unavailable detail={journal.error} reason="Journal unavailable" />
        )}
      </Panel>
    </>
  );
}

export function convictionWeight(value: number | null): string {
  return value === null ? EMPTY : formatPercent(value);
}
