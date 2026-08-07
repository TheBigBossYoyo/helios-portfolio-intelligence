import { NewsFeed, NewsSourceNote } from "@/components/news-feed";
import { Panel, Unavailable } from "@/components/panel";
import { getNews, getPositions } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function NewsPage({
  searchParams,
}: {
  searchParams: Promise<{ ticker?: string }>;
}) {
  const { ticker } = await searchParams;
  const [news, positions] = await Promise.all([
    getNews({ ticker, limit: 100 }),
    getPositions(),
  ]);

  const heldTickers = positions.ok
    ? positions.data.map((position) => position.instrument.ticker)
    : [];
  const sources = news.ok ? [...new Set(news.data.map((item) => item.sourceLabel))] : [];

  return (
    <>
      <Panel
        subtitle={
          ticker
            ? `Articles from feeds you configured for ${ticker}.`
            : "Articles from the feeds you configured, newest first."
        }
        title={ticker ? `News — ${ticker}` : "News"}
      >
        {heldTickers.length > 0 ? (
          <nav aria-label="Filter news by holding" className="mb-4 flex flex-wrap gap-2">
            <FilterLink active={!ticker} href="/news" label="All" />
            {heldTickers.map((held) => (
              <FilterLink
                active={ticker === held}
                href={`/news?ticker=${encodeURIComponent(held)}`}
                key={held}
                label={held}
              />
            ))}
          </nav>
        ) : null}

        {news.ok ? (
          <>
            <NewsFeed items={news.data} />
            <div className="mt-4">
              <NewsSourceNote sources={sources} />
            </div>
          </>
        ) : (
          <Unavailable detail={news.error} reason="News unavailable" />
        )}
      </Panel>

      {news.ok && news.data.length === 0 ? <GettingStarted /> : null}
    </>
  );
}

function FilterLink({
  href,
  label,
  active,
}: {
  href: string;
  label: string;
  active: boolean;
}) {
  return (
    <a
      aria-current={active ? "true" : undefined}
      className={`border px-2 py-1 text-[10px] uppercase tracking-wider transition-colors ${
        active
          ? "border-amber-accent/50 bg-amber-accent/10 text-amber-accent"
          : "border-neutral-800 text-neutral-500 hover:text-neutral-300"
      }`}
      href={href}
    >
      {label}
    </a>
  );
}

function GettingStarted() {
  return (
    <Panel subtitle="Helios ships no feeds — you choose the publishers." title="Adding a source">
      <ol className="flex list-decimal flex-col gap-2 pl-4 text-[11px] leading-relaxed text-neutral-500">
        <li>
          Check the publisher&apos;s terms permit personal, non-redistributed use of their feed.
        </li>
        <li>
          Add an entry to <code className="text-neutral-300">config/news_feeds.yaml</code> with a{" "}
          <code className="text-neutral-300">key</code>,{" "}
          <code className="text-neutral-300">label</code> and{" "}
          <code className="text-neutral-300">url</code> (or a{" "}
          <code className="text-neutral-300">url_template</code> containing{" "}
          <code className="text-neutral-300">{"{ticker}"}</code> to follow each holding).
        </li>
        <li>
          Name one <code className="text-neutral-300">tickers:</code> entry to attribute a feed to
          an instrument. Helios never infers the link from a headline.
        </li>
        <li>
          Run <code className="text-neutral-300">helios news-sync</code>, or let the worker pick it
          up on its next cycle.
        </li>
      </ol>
    </Panel>
  );
}
