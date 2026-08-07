import { EMPTY, formatDateTime } from "@/lib/format";
import type { NewsItem } from "@/lib/types";

/**
 * A news list.
 *
 * Every item shows its publisher and links to the original article. Helios stores only the
 * headline and the summary the publisher put in their own feed — it never fetches article
 * bodies — so the link out is the only way to read the piece, by design.
 */
export function NewsFeed({ items, compact = false }: { items: NewsItem[]; compact?: boolean }) {
  if (items.length === 0) {
    return (
      <p className="px-1 py-6 text-center font-mono text-xs text-neutral-600">
        No stored articles. Configure sources in <code>config/news_feeds.yaml</code>, then run{" "}
        <code>helios news-sync</code>.
      </p>
    );
  }

  return (
    <ul className="flex flex-col divide-y divide-neutral-900">
      {items.map((item) => (
        <li className="py-3 first:pt-0 last:pb-0" key={item.dedupeKey}>
          <article className="flex flex-col gap-1">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] uppercase tracking-wider text-neutral-600">
              <span className="text-neutral-400">{item.sourceLabel}</span>
              <time dateTime={item.publishedAt ?? undefined}>
                {item.publishedAt ? formatDateTime(item.publishedAt) : "Undated"}
              </time>
              {item.t212Ticker ? (
                <span className="border border-neutral-800 px-1.5 py-0.5 text-neutral-500">
                  {item.t212Ticker}
                </span>
              ) : (
                <span className="text-neutral-700">Market-wide</span>
              )}
            </div>
            <a
              className="text-sm leading-snug text-neutral-200 underline-offset-2 hover:text-amber-accent hover:underline"
              href={item.url}
              rel="noopener noreferrer nofollow"
              target="_blank"
            >
              {item.headline}
            </a>
            {!compact && item.summary ? (
              <p className="line-clamp-3 text-[11px] leading-relaxed text-neutral-500">
                {item.summary}
              </p>
            ) : null}
          </article>
        </li>
      ))}
    </ul>
  );
}

export function NewsSourceNote({ sources }: { sources: string[] }) {
  return (
    <p className="border-l-2 border-neutral-800 pl-3 text-[11px] leading-relaxed text-neutral-500">
      {sources.length > 0 ? (
        <>
          Sources: {sources.join(", ")}. Headlines and summaries are reproduced from each
          publisher&apos;s own feed and link back to the original; Helios does not fetch or store
          article text.
        </>
      ) : (
        <>No sources configured yet. {EMPTY}</>
      )}
    </p>
  );
}
