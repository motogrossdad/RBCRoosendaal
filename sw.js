/* ════════════════════════════════════════════════════════════
   RBC Roosendaal — service worker
   De app moet het doen op een tribune met slecht bereik. Daarom:
   · de app zelf komt altijd uit de cache en wordt op de
     achtergrond ververst (stale-while-revalidate)
   · data.json net zo, zodat de selectie er meteen staat
   · de externe bronnen en proxies gaan er nooit in: die zijn
     traag en wisselvallig, en de app bewaart die zelf al
   ════════════════════════════════════════════════════════════ */
const CACHE = 'rbc-app-v15';

const SCHIL = [
    '/', '/index.html', '/data.json', '/competitie.json',
    '/icons/icon-192.png', '/icons/icon-512.png', '/icons/maskable-512.png',
    '/rbc.png', '/rbc-klein.png', '/manifest.json',
    '/klassiek.html', '/teletekst.html', '/snes.html',
    // Portretten mee offline: op de tribune is juist dan de vraag
    // "welke van die elf is nummer 14".
    '/players/akram-tourki.jpg',
    '/players/daan-van-reeuwijk.jpg',
    '/players/desley-ubbink.jpg',
    '/players/glaucio-ventura-tiago.jpg',
    '/players/jelte-pal.jpg',
    '/players/jens-verschuren.jpg',
    '/players/jesper-troost.jpg',
    '/players/jordi-ewanena.jpg',
    '/players/leonardo-rocha-de-almeida.jpg',
    '/players/lloyd-hendriks.jpg',
    '/players/luque-casas-diaz.jpg',
    '/players/marwin-reuvers.jpg',
    '/players/oussama-bouyaghlafen.jpg',
    '/players/timo-townsend.jpg',
    '/players/wai-ming-yu.jpg',
    '/players/wesley-spieringhs.jpg'
];

self.addEventListener('install', e => {
    e.waitUntil(
        caches.open(CACHE)
            // Losse verzoeken: één ontbrekend bestand mag de hele
            // installatie niet laten mislukken.
            .then(c => Promise.allSettled(SCHIL.map(u => c.add(u))))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', e => {
    e.waitUntil(
        caches.keys()
            .then(k => Promise.all(k.filter(n => n !== CACHE).map(n => caches.delete(n))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', e => {
    const req = e.request;
    if (req.method !== 'GET') return;

    const url = new URL(req.url);

    // Alles van buiten laten we met rust: de proxies zijn traag en
    // wisselvallig, en de app bewaart die uitkomsten zelf al.
    if (url.origin !== location.origin) return;

    // Navigatie: eerst tonen wat we hebben, dan bijwerken.
    if (req.mode === 'navigate') {
        e.respondWith(
            caches.match('/index.html').then(hit => {
                const net = fetch(req)
                    .then(res => {
                        caches.open(CACHE).then(c => c.put('/index.html', res.clone()));
                        return res;
                    })
                    .catch(() => hit);
                return hit || net;
            })
        );
        return;
    }

    e.respondWith(
        caches.match(req).then(hit => {
            const net = fetch(req)
                .then(res => {
                    if (res && res.status === 200 && res.type === 'basic') {
                        const kopie = res.clone();
                        caches.open(CACHE).then(c => c.put(req, kopie));
                    }
                    return res;
                })
                .catch(() => hit);
            return hit || net;
        })
    );
});
