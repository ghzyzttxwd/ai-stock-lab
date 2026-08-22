const CACHE = 'ai-trade-v3-shell-4';
const SHELL = ['./', './index.html', './app.css', './app.js', './data.json', './manifest.webmanifest', './ai-stock-icon-192.png', './ai-stock-icon-512.png', './ai-stock-maskable-192.png', './ai-stock-maskable-512.png', './ai-stock-apple-touch.png'];

self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL))));
self.addEventListener('activate', event => event.waitUntil(
  caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
));
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  event.respondWith(fetch(event.request).then(response => {
    const copy = response.clone();
    caches.open(CACHE).then(cache => cache.put(event.request, copy));
    return response;
  }).catch(() => caches.match(event.request)));
});
