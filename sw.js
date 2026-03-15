const CACHE = 'rbc-v1';
const ASSETS = ['/', '/index.html'];
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
    if (e.request.url.includes('hollandsevelden') || e.request.url.includes('allorigins') ||
        e.request.url.includes('corsproxy') || e.request.url.includes('codetabs') ||
        e.request.url.includes('fonts.google')) return;
    e.respondWith(
        fetch(e.request).catch(() => caches.match(e.request))
    );
});
