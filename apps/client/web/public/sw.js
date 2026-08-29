// Minimal PWA service worker. Only registered outside the Electron desktop
// shell (see src/main.tsx) so the WebView cache never interferes with desktop.
const CACHE = 'dsa-pwa-v1';

self.addEventListener('install', (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  // Network-first for navigations, cache fallback when offline.
  event.respondWith(
    fetch(request).catch(() => caches.match(request)),
  );
});
