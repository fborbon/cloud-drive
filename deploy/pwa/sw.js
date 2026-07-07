const CACHE = 'cloud-drive-v1';
const OFFLINE_URL = '/pwa/offline.html';
const PRECACHE = [OFFLINE_URL, '/pwa/icons/icon-192.png', '/pwa/icons/icon-512.png', '/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request).catch(() => caches.match(OFFLINE_URL))
    );
    return;
  }
  if (PRECACHE.some(u => e.request.url.endsWith(u))) {
    e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
  }
});
