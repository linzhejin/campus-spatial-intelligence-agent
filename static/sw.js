/* ===========================================================
 * 漫步珞珈 · Service Worker（T-018 §5 标准）
 * 三缓存策略（TDD §9.4 明确）:
 *   1. cache-first        → 首屏静态资源 /static/*  (HTML/CSS/JS/icon/manifest)
 *   2. stale-while-revalidate → /api/* 后端接口调用（SWR：出缓存 + 后台静默更新）
 *   3. network-only       → 第三方资源（高德 JS API / amap.com / amapw.com）
 * =========================================================== */

var CACHE_NAME = 'whu-walker-v4';
var PRECACHE_URLS = [
    '/',
    '/index.html',
    '/css/style.css',
    '/js/app.js',
    '/js/config.js',
    '/manifest.json',
];

/* ---------- install: 预缓存首屏资源 ---------- */
self.addEventListener('install', function (event) {
    event.waitUntil(
        caches.open(CACHE_NAME).then(function (cache) {
            return cache.addAll(PRECACHE_URLS).catch(function () {
                /* 忽略个别资源失败，保证 SW 安装成功 */
            });
        })
    );
    self.skipWaiting();
});

/* ---------- activate: 清理旧版本缓存 ---------- */
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

/* ---------- fetch: 三策略分发 ---------- */
self.addEventListener('fetch', function (event) {
    var request = event.request;
    var url = new URL(request.url);

    /* ───────────────────────────────────────────────
     * 策略 ③ network-only：第三方高德 API / 非同源
     *   · 不缓存任何 amap.com / amapw.com 资源
     *   · 其他非同源请求也一律直走网络
     * ─────────────────────────────────────────────── */
    var isThirdParty = url.origin !== location.origin;
    var isAmapDomain = /amapw?\.com$/i.test(url.hostname) ||
                       /webapi\.amap\.com/i.test(url.href) ||
                       /restapi\.amap\.com/i.test(url.href);

    if (isThirdParty || isAmapDomain) {
        /* NETWORK-ONLY：直接 fetch，不读也不写缓存 */
        event.respondWith(fetch(request));
        return;
    }

    /* ───────────────────────────────────────────────
     * 策略 ② stale-while-revalidate：后端 /api/* 接口
     *   · 优先从缓存返回（秒级响应）
     *   · 同时后台发起新请求更新缓存（下次命中）
     *   · 缓存 miss 时直走网络，成功后写缓存
     * ─────────────────────────────────────────────── */
    var isApiCall = url.pathname.startsWith('/api/');

    if (isApiCall) {
        event.respondWith(
            caches.open(CACHE_NAME).then(function (cache) {
                return cache.match(request).then(function (cached) {
                    /* 后台无论如何都重新 fetch，更新到缓存（revalidate） */
                    var fetchPromise = fetch(request).then(function (networkResponse) {
                        if (networkResponse && networkResponse.status === 200) {
                            cache.put(request, networkResponse.clone());
                        }
                        return networkResponse;
                    }).catch(function () {
                        /* 离线/网络失败：吞掉错误，返回 cached（或兜底 504） */
                        return cached || new Response(
                            JSON.stringify({ error: 'Network unavailable (offline fallback)' }),
                            { status: 504, statusText: 'Offline', headers: { 'Content-Type': 'application/json' } }
                        );
                    });
                    /* SWR 核心：有 cached → 立即返回 cached，同时后台 revalidate */
                    return cached || fetchPromise;
                });
            })
        );
        return;
    }

    /* ───────────────────────────────────────────────
     * 策略 ① 静态资源
     *   · navigate（HTML 导航）：network-first —— 有网必拿最新页面，
     *     成功后写缓存；离线才回退缓存的 index.html（SPA offline shell）
     *   · 其他静态（CSS/JS/img/icon）：cache-first（配合 index.html 里的 ?v= 版本号失效）
     * ─────────────────────────────────────────────── */
    if (request.mode === 'navigate') {
        event.respondWith(
            fetch(request).then(function (response) {
                if (response && response.status === 200) {
                    var copy = response.clone();
                    caches.open(CACHE_NAME).then(function (cache) {
                        cache.put('/index.html', copy);
                    });
                }
                return response;
            }).catch(function () {
                return caches.match('/index.html');
            })
        );
        return;
    }

    event.respondWith(
        caches.match(request).then(function (cached) {
            if (cached) {
                return cached; /* CACHE-HIT：直接返回，不必访问网络 */
            }
            /* CACHE-MISS：回源 fetch，成功后写缓存下次命中 */
            return fetch(request).then(function (response) {
                if (response && response.status === 200) {
                    var copy = response.clone();
                    caches.open(CACHE_NAME).then(function (cache) {
                        cache.put(request, copy);
                    });
                }
                return response;
            }).catch(function () {
                /* navigate：离线时兜底返回已缓存的 index.html（SPA offline shell） */
                if (request.mode === 'navigate') {
                    return caches.match('/index.html');
                }
                return new Response('', { status: 504, statusText: 'Offline' });
            });
        })
    );
});
