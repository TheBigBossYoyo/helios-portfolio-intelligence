import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AddJournalEntryForm } from "@/components/thesis-forms";
import type { ActionResult } from "@/lib/actions";
import type { Thesis } from "@/lib/types";

const noop = async (): Promise<ActionResult> => ({
  ok: true,
  message: "ok",
  timestamp: "2024-01-01T00:00:00Z",
});

const thesis: Thesis = {
  id: 1,
  t212Ticker: "AAPL_US_EQ",
  isin: null,
  title: "Services revenue compounds faster than hardware",
  body: "Recurring services margin should keep expanding.",
  conviction: "high",
  status: "active",
  openedOn: "2024-02-01",
  outcomeNote: null,
  closedAt: null,
  createdAt: "2024-02-01T09:00:00Z",
  updatedAt: "2024-02-01T09:00:00Z",
};

describe("AddJournalEntryForm", () => {
  it("defaults to a general (unattached) note when no thesis is preselected", () => {
    render(<AddJournalEntryForm action={noop} theses={[thesis]} />);

    const select = screen.getByLabelText(/Thesis \(optional\)/) as HTMLSelectElement;
    expect(select.value).toBe("");
  });

  it("pre-attaches the note to a thesis when a default id is given, as the detail page does", () => {
    render(<AddJournalEntryForm action={noop} defaultThesisId="1" theses={[thesis]} />);

    const select = screen.getByLabelText(/Thesis \(optional\)/) as HTMLSelectElement;
    expect(select.value).toBe("1");
  });
});
