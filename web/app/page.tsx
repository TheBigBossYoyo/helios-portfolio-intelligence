interface HealthResponse {
  status: string;
  trading212Configured: boolean;
  sqlitePath: string;
}

function parseHealth(data: unknown): HealthResponse | null {
  if (!data || typeof data !== "object") return null;
  const obj = data as Record<string, unknown>;
  if (typeof obj.status !== "string") return null;
  if (typeof obj.trading212Configured !== "boolean") return null;
  if (typeof obj.sqlitePath !== "string") return null;

  return {
    status: obj.status,
    trading212Configured: obj.trading212Configured,
    sqlitePath: obj.sqlitePath,
  };
}

interface HealthResult {
  data: HealthResponse | null;
  error: string | null;
  timestamp: string;
}

async function getHealth(): Promise<HealthResult> {
  const apiUrl = process.env.HELIOS_API_URL || "http://127.0.0.1:8000";
  try {
    const res = await fetch(`${apiUrl}/health`, { cache: "no-store" });
    const timestamp = new Date().toISOString();
    if (!res.ok) {
      return { data: null, error: `HTTP ${res.status}`, timestamp };
    }
    const json = await res.json();
    const data = parseHealth(json);
    if (!data) {
      return { data: null, error: "Malformed response", timestamp };
    }
    return { data, error: null, timestamp };
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : "Network error";
    return { data: null, error: msg, timestamp: new Date().toISOString() };
  }
}

interface StatusIndicatorProps {
  label: string;
  active: boolean;
  value: string;
  timestamp: string;
}

function StatusIndicator({ label, active, value, timestamp }: StatusIndicatorProps) {
  return (
    <div className="relative flex flex-col border border-border bg-graphite-light/50 p-3 sm:p-4">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs uppercase tracking-wider text-neutral-500">{label}</span>
        <span
          aria-hidden="true"
          className={`h-2 w-2 rounded-full ${active ? "bg-amber-accent shadow-[0_0_8px_rgba(255,176,0,0.6)]" : "bg-neutral-600"}`}
        />
      </div>
      <div className="truncate font-mono text-sm text-neutral-200 sm:text-base" title={value}>
        {value}
      </div>
      <div className="mt-2 text-right font-mono text-[10px] text-neutral-600">
        AS_OF: {timestamp}
      </div>
    </div>
  );
}

export default async function Page() {
  const { data, error, timestamp } = await getHealth();
  const isOnline = data?.status === "ok" && !error;

  return (
    <main className="noise-bg flex min-h-screen items-center justify-center p-4 sm:p-8">
      <div className="relative z-10 flex w-full max-w-4xl flex-col gap-8">
        <header className="flex flex-col justify-between gap-4 border-b border-border pb-4 sm:flex-row sm:items-end">
          <div>
            <h1 className="text-3xl sm:text-4xl font-bold tracking-tight text-white mb-1">
              HELIOS<span className="text-amber-accent">_</span>
            </h1>
            <p className="text-sm text-neutral-500 uppercase tracking-widest">
              Milestone 1 Connectivity
            </p>
          </div>
          <div className="flex flex-col gap-1 font-mono text-xs sm:items-end">
            <div className="flex items-center gap-2 border border-neutral-800 bg-neutral-900 px-2 py-1 text-neutral-400">
              <span className="text-acid-green">MODE:</span> DEMO_LOCAL
            </div>
            <div className="flex items-center gap-2 border border-red-900/50 bg-red-950/30 px-2 py-1 text-red-400">
              <span className="h-1.5 w-1.5 rounded-full bg-red-500 motion-safe:animate-pulse" />
              READ-ONLY / NO-TRADE
            </div>
          </div>
        </header>

        <div aria-live="polite" className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <StatusIndicator
            label="API_STATE"
            active={isOnline}
            value={isOnline ? "ONLINE" : `OFFLINE / STALE [${error}]`}
            timestamp={timestamp}
          />
          <StatusIndicator
            label="DATABASE"
            active={!!data?.sqlitePath}
            value={data?.sqlitePath || "UNAVAILABLE / STALE"}
            timestamp={timestamp}
          />
          <StatusIndicator
            label="T212_UPLINK"
            active={!!data?.trading212Configured}
            value={data?.trading212Configured ? "CONFIGURED" : "PENDING"}
            timestamp={timestamp}
          />
        </div>

        <section className="flex flex-col gap-2 border border-border bg-[#121214] p-4 font-mono text-xs text-neutral-400">
          <div className="flex justify-between border-b border-neutral-800 pb-2">
            <span>SYSTEM_TICK</span>
            <span className="text-neutral-300">{timestamp}</span>
          </div>
          <div className="flex justify-between border-b border-neutral-800 pb-2 pt-1">
            <span>TARGET_ACCOUNT</span>
            <span className="text-neutral-300">EUR INVEST</span>
          </div>
          <div className="flex justify-between pt-1">
            <span>SHELL_VERSION</span>
            <span className="text-neutral-300">v0.1.0-M1</span>
          </div>
        </section>

        <nav aria-label="Build milestones" className="grid grid-cols-4 gap-px border border-border bg-border sm:grid-cols-8">
          {Array.from({ length: 8 }, (_, index) => (
            <div
              className={`bg-graphite px-2 py-2 text-center text-[10px] tracking-wider ${index === 0 ? "text-amber-accent" : "text-neutral-600"}`}
              key={index}
            >
              M{index + 1} / {index === 0 ? "ACTIVE" : "LOCKED"}
            </div>
          ))}
        </nav>
      </div>
    </main>
  );
}
