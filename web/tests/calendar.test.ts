import { describe, expect, it } from "vitest";

import {
  agendaGroups,
  monthGrid,
  payMatrix,
  relativeDay,
  shiftMonth,
  shortDate,
  tickerSymbol,
} from "@/lib/calendar";
import { toIcs } from "@/lib/ics";
import type { CalendarEvent } from "@/lib/types";

function event(day: string, kind: CalendarEvent["kind"], extra: Partial<CalendarEvent> = {}): CalendarEvent {
  return {
    id: `${kind}:X:${day}`,
    day,
    kind,
    ticker: "JPM_US_EQ",
    name: "JPMorgan Chase",
    held: true,
    confirmed: true,
    timeOfDay: null,
    estimateEps: null,
    epsCurrency: null,
    exDate: null,
    amountPerShare: null,
    currencyCode: null,
    amountEur: null,
    afterTax: false,
    past: false,
    received: false,
    fiscalDateEnding: null,
    ...extra,
  };
}

describe("calendar helpers", () => {
  it("lays a month out in whole Monday-first weeks", () => {
    const weeks = monthGrid("2026-10");
    expect(weeks[0][0]).toBe("2026-09-28"); // 1 Oct 2026 is a Thursday
    expect(weeks.at(-1)?.at(-1)).toBe("2026-11-01");
    expect(weeks.every((week) => week.length === 7)).toBe(true);
    expect(monthGrid("2026-02")).toHaveLength(5);
  });

  it("names days relative to today", () => {
    expect(relativeDay("2026-09-30", "2026-09-30")).toBe("Today");
    expect(relativeDay("2026-10-01", "2026-09-30")).toBe("Tomorrow");
    expect(relativeDay("2026-10-05", "2026-09-30")).toBe("In 5 days");
    expect(relativeDay("2026-11-11", "2026-09-30")).toBe("In 6 weeks");
    expect(relativeDay("2026-09-27", "2026-09-30")).toBe("3 days ago");
    expect(shortDate("2026-10-05")).toBe("Mon 5 Oct");
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
    expect(tickerSymbol("VUAGl_EQ")).toBe("VUAG");
    expect(tickerSymbol("NVDA_US_EQ")).toBe("NVDA");
  });

  it("groups the agenda by today, this week, next week and month", () => {
    const groups = agendaGroups(
      [
        event("2026-09-29", "dividend", { past: true }),
        event("2026-09-30", "earnings"),
        event("2026-10-02", "dividend"),
        event("2026-10-06", "ex-dividend"),
        event("2026-10-31", "dividend"),
      ],
      "2026-09-30",
    );
    expect(groups.map((group) => [group.title, group.events.length])).toEqual([
      ["Today", 1],
      ["This week", 1],
      ["Next week", 1],
      ["October 2026", 1],
    ]);
  });

  it("maps who pays when over the next twelve months", () => {
    const matrix = payMatrix(
      [
        event("2026-10-31", "dividend", { amountEur: "0.29" }),
        event("2027-01-31", "dividend", { amountEur: "0.29", confirmed: false }),
        event("2026-10-06", "ex-dividend", { amountEur: "0.29" }),
        event("2026-09-10", "dividend", { amountEur: "0.30", past: true }),
      ],
      "2026-09-30",
    );
    expect(matrix.months[0]).toBe("2026-09");
    expect(matrix.rows).toHaveLength(1);
    expect(matrix.rows[0].total).toBeCloseTo(0.58);
    expect(matrix.rows[0].cells[1]).toEqual({ month: "2026-10", amount: 0.29, confirmed: true });
    expect(matrix.rows[0].cells[4].confirmed).toBe(false);
    expect(matrix.max).toBeCloseTo(0.29);
  });
});

describe("ics export", () => {
  it("writes all-day events with stable ids and escaped text", () => {
    const text = toIcs(
      [
        event("2026-10-13", "earnings", {
          id: "earnings:JPM_US_EQ:2026-10-13",
          timeOfDay: "pre-market",
          estimateEps: "4.85",
          epsCurrency: "USD",
          name: "JPMorgan Chase, & Co",
        }),
      ],
      "20260930T080000Z",
    );
    expect(text).toContain("BEGIN:VCALENDAR\r\n");
    expect(text).toContain("UID:earnings-JPM_US_EQ-2026-10-13@helios.local");
    expect(text).toContain("DTSTART;VALUE=DATE:20261013");
    expect(text).toContain("DTEND;VALUE=DATE:20261014");
    expect(text).toContain("SUMMARY:JPMorgan Chase\\, & Co reports results");
    // Long lines are folded; unfold before reading the description.
    expect(text.replace(/\r\n /g, "")).toContain("Analysts expect 4.85 USD a share.");
    expect(text.split("\r\n").every((line) => line.length <= 75)).toBe(true);
  });
});

