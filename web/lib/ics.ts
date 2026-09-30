/**
 * iCalendar (RFC 5545) export, so a phone's own calendar can hold Helios's dates: all-day
 * events, one per report or dividend, with a stable UID so importing twice updates rather than
 * duplicates.
 */

import { addDays } from "./calendar";
import type { CalendarEvent } from "./types";

function escapeText(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n");
}

/** Lines longer than 75 octets are folded with a leading space, as the format requires. */
function fold(line: string): string {
  const parts: string[] = [];
  let rest = line;
  while (rest.length > 74) {
    parts.push(rest.slice(0, 74));
    rest = ` ${rest.slice(74)}`;
  }
  parts.push(rest);
  return parts.join("\r\n");
}

function compact(day: string): string {
  return day.replaceAll("-", "");
}

export function eventSummary(event: CalendarEvent): string {
  const name = event.name ?? event.ticker.split("_")[0];
  if (event.kind === "earnings") return `${name} reports results`;
  if (event.kind === "ex-dividend") return `${name} goes ex-dividend`;
  return event.received ? `Dividend received from ${name}` : `Dividend from ${name}`;
}

export function eventDescription(event: CalendarEvent): string {
  const lines: string[] = [];
  if (event.kind === "earnings") {
    if (event.timeOfDay === "pre-market") lines.push("Before the market opens.");
    if (event.timeOfDay === "post-market") lines.push("After the close.");
    if (event.estimateEps) lines.push(`Analysts expect ${event.estimateEps} ${event.epsCurrency ?? ""} a share.`.replace(" .", "."));
  } else {
    if (event.amountEur) lines.push(`About €${Number(event.amountEur).toFixed(2)} ${event.afterTax ? "after tax" : "before tax"}.`);
    if (event.amountPerShare) lines.push(`${event.amountPerShare} ${event.currencyCode ?? ""} a share.`.replace(" .", "."));
    if (event.kind === "ex-dividend") lines.push("Own the shares before this day to receive the dividend.");
    if (!event.confirmed) lines.push("Estimated by Helios from the company's usual rhythm.");
  }
  lines.push("From Helios (read-only; not advice).");
  return lines.join("\n");
}

export function toIcs(events: CalendarEvent[], stamp: string): string {
  const lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Helios//Portfolio calendar//EN",
    "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH",
    "X-WR-CALNAME:Helios",
  ];
  for (const event of events) {
    lines.push(
      "BEGIN:VEVENT",
      `UID:${event.id.replaceAll(":", "-")}@helios.local`,
      `DTSTAMP:${stamp}`,
      `DTSTART;VALUE=DATE:${compact(event.day)}`,
      `DTEND;VALUE=DATE:${compact(addDays(event.day, 1))}`,
      fold(`SUMMARY:${escapeText(eventSummary(event))}`),
      fold(`DESCRIPTION:${escapeText(eventDescription(event))}`),
      "TRANSP:TRANSPARENT",
      "END:VEVENT",
    );
  }
  lines.push("END:VCALENDAR");
  return `${lines.join("\r\n")}\r\n`;
}
