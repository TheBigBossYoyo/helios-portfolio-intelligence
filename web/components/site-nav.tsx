"use client";

import {
  BookOpen,
  CalendarDays,
  ChartLine,
  CreditCard,
  Eye,
  LayoutDashboard,
  LayoutGrid,
  Lock,
  Newspaper,
  RotateCw,
  Scale,
  Settings,
  ShieldCheck,
  Sparkles,
  Target,
  Wallet,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState, useTransition } from "react";
import { ThemeToggle } from "./theme-toggle";

const LINKS = [
  { href: "/", label: "Overview", Icon: LayoutDashboard },
  { href: "/holdings", label: "Holdings", Icon: Wallet },
  { href: "/watchlist", label: "Watchlist", Icon: Eye },
  { href: "/performance", label: "Performance", Icon: ChartLine },
  { href: "/card", label: "Card", Icon: CreditCard },
  { href: "/calendar", label: "Calendar", Icon: CalendarDays },
  { href: "/plan", label: "Plan", Icon: Target },
  { href: "/targets", label: "Targets", Icon: Scale },
  { href: "/news", label: "News", Icon: Newspaper },
  { href: "/insights", label: "Insights", Icon: Sparkles },
  { href: "/journal", label: "Journal", Icon: BookOpen },
  { href: "/data-quality", label: "Data quality", Icon: ShieldCheck },
  { href: "/settings", label: "Settings", Icon: Settings },
] as const;

/** The phone's tab bar: the four pages opened most, then everything else under More. */
const TAB_HREFS: readonly string[] = ["/", "/holdings", "/card", "/news"];

export interface AccountStatus {
  /** Which Trading 212 account the configured credentials belong to; null if the API is down. */
  environment: "demo" | "live" | null;
  configured: boolean;
}

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

export function HeliosMark({ size = 28 }: { size?: number }) {
  return (
    <svg aria-hidden="true" height={size} viewBox="0 0 32 32" width={size}>
      <rect fill="var(--ink)" height="32" rx="9" width="32" />
      <circle cx="16" cy="16" fill="var(--brand)" r="6" />
      <g stroke="var(--brand)" strokeLinecap="round" strokeWidth="2">
        <path d="M16 4.5v3M16 24.5v3M4.5 16h3M24.5 16h3M7.9 7.9l2.1 2.1M22 22l2.1 2.1M24.1 7.9 22 10M10 22l-2.1 2.1" />
      </g>
    </svg>
  );
}

function AccountBadge({ account }: { account: AccountStatus }) {
  if (account.environment === null) {
    return (
      <div className="flex items-center gap-2 rounded-lg bg-negative-soft px-3 py-2 text-xs font-medium text-negative">
        <span aria-hidden="true" className="h-2 w-2 rounded-full bg-[var(--status-critical)]" />
        API offline
      </div>
    );
  }
  const live = account.environment === "live";
  return (
    <div
      className={`flex flex-col gap-0.5 rounded-lg px-3 py-2 text-xs ${
        live ? "bg-positive-soft" : "bg-warning-soft"
      }`}
    >
      <span className={`flex items-center gap-2 font-semibold ${live ? "text-positive" : "text-warning"}`}>
        <span
          aria-hidden="true"
          className="h-2 w-2 rounded-full"
          style={{ background: live ? "var(--status-good)" : "var(--status-warning)" }}
        />
        {live ? "Live account" : "Practice account"}
      </span>
      <span className="text-ink-3">
        {account.configured ? "Trading 212 key saved" : "Not connected yet"}
      </span>
    </div>
  );
}

