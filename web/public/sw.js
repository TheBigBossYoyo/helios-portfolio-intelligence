/*
 * Helios service worker: notifications on the phone, and the last-seen pages when the computer
 * running Helios cannot be reached.
 *
 * Pages: network first (8 s), falling back to the copy saved the last time the page loaded,
 * marked with the time it was saved so the page can say so. Built assets (/_next/static) never
 * change under a given name, so they come from the cache first. Nothing is saved unless the
 * computer answered normally, so a pairing page or an error is never kept.
 */

const PAGES = "helios-pages-v1";
const ASSETS = "helios-assets-v1";
const NAVIGATION_TIMEOUT_MS = 8000;
const WARM_EVERY_MS = 15 * 60 * 1000;
const WARM_PAGES = [
  "/",
  "/holdings",
  "/exposure",
  "/performance",
  "/card",
  "/calendar",
  "/plan",
  "/targets",
  "/news",
  "/watchlist",
];
let lastWarm = 0;

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const name of await caches.keys()) {
        if (name !== PAGES && name !== ASSETS) await caches.delete(name);
      }
      await self.clients.claim();
    })(),
  );
});

function isAsset(url) {
  return (
    url.pathname.startsWith("/_next/static/") ||
    /^\/(icon[^/]*\.(png|svg)|apple-icon\.png|manifest\.webmanifest)$/.test(url.pathname)
  );
}

function timeout(ms) {
  return new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), ms));
}

async function savePage(url, response) {
  const body = await response.clone().text();
  const headers = new Headers(response.headers);
  headers.set("x-helios-saved-at", new Date().toISOString());
  const cache = await caches.open(PAGES);
  await cache.put(url, new Response(body, { status: 200, headers }));
  // Keep the page's own scripts and styles so it can render offline too.
  const assets = new Set(body.match(/\/_next\/static\/[^"'\s)\\]+/g) || []);
  const store = await caches.open(ASSETS);
  await Promise.all(
    [...assets].map(async (path) => {
      if (await store.match(path)) return;
      try {
        const asset = await fetch(path, { credentials: "same-origin" });
        if (asset.ok) await store.put(path, asset);
      } catch {
        // Offline again already: the next visit fills it in.
      }
    }),
  );
}

async function savedPage(url) {
  const cache = await caches.open(PAGES);
  const exact = await cache.match(url);
  const saved = exact || (await cache.match(new URL(url).pathname)) || null;
  if (!saved) return null;
  const html = await saved.text();
  const at = saved.headers.get("x-helios-saved-at") || "";
  const marked = html.replace("</head>", `<meta name="helios-offline" content="${at}"></head>`);
  return new Response(marked, {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
}

function offlinePage() {
  return new Response(
    `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Helios is offline</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;font:16px/1.5 -apple-system,system-ui,sans-serif;background:#f5f6f8;color:#101828;padding:24px}
@media (prefers-color-scheme:dark){body{background:#0b0e14;color:#f2f4f7}p{color:#939cad}}main{max-width:340px;text-align:center}p{color:#667085}
button{margin-top:12px;padding:12px 20px;border:0;border-radius:12px;background:#4f46e5;color:#fff;font:600 15px system-ui}</style></head>
<body><main><h1>Can't reach Helios</h1><p>The computer running Helios may be off or asleep, or this phone is offline. Pages you opened before are still available.</p>
<button onclick="location.reload()">Try again</button></main></body></html>`,
    { status: 503, headers: { "content-type": "text/html; charset=utf-8" } },
  );
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/__helios/") || url.pathname === "/sw.js") return;

  if (isAsset(url)) {
    event.respondWith(
      (async () => {
        const store = await caches.open(ASSETS);
        const hit = await store.match(request);
        if (hit) return hit;
        const response = await fetch(request);
        if (response.ok) await store.put(request, response.clone());
        return response;
      })(),
    );
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          const response = await Promise.race([fetch(request), timeout(NAVIGATION_TIMEOUT_MS)]);
          const html = (response.headers.get("content-type") || "").includes("text/html");
          if (response.ok && html) {
            event.waitUntil(savePage(request.url, response));
            return response;
          }
          if (response.status >= 500) throw new Error(`upstream ${response.status}`);
          return response;
        } catch {
          return (await savedPage(request.url)) || offlinePage();
        }
      })(),
    );
  }
});

self.addEventListener("message", (event) => {
  if (!event.data || event.data.type !== "warm") return;
  const now = Date.now();
  if (now - lastWarm < WARM_EVERY_MS) return;
  lastWarm = now;
  event.waitUntil(
    Promise.all(
      WARM_PAGES.map(async (path) => {
        try {
          const response = await fetch(path, { credentials: "same-origin" });
          if (response.ok && (response.headers.get("content-type") || "").includes("text/html")) {
            await savePage(new URL(path, self.location.origin).href, response);
          }
        } catch {
          // The computer went away mid-way: keep what was saved.
        }
      }),
    ),
  );
});

self.addEventListener("push", (event) => {
  let message = {};
  try {
    message = event.data ? event.data.json() : {};
  } catch {
    message = { body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(message.title || "Helios", {
      body: message.body || "",
      tag: message.tag || undefined,
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      data: { url: message.url || "/" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin).href;
  event.waitUntil(
    (async () => {
      const open = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of open) {
        if ("focus" in client) {
          await client.focus();
          if ("navigate" in client) await client.navigate(target);
          return;
        }
      }
      await self.clients.openWindow(target);
    })(),
  );
});
