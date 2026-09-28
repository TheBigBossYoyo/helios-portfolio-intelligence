import { CompassIcon } from "lucide-react";
import Link from "next/link";
import { Panel } from "@/components/panel";
import { BUTTON } from "@/lib/ui";

/**
 * The catch-all for an unmatched route, and for any page that calls `notFound()` explicitly
 * (a thesis or journal lookup by an id that does not exist, for instance).
 */
export default function NotFound() {
  return (
    <Panel subtitle="There is nothing at this address." title="Page not found">
      <div className="flex flex-col items-center gap-4 py-6 text-center">
        <span
          aria-hidden="true"
          className="flex h-11 w-11 items-center justify-center rounded-full bg-surface-3 text-ink-3"
        >
          <CompassIcon size={20} strokeWidth={1.75} />
        </span>
        <p className="max-w-sm text-sm leading-relaxed text-ink-3">
          The page you asked for does not exist, or whatever it pointed to was removed.
        </p>
        <Link className={BUTTON.primary} href="/">
          ← Back to overview
        </Link>
      </div>
    </Panel>
  );
}
