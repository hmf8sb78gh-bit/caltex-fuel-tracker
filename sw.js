// PWA service worker
// 策略：HTML 同油價 JSON 用 network-first（保持最新）；圖示/manifest 用 cache-first；離線時回落快取。
const CACHE = "caltex-fuel-v1";
const SHELL = [
  "./index.html",
  "./manifest.webmanifest",
  "./icon-192.png",
  "./icon-512.png",
  "./apple-touch-icon.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

async function networkFirst(event) {
  const cache = await caches.open(CACHE);
  try {
    const fresh = await fetch(event.request);
    if (fresh && fresh.ok) cache.put(event.request, fresh.clone());
    return fresh;
  } catch (e) {
    const cached = await cache.match(event.request);
    if (cached) return cached;
    // 導航請求離線回落主頁
    if (event.request.mode === "navigate") return cache.match("./index.html");
    return Response.error();
  }
}

async function cacheFirst(event) {
  const cached = await caches.match(event.request);
  if (cached) return cached;
  const cache = await caches.open(CACHE);
  try {
    const fresh = await fetch(event.request);
    if (fresh && fresh.ok) cache.put(event.request, fresh.clone());
    return fresh;
  } catch (e) {
    return Response.error();
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // Supabase / CDN 一律走網絡，唔快取

  // HTML 同油價 JSON：network-first
  if (req.mode === "navigate" || url.pathname.endsWith(".json")) {
    event.respondWith(networkFirst(event));
    return;
  }
  // 其餘同源靜態檔（圖示、manifest）：cache-first
  if (/\.(png|jpg|jpeg|webp|svg|webmanifest|ico)$/i.test(url.pathname)) {
    event.respondWith(cacheFirst(event));
  }
});
