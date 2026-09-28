import { ExternalLink } from "lucide-react";
import type { ReactNode } from "react";
import { Note } from "@/components/panel";
import { EMPTY, displayTicker, formatDateTime, plainText } from "@/lib/format";
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
      <p className="px-1 py-8 text-center text-sm leading-relaxed text-ink-3">
        No stored articles. Configure sources in{" "}
        <code className="rounded bg-surface-3 px-1 py-0.5 font-mono text-xs text-ink-2">
          config/news_feeds.yaml
        </code>
        , then run{" "}
        <code className="rounded bg-surface-3 px-1 py-0.5 font-mono text-xs text-ink-2">
          helios news-sync
        </code>
        .
      </p>
    );
  }

  return (
    <ul className={compact ? "flex flex-col divide-y divide-border" : "flex flex-col gap-3"}>
      {items.map((item) => (
        <li key={item.dedupeKey}>
          <NewsCard compact={compact} item={item} />
        </li>
      ))}
    </ul>
  );
}

function NewsCard({ item, compact }: { item: NewsItem; compact: boolean }) {
  return (
    <article
      className={
        compact
          ? "flex flex-col gap-1.5 py-3 first:pt-0 last:pb-0"
          : "flex flex-col gap-2 rounded-xl border border-border bg-surface p-4 shadow-card transition-colors hover:border-border-strong"
      }
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Chip tone="quiet">{item.sourceLabel}</Chip>
        <time className="text-xs text-ink-3" dateTime={item.publishedAt ?? undefined}>
          {item.publishedAt ? formatDateTime(item.publishedAt) : "Undated"}
        </time>
        {item.t212Ticker ? (
          <Chip tone="accent">{displayTicker(item.t212Ticker)}</Chip>
        ) : (
          <span className="text-xs text-ink-4">Market-wide</span>
        )}
        <RelevanceNote item={item} />
      </div>
      <a
        className="group flex items-start gap-1.5 text-sm font-medium leading-snug text-ink hover:text-accent"
        href={item.url}
        rel="noopener noreferrer nofollow"
        target="_blank"
      >
        <span className="underline-offset-2 group-hover:underline">{item.headline}</span>
        <ExternalLink
          aria-hidden="true"
          className="mt-0.5 shrink-0 text-ink-4 group-hover:text-accent"
          size={13}
        />
      </a>
      {!compact && summaryAddsSomething(item) ? (
        <p className="line-clamp-3 text-sm leading-relaxed text-ink-3">{plainText(item.summary)}</p>
      ) : null}
    </article>
  );
}

function comparable(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "");
}

/**
 * Google News "summaries" are the headline again plus the publisher's name: repeating it adds
 * nothing, so only a summary that says something the headline does not is shown.
 */
function summaryAddsSomething(item: NewsItem): boolean {
  const summary = comparable(plainText(item.summary));
  if (!summary) return false;
  const headline = comparable(item.headline.replace(/ - [^-]+$/, ""));
  return !summary.startsWith(headline);
}

/**
 * Why this story is in the list, in words: the feed is bound to a holding, and Helios checks
 * whether the text actually names it. Shown on every bound item so a loose match is visible.
 */
function RelevanceNote({ item }: { item: NewsItem }) {
  if (!item.t212Ticker || !item.relevance || item.relevance === "market") return null;
  const parts: string[] = [];
  if (item.relevance === "headline") parts.push(`Names ${item.matchedTerm ?? "the holding"}`);
  else if (item.relevance === "summary") parts.push(`Summary names ${item.matchedTerm ?? "it"}`);
  else parts.push("Doesn't name the holding");
  if (item.held === false) parts.push("no longer held");
  return (
    <span
      className={`text-xs ${item.relevance === "unconfirmed" ? "text-ink-4 italic" : "text-ink-3"}`}
    >
      {parts.join(" · ")}
    </span>
  );
}

function Chip({ children, tone }: { children: ReactNode; tone: "quiet" | "accent" }) {
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${
        tone === "accent" ? "bg-accent-soft text-accent-ink" : "bg-surface-3 text-ink-2"
      }`}
    >
      {children}
    </span>
  );
}

export function NewsSourceNote({ sources }: { sources: string[] }) {
  return (
    <Note>
      {sources.length > 0 ? (
        <>
          Sources: {sources.join(", ")}. Headlines and summaries are reproduced from each
          publisher&apos;s own feed and link back to the original; Helios does not fetch or store
          article text.
        </>
      ) : (
        <>No sources configured yet. {EMPTY}</>
      )}
    </Note>
  );
}
