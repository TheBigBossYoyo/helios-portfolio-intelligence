import { BUTTON } from "@/lib/ui";

/**
 * A server-rendered, URL-driven pager for lists sliced with `lib/pagination`.
 *
 * Every control is a plain `<a href>` — the same idiom the news ticker filter already uses —
 * rather than a client-side click handler, so a page is reachable by URL, works with client JS
 * disabled, and survives a bookmark or a shared link.
 */
export function Pager({
  page,
  pageCount,
  basePath,
  extraParams = {},
}: {
  page: number;
  pageCount: number;
  basePath: string;
  /** Non-pagination filters to preserve across page links, e.g. the news ticker filter. */
  extraParams?: Record<string, string | undefined>;
}) {
  if (pageCount <= 1) return null;

  const hrefFor = (target: number) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(extraParams)) {
      if (value) params.set(key, value);
    }
    if (target > 1) params.set("page", String(target));
    const query = params.toString();
    return query ? `${basePath}?${query}` : basePath;
  };

  return (
    <nav
      aria-label="Pagination"
      className="mt-4 flex items-center justify-between gap-3 border-t border-border pt-4"
    >
      <PagerLink disabled={page <= 1} href={hrefFor(page - 1)} label="← Previous" />
      <span className="tabular text-xs font-medium text-ink-3">
        Page {page} of {pageCount}
      </span>
      <PagerLink disabled={page >= pageCount} href={hrefFor(page + 1)} label="Next →" />
    </nav>
  );
}

function PagerLink({
  href,
  label,
  disabled,
}: {
  href: string;
  label: string;
  disabled: boolean;
}) {
  if (disabled) {
    return (
      <span
        aria-disabled="true"
        className={`${BUTTON.secondary} ${BUTTON.small} cursor-not-allowed opacity-50`}
      >
        {label}
      </span>
    );
  }
  return (
    <a className={`${BUTTON.secondary} ${BUTTON.small}`} href={href}>
      {label}
    </a>
  );
}
