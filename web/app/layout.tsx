import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { SiteNav, type AccountStatus } from "@/components/site-nav";
import { THEME_BOOT_SCRIPT } from "@/components/theme-toggle";
import { getHealth } from "@/lib/api";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: { default: "Helios", template: "%s · Helios" },
  description: "Local-first, read-only Trading 212 portfolio intelligence",
  other: { google: "notranslate" },
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const health = await getHealth();
  const account: AccountStatus = health.ok
    ? {
        environment: health.data.trading212Environment ?? "demo",
        configured: health.data.trading212Configured,
      }
    : { environment: null, configured: false };

  return (
    // translate="no": machine translation rewrites tickers, status labels and figures in place,
    // and a browser in another language offers it on every load. A garbled number is worse
    // than an untranslated one.
    //
    // suppressHydrationWarning: the boot script sets data-theme before React hydrates, so the
    // attribute legitimately differs from the server render.
    <html data-theme="light" lang="en" suppressHydrationWarning translate="no">
      <head>
        {/* Must run before first paint, or the page flashes light before switching to dark. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body
        className={`${inter.variable} ${jetbrainsMono.variable} min-h-screen font-sans antialiased`}
      >
        <div className="flex min-h-screen flex-col lg:flex-row">
          <SiteNav account={account} />
          <div className="flex min-w-0 flex-1 flex-col">
            <main className="mx-auto flex w-full max-w-[1360px] flex-1 flex-col gap-6 px-4 py-6 sm:px-6 lg:px-10 lg:py-9">
              {children}
            </main>
            <footer className="mx-auto w-full max-w-[1360px] px-4 pb-8 text-xs leading-relaxed text-ink-4 sm:px-6 lg:px-10">
              Helios is a personal, read-only analytics tool. It never places trades. Figures are
              reconstructed from your own Trading 212 history and labelled ETF proxies — they are
              not investment advice, and benchmark proxies are not official index levels.
            </footer>
          </div>
        </div>
      </body>
    </html>
  );
}
