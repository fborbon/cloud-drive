const CACHE      = 'cloud-drive-v3';
const TREE_CACHE = 'cloud-drive-tree-v1';
const OFFLINE_URL = '/pwa/offline.html';
const PRECACHE = [OFFLINE_URL, '/pwa/icons/icon-192.png', '/pwa/icons/icon-512.png', '/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE && k !== TREE_CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Tree index: stale-while-revalidate so the app loads instantly on repeat visits
  if (url.pathname === '/cloud-api/tree') {
    e.respondWith(
      caches.open(TREE_CACHE).then(async cache => {
        const cached = await cache.match(e.request);
        // Always kick off a background refresh
        const networkFetch = fetch(e.request).then(r => {
          if (r.ok) cache.put(e.request, r.clone());
          return r;
        }).catch(() => null);
        // Serve cache instantly if available; otherwise wait for network
        return cached || networkFetch;
      })
    );
    return;
  }

  // Auth endpoints: let the browser handle natively so cookies + redirects work
  if (url.pathname.startsWith('/cloud-api/')) return;

  if (e.request.mode === 'navigate') {
    e.respondWith(fetch(e.request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }
  if (PRECACHE.some(u => e.request.url.endsWith(u))) {
    e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
  }
});
