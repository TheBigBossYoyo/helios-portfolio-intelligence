"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/holdings", label: "Holdings" },
  { href: "/performance", label: "Performance" },
  { href: "/news", label: "News" },
  { href: "/insights", label: "Insights" },
  { href: "/journal", label: "Journal" },
  { href: "/data-quality", label: "Data quality" },
] as const;

export function SiteNav() {
  const pathname = usePathname();

  return (
    <nav aria-label="Primary" className="flex flex-wrap gap-px border border-border bg-border">
      {LINKS.map((link) => {
        const active = pathname === link.href;
        return (
          <Link
            aria-current={active ? "page" : undefined}
            className={`nav-underline relative flex-1 whitespace-nowrap bg-graphite px-3 py-2.5 text-center text-[11px] uppercase tracking-wider transition-colors duration-200 ${
              active ? "bg-graphite-light/60 text-amber-accent" : "text-neutral-500 hover:text-neutral-200"
            }`}
            href={link.href}
            key={link.href}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
