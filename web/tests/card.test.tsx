import { describe, expect, it } from "vitest";
import { parseCardHistory } from "@/lib/api";
import { categoryLabel } from "@/lib/format";

describe("categoryLabel", () => {
  it("reads Trading 212's category codes as words", () => {
    expect(categoryLabel("SERVICE_PROVIDERS")).toBe("Service providers");
    expect(categoryLabel("MISCELLANEOUS")).toBe("Miscellaneous");
    expect(categoryLabel(null)).toBe("Uncategorised");
    expect(categoryLabel("UNCATEGORISED")).toBe("Uncategorised");
  });
});

describe("parseCardHistory", () => {
  it("accepts the API shape and rejects anything without transactions", () => {
    const valid = {
      status: { enabled: true, cardRows: 0, cashRows: 0, pending: false },
      summary: { spent: "0", transactions: [], months: [], categories: [], merchants: [] },
    };
    expect(parseCardHistory(valid)).not.toBeNull();
    expect(parseCardHistory({ status: {} })).toBeNull();
    expect(parseCardHistory({ status: {}, summary: { spent: "0" } })).toBeNull();
  });
});
