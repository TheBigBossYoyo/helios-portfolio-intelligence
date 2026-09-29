import { RefreshCw } from "lucide-react";
import { ActionButton } from "@/components/action-button";
import { NewsFeed, NewsSourceNote } from "@/components/news-feed";
import { PageHeader, Panel, Unavailable } from "@/components/panel";
import { Pager } from "@/components/pager";
import { syncNewsAction } from "@/lib/actions";
import { getNews, getPositions } from "@/lib/api";
import { paginate, parsePageParam } from "@/lib/pagination";
import { displayTicker } from "@/lib/format";
import type { NewsItem } from "@/lib/types";
import { SEGMENTED } from "@/lib/ui";

export const dynamic = "force-dynamic";

// The `/api/v1/news` endpoint only supports capping the result with `limit` (see
// src/helios/api.py) — there is no offset param to page through. Fetch well past what one page
// shows, then slice the fetched list here; fine for a single-user portfolio's feed volume.
const FETCH_LIMIT = 200;
const PAGE_SIZE = 10;

export default async function NewsPage({
  searchParams,
}: {
  searchParams: Promise<{ ticker?: string; page?: string; show?: string }>;
}) {
  const { ticker, page, show } = await searchParams;
  // By default only stories that name a holding you own: a per-ticker feed also carries general
  // market commentary under every symbol, and that is noise here. "Everything" shows it all.
  const everything = show === "all";
  const [news, positions] = await Promise.all([
    getNews({
      ticker,
      limit: FETCH_LIMIT,
      heldOnly: !everything,
      mentionsOnly: !everything,
    }),
    getPositions(),
  ]);

  const held = positions.ok
    ? [...positions.data].sort((left, right) =>
        displayTicker(left.instrument.ticker).localeCompare(displayTicker(right.instrument.ticker)),
      )
    : [];
  const sources = news.ok ? [...new Set(news.data.map((item) => item.sourceLabel))] : [];
  const query = (next: { ticker?: string; show?: string }) => {
    const params = new URLSearchParams();
    if (next.ticker) params.set("ticker", next.ticker);
    if (next.show) params.set("show", next.show);
    const text = params.toString();
    return text ? `/news?${text}` : "/news";
  };

  return (
    <>
      <PageHeader
        actions={<NewsSyncButton />}
        description={
          everything
            ? "Everything the feeds you configured returned, newest first — including stories that do not name your holdings."
            : "Stories that name a holding you own, newest first. Each says which words matched."
        }
        title={ticker ? `News — ${displayTicker(ticker)}` : "News"}
      />

      <Panel
        actions={
          <nav aria-label="Which stories" className={SEGMENTED}>
            <ScopeTab active={!everything} href={query({ ticker })} label="About my holdings" />
            <ScopeTab active={everything} href={query({ ticker, show: "all" })} label="Everything" />
          </nav>
        }
        subtitle="Every headline links back to the publisher."
        title="Feed"
      >
        {held.length > 0 ? (
          <nav aria-label="Filter news by holding" className="mb-4 flex flex-wrap gap-2">
            <FilterPill
              active={!ticker}
              href={query({ show: everything ? "all" : undefined })}
              label="All holdings"
            />
            {held.map((position) => (
              <FilterPill
                active={ticker === position.instrument.ticker}
                href={query({
                  ticker: position.instrument.ticker,
                  show: everything ? "all" : undefined,
                })}
                key={position.instrument.ticker}
                label={displayTicker(position.instrument.ticker)}
                title={position.instrument.name ?? undefined}
              />
            ))}
          </nav>
        ) : null}

        {news.ok ? (
          <NewsList
            everything={everything}
            items={news.data}
            page={page}
            sources={sources}
            ticker={ticker}
          />
        ) : (
          <Unavailable detail={news.error} reason="News unavailable" />
        )}
      </Panel>

      {news.ok && news.data.length === 0 ? <GettingStarted /> : null}
    </>
  );
}

