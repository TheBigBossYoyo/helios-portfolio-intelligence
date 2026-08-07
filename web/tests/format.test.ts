import { describe, expect, it } from "vitest";
import {
  EMPTY,
  decimalToNumber,
  formatEur,
  formatPercent,
  formatQuantity,
  formatSignedPercent,
  humanizeStatus,
  metricIsAvailable,
  metricText,
} from "@/lib/format";
import { metric } from "./fixtures";

describe("formatters", () => {
  it("renders an absent value as an em dash, never as zero", () => {
    expect(formatEur(null)).toBe(EMPTY);
    expect(formatEur(undefined)).toBe(EMPTY);
    expect(formatEur("")).toBe(EMPTY);
    expect(formatPercent(null)).toBe(EMPTY);
    expect(formatQuantity(null)).toBe(EMPTY);
  });

  it("distinguishes a real zero from a missing value", () => {
    expect(formatPercent(0)).toBe("0.00%");
    expect(formatEur("0")).toContain("0.00");
  });

  it("keeps quantity precision exactly as the backend sent it", () => {
    expect(formatQuantity("12.3456789")).toBe("12.3456789");
  });

  it("signs percentages", () => {
    expect(formatSignedPercent(0.0259)).toBe("+2.59%");
    expect(formatSignedPercent(-0.0672)).toBe("-6.72%");
  });

  it("parses decimal strings only at the render boundary", () => {
    expect(decimalToNumber("1234.5678")).toBe(1234.5678);
    expect(decimalToNumber("not-a-number")).toBeNull();
    expect(decimalToNumber(null)).toBeNull();
  });

  it("humanizes backend statuses", () => {
    expect(humanizeStatus("insufficient_data")).toBe("Insufficient data");
    expect(humanizeStatus("unavailable")).toBe("Unavailable");
  });
});

describe("metric rendering", () => {
  it("renders the value only when the backend says ok", () => {
    expect(metricText(metric(0.1), (value) => formatPercent(value))).toBe("10.00%");
    expect(metricIsAvailable(metric(0.1))).toBe(true);
  });

  it("refuses to render a number for an insufficient_data metric", () => {
    const unavailable = metric(null);
    expect(metricText(unavailable, (value) => formatPercent(value))).toBe(EMPTY);
    expect(metricIsAvailable(unavailable)).toBe(false);
  });

  it("refuses to render a value the backend flagged as not ok, even if a number is present", () => {
    const suspicious = { status: "no_bracket", value: 0.5, observations: 3, detail: null };
    expect(metricText(suspicious, (value) => formatPercent(value))).toBe(EMPTY);
  });
});
