"use client";

import {
  BookOpen,
  ChartLine,
  CreditCard,
  Eye,
  LayoutDashboard,
  Lock,
  Menu,
  Newspaper,
  Settings,
  ShieldCheck,
  Sparkles,
  Wallet,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { ThemeToggle } from "./theme-toggle";

const LINKS = [
  { href: "/", label: "Overview", Icon: LayoutDashboard },
  { href: "/holdings", label: "Holdings", Icon: Wallet },
  { href: "/watchlist", label: "Watchlist", Icon: Eye },
  { href: "/performance", label: "Performance", Icon: ChartLine },
  { href: "/card", label: "Card", Icon: CreditCard },
  { href: "/news", label: "News", Icon: Newspaper },
  { href: "/insights", label: "Insights", Icon: Sparkles },
  { href: "/journal", label: "Journal", Icon: BookOpen },
  { href: "/data-quality", label: "Data quality", Icon: ShieldCheck },
  { href: "/settings", label: "Settings", Icon: Settings },
] as const;

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

function NavLinks({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
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
              onClick={onNavigate}
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

/**
 * Primary navigation: a fixed sidebar on desktop, a top bar with a drawer on small screens.
 * The account badge states which Trading 212 environment the numbers come from — demo and live
 * figures look identical, so the page must say which one you are reading.
 */
export function SiteNav({ account }: { account: AccountStatus }) {
  const pathname = usePathname();
  // Closed by the links themselves (onNavigate) and the backdrop, not by watching the route.
  const [open, setOpen] = useState(false);

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

      <div className="theme-fade sticky top-0 z-30 flex items-center justify-between border-b border-border bg-surface/90 px-4 py-3 backdrop-blur lg:hidden">
        <Link className="flex items-center gap-2" href="/">
          <HeliosMark size={26} />
          <span className="text-base font-semibold tracking-tight text-ink">Helios</span>
        </Link>
        <button
          aria-expanded={open}
          aria-label={open ? "Close menu" : "Open menu"}
          className="rounded-lg p-2 text-ink-2 hover:bg-surface-3"
          onClick={() => setOpen((value) => !value)}
          type="button"
        >
          {open ? <X size={20} /> : <Menu size={20} />}
        </button>
      </div>

      {open ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            aria-label="Close menu"
            className="absolute inset-0 bg-black/30"
            onClick={() => setOpen(false)}
            type="button"
          />
          <div className="absolute inset-y-0 left-0 flex w-72 flex-col gap-6 overflow-y-auto border-r border-border bg-surface px-4 py-5 shadow-pop">
            <nav aria-label="Primary">
              <NavLinks onNavigate={() => setOpen(false)} pathname={pathname} />
            </nav>
            <SidebarFooter account={account} />
          </div>
        </div>
      ) : null}
    </>
  );
}
