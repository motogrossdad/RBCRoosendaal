const CACHE = 'rbc-v5-2026-2027';
const ASSETS = ['/', '/index.html', '/teletekst.html', '/snes.html', '/rbc.png', '/data.json'];
self.addEventListener('install', e => {
    e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)));
    self.skipWaiting();
});
self.addEventListener('activate', e => {
    e.waitUntil(caches.keys().then(keys =>
        Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))));
    self.clients.claim();
});
self.addEventListener('fetch', e => {
    // Never cache live data or the CORS proxies that fetch it.
    const bypass = ['hollandsevelden', 'rbcvoetbal', 'sofascore', 'svsplus', 'nos.nl',
                    'allorigins', 'codetabs', 'cors.lol', 'fonts.google'];
    if (bypass.some(h => e.request.url.includes(h))) return;
    e.respondWith(
        fetch(e.request).catch(() => caches.match(e.request))
    );
});
