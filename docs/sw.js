const CACHE_NAME = "chess-analyzer-v3";
const urlsToCache = [
  "/",
  "/demo",
  "/manifest.json",
  "/assets/chess/physical_chess_board_empty.png",
  "/assets/chess/pieces/white_king_e1.png",
  "/assets/chess/pieces/black_king_e8.png"
];

self.addEventListener("install", event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache))
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(names => Promise.all(names.map(name => name !== CACHE_NAME ? caches.delete(name) : undefined)))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  if (event.request.url.includes("/api/")) {
    event.respondWith(fetch(event.request));
    return;
  }

  if (event.request.mode === "navigate" || event.request.url.endsWith("/demo")) {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, response.clone()));
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then(response => response || fetch(event.request))
  );
});
