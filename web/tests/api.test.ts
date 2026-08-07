import { describe, expect, it } from "vitest";
import {
  parseHealth,
  parsePerformanceReport,
  parsePositions,
  parseQualityReport,
} from "@/lib/api";
import { performanceReport, positions, qualityReport } from "./fixtures";

describe("parseHealth", () => {
  it("accepts a well-formed payload", () => {
    expect(
      parseHealth({ status: "ok", trading212Configured: true, databaseReady: true }),
    ).toEqual({ status: "ok", trading212Configured: true, databaseReady: true });
  });

  it("rejects payloads with the wrong types", () => {
    expect(parseHealth({ status: "ok", trading212Configured: "yes", databaseReady: true })).toBeNull();
    expect(parseHealth(null)).toBeNull();
    expect(parseHealth([])).toBeNull();
  });
});

describe("parsePositions", () => {
  it("round-trips the fixture", () => {
    expect(parsePositions(positions())).toEqual(positions());
  });

  it("keeps decimals as strings so precision survives", () => {
    const parsed = parsePositions(positions());

    expect(parsed?.[0].quantity).toBe("12.3456789");
    expect(typeof parsed?.[0].walletImpact?.currentValue).toBe("string");
  });

  it("rejects a row without a ticker or quantity", () => {
    expect(parsePositions([{ instrument: {}, quantity: "1" }])).toBeNull();
    expect(parsePositions([{ instrument: { ticker: "A" } }])).toBeNull();
  });

  it("tolerates a missing walletImpact", () => {
    const parsed = parsePositions([{ instrument: { ticker: "A" }, quantity: "1" }]);

    expect(parsed?.[0].walletImpact).toBeNull();
  });
});

describe("parsePerformanceReport", () => {
  it("accepts the full report", () => {
    expect(parsePerformanceReport(performanceReport())).not.toBeNull();
  });

  it("rejects a report missing a field the charts index into", () => {
    const broken = { ...performanceReport(), navSeries: undefined };

    expect(parsePerformanceReport(broken)).toBeNull();
  });

  it("rejects a report whose series field is not an array", () => {
    const broken = { ...performanceReport(), rollingBeta30d: "nope" };

    expect(parsePerformanceReport(broken)).toBeNull();
  });
});

describe("parseQualityReport", () => {
  it("accepts the full report", () => {
    expect(parseQualityReport(qualityReport())).not.toBeNull();
  });

  it("rejects a report whose issue lists are not arrays", () => {
    const broken = { ...qualityReport(), unresolvedInstruments: null };

    expect(parseQualityReport(broken)).toBeNull();
  });
});