function NavLinks({ pathname }: { pathname: string }) {
  return (
    <ul className="flex flex-col gap-0.5">
      {LINKS.map(({ href, label, Icon }) => {
        const active = isActive(pathname, href);
        return (
          <li key={href}>
            <Link
              aria-current={active ? "page" : undefined}
              className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                active
                  ? "bg-accent-soft text-accent-ink"
                  : "text-ink-2 hover:bg-surface-3 hover:text-ink"
              }`}
              href={href}
            >
              <Icon aria-hidden="true" size={18} strokeWidth={active ? 2.25 : 1.75} />
              {label}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function SidebarFooter({ account }: { account: AccountStatus }) {
  return (
    <div className="flex flex-col gap-3">
      <AccountBadge account={account} />
      <div className="flex items-center gap-2 px-1 text-xs text-ink-3">
        <Lock aria-hidden="true" size={13} />
        Read-only · never places trades
      </div>
      <ThemeToggle />
    </div>
  );
}

/** Re-renders the page from the server: the standalone phone app has no browser reload. */
function RefreshButton() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  return (
    <button
      aria-label="Refresh"
      className="flex h-10 w-10 items-center justify-center rounded-full text-ink-2 active:bg-surface-3"
      disabled={pending}
      onClick={() => startTransition(() => router.refresh())}
      type="button"
    >
      <RotateCw
        aria-hidden="true"
        className={pending ? "animate-spin" : undefined}
        size={19}
        strokeWidth={2}
      />
    </button>
  );
}

function AccountPill({ account }: { account: AccountStatus }) {
  const offline = account.environment === null;
  const live = account.environment === "live";
  const tone = offline
    ? "bg-negative-soft text-negative"
    : live
      ? "bg-positive-soft text-positive"
      : "bg-warning-soft text-warning";
  const dot = offline ? "var(--status-critical)" : live ? "var(--status-good)" : "var(--status-warning)";
  return (
    <span className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${tone}`}>
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full" style={{ background: dot }} />
      {offline ? "Offline" : live ? "Live" : "Practice"}
    </span>
  );
}

function MoreSheet({
  account,
  pathname,
  onClose,
}: {
  account: AccountStatus;
  pathname: string;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  const rest = LINKS.filter((link) => !TAB_HREFS.includes(link.href));
  return (
    <div className="fixed inset-0 z-50 lg:hidden">
      <button
        aria-label="Close"
        className="sheet-backdrop absolute inset-0 bg-black/40"
        onClick={onClose}
        type="button"
      />
      <div
        aria-label="More pages"
        aria-modal="true"
        className="sheet-panel absolute inset-x-0 bottom-0 flex max-h-[85vh] flex-col gap-5 overflow-y-auto rounded-t-3xl border-t border-border bg-surface px-5 pb-[calc(env(safe-area-inset-bottom)+1.25rem)] pt-3 shadow-pop"
        role="dialog"
      >
        <span aria-hidden="true" className="mx-auto h-1.5 w-10 shrink-0 rounded-full bg-border-strong" />
        <nav aria-label="More">
          <ul className="grid grid-cols-3 gap-2.5">
            {rest.map(({ href, label, Icon }) => {
              const active = isActive(pathname, href);
              return (
                <li key={href}>
                  <Link
                    aria-current={active ? "page" : undefined}
                    className={`flex h-20 flex-col items-center justify-center gap-1.5 rounded-2xl border text-xs font-medium transition-colors ${
                      active
                        ? "border-transparent bg-accent-soft text-accent-ink"
                        : "border-border bg-surface-2 text-ink-2 active:bg-surface-3"
                    }`}
                    href={href}
                    onClick={onClose}
                  >
                    <Icon aria-hidden="true" size={22} strokeWidth={active ? 2.25 : 1.75} />
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <SidebarFooter account={account} />
      </div>
    </div>
  );
}

function TabBar({
  pathname,
  moreOpen,
  onMore,
}: {
  pathname: string;
  moreOpen: boolean;
  onMore: () => void;
}) {
  const tabs = LINKS.filter((link) => TAB_HREFS.includes(link.href));
  const moreActive = moreOpen || !tabs.some((tab) => isActive(pathname, tab.href));
  const item = (active: boolean) =>
    `flex w-full flex-col items-center gap-0.5 pb-1.5 pt-2 text-[11px] font-medium transition-colors ${
      active ? "text-accent-ink" : "text-ink-3 active:text-ink"
    }`;
  const pill = (active: boolean) =>
    `flex h-8 w-14 items-center justify-center rounded-full transition-colors ${
      active ? "bg-accent-soft" : ""
    }`;
  return (
    <nav
      aria-label="Tabs"
      className="theme-fade fixed inset-x-0 bottom-0 z-40 border-t border-border bg-surface/90 pb-[env(safe-area-inset-bottom)] backdrop-blur-xl lg:hidden"
    >
      <ul className="mx-auto grid max-w-lg grid-cols-5">
        {tabs.map(({ href, label, Icon }) => {
          const active = isActive(pathname, href);
          return (
            <li key={href}>
              <Link aria-current={active ? "page" : undefined} className={item(active)} href={href}>
                <span className={pill(active)}>
                  <Icon aria-hidden="true" size={21} strokeWidth={active ? 2.25 : 1.75} />
                </span>
                {label}
              </Link>
            </li>
          );
        })}
        <li>
          <button aria-expanded={moreOpen} className={item(moreActive)} onClick={onMore} type="button">
            <span className={pill(moreActive)}>
              <LayoutGrid aria-hidden="true" size={21} strokeWidth={moreActive ? 2.25 : 1.75} />
            </span>
            More
          </button>
        </li>
      </ul>
    </nav>
  );
}

/**
 * Primary navigation: a fixed sidebar on desktop; on a phone, a slim top bar and an app-style
 * tab bar with the remaining pages in a sheet. The account badge states which Trading 212
 * environment the numbers come from — demo and live figures look identical, so the page must
 * say which one you are reading.
 */
export function SiteNav({ account }: { account: AccountStatus }) {
  const pathname = usePathname();
  const [more, setMore] = useState(false);
  const close = useCallback(() => setMore(false), []);

  return (
    <>
      <aside className="theme-fade sticky top-0 hidden h-screen w-64 shrink-0 flex-col border-r border-border bg-surface px-4 py-5 lg:flex">
        <Link className="mb-7 flex items-center gap-2.5 px-2" href="/">
          <HeliosMark />
          <span className="text-lg font-semibold tracking-tight text-ink">Helios</span>
        </Link>
        <nav aria-label="Primary" className="flex-1 overflow-y-auto">
          <NavLinks pathname={pathname} />
        </nav>
        <SidebarFooter account={account} />
      </aside>

      <header className="theme-fade sticky top-0 z-30 border-b border-border bg-surface/90 pt-[env(safe-area-inset-top)] backdrop-blur-xl lg:hidden">
        <div className="flex h-14 items-center justify-between pl-4 pr-2">
          <Link className="flex items-center gap-2" href="/">
            <HeliosMark size={26} />
            <span className="text-base font-semibold tracking-tight text-ink">Helios</span>
          </Link>
          <div className="flex items-center gap-1">
            <AccountPill account={account} />
            <RefreshButton />
          </div>
        </div>
      </header>

      <TabBar moreOpen={more} onMore={() => setMore((value) => !value)} pathname={pathname} />
      {more ? <MoreSheet account={account} onClose={close} pathname={pathname} /> : null}
    </>
  );
}
