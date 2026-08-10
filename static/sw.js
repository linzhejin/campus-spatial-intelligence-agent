var CACHE_NAME = 'whu-walker-v1';
var PRECACHE_URLS = [
    '/',
    '/index.html',
    '/css/style.css',
    '/js/app.js',
    '/js/config.js',
    '/manifest.json',
];

self.addEventListener('install', function (event) {
    event.waitUntil(
        caches.open(CACHE_NAME).then(function (cache) {
            return cache.addAll(PRECACHE_URLS).catch(function () {});
        })
    );
    self.skipWaiting();
});

self.addEventListener('activate', function (event) {
    event.waitUntil(
        caches.keys().then(function (keys) {
            return Promise.all(
                keys.filter(function (k) { return k !== CACHE_NAME; })
                    .map(function (k) { return caches.delete(k); })
            );
        })
    );
    self.clients.claim();
});

self.addEventListener('fetch', function (event) {
    var request = event.request;
    var url = new URL(request.url);

    if (url.origin === location.origin) {
        if (request.mode === 'navigate') {
            event.respondWith(
                fetch(request).then(function (response) {
                    var copy = response.clone();
                    caches.open(CACHE_NAME).then(function (cache) {
                        cache.put(request, copy);
                    });
                    return response;
                }).catch(function () {
                    return caches.match(request).then(function (cached) {
                        return cached || caches.match('/index.html');
                    });
                })
            );
        } else {
            event.respondWith(
                caches.match(request).then(function (cached) {
                    return cached || fetch(request).then(function (response) {
                        if (response.status === 200) {
                            var copy = response.clone();
                            caches.open(CACHE_NAME).then(function (cache) {
                                cache.put(request, copy);
                            });
                        }
                        return response;
                    }).catch(function () {
                        return new Response('', { status: 504, statusText: 'Offline' });
                    });
                })
            );
        }
    }
});