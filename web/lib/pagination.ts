/**
 * URL-driven pagination for lists the backend returns whole.
 *
 * None of positions, news or journal expose a real offset/cursor on the backend — see
 * `src/helios/api.py`: news and journal only cap `limit`, and `/api/v1/t212/positions` has no
 * paging concept at all. For a single-user, local-only portfolio the full list is small enough
 * that slicing it here, after the fetch, is a reasonable trade against building backend
 * pagination nobody else will ever call. If that stops being true (a household running this
 * against a much larger ledger), the fix is an `offset`/cursor param on those endpoints, not a
 * bigger slice here.
 */

export interface Page<T> {
  /** The rows for this page, already sliced. */
  items: T[];
  /** 1-indexed, already clamped into `[1, pageCount]` — always a page that exists. */
  page: number;
  /** At least 1, even for an empty list, so "page 1 of 1" is always a valid thing to render. */
  pageCount: number;
  pageSize: number;
  /** Total row count before slicing. */
  total: number;
}

/**
 * Reads a `?page=` search param into the number `paginate` expects.
 *
 * Next hands duplicate query params back as an array; this takes the first. Anything that is
 * not a finite number (missing, empty, `"abc"`, `"NaN"`) becomes `NaN`, which `clampPage` treats
 * exactly like an absent param: page 1.
 */
export function parsePageParam(value: string | string[] | undefined): number {
  const raw = Array.isArray(value) ? value[0] : value;
  if (raw === undefined || raw.trim() === "") return 1;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : NaN;
}

/** Clamps a requested page into a page that actually exists, defaulting to the first. */
export function clampPage(requestedPage: number, pageCount: number): number {
  if (!Number.isFinite(requestedPage)) return 1;
  const truncated = Math.trunc(requestedPage);
  if (truncated < 1) return 1;
  if (truncated > pageCount) return pageCount;
  return truncated;
}

/** Slices `items` into the requested page, clamping out-of-range requests instead of erroring. */
export function paginate<T>(items: T[], requestedPage: number, pageSize: number): Page<T> {
  const total = items.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const page = clampPage(requestedPage, pageCount);
  const start = (page - 1) * pageSize;
  return { items: items.slice(start, start + pageSize), page, pageCount, pageSize, total };
}
