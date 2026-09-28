import { describe, expect, it } from "vitest";
import { clampPage, paginate, parsePageParam } from "@/lib/pagination";

describe("parsePageParam", () => {
  it("defaults an absent param to page 1", () => {
    expect(parsePageParam(undefined)).toBe(1);
  });

  it("defaults an empty string to page 1", () => {
    expect(parsePageParam("")).toBe(1);
  });

  it("parses a numeric string", () => {
    expect(parsePageParam("3")).toBe(3);
  });

  it("takes the first value when Next hands back a duplicate param as an array", () => {
    expect(parsePageParam(["2", "5"])).toBe(2);
  });

  it("turns unparsable input into NaN rather than throwing", () => {
    expect(parsePageParam("abc")).toBeNaN();
  });
});

describe("clampPage", () => {
  it("clamps page 0 up to the first page", () => {
    expect(clampPage(0, 5)).toBe(1);
  });

  it("clamps a negative page up to the first page", () => {
    expect(clampPage(-3, 5)).toBe(1);
  });

  it("clamps NaN to the first page", () => {
    expect(clampPage(NaN, 5)).toBe(1);
  });

  it("clamps a page beyond the last one down to the last page", () => {
    expect(clampPage(999, 5)).toBe(5);
  });

  it("truncates a fractional page", () => {
    expect(clampPage(2.9, 5)).toBe(2);
  });

  it("passes an in-range page through unchanged", () => {
    expect(clampPage(3, 5)).toBe(3);
  });
});

describe("paginate", () => {
  const items = Array.from({ length: 23 }, (_, index) => index + 1);

  it("returns the first slice for page 1", () => {
    const result = paginate(items, 1, 10);
    expect(result).toEqual({
      items: Array.from({ length: 10 }, (_, i) => i + 1),
      page: 1,
      pageCount: 3,
      pageSize: 10,
      total: 23,
    });
  });

  it("returns a partial final slice", () => {
    const result = paginate(items, 3, 10);
    expect(result.items).toEqual([21, 22, 23]);
    expect(result.page).toBe(3);
    expect(result.pageCount).toBe(3);
  });

  it("clamps page 0 to page 1", () => {
    const result = paginate(items, 0, 10);
    expect(result.page).toBe(1);
    expect(result.items).toEqual(Array.from({ length: 10 }, (_, i) => i + 1));
  });

  it("clamps a negative page to page 1", () => {
    const result = paginate(items, -5, 10);
    expect(result.page).toBe(1);
  });

  it("clamps NaN to page 1", () => {
    const result = paginate(items, NaN, 10);
    expect(result.page).toBe(1);
  });

  it("clamps a page past the end to the last real page instead of returning an empty slice", () => {
    const result = paginate(items, 99, 10);
    expect(result.page).toBe(3);
    expect(result.items).toEqual([21, 22, 23]);
  });

  it("reports one page of nothing for an empty list, never a zero or negative page count", () => {
    const result = paginate<number>([], 1, 10);
    expect(result).toEqual({ items: [], page: 1, pageCount: 1, pageSize: 10, total: 0 });
  });

  it("clamps an out-of-range page against an empty list back to page 1", () => {
    const result = paginate<number>([], 5, 10);
    expect(result.page).toBe(1);
    expect(result.items).toEqual([]);
  });

  it("puts everything on one page when the list is smaller than the page size", () => {
    const result = paginate([1, 2, 3], 1, 10);
    expect(result.pageCount).toBe(1);
    expect(result.items).toEqual([1, 2, 3]);
  });
});