function NewsList({
  items,
  page,
  sources,
  ticker,
  everything,
}: {
  items: NewsItem[];
  page?: string;
  sources: string[];
  ticker?: string;
  everything: boolean;
}) {
  const paged = paginate(items, parsePageParam(page), PAGE_SIZE);
  if (items.length === 0 && !everything) {
    return (
      <p className="px-1 py-8 text-center text-sm leading-relaxed text-ink-3">
        No stored story names {ticker ? displayTicker(ticker) : "one of your holdings"} yet.{" "}
        <a className="text-accent hover:underline" href={`/news?${new URLSearchParams({ ...(ticker ? { ticker } : {}), show: "all" }).toString()}`}>
          Show everything
        </a>{" "}
        to see what the feeds returned.
      </p>
    );
  }
  return (
    <>
      <NewsFeed items={paged.items} />
      <Pager
        basePath="/news"
        extraParams={{ ticker, show: everything ? "all" : undefined }}
        page={paged.page}
        pageCount={paged.pageCount}
      />
      <div className="mt-4">
        <NewsSourceNote sources={sources} />
      </div>
    </>
  );
}

function ScopeTab({ href, label, active }: { href: string; label: string; active: boolean }) {
  return (
    <a
      aria-current={active ? "true" : undefined}
      className={`rounded-lg px-3 py-1 text-xs font-medium transition-colors ${
        active ? "bg-surface text-ink shadow-card" : "text-ink-3 hover:text-ink"
      }`}
      href={href}
    >
      {label}
    </a>
  );
}

function FilterPill({
  href,
  label,
  active,
  title,
}: {
  href: string;
  label: string;
  active: boolean;
  title?: string;
}) {
  return (
    <a
      aria-current={active ? "true" : undefined}
      title={title}
      className={`inline-flex items-center whitespace-nowrap rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
        active
          ? "border-transparent bg-accent-soft text-accent-ink"
          : "border-border text-ink-2 hover:border-border-strong hover:bg-surface-2"
      }`}
      href={href}
    >
      {label}
    </a>
  );
}

function GettingStarted() {
  return (
    <Panel
      subtitle="Yahoo Finance, Google News and SEC EDGAR ship enabled. Add your own publishers here."
      title="Adding a source"
    >
      <ol className="flex list-decimal flex-col gap-2 pl-4 text-sm leading-relaxed text-ink-2">
        <li>
          Check the publisher&apos;s terms permit personal, non-redistributed use of their feed.
        </li>
        <li>
          Add an entry to{" "}
          <code className="rounded bg-surface-3 px-1 py-0.5 font-mono text-xs text-ink-2">
            config/news_feeds.yaml
          </code>{" "}
          with a{" "}
          <code className="rounded bg-surface-3 px-1 py-0.5 font-mono text-xs text-ink-2">
            key
          </code>
          ,{" "}
          <code className="rounded bg-surface-3 px-1 py-0.5 font-mono text-xs text-ink-2">
            label
          </code>{" "}
          and{" "}
          <code className="rounded bg-surface-3 px-1 py-0.5 font-mono text-xs text-ink-2">
            url
          </code>{" "}
          (or a{" "}
          <code className="rounded bg-surface-3 px-1 py-0.5 font-mono text-xs text-ink-2">
            url_template
          </code>{" "}
          containing{" "}
          <code className="rounded bg-surface-3 px-1 py-0.5 font-mono text-xs text-ink-2">
            {"{ticker}"}
          </code>{" "}
          to follow each holding).
        </li>
        <li>
          Name one{" "}
          <code className="rounded bg-surface-3 px-1 py-0.5 font-mono text-xs text-ink-2">
            tickers:
          </code>{" "}
          entry to attribute a feed to an instrument. Helios never infers the link from a
          headline.
        </li>
        <li>
          Press <span className="font-medium text-ink">Sync news</span> above, run{" "}
          <code className="rounded bg-surface-3 px-1 py-0.5 font-mono text-xs text-ink-2">
            helios news-sync
          </code>
          , or let the worker pick it up on its next cycle.
        </li>
      </ol>
    </Panel>
  );
}

/**
 * News sync only fetches feeds you configured, so it needs no confirmation: nothing is
 * overwritten and no metered quota is spent beyond the publishers' own free endpoints.
 */
function NewsSyncButton() {
  return (
    <ActionButton
      action={syncNewsAction}
      icon={<RefreshCw aria-hidden="true" size={15} />}
      label="Sync news"
      pendingLabel="Syncing…"
      variant="primary"
    />
  );
}
