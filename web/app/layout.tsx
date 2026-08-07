import type { Metadata } from "next";
import { IBM_Plex_Sans, JetBrains_Mono } from "next/font/google";
import { SiteNav } from "@/components/site-nav";
import "./globals.css";

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
});

const ibmPlexSans = IBM_Plex_Sans({
  variable: "--font-ibm-plex-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

export const metadata: Metadata = {
  title: "Helios",
  description: "Local-first, read-only Trading 212 portfolio intelligence",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body
        className={`${jetbrainsMono.variable} ${ibmPlexSans.variable} min-h-screen bg-[#09090b] font-mono text-[#a1a1aa] antialiased`}
      >
        <div className="noise-bg min-h-screen">
          <div className="relative z-10 mx-auto flex w-full max-w-7xl flex-col gap-6 p-4 sm:p-6 lg:p-8">
            <header className="relative flex flex-col justify-between gap-4 pb-5 after:absolute after:inset-x-0 after:bottom-0 after:h-px after:rule-fade sm:flex-row sm:items-end">
              <div>
                <h1 className="mb-1 text-3xl font-bold tracking-tight sm:text-4xl">
                  <span className="bg-gradient-to-b from-white via-white to-neutral-400 bg-clip-text text-transparent">
                    HELIOS
                  </span>
                  <span className="text-amber-accent [text-shadow:0_0_18px_rgba(255,176,0,0.45)]">
                    _
                  </span>
                </h1>
                <p className="text-xs uppercase tracking-widest text-neutral-500">
                  Portfolio intelligence · local only
                </p>
              </div>
              <div className="flex flex-col gap-1 font-mono text-xs sm:items-end">
                <div className="panel-raised flex items-center gap-2 border border-neutral-800 px-2 py-1 text-neutral-400">
                  <span className="text-acid-green">MODE:</span> DEMO_LOCAL
                </div>
                <div className="flex items-center gap-2 border border-red-900/50 bg-red-950/30 px-2 py-1 text-red-400">
                  <span className="h-1.5 w-1.5 rounded-full bg-red-500 motion-safe:animate-pulse" />
                  READ-ONLY / NO-TRADE
                </div>
              </div>
            </header>

            <SiteNav />

            <main className="flex flex-col gap-6">{children}</main>

            <footer className="relative pt-5 text-[10px] leading-relaxed text-neutral-600 before:absolute before:inset-x-0 before:top-0 before:h-px before:rule-fade">
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
