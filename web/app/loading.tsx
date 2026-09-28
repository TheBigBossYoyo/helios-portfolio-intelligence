import { CARD } from "@/lib/ui";

/**
 * Root loading fallback.
 *
 * Every route here is `force-dynamic`, so the App Router shows this while the next page's server
 * render is in flight — most visibly on the first navigation to the heavier pages (performance,
 * journal). The shapes mirror `Panel`'s own surface and header rule so the swap-in reads as the
 * same UI settling, not a different screen appearing. The pulse follows the same
 * `motion-safe:animate-pulse` convention as the read-only indicator in the header: animated only
 * when the visitor has not asked their OS to turn animation off.
 */
export default function Loading() {
  return (
    <div className="flex flex-col gap-6">
      <span className="sr-only" role="status">
        Loading…
      </span>
      <div aria-hidden="true" className="flex flex-col gap-6">
        <SkeletonPanel headerWidth="9rem" rows={2} />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <SkeletonTile key={index} />
          ))}
        </div>
        <SkeletonPanel headerWidth="11rem" rows={6} />
      </div>
    </div>
  );
}

function SkeletonPanel({ headerWidth, rows }: { headerWidth: string; rows: number }) {
  return (
    <section className={`${CARD} flex flex-col`}>
      <header className="flex items-baseline justify-between gap-2 border-b border-border px-5 pb-3 pt-4">
        <Bar width={headerWidth} />
      </header>
      <div className="flex flex-col gap-3 px-5 pb-5 pt-4">
        {Array.from({ length: rows }).map((_, index) => (
          <Bar key={index} width={index % 3 === 2 ? "45%" : index % 2 === 0 ? "100%" : "72%"} />
        ))}
      </div>
    </section>
  );
}

function SkeletonTile() {
  return (
    <div className={`${CARD} flex flex-col gap-3 p-4`}>
      <Bar width="60%" />
      <Bar width="40%" />
    </div>
  );
}

function Bar({ width }: { width: string }) {
  return <div className="h-3 rounded-full bg-surface-3 motion-safe:animate-pulse" style={{ width }} />;
}
