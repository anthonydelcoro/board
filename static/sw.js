/* Board service worker.
   Network first so that copying new files onto the NAS shows up right away,
   with the cache as the fallback when the tailnet is out of reach.
   API traffic is never cached - the app keeps its own copy of the board in
   localStorage and merges when it reconnects. */

const CACHE = "board-shell-v1";
const SHELL = [
  "/",
  "/manifest.json",
  "/favicon.ico",
  "/favicon-32.png",
  "/icon-192.png",
  "/icon-512.png",
  "/apple-touch-icon.png"
];

self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(SHELL))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting())
  );
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if(e.request.method !== "GET") return;
  if(url.origin !== self.location.origin) return;
  if(url.pathname.startsWith("/api/")) return;

  e.respondWith(
    fetch(e.request)
      .then(res => {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(e.request).then(hit => hit || caches.match("/")))
  );
});
