// Service Worker para PWA Oráculo KYC
const VERSION = "v1.0.0";
const CACHE = `oraculo-${VERSION}`;

const PRECACHE = [
  "/m.html",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png"
];

// Install: precache static
self.addEventListener("install", (e) => {
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE).catch(() => {}))
  );
});

// Activate: limpia caches viejas
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Fetch: network-first para API, cache-first para assets
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // API: network only, no cache
  if (url.pathname.startsWith("/api/")) return;
  // static: cache-first
  e.respondWith(
    caches.match(e.request).then(cached => {
      if (cached) return cached;
      return fetch(e.request).then(r => {
        if (r.ok && (url.pathname.endsWith(".html") || url.pathname.endsWith(".js") || url.pathname.endsWith(".css") || url.pathname.includes("/static/"))) {
          const clone = r.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return r;
      }).catch(() => cached);
    })
  );
});
