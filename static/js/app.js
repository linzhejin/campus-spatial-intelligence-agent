(function () {
    'use strict';

    var CFG = (function () {
        var fallback = {
            TIANDITU_KEY: '',
            API_BASE_URL: '',
            DEFAULT_CENTER: [114.3630, 30.5365],
            MAP_ZOOM: 16,
        };
        var w = window.WHU_WALKER_CONFIG || {};
        return {
            TIANDITU_KEY: w.TIANDITU_KEY || w.tiandituKey || fallback.TIANDITU_KEY,
            API_BASE_URL: w.API_BASE_URL || w.apiBase || fallback.API_BASE_URL,
            DEFAULT_CENTER: w.DEFAULT_CENTER || w.mapCenter || fallback.DEFAULT_CENTER,
            MAP_ZOOM: w.MAP_ZOOM != null ? w.MAP_ZOOM : (w.mapZoom != null ? w.mapZoom : fallback.MAP_ZOOM),
        };
    })();

    var API_BASE = CFG.API_BASE_URL;
    var MAP_CENTER = CFG.DEFAULT_CENTER;
    var MAP_ZOOM = CFG.MAP_ZOOM;
    var CONTEXT_KEY_PREFIX = 'whu_walker:context:';
    var PREFS_KEY = 'whu_walker:preferences';
    var TRAVEL_MODE_KEY = 'whu_walker:travel_mode';  // 出行方式持久化偏好

    var state = {
        map: null,
        recommendedLine: null,
        shortestLine: null,
        poiMarkers: [],
        roadConditionMarkers: [],  // 路况事件标记
        loading: false,
        sessionId: null,
        conversationHistory: [],
        lastIntent: null,  // 最近一轮完整意图快照（多轮对话承接用）
        activeMode: null,
        loadingTimer: null,  // 轮播加载语定时器
        requestSeq: 0,  // 请求序号：防止先发的请求后返回覆盖后发请求的结果
        travelMode: 'walk',  // 出行方式：walk / bike / drive（持久化偏好，默认步行）
        userLocation: null,  // GPS 定位结果（WGS-84）：{lng, lat, accuracy}
        userMarker: null,    // 蓝点标记
        userAccuracyCircle: null,  // 定位精度圈
        locateWatchId: null, // navigator.geolocation.watchPosition 句柄
        locateBtn: null,     // ◎ GPS 定位按钮 DOM（setLocateBtnState 用）
        routeAcceptTimer: null,  // 路线采纳判定定时器（20s 未覆盖视为采纳）
    };

    // ========== 用户标识与行为埋点（P4：画像学习 + 产品观测） ==========
    var UID_KEY = 'whu_walker:uid';
    var ROUTE_ACCEPT_DELAY_MS = 20000;  // 路线展示 20s 未被新请求覆盖/重置 → 视为采纳

    // 匿名用户 ID：首次访问生成并持久化，用于后端用户画像（EMA 权重先验）
    function getUid() {
        var uid = null;
        try { uid = window.localStorage.getItem(UID_KEY); } catch (e) { /* 隐私模式降级 */ }
        if (!uid) {
            uid = 'u_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
            try { window.localStorage.setItem(UID_KEY, uid); } catch (e) { /* 写不进就用本次的 */ }
        }
        return uid;
    }

    // 埋点上报：sendBeacon 优先（页面关闭也不丢），任何失败静默——绝不影响主流程
    function trackEvent(event, payload) {
        try {
            var body = JSON.stringify(Object.assign({ uid: getUid(), event: event }, payload || {}));
            var url = API_BASE + '/api/telemetry';
            if (navigator.sendBeacon) {
                navigator.sendBeacon(url, new Blob([body], { type: 'application/json' }));
            } else {
                fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: body,
                    keepalive: true,
                }).catch(function () {});
            }
        } catch (e) { /* 埋点失败静默 */ }
    }

    function cancelRouteAccept() {
        if (state.routeAcceptTimer) {
            clearTimeout(state.routeAcceptTimer);
            state.routeAcceptTimer = null;
        }
    }

    // 路线渲染后记曝光；20s 内用户没发新请求/重置 → 视为采纳，回传画像学习
    function scheduleRouteAccept(data) {
        cancelRouteAccept();
        if (!data || !data.applied_weights) return;
        trackEvent('route_shown', {
            applied_weights: data.applied_weights,
            route_kind: data.route_kind || 'direct',
        });
        var weights = data.applied_weights;
        var routeKind = data.route_kind || 'direct';
        state.routeAcceptTimer = setTimeout(function () {
            state.routeAcceptTimer = null;
            trackEvent('route_accept', { applied_weights: weights, route_kind: routeKind });
        }, ROUTE_ACCEPT_DELAY_MS);
    }

    // ========== 出行方式配置（珞珈秋色：步行=樱花粉 / 骑行=松绿 / 驾车=黛蓝） ==========
    var TRAVEL_MODES = {
        walk: {
            label: '步行',
            color: '#E8929C',
            speedKmh: 4.5,   // 前端兜底估速（后端未返回 duration_min 时用）
            loadingText: '正在漫步找路…',
            loadingSub: '穿过樱花大道，慢慢走就好',
        },
        bike: {
            label: '骑行',
            color: '#7BA37B',
            speedKmh: 14,
            loadingText: '正在规划骑行路线…',
            loadingSub: '帮你避开台阶和陡坡',
        },
        drive: {
            label: '驾车',
            color: '#6A9FB5',
            speedKmh: 25,
            loadingText: '正在规划车行路线…',
            loadingSub: '优先校园车行道，省时省心',
        },
    };

    // ========== 出行方式：固定默认步行（不持久化其他模式） ==========
    function loadTravelMode() {
        state.travelMode = 'walk';  // 固定默认步行，不读取 localStorage
    }

    function persistTravelMode() {
        // 不再持久化出行方式，每次刷新默认步行
    }

    // 同步分段选择器选中态、aria、图例配色与文案、驾车模式下隐藏"平坦优先"
    function syncTravelModeUI() {
        var mode = TRAVEL_MODES[state.travelMode] ? state.travelMode : 'walk';
        state.travelMode = mode;

        document.querySelectorAll('.travel-mode-btn').forEach(function (btn) {
            var active = btn.getAttribute('data-travel-mode') === mode;
            btn.classList.toggle('active', active);
            btn.setAttribute('aria-pressed', active ? 'true' : 'false');
        });

        var chips = document.getElementById('quick-chips');
        if (chips) chips.classList.toggle('is-drive', mode === 'drive');

        // 驾车不关心坡度：若"平坦优先"正高亮，清掉高亮，避免重算时带上矛盾偏好
        if (mode === 'drive' && state.activeMode === 'slope_avoid') {
            state.activeMode = null;
            document.querySelectorAll('.quick-chip').forEach(function (c) { c.classList.remove('active'); });
        }

        // 图例：推荐路线色块 + 文案随模式
        var tm = TRAVEL_MODES[mode];
        var legendLine = document.getElementById('legend-recommended-line');
        if (legendLine) legendLine.style.background = tm.color;
        var legendText = document.getElementById('legend-recommended-text');
        if (legendText) legendText.textContent = '推荐路线（' + tm.label + '）';

        // 模式色渗透：模式栏底色 / 说明卡左边框 / 预计用时数字色
        var modeBar = document.getElementById('mode-bar');
        if (modeBar) {
            modeBar.classList.remove('mode-walk', 'mode-bike', 'mode-drive');
            modeBar.classList.add('mode-' + mode);
        }
        var expBox = document.getElementById('explanation-box');
        if (expBox) {
            expBox.classList.remove('mode-walk', 'mode-bike', 'mode-drive');
            expBox.classList.add('mode-' + mode);
        }
        var durCard = document.getElementById('duration-card');
        if (durCard) {
            durCard.setAttribute('data-mode-color', mode);
        }
    }

    // 用户手动切换出行方式：更新状态 + 持久化 + 必要时用当前起终点自动重算
    function setTravelMode(mode) {
        if (!TRAVEL_MODES[mode]) mode = 'walk';
        if (mode === state.travelMode) return;
        state.travelMode = mode;
        persistTravelMode();
        syncTravelModeUI();
        if (state.map) loadAndRenderRoadConditions(state.roadConditionAdminView);
        maybeRecomputeRouteForMode();
    }

    // 服务端响应里的 mode（NL 识别"骑车/开车"）以服务端为准，回写选择器状态
    function syncModeFromServer(data) {
        if (data && data.mode && TRAVEL_MODES[data.mode] && data.mode !== state.travelMode) {
            state.travelMode = data.mode;
            persistTravelMode();
            syncTravelModeUI();
            if (state.map) loadAndRenderRoadConditions(state.roadConditionAdminView);
        }
    }

    // 切换出行方式后，若当前正展示路径规划结果且有起终点，自动重算（走 /api/parse shortcut 链路，带 travel_mode）
    function maybeRecomputeRouteForMode() {
        var resultsSec = document.getElementById('results-section');
        if (!resultsSec || resultsSec.hidden) return;  // 当前没有路线结果，不重算
        var intent = state.lastIntent;
        if (!intent || intent.task_type !== 'path_planning') return;
        if (!intent.start || !intent.end) return;
        if (state.loading) {
            // 上一轮请求还在飞：先挂起，等它落地后再按新方式重算（避免结果与选择器不一致）
            state.pendingModeRecompute = true;
            return;
        }
        handleShortcutMode(state.activeMode);  // activeMode 为 null 时不带偏好，仅带 travel_mode
    }

    // 一轮请求结束后，若用户在等待期间切换过出行方式，按最新方式补一次重算
    function flushPendingModeRecompute() {
        if (state.pendingModeRecompute && !state.loading) {
            state.pendingModeRecompute = false;
            maybeRecomputeRouteForMode();
        }
    }

    // ========== 轮播加载语（有人味儿，随出行方式变化） ==========
    function getLoadingMessages(query) {
        var mode = TRAVEL_MODES[state.travelMode] ? state.travelMode : 'walk';
        var first = TRAVEL_MODES[mode];
        var calcSub = mode === 'bike' ? '帮你避开台阶和陡坡，骑车更省心'
                   : mode === 'drive' ? '优先校园车行道，避开步行小路'
                   : '帮你避开那些不好走的路';

        // 根据 query 内容智能匹配 Agent 正在做什么
        var q = (query || '').toLowerCase();
        var msgs = [{ text: first.loadingText, sub: first.loadingSub }];

        if (q.indexOf('吃') >= 0 || q.indexOf('饭') >= 0 || q.indexOf('食堂') >= 0 || q.indexOf('饿') >= 0) {
            msgs.push({ text: '正在搜食堂…', sub: '看看哪个最近' });
        } else if (q.indexOf('咖啡') >= 0 || q.indexOf('奶茶') >= 0 || q.indexOf('喝') >= 0) {
            msgs.push({ text: '正在找饮品店…', sub: '给你最近的' });
        } else if (q.indexOf('逛') >= 0 || q.indexOf('参观') >= 0 || q.indexOf('游览') >= 0 || q.indexOf('旅游') >= 0) {
            msgs.push({ text: '正在安排游览路线…', sub: '串起最好看的几个点' });
        } else if (q.indexOf('买') >= 0 || q.indexOf('超市') >= 0 || q.indexOf('快递') >= 0) {
            msgs.push({ text: '正在找顺路的店…', sub: '不绕路那种' });
        } else if (q.indexOf('天气') >= 0 || q.indexOf('下雨') >= 0 || q.indexOf('带伞') >= 0) {
            msgs.push({ text: '正在查天气…', sub: '看适不适合出门' });
        } else if (q.indexOf('修路') >= 0 || q.indexOf('封') >= 0 || q.indexOf('施工') >= 0) {
            msgs.push({ text: '正在查路况…', sub: '看看有没有管制' });
        } else if (q.indexOf('樱花') >= 0 || q.indexOf('赏') >= 0) {
            msgs.push({ text: '正在安排赏花路线…', sub: '樱花季这条线最美' });
        } else {
            msgs.push({ text: '正在查地图…', sub: '珞珈山的路我都熟' });
        }

        msgs.push({ text: '正在计算最佳路线…', sub: calcSub });
        msgs.push({ text: '快好了…', sub: '稍等一下下' });
        return msgs;
    }

    function startLoadingMessages(query) {
        var messages = getLoadingMessages(query);
        var idx = 0;
        var textEl = document.getElementById('loading-text');
        var subEl = document.getElementById('loading-subtext');
        showLoading(messages[0].text, messages[0].sub);

        state.loadingTimer = setInterval(function () {
            idx = (idx + 1) % messages.length;
            var msg = messages[idx];
            // 淡入淡出效果
            textEl.classList.add('fade');
            subEl.classList.add('fade');
            setTimeout(function () {
                textEl.textContent = msg.text;
                subEl.textContent = msg.sub;
                textEl.classList.remove('fade');
                subEl.classList.remove('fade');
            }, 200);
        }, 2200);
    }

    function stopLoadingMessages() {
        if (state.loadingTimer) {
            clearInterval(state.loadingTimer);
            state.loadingTimer = null;
        }
    }

    function generateSessionId() {
        return 'sess_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
    }

    function getContextKey() {
        return CONTEXT_KEY_PREFIX + (state.sessionId || 'default');
    }

    function loadContext() {
        try {
            var raw = localStorage.getItem(getContextKey());
            if (raw) {
                var parsed = JSON.parse(raw);
                // 新结构 {history:[{role,content,...}], routeSlot:{...}}；
                // 旧结构 [{query,...}] 直接丢弃，避免污染
                if (parsed && Array.isArray(parsed.history)) {
                    state.conversationHistory = parsed.history;
                    state.lastIntent = parsed.routeSlot || null;
                }
            }
        } catch (e) {
            state.conversationHistory = [];
        }
    }

    function saveContext() {
        try {
            // 最近 4 轮（8 条消息）+ 规划槽位
            localStorage.setItem(getContextKey(), JSON.stringify({
                history: state.conversationHistory.slice(-8),
                routeSlot: state.lastIntent,
            }));
        } catch (e) {}
    }

    function addConversationTurn(query, result) {
        // 对话历史：真实的用户/助手消息流（含回复内容与涉及地点，供指代消解）
        state.conversationHistory.push({ role: 'user', content: query });
        var replyText = result.explanation || result.reply || result.message || '';
        // 候选列表注入：让下一轮 Agent 知道用户刚看了哪些候选
        if (result.response_kind === 'candidates' && result.candidates && result.candidates.length) {
            var candNames = result.candidates.slice(0, 6).map(function (c) { return c.name; }).join('、');
            replyText = (replyText || '') + '（候选：' + candNames + '）';
        }
        state.conversationHistory.push({
            role: 'assistant',
            content: replyText,
            task_type: result.task_type || null,
            entities: {
                start: result.start && result.start.name ? result.start.name : null,
                end: result.end && result.end.name ? result.end.name : null,
                poi: result.poi && result.poi.name ? result.poi.name : null,
                mode: result.mode || null,
            },
        });
        if (state.conversationHistory.length > 20) {
            state.conversationHistory = state.conversationHistory.slice(-20);
        }
        // 规划槽位：路径规划轮次才更新（闲聊/候选/澄清不冲掉在途规划）
        var isPlanningTurn = result.task_type === 'path_planning'
            || (result.response_kind === 'route' && result.start && result.end);
        if (isPlanningTurn) {
            state.lastIntent = {
                task_type: result.task_type || 'path_planning',
                start: result.start || null,
                end: result.end || null,
                constraints: result.constraints || null,
                weights: result.weights || result.applied_weights || null,
                ambiguity: result.ambiguity || null,
                mode: result.mode || state.travelMode,
            };
        }
        saveContext();
    }

    function wgs84ToGcj02(lng, lat) {
        var a = 6378245.0;
        var ee = 0.006693421622965823;

        if (lng < 72.004 || lng > 137.8347 || lat < 0.8293 || lat > 55.8271) {
            return [lng, lat];
        }

        var dlat = _transformLat(lng - 105.0, lat - 35.0);
        var dlng = _transformLng(lng - 105.0, lat - 35.0);
        var radlat = lat / 180.0 * Math.PI;
        var magic = Math.sin(radlat);
        magic = 1 - ee * magic * magic;
        var sqrtmagic = Math.sqrt(magic);
        dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * Math.PI);
        dlng = (dlng * 180.0) / (a / sqrtmagic * Math.cos(radlat) * Math.PI);
        return [lng + dlng, lat + dlat];
    }

    function gcj02ToWgs84(lng, lat) {
        var clng = lng;
        var clat = lat;
        for (var i = 0; i < 3; i++) {
            var wlng = clng;
            var wlat = clat;
            var glng_lat = wgs84ToGcj02(wlng, wlat);
            clng += (lng - glng_lat[0]);
            clat += (lat - glng_lat[1]);
        }
        return [clng, clat];
    }

    function _transformLat(x, y) {
        var ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
        ret += (20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0 / 3.0;
        ret += (20.0 * Math.sin(y * Math.PI) + 40.0 * Math.sin(y / 3.0 * Math.PI)) * 2.0 / 3.0;
        ret += (160.0 * Math.sin(y / 12.0 * Math.PI) + 320 * Math.sin(y * Math.PI / 30.0)) * 2.0 / 3.0;
        return ret;
    }

    function _transformLng(x, y) {
        var ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
        ret += (20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0 / 3.0;
        ret += (20.0 * Math.sin(x * Math.PI) + 40.0 * Math.sin(x / 3.0 * Math.PI)) * 2.0 / 3.0;
        ret += (150.0 * Math.sin(x / 12.0 * Math.PI) + 300.0 * Math.sin(x / 30.0 * Math.PI)) * 2.0 / 3.0;
        return ret;
    }

    function toGcj02Path(coords) {
        return coords.map(function (c) {
            return wgs84ToGcj02(c.lng || c[0], c.lat || c[1]);
        });
    }

    // 后端所有坐标（POI、路径、路况）均为 GCJ-02；高德瓦片也是 GCJ-02，
    // 直接用 GCJ-02 坐标入图（[lat, lng]），无需转换
    function gcjToLatLng(lng, lat) {
        return [lat, lng];
    }

    // Leaflet divIcon 小工具
    function divMarker(latlng, html, size, anchor, title) {
        var icon = L.divIcon({
            className: 'whu-div-icon',
            html: html,
            iconSize: size,
            iconAnchor: anchor,
        });
        return L.marker(latlng, { icon: icon, title: title || '', keyboard: false });
    }

    function initMap() {
        if (typeof L === 'undefined') {
            setTimeout(initMap, 300);
            return;
        }

        if (state.map) return;

        var placeholder = document.getElementById('map-placeholder');
        if (placeholder) placeholder.style.display = 'none';

        try {
            // 手机屏幕小，用更小的 zoom（比例尺更小）显示更大范围；电脑保持 16
            var initialZoom = MAP_ZOOM;
            if (window.innerWidth <= 767) {
                initialZoom = 14;
            }
            var center = gcjToLatLng(MAP_CENTER[0], MAP_CENTER[1]);

            state.map = L.map('map-container', {
                center: center,
                zoom: initialZoom,
                zoomControl: true,
                attributionControl: true,
                preferCanvas: false,
            });

            // 底图：高德瓦片（GCJ-02，国内秒开，中文标注，高缩放全覆盖）
            // 全站统一 GCJ-02：POI/路线/GPS 均按 GCJ-02 渲染，底图不可混入 WGS-84 源
            // （OSM/Esri 为 WGS-84 且国内不可达/会串位约 500m，已移除）
            var baseLayers = {};
            var defaultLayer = null;

            // ===== 高德矢量街道图（GCJ-02，国内手机端秒开，无需 Key）=====
            var amap = L.tileLayer(
                'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
                { subdomains: ['1', '2', '3', '4'],
                  maxZoom: 18,
                  attribution: '© <a href="https://ditu.amap.com/" target="_blank" rel="noopener">高德地图</a>' }
            );
            baseLayers['高德矢量'] = amap;

            // 天地图矢量+注记（需 Key，CGCS2000≈GCJ-02，国内最快）
            if (CFG.TIANDITU_KEY) {
                var tdtAttr = '© <a href="https://www.tianditu.gov.cn/" target="_blank" rel="noopener">天地图</a>';
                var tdtVec = L.tileLayer(
                    'https://t{s}.tianditu.gov.cn/vec_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0'
                    + '&LAYER=vec&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles'
                    + '&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=' + encodeURIComponent(CFG.TIANDITU_KEY),
                    { subdomains: ['0', '1', '2', '3', '4', '5', '6', '7'],
                      maxZoom: 18, attribution: tdtAttr }
                );
                var tdtCva = L.tileLayer(
                    'https://t{s}.tianditu.gov.cn/cva_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0'
                    + '&LAYER=cva&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles'
                    + '&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=' + encodeURIComponent(CFG.TIANDITU_KEY),
                    { subdomains: ['0', '1', '2', '3', '4', '5', '6', '7'],
                      maxZoom: 18, attribution: tdtAttr }
                );
                tdtVec.addTo(state.map);
                tdtCva.addTo(state.map);  // 注记作为固定叠加层
                baseLayers['天地图矢量'] = tdtVec;
                defaultLayer = tdtVec;
            }

            // 高德卫星影像 + 路网注记（GCJ-02，无需 Key；与 POI/路线同坐标系，天然对齐）
            var amapSat = L.layerGroup([
                L.tileLayer(
                    'https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}',
                    { subdomains: ['1', '2', '3', '4'],
                      maxZoom: 18,
                      attribution: '© <a href="https://ditu.amap.com/" target="_blank" rel="noopener">高德地图</a>' }
                ),
                L.tileLayer(
                    'https://webst0{s}.is.autonavi.com/appmaptile?style=8&x={x}&y={y}&z={z}',
                    { subdomains: ['1', '2', '3', '4'], maxZoom: 18 }
                ),
            ]);
            baseLayers['卫星影像'] = amapSat;

            if (!defaultLayer) {
                // 默认用高德矢量（国内手机端秒开，GCJ-02 与 POI/路径天然对齐）
                amap.addTo(state.map);
                defaultLayer = amap;
            }

            L.control.layers(baseLayers, null, { position: 'topright', collapsed: true }).addTo(state.map);
            L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(state.map);

            addLocateControl();

            // 容器在隐藏状态下初始化时需要刷新尺寸
            setTimeout(function () { if (state.map) state.map.invalidateSize(); }, 0);

            // 加载路况事件标记
            loadAndRenderRoadConditions();
            // 加载实时天气徽章
            loadWeatherBadge();
        } catch (e) {
            console.error('地图初始化失败:', e);
            showError('地图加载失败', '无法初始化地图组件，请刷新页面重试');
        }
    }

    // ========== GPS 定位 + 手动设起点 ==========
    function addLocateControl() {
        var container = L.DomUtil.create('div', 'whu-locate-group');
        container.style.display = 'flex';
        container.style.gap = '4px';
        container.style.flexDirection = 'column';

        // GPS 定位按钮
        var btn = L.DomUtil.create('div', 'whu-locate-btn leaflet-bar', container);
        btn.setAttribute('role', 'button');
        btn.setAttribute('aria-label', 'GPS定位我的位置');
        btn.title = 'GPS定位（手机精度高；桌面IP定位可能不准，建议手动选点）';
        btn.innerHTML = '<span class="whu-locate-icon">◎</span>';
        L.DomEvent.disableClickPropagation(btn);
        L.DomEvent.disableScrollPropagation(btn);
        btn.addEventListener('click', onLocateClick);
        btn.style.cursor = 'pointer';
        btn.style.width = '30px';
        btn.style.height = '30px';
        btn.style.display = 'flex';
        btn.style.alignItems = 'center';
        btn.style.justifyContent = 'center';
        btn.style.background = '#fff';
        btn.style.borderRadius = '4px';
        btn.style.boxShadow = '0 1px 4px rgba(0,0,0,0.2)';
        btn.style.fontSize = '16px';
        btn.style.fontWeight = 'bold';
        btn.style.color = '#2B7CFF';
        // 注意：本函数是普通调用（严格模式下 this 为 undefined），
        // 按钮引用必须挂到 state，不能写 this._btn（曾导致 TypeError、定位按钮整块不渲染）
        state.locateBtn = btn;

        // 手动设起点按钮
        var manualBtn = L.DomUtil.create('div', 'whu-manual-locate-btn leaflet-bar', container);
        manualBtn.setAttribute('role', 'button');
        manualBtn.setAttribute('aria-label', '手动点击地图设我的位置');
        manualBtn.title = '手动选点：点击地图任意位置作为「我的位置」';
        manualBtn.innerHTML = '<span style="font-size:16px;">📌</span>';
        L.DomEvent.disableClickPropagation(manualBtn);
        L.DomEvent.disableScrollPropagation(manualBtn);
        manualBtn.addEventListener('click', toggleManualLocateMode);
        manualBtn.style.cursor = 'pointer';
        manualBtn.style.width = '30px';
        manualBtn.style.height = '30px';
        manualBtn.style.display = 'flex';
        manualBtn.style.alignItems = 'center';
        manualBtn.style.justifyContent = 'center';
        manualBtn.style.background = '#fff';
        manualBtn.style.borderRadius = '4px';
        manualBtn.style.boxShadow = '0 1px 4px rgba(0,0,0,0.2)';

        var LocateCtrl = L.Control.extend({
            options: { position: 'topright' },
            onAdd: function () { return container; },
        });
        state.locateControl = new LocateCtrl().addTo(state.map);
        state.manualLocateBtn = manualBtn;
    }

    function setLocateBtnState(busy) {
        var btn = state.locateBtn;
        if (btn) btn.classList.toggle('is-busy', !!busy);
    }

    function onLocateClick() {
        setLocateBtnState(true);
        // 已有持续跟踪 → 直接回到我的位置（不重复启动 watch）
        if (state.locateWatchId != null || state._amapWatchListener) {
            setLocateBtnState(false);
            if (state.userLocation) state.map.setView([state.userLocation.gcjLat, state.userLocation.gcjLng], 17);
            return;
        }
        // 已有一次性定位（还没启动 watch）→ 也先回到位置，再启动跟踪
        if (state.userLocation) {
            state.map.setView([state.userLocation.gcjLat, state.userLocation.gcjLng], 17);
        }
        startTracking(false);  // 非静默（会弹错误框）
    }

    // ===== 持续跟踪定位（核心逻辑：共享 state.locateWatchId，防重复 watch） =====
    // 统一入口：startTracking() 负责清理旧 watch + 启动新 watch（AMap优先 + 原生fallback）
    // 调用方：autoLocateSilent（页面加载）、onLocateClick（用户点◎）

    // 页面上可见的定位状态指示器（微信里看不到 console，靠这个排查）
    // success/warn/info 类型：成功后自动淡出隐藏；error 类型：保持显示直到用户点掉
    var _locStatusHideTimer = null;
    function _setLocStatus(text, type) {
        var el = document.getElementById('loc-status');
        if (!el) return;
        if (_locStatusHideTimer) { clearTimeout(_locStatusHideTimer); _locStatusHideTimer = null; }
        if (!text) { el.style.display = 'none'; return; }
        el.textContent = text;
        el.style.display = 'block';
        el.style.opacity = '1';
        el.className = 'loc-status loc-status--' + (type || 'info');
        console.log('[LOC_STATUS]', text);
        // 成功/信息/警告 → 3 秒后淡出隐藏
        if (type !== 'error') {
            _locStatusHideTimer = setTimeout(function () {
                el.style.transition = 'opacity 0.5s ease';
                el.style.opacity = '0';
                setTimeout(function () { el.style.display = 'none'; el.style.transition = ''; }, 500);
            }, 3000);
        }
    }

    function _clearWatch() {
        if (state.locateWatchId != null) {
            try { navigator.geolocation.clearWatch(state.locateWatchId); } catch(e) {}
            state.locateWatchId = null;
        }
        if (state._amapWatchListener) {
            try { state._amapGeolocation && state._amapGeolocation.clearWatch(state._amapWatchListener); } catch(e) {}
            state._amapWatchListener = null;
        }
    }

    function _startNativeWatch(silent, onError) {
        if (!navigator.geolocation || window.isSecureContext === false) {
            var reason = !navigator.geolocation ? '浏览器不支持定位' : '非 HTTPS 环境';
            if (!silent) showError('定位不可用', reason);
            _setLocStatus('定位不可用：' + reason, 'error');
            console.warn('[TRACK] 环境不支持原生定位:', reason);
            return false;
        }
        _clearWatch();
        _setLocStatus('定位中…（原生 GPS）', 'info');
        state.locateWatchId = navigator.geolocation.watchPosition(
            function (pos) {
                if (!silent) setLocateBtnState(false);
                renderUserLocation(pos.coords.longitude, pos.coords.latitude, pos.coords.accuracy);
                console.log('[TRACK] 原生位置更新:', pos.coords.latitude.toFixed(4), pos.coords.longitude.toFixed(4));
            },
            function (err) {
                var msg = '定位失败：';
                if (err && err.code === 1) msg += '你拒绝了定位授权';
                else if (err && err.code === 2) msg += '获取不到位置信号';
                else if (err && err.code === 3) msg += '定位超时';
                else msg += '请稍后再试';
                if (!silent) { setLocateBtnState(false); showError('定位失败', msg); }
                _setLocStatus(msg, 'error');
                console.warn('[TRACK] 原生 watch 错误:', err && err.code, err && err.message);
                if (onError) onError(err);
            },
            { enableHighAccuracy: true, timeout: 15000, maximumAge: 1000, distanceFilter: 3 }
        );
        _setLocStatus('已启动原生 GPS 跟踪', 'success');
        console.log('[TRACK] 原生 watch 已启动, id:', state.locateWatchId);
        return true;
    }

    function _startAmapWatch(silent) {
        if (!window.AMap || !window.AMap.plugin) return false;
        _setLocStatus('加载 AMap 定位插件…', 'info');
        AMap.plugin('AMap.Geolocation', function () {
            try {
                _clearWatch();
                var geo = new AMap.Geolocation({
                    enableHighAccuracy: true, timeout: 10000, maximumAge: 30000, convert: false,
                });
                state._amapGeolocation = geo;
                _setLocStatus('定位中…（AMap WiFi+GPS）', 'info');
                state._amapWatchListener = geo.watchPosition(function (status, result) {
                    if (status === 'complete' && result && result.position) {
                        var gcjLng = result.position.lng, gcjLat = result.position.lat;
                        var wgs = gcj02ToWgs84(gcjLng, gcjLat);
                        if (!silent) setLocateBtnState(false);
                        renderUserLocation(wgs[0], wgs[1], result.accuracy || 0);
                        console.log('[TRACK] AMap 位置更新, GCJ:', gcjLng.toFixed(4), gcjLat.toFixed(4));
                    } else {
                        console.warn('[TRACK] AMap watch 状态:', status);
                        // AMap watch 失败 → 切原生
                        if (!state.locateWatchId) _startNativeWatch(silent);
                    }
                });
                _setLocStatus('已启动 AMap 跟踪', 'success');
                console.log('[TRACK] AMap watch 已启动');
            } catch (e) {
                console.warn('[TRACK] AMap watch 异常:', e.message, '→ fallback 原生');
                _setLocStatus('AMap 异常，切换原生', 'warn');
                _startNativeWatch(silent);
            }
        });
        return true;
    }

    // 对外：启动持续跟踪（AMap 优先 + 原生 fallback）
    function startTracking(silent) {
        _clearWatch();
        _setLocStatus('准备定位…', 'info');
        if (window.AMap && window.AMap.plugin) {
            _startAmapWatch(silent);
        } else {
            // SDK 还在加载，等最多 3 秒
            _setLocStatus('等待 AMap SDK 加载…', 'info');
            var waited = 0, interval = 200, maxWait = 3000;
            var tick = setInterval(function () {
                waited += interval;
                if (window.AMap && window.AMap.plugin) {
                    clearInterval(tick);
                    _startAmapWatch(silent);
                } else if (waited >= maxWait) {
                    clearInterval(tick);
                    console.warn('[TRACK] AMap 3 秒未加载 → fallback 原生');
                    _setLocStatus('AMap 未加载，切换原生', 'warn');
                    _startNativeWatch(silent);
                }
            }, interval);
        }
    }

    // ===== 页面加载自动定位（静默版，持续跟踪） =====
    function autoLocateSilent() {
        if (!state.map) { console.warn('[AUTO_LOC] map 未就绪，跳过'); _setLocStatus('地图未就绪，跳过自动定位', 'warn'); return; }
        _setLocStatus('自动定位中…', 'info');
        startTracking(true);
    }

    function renderUserLocation(lng, lat, accuracy, isManual) {
        // GPS 返回 WGS-84；高德瓦片 GCJ-02，渲染前需转换
        var gcj = wgs84ToGcj02(lng, lat);
        var gcjLng = gcj[0], gcjLat = gcj[1];
        state.userLocation = {
            lng: lng, lat: lat,
            gcjLng: gcjLng, gcjLat: gcjLat,
            accuracy: accuracy || 0,
            manual: !!isManual,
        };
        // 更新可见状态
        var accText = accuracy > 0 ? '（精度约 ' + Math.round(accuracy) + 'm）' : '';
        _setLocStatus('已定位' + accText, 'success');
        if (!state.map) return;

        // 精度警告：accuracy 过大或无数据 → 显示黄色警告 banner
        var acc = accuracy || 0;
        if (!isManual && (acc > 500 || acc === 0)) {
            showLowAccuracyWarning(acc);
        } else {
            hideLowAccuracyWarning();
        }

        // 精度圈
        if (state.userAccuracyCircle) {
            state.userAccuracyCircle.setLatLng([gcjLat, gcjLng]);
            if (acc > 0) state.userAccuracyCircle.setRadius(acc);
            state.userAccuracyCircle.setStyle({
                opacity: isManual ? 0.3 : 0.5,
                fillOpacity: isManual ? 0.08 : 0.12,
            });
        } else if (acc > 0) {
            state.userAccuracyCircle = L.circle([gcjLat, gcjLng], {
                radius: acc,
                color: isManual ? '#E67E22' : '#2B7CFF',
                weight: 1, opacity: 0.5,
                fillColor: isManual ? '#E67E22' : '#2B7CFF',
                fillOpacity: isManual ? 0.08 : 0.12,
                interactive: false,
            }).addTo(state.map);
        }

        // 蓝点标记
        var dotColor = isManual ? '#E67E22' : '#2B7CFF';  // 手动=橙，GPS=蓝
        var dotHtml = '<div style="position:relative;width:20px;height:20px;">'
            + '<div style="position:absolute;inset:0;border-radius:50%;background:' + dotColor + ';opacity:0.25;"></div>'
            + '<div style="position:absolute;left:5px;top:5px;width:10px;height:10px;border-radius:50%;'
            + 'background:' + dotColor + ';border:2px solid #fff;box-sizing:border-box;box-shadow:0 1px 3px rgba(0,0,0,0.4);"></div>'
            + '</div>';
        var tooltipTxt = isManual
            ? '我的位置（手动设置）· 说「从我这到樱顶」'
            : '我的位置' + (acc > 0 ? '（精度约 ' + Math.round(acc) + 'm）' : '（IP定位精度可能低）') + ' · 说「从我这到樱顶」';

        if (state.userMarker) {
            state.map.removeLayer(state.userMarker);
            state.userMarker = null;
        }
        if (state.userMarkerRaw) { state.map.removeLayer(state.userMarkerRaw); state.userMarkerRaw = null; }

        state.userMarker = divMarker([gcjLat, gcjLng], dotHtml, [20, 20], [10, 10], '我的位置')
            .bindTooltip(tooltipTxt, { direction: 'top', offset: [0, -12], opacity: 0.95 })
            .addTo(state.map);

        // 首次定位：居中
        state.map.setView([gcjLat, gcjLng], 17);
    }

    // 精度警告 banner（桌面 Chrome IP 定位精度差时弹）
    var _lowAccBanner = null;
    function showLowAccuracyWarning(acc) {
        if (_lowAccBanner) return;
        var msg = acc === 0
            ? '⚠️ 当前位置由 IP 推断，精度差。点右上角 📌 手动选点更准。'
            : '⚠️ 定位精度约 ' + Math.round(acc) + 'm（' + (acc > 1000 ? '误差较大' : '可能不够准') + '）。点 📌 手动选点。';
        _lowAccBanner = showTopBanner(msg, 'warning');
    }
    function hideLowAccuracyWarning() {
        if (_lowAccBanner) {
            _lowAccBanner.remove();
            _lowAccBanner = null;
        }
    }

    // 轻量顶部 banner（精度警告 / 手动选点提示）
    function showTopBanner(msg, type) {
        var el = document.createElement('div');
        var bg = type === 'warning' ? '#FFF3CD' : type === 'success' ? '#D4EDDA' : '#D1ECF1';
        var color = type === 'warning' ? '#856404' : type === 'success' ? '#155724' : '#0C5460';
        var border = type === 'warning' ? '#FFC107' : type === 'success' ? '#28A745' : '#17A2B8';
        el.style.cssText = 'position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:1000;'
            + 'padding:8px 16px;background:' + bg + ';color:' + color + ';border:1px solid ' + border + ';'
            + 'border-radius:6px;font-size:13px;box-shadow:0 2px 8px rgba(0,0,0,0.15);'
            + 'max-width:90%;text-align:center;pointer-events:auto;cursor:pointer;';
        el.textContent = msg;
        el.addEventListener('click', function () { el.remove(); });
        document.body.appendChild(el);
        setTimeout(function () { if (el.parentNode) el.remove(); }, 10000);  // 10 秒自动消失
        return el;
    }

    // ====== 手动设起点（点 📌 按钮 → 点击地图任意位置） ======
    var _manualLocateActive = false;
    var _manualLocateHandler = null;
    function toggleManualLocateMode() {
        if (_manualLocateActive) {
            // 退出模式
            if (_manualLocateHandler && state.map) state.map.off('click', _manualLocateHandler);
            _manualLocateActive = false;
            _manualLocateHandler = null;
            if (state.manualLocateBtn) state.manualLocateBtn.style.outline = '';
            showTopBanner('手动选点已退出', 'info');
            state.map.getContainer().style.cursor = '';
        } else {
            // 进入选点模式
            _manualLocateActive = true;
            showTopBanner('📌 点击地图任意位置设为「我的位置」', 'info');
            if (state.manualLocateBtn) state.manualLocateBtn.style.outline = '2px solid #E67E22';
            state.map.getContainer().style.cursor = 'crosshair';
            _manualLocateHandler = function (e) {
                // leaflet event 里 latlng 是 GCJ-02（高德瓦片坐标系）
                var gcjLat = e.latlng.lat;
                var gcjLng = e.latlng.lng;
                // 反向转成 WGS-84 存起来（跟 GPS 流程一致，存 WGS-84，渲染再转 GCJ）
                var wgs = gcj02ToWgs84(gcjLng, gcjLat);
                var wgsLng = wgs[0], wgsLat = wgs[1];
                hideLowAccuracyWarning();
                renderUserLocation(wgsLng, wgsLat, 5, true);  // accuracy 随便给，手动设的不准也准
                showTopBanner('✅ 已设为「我的位置」', 'success');
                // 退出选点模式
                _manualLocateActive = false;
                if (state.manualLocateBtn) state.manualLocateBtn.style.outline = '';
                state.map.getContainer().style.cursor = '';
            };
            state.map.on('click', _manualLocateHandler);
        }
    }

    function renderRoute(routeData) {
        if (!state.map) return;

        clearMap();

        var recommended = routeData.recommended || [];
        var shortest = routeData.shortest || [];

        if (recommended.length > 0) {
            // 后端返回 GCJ-02，高德瓦片也是 GCJ-02，直接入图（[lat, lng]）
            var recPath = recommended.map(function (c) {
                return gcjToLatLng(c.lng, c.lat);
            });

            // 推荐线配色按出行方式：步行=樱花粉 / 骑行=松绿 / 驾车=黛蓝（响应 mode 优先，兜底当前选择器）
            var routeMode = (routeData && routeData.mode && TRAVEL_MODES[routeData.mode])
                ? routeData.mode : state.travelMode;
            var recColor = (TRAVEL_MODES[routeMode] || TRAVEL_MODES.walk).color;

            state.recommendedLine = L.polyline(recPath, {
                color: recColor,
                weight: 6,
                opacity: 0.9,
                lineJoin: 'round',
                lineCap: 'round',
            }).addTo(state.map);
        }

        if (shortest.length > 0) {
            // 最短路径：灰色虚线对照
            var shortPath = shortest.map(function (c) {
                return gcjToLatLng(c.lng, c.lat);
            });

            state.shortestLine = L.polyline(shortPath, {
                color: '#B5B0AB',
                weight: 4,
                opacity: 0.7,
                dashArray: '6,8',
                lineJoin: 'round',
                lineCap: 'round',
            }).addTo(state.map);
        }

        var pois = routeData.pois || [];
        // 只标前 8 个途经点，避免大量标签遮挡路线；用小圆点，悬停显示名称
        pois.slice(0, 8).forEach(function (poi) {
            var lng = poi.lng || poi.lon;
            var lat = poi.lat;
            if (lng == null || lat == null) return;

            var dotHtml = '<div style="width:10px;height:10px;border-radius:50%;background:#D4915C;'
                + 'border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,0.35);"></div>';
            var marker = divMarker(gcjToLatLng(lng, lat), dotHtml, [10, 10], [5, 5], poi.name || '')
                .addTo(state.map);
            state.poiMarkers.push(marker);
        });

        var bounds = [];
        if (state.recommendedLine) bounds.push(state.recommendedLine.getBounds());
        if (state.shortestLine) bounds.push(state.shortestLine.getBounds());
        state.poiMarkers.forEach(function (m) { bounds.push(m.getLatLng()); });

        if (bounds.length > 0) {
            var b = bounds[0];
            bounds.slice(1).forEach(function (x) { b.extend(x); });
            state.map.fitBounds(b, { paddingTopLeft: [40, 90], paddingBottomRight: [40, 40], maxZoom: 17 });
        }
    }

    function clearMap() {
        if (state.recommendedLine) {
            state.map.removeLayer(state.recommendedLine);
            state.recommendedLine = null;
        }
        if (state.shortestLine) {
            state.map.removeLayer(state.shortestLine);
            state.shortestLine = null;
        }
        state.poiMarkers.forEach(function (m) { state.map.removeLayer(m); });
        state.poiMarkers = [];
    }

    // ===================== 路况事件 =====================

    // 路况类型对应的图标、颜色、线型（实线=硬封，虚线=软影响）
    var ROAD_CONDITION_STYLES = {
        closure:     { icon: '🚫', color: '#C0392B', label: '封闭', dash: null },
        construction:{ icon: '🚧', color: '#E67E22', label: '施工', dash: '10 7' },
        event:       { icon: '🎉', color: '#9B59B6', label: '活动', dash: '4 7' },
        flooding:    { icon: '🌊', color: '#2980B9', label: '积水', dash: '7 5' },
        accident:    { icon: '⚠️', color: '#F39C12', label: '事故', dash: '12 7' },
    };
    // 与后端 spatial/road_conditions.py 的 CONDITION_EFFECTS 保持一致：
    // 'block' = 当前出行模式禁行（画实线，路线绕行）；数字 = 缓行成本倍数（画虚线，仍可通行）
    var CONDITION_MODE_EFFECTS = {
        closure:      { walk: 'block', bike: 'block', drive: 'block' },
        construction: { walk: 1.5,     bike: 'block', drive: 'block' },
        flooding:     { walk: 'block', bike: 'block', drive: 1.5 },
        accident:     { walk: 1.3,     bike: 1.3,     drive: 'block' },
        event:        { walk: 1.2,     bike: 1.2,     drive: 'block' },
    };
    var COND_STATUS_LABELS = { active: '生效中', scheduled: '未开始', expired: '已结束' };

    // 清除路况标记（线段 + 胶囊）
    function clearRoadConditionMarkers() {
        state.roadConditionMarkers.forEach(function (m) {
            if (state.map) state.map.removeLayer(m);
        });
        state.roadConditionMarkers = [];
    }

    // 拉取并渲染路况。includeAll=true 为管理员视图（含未开始/已结束，灰显）
    function loadAndRenderRoadConditions(includeAll) {
        if (!state.map) return;
        var url = '/api/road-conditions' + (includeAll ? '?all=1' : '');
        apiRequest(url, null, 'GET').then(function (data) {
            clearRoadConditionMarkers();
            state.roadConditionAdminView = !!includeAll;
            var conditions = (data && data.conditions) || [];
            var hasClosure = false, hasSlow = false;
            conditions.forEach(function (cond) {
                var active = !includeAll || !cond.status || cond.status === 'active';
                if (active) {
                    var eff = (CONDITION_MODE_EFFECTS[cond.type] || {})[state.travelMode];
                    if (eff === 'block') hasClosure = true; else if (eff) hasSlow = true;
                }
                try {
                    renderRoadCondition(cond, !!includeAll);
                } catch (err) {
                    console.warn('[路况] 单条事件渲染失败，跳过:', err, cond && cond.id);
                }
            });
            updateRoadConditionLegend(hasClosure, hasSlow);
        }).catch(function (e) {
            console.warn('[路况] 加载失败:', e);
        });
    }

    // 地图图例中"管制封闭/缓行"两行仅在存在对应事件时显示
    function updateRoadConditionLegend(hasClosure, hasSlow) {
        var elClosure = document.getElementById('legend-closure-item');
        var elSlow = document.getElementById('legend-slow-item');
        if (elClosure) elClosure.hidden = !hasClosure;
        if (elSlow) elSlow.hidden = !hasSlow;
    }

    function renderRoadCondition(cond, adminView) {
        var style = ROAD_CONDITION_STYLES[cond.type]
            || { icon: '⚠️', color: '#999', label: cond.type, dash: null };
        var inactive = adminView && cond.status && cond.status !== 'active';
        var color = inactive ? '#9AA5B1' : style.color;
        var edge = cond.edge || {};
        var anchor = edge.snap || cond.coordinates || null;
        if (!anchor) return;
        var latlng = gcjToLatLng(anchor.lng, anchor.lat);
        var layers = [];

        // 沿真实路段画高亮线：当前模式禁行画实线，可通行但缓行画虚线
        var modeEffect = (CONDITION_MODE_EFFECTS[cond.type] || {})[state.travelMode];
        var hardClosed = modeEffect === 'block';
        var geom = edge.geometry_gcj || [];
        if (geom.length >= 2) {
            var lineLatLngs = geom.map(function (p) { return gcjToLatLng(p[0], p[1]); });
            var lineOpt = { color: color, weight: 6, opacity: inactive ? 0.4 : 0.9, lineCap: 'round' };
            if (inactive) {
                lineOpt.dashArray = '3 8';
            } else if (!hardClosed) {
                lineOpt.dashArray = style.dash || '8 6';
            }
            var line = L.polyline(lineLatLngs, lineOpt);
            line.addTo(state.map);
            layers.push(line);
        } else if (cond.radius_m) {
            // 兼容旧版半径圆数据
            var circle = L.circle(latlng, {
                radius: cond.radius_m, color: color, weight: 1, opacity: 0.6,
                fillColor: color, fillOpacity: 0.12,
            });
            circle.addTo(state.map);
            layers.push(circle);
        }

        // 吸附点胶囊（加大点击区域）
        var label = cond.name || style.label;
        if (inactive && cond.status) label = label + '（' + COND_STATUS_LABELS[cond.status] + '）';
        var pillHtml = '<div class="whu-map-pill" style="background:' + color + ';color:white;'
            + (inactive ? 'opacity:0.65;' : '') + '">'
            + style.icon + ' ' + escapeHTML(label) + '</div>';
        var marker = divMarker(latlng, pillHtml, [0, 0], [0, 0], label);
        marker.addTo(state.map);
        layers.push(marker);

        var onClick = function () { showConditionPopup(cond, style, color, inactive, layers); };
        layers.forEach(function (ly) { ly.on('click', onClick); });
        state.roadConditionMarkers = state.roadConditionMarkers.concat(layers);
    }

    // 路况事件信息窗
    function showConditionPopup(cond, style, color, inactive, layers) {
        try {
            var edge = cond.edge || {};
            var anchor = edge.snap || cond.coordinates;
            var fmt = function (ts) {
                var d = new Date(ts * 1000);
                var p = function (n) { return (n < 10 ? '0' : '') + n; };
                return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
                    ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
            };
            var rows = [];
            if (edge.road_name) rows.push('<div style="font-size:12px;">📍 路段：<b>' + escapeHTML(edge.road_name) + '</b></div>');
            if (edge.chain_length_m) {
                rows.push('<div style="color:#888;font-size:12px;">影响长度：约 '
                    + Math.round(edge.chain_length_m) + ' 米（两端路口之间整段）</div>');
            }
            if (!inactive) {
                var eff = (CONDITION_MODE_EFFECTS[cond.type] || {})[state.travelMode];
                var modeName = (TRAVEL_MODES[state.travelMode] || {}).label || state.travelMode;
                var effText = eff === 'block'
                    ? '🚷 当前方式（' + modeName + '）<b>禁止通行</b>，路线将自动绕行'
                    : ('🚶 当前方式（' + modeName + '）<b>可缓行通过</b>，通过成本约 ×' + eff);
                rows.push('<div style="font-size:12px;color:' + color + ';">' + effText + '</div>');
            }
            var start = cond.start_time || 0, end = cond.end_time || 0;
            if (start || end) {
                rows.push('<div style="color:#888;font-size:12px;">⏱ '
                    + (start ? fmt(start) : '即时') + ' 至 ' + (end ? fmt(end) : '长期有效') + '</div>');
            }
            if (cond.status && cond.status !== 'active') {
                rows.push('<div style="font-size:12px;color:#9AA5B1;">状态：'
                    + (COND_STATUS_LABELS[cond.status] || cond.status) + '</div>');
            }
            if (cond.description) {
                rows.push('<div style="font-size:12px;color:#555;">' + escapeHTML(cond.description) + '</div>');
            }
            var info = '<div style="padding:4px 2px;font-size:13px;line-height:1.7;min-width:180px;max-width:240px;">' +
                '<div style="font-weight:600;color:' + color + ';margin-bottom:4px;font-size:14px;">' +
                style.icon + ' ' + escapeHTML(cond.name || style.label) + '</div>' +
                rows.join('') +
                '<div class="cond-actions" style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;"></div>' +
                '</div>';

            var popup = L.popup({ offset: [0, -6], closeButton: true, autoPan: true })
                .setContent(info)
                .setLatLng(gcjToLatLng(anchor.lng, anchor.lat));
            layers.forEach(function (ly) { ly.bindPopup(popup); });
            popup.openOn(state.map);

            // 已登录管理员才注入操作按钮
            apiRequest('/api/admin/status', null, 'GET').then(function (data) {
                if (!(data && data.is_admin)) return;
                setTimeout(function () {
                    var area = popup.getElement()
                        ? popup.getElement().querySelector('.cond-actions')
                        : document.querySelector('.leaflet-popup .cond-actions');
                    if (!area) return;
                    var btnStyle = 'border:none;padding:7px 14px;border-radius:8px;font-size:13px;cursor:pointer;font-weight:600;color:white;';
                    if (!cond.end_time && !inactive) {
                        var endBtn = document.createElement('button');
                        endBtn.type = 'button';
                        endBtn.textContent = '⏹ 结束';
                        endBtn.style.cssText = btnStyle + 'background:#7F8C8D;';
                        endBtn.addEventListener('click', function () { endRoadCondition(cond.id, popup); });
                        area.appendChild(endBtn);
                    }
                    var delBtn = document.createElement('button');
                    delBtn.type = 'button';
                    delBtn.textContent = '🗑 删除';
                    delBtn.style.cssText = btnStyle + 'background:#e74c3c;';
                    delBtn.addEventListener('click', function () { deleteRoadCondition(cond.id, popup); });
                    area.appendChild(delBtn);
                }, 80);
            }).catch(function () {});
        } catch (e) {
            console.error('[路况] 信息窗错误:', e);
        }
    }

    // 结束路况（保留记录，end_time=now）
    function endRoadCondition(id, popup) {
        apiRequest('/api/road-conditions/' + id, { action: 'end' }, 'PATCH').then(function () {
            if (popup) state.map.closePopup();
            loadAndRenderRoadConditions(state.roadConditionAdminView);
        }).catch(function (err) {
            alert('结束失败：' + (err && err.message ? err.message : '请重试'));
        });
    }

    // 删除路况事件
    function deleteRoadCondition(id, popup) {
        if (!window.confirm('确定删除这条路况？删除后不可恢复（若只需让它失效，建议用「结束」保留记录）。')) return;
        apiRequest('/api/road-conditions/' + id, null, 'DELETE').then(function () {
            if (popup) state.map.closePopup();
            loadAndRenderRoadConditions(state.roadConditionAdminView);
        }).catch(function (err) {
            alert('删除失败：' + (err && err.message ? err.message : '请重试'));
        });
    }

    // 天气图标映射
    var WEATHER_ICONS = {
        '晴': '☀️', '少云': '🌤', '晴间多云': '🌤', '多云': '⛅', '阴': '☁️',
        '有风': '🌬', '风': '🌬', '霾': '😷', '雾': '🌫',
        '小雨': '🌦', '中雨': '🌧', '大雨': '🌧', '暴雨': '⛈', '阵雨': '🌦',
        '雷阵雨': '⛈', '雨': '🌧', '雪': '🌨', '小雪': '🌨', '中雪': '🌨',
        '大雪': '❄️', '暴雪': '❄️', '雨夹雪': '🌨'
    };

    // 加载实时天气徽章（真实动态路况来源之一：雨雪避坡、高温走树荫）
    function loadWeatherBadge() {
        apiRequest('/api/weather', null, 'GET').then(function (w) {
            if (!w) return;
            var badge = document.getElementById('weather-badge');
            var iconEl = document.getElementById('weather-icon');
            var textEl = document.getElementById('weather-text');
            if (!badge) return;
            var icon = WEATHER_ICONS[w.weather] || '🌡';
            var temp = w.temperature != null ? Math.round(w.temperature) : null;
            var text = w.weather || '';
            if (temp != null) text += ' ' + temp + '°';
            if (w.label) text += ' · ' + w.label;
            if (iconEl) iconEl.textContent = icon;
            if (textEl) textEl.textContent = text;
            badge.title = w.advice || ('当前武汉天气：' + w.weather + (temp != null ? '，' + temp + '°C' : ''));
            badge.hidden = false;
            if (w.slippery) badge.classList.add('weather-slippery');
            else if (w.hot) badge.classList.add('weather-hot');
        }).catch(function () { /* 静默失败 */ });
    }

    // ====== 上报路况（需管理员登录）======
    // HTML 转义（主 IIFE 内的本地工具；候选面板 IIFE 内另有同名函数，作用域独立）
    function escapeHTML(str) {
        if (str == null) return '';
        return String(str).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    var _pickHandler = null;
    var _pickMarker = null;      // 吸附后的红点
    var _pickEdgeLine = null;    // 吸附路段的高亮预览线
    var _pickBanner = null;      // 选点模式地图顶部浮层提示
    var _isAdmin = false;

    function openRoadReport() {
        // 先检查管理员登录态
        apiRequest('/api/admin/status', null, 'GET').then(function (data) {
            if (data && data.is_admin) {
                _isAdmin = true;
                showRoadReportForm();
                loadAndRenderRoadConditions(true);  // 管理员视图：含未开始/已结束
            } else {
                openAdminLogin();
            }
        }).catch(function () {
            openAdminLogin();
        });
    }

    function showRoadReportForm() {
        var section = document.getElementById('road-report-section');
        if (section) section.hidden = false;
        var hint = document.getElementById('road-report-hint');
        if (hint) hint.textContent = '';
    }

    // 彻底清理选点状态：解绑点击、移除红点与路段预览、清浮层与已选坐标。
    // 取消 / 退出管理 / 提交完成 都必须走这里（修复旧版红点残留在点击位置的 bug）。
    function clearPickState() {
        stopPickLocation();
        if (_pickMarker && state.map) { state.map.removeLayer(_pickMarker); _pickMarker = null; }
        if (_pickEdgeLine && state.map) { state.map.removeLayer(_pickEdgeLine); _pickEdgeLine = null; }
        hidePickBanner();
        state._pickedLng = null;
        state._pickedLat = null;
    }

    function closeRoadReport() {
        var section = document.getElementById('road-report-section');
        if (section) section.hidden = true;
        clearPickState();
        var form = document.getElementById('road-report-form');
        if (form) form.reset();
        var loc = document.getElementById('rr-location');
        if (loc) loc.value = '';
    }

    function showPickBanner(text, isError) {
        hidePickBanner();
        if (!state.map) return;
        var el = document.createElement('div');
        el.textContent = text;
        el.style.cssText = 'position:absolute;top:12px;left:50%;transform:translateX(-50%);z-index:1000;'
            + 'background:' + (isError ? 'rgba(192,57,43,0.95)' : 'rgba(33,33,33,0.9)')
            + ';color:white;padding:9px 18px;border-radius:20px;font-size:13px;max-width:86%;text-align:center;'
            + 'box-shadow:0 2px 10px rgba(0,0,0,0.25);pointer-events:auto;cursor:pointer;';
        // 点击浮层可取消选点
        el.addEventListener('click', function () {
            stopPickLocation();
            hidePickBanner();
            var section = document.getElementById('road-report-section');
            if (section) section.hidden = false;
        });
        state.map.getContainer().appendChild(el);
        _pickBanner = el;
    }

    function hidePickBanner() {
        if (_pickBanner && _pickBanner.parentNode) _pickBanner.parentNode.removeChild(_pickBanner);
        _pickBanner = null;
    }

    // ====== 管理员登录 ======
    function openAdminLogin() {
        var section = document.getElementById('admin-login-section');
        if (section) section.hidden = false;
        var hint = document.getElementById('admin-login-hint');
        if (hint) hint.textContent = '';
        var pwd = document.getElementById('admin-password');
        if (pwd) pwd.value = '';
        setTimeout(function () { if (pwd) pwd.focus(); }, 100);
    }

    function closeAdminLogin() {
        var section = document.getElementById('admin-login-section');
        if (section) section.hidden = true;
    }

    function handleAdminLogin(e) {
        e.preventDefault();
        var pwd = document.getElementById('admin-password');
        var password = pwd ? pwd.value : '';
        var hint = document.getElementById('admin-login-hint');
        var submitBtn = document.getElementById('admin-login-submit');
        if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = '登录中…'; }

        apiRequest('/api/admin/login', { password: password }).then(function () {
            _isAdmin = true;
            closeAdminLogin();
            showRoadReportForm();
            loadAndRenderRoadConditions(true);  // 进入管理视图
        }).catch(function (err) {
            if (hint) hint.textContent = (err && err.message) ? err.message : '登录失败，请重试';
            if (pwd) pwd.select();
        }).finally(function () {
            if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = '登录'; }
        });
    }

    function adminLogout() {
        apiRequest('/api/admin/logout', {}).catch(function () {}).then(function () {
            _isAdmin = false;
            closeRoadReport();               // 内含 clearPickState，红点必被移除
            loadAndRenderRoadConditions(false);  // 回到普通用户视图
        });
    }

    function startPickLocation() {
        if (!state.map) {
            var hint0 = document.getElementById('road-report-hint');
            if (hint0) hint0.textContent = '地图尚未加载，请稍后再试';
            return;
        }
        // 重置上一次选择，进入地图选点模式
        if (_pickMarker) { state.map.removeLayer(_pickMarker); _pickMarker = null; }
        if (_pickEdgeLine) { state.map.removeLayer(_pickEdgeLine); _pickEdgeLine = null; }
        state._pickedLng = null;
        state._pickedLat = null;
        var loc0 = document.getElementById('rr-location');
        if (loc0) loc0.value = '';

        var section = document.getElementById('road-report-section');
        if (section) section.hidden = true;
        showPickBanner('📍 请直接点击事发路段，系统会自动吸附到道路上（点此取消）');

        if (_pickHandler) return;
        _pickHandler = function (e) {
            var gcjLng = e.latlng.lng, gcjLat = e.latlng.lat;
            showPickBanner('⏳ 正在吸附到道路…');
            apiRequest('/api/road-conditions/snap?lng=' + encodeURIComponent(gcjLng)
                + '&lat=' + encodeURIComponent(gcjLat), null, 'GET')
                .then(function (data) {
                    var snap = data && data.snap;
                    if (!snap) {
                        showPickBanner('⚠️ 吸附失败，请再点一次道路', true);
                        return;
                    }
                    // 吸附成功：红点落到路上，并高亮该路段
                    if (_pickMarker) state.map.removeLayer(_pickMarker);
                    if (_pickEdgeLine) state.map.removeLayer(_pickEdgeLine);
                    var snapLatLng = gcjToLatLng(snap.snap_lng_gcj, snap.snap_lat_gcj);
                    var pickHtml = '<div style="width:16px;height:16px;background:#e74c3c;border-radius:50%;'
                        + 'border:2px solid white;box-shadow:0 1px 4px rgba(0,0,0,0.4);"></div>';
                    _pickMarker = divMarker(snapLatLng, pickHtml, [16, 16], [8, 8], '事发位置')
                        .addTo(state.map);
                    if ((snap.geometry_gcj || []).length >= 2) {
                        var edgeLatLngs = snap.geometry_gcj.map(function (p) {
                            return gcjToLatLng(p[0], p[1]);
                        });
                        _pickEdgeLine = L.polyline(edgeLatLngs, {
                            color: '#FFD400', weight: 8, opacity: 0.75, lineCap: 'round',
                        }).addTo(state.map);
                    }
                    // 提交时仍发原始点击点，服务端重新吸附（不信任前端）
                    state._pickedLng = gcjLng;
                    state._pickedLat = gcjLat;
                    stopPickLocation();
                    hidePickBanner();
                    if (section) section.hidden = false;
                    var loc = document.getElementById('rr-location');
                    if (loc) loc.value = (snap.road_name || '未命名道路')
                        + '（吸附偏移 ' + (snap.dist_m != null ? snap.dist_m : '?') + ' 米）';
                    var hint = document.getElementById('road-report-hint');
                    if (hint) {
                        hint.style.color = '#27AE60';
                        hint.textContent = '✅ 已吸附到「' + (snap.road_name || '未命名道路')
                            + '」，偏移 ' + (snap.dist_m != null ? snap.dist_m : '?') + ' 米'
                            + (snap.chain_length_m
                                ? '，管制将覆盖两端路口间整段约 ' + Math.round(snap.chain_length_m) + ' 米'
                                : '');
                    }
                })
                .catch(function (err) {
                    var msg = err && err.message ? err.message : '吸附失败';
                    showPickBanner('⚠️ ' + msg + '，请点在道路上重试', true);
                });
        };
        state.map.on('click', _pickHandler);
    }

    function stopPickLocation() {
        if (_pickHandler) {
            state.map.off('click', _pickHandler);
            _pickHandler = null;
        }
    }

    function handleRoadReportSubmit(e) {
        e.preventDefault();
        var type = document.getElementById('rr-type').value;
        var name = document.getElementById('rr-name').value.trim();
        var descEl = document.getElementById('rr-desc');
        var desc = descEl ? descEl.value.trim() : '';
        var lng = state._pickedLng;
        var lat = state._pickedLat;

        var hint = document.getElementById('road-report-hint');
        if (hint) hint.style.color = '';
        if (lng == null || lat == null) {
            if (hint) hint.textContent = '请先在地图上选择事发路段';
            return;
        }
        if (!name) {
            if (hint) hint.textContent = '请填写事件名称';
            return;
        }

        var startTime = document.getElementById('rr-start-time').value;
        var endTime = document.getElementById('rr-end-time').value;
        if (startTime && endTime && endTime <= startTime) {
            if (hint) hint.textContent = '结束时间必须晚于开始时间';
            return;
        }

        var submitBtn = document.getElementById('rr-submit-btn');
        if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = '提交中…'; }

        var payload = { type: type, name: name, lng: lng, lat: lat, description: desc };
        if (startTime) payload.start_time = startTime;
        if (endTime) payload.end_time = endTime;

        apiRequest('/api/road-conditions', payload).then(function () {
            if (hint) { hint.style.color = '#27AE60'; hint.textContent = '✅ 上报成功！路线将自动绕行。'; }
            clearPickState();
            loadAndRenderRoadConditions(true);  // 保持管理员视图刷新
            setTimeout(function () {
                closeRoadReport();
                if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = '提交'; }
            }, 1200);
        }).catch(function (err) {
            if (hint) {
                hint.style.color = '#C0392B';
                hint.textContent = '提交失败：' + (err && err.message ? err.message : '请重试');
            }
            if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = '提交'; }
        });
    }

    // 清除路线结果区 + 地图覆盖物（非路径规划响应时调用，避免旧路线残留）
    function clearRouteResult() {
        clearMap();
        var section = document.getElementById('results-section');
        if (section) section.hidden = true;
    }

    // 定位单个 POI：清空现有覆盖物，移动地图中心到该 POI 并高亮标记
    function focusPoiOnMap(poi) {
        if (!state.map) return;
        clearMap();
        var lng = poi.lon != null ? poi.lon : poi.lng;
        var lat = poi.lat;
        if (lng == null || lat == null) return;

        var pillHtml = '<div class="whu-map-pill whu-map-pill-poi">' + (poi.name || 'POI') + '</div>';
        var marker = divMarker(gcjToLatLng(lng, lat), pillHtml, [0, 0], [0, 0], poi.name || '')
            .addTo(state.map);
        state.poiMarkers.push(marker);
        state.map.setView(gcjToLatLng(lng, lat), 17);
    }

    // 返回键：清空路线 + 清空对话 + 复位地图，回到初始欢迎状态
    function handleReset() {
        cancelRouteAccept();  // 重置 → 当前路线不计为采纳
        // 1. 清空地图路线和标记
        clearMap();
        // 2. 复位地图视角（GCJ 中心点转 WGS-84）
        if (state.map) {
            var resetCenter = gcjToLatLng(MAP_CENTER[0], MAP_CENTER[1]);
            var resetZoom = window.innerWidth <= 767 ? 14 : MAP_ZOOM;
            state.map.setView(resetCenter, resetZoom);
        }
        // 3. 清空对话气泡（保留欢迎元素）
        var chatContent = document.getElementById('chat-content');
        if (chatContent) {
            chatContent.querySelectorAll('.chat-bubble-row').forEach(function (b) {
                b.remove();
            });
        }
        // 4. 恢复欢迎元素
        var welcomeBubble = document.getElementById('welcome-bubble');
        var shortcutCards = document.getElementById('shortcut-cards-row');
        if (welcomeBubble) welcomeBubble.style.display = '';
        if (shortcutCards) shortcutCards.style.display = '';
        // 5. 隐藏结果区
        var results = document.getElementById('results-section');
        if (results) results.hidden = true;
        var suggestions = document.getElementById('suggestions-area');
        if (suggestions) suggestions.hidden = true;
        // 6. 清空多轮对话上下文
        state.conversationHistory = [];
        saveContext();
        // 7. 清空输入框
        var nlInput = document.getElementById('nl-input');
        if (nlInput) { nlInput.value = ''; nlInput.style.height = 'auto'; }
        var charCount = document.getElementById('char-count');
        if (charCount) charCount.textContent = '0';
        // 8. 隐藏错误与加载遮罩
        hideError();
        hideLoading();
        // 9. 重置快捷 chip 高亮
        state.activeMode = null;
        document.querySelectorAll('.quick-chip').forEach(function (c) { c.classList.remove('active'); });
    }

    // 预计用时文案：后端 duration_min 优先（≥1 分钟"约 X 分钟"，<1 显示"约 1 分钟"）；
    // 后端未给时用前端兜底速度 walk 4.5 / bike 14 / drive 25 km/h 按推荐距离估算
    function estimateDurationText(data) {
        var min = data ? data.duration_min : null;
        if (min == null || isNaN(min) || min <= 0) {
            var recM = (data && (data.recommended_length_m || data.distance_m)) || 0;
            var mode = (data && data.mode && TRAVEL_MODES[data.mode]) ? data.mode : state.travelMode;
            var speed = (TRAVEL_MODES[mode] || TRAVEL_MODES.walk).speedKmh;
            min = recM > 0 ? (recM / 1000) / speed * 60 : null;
        }
        if (min == null || isNaN(min)) return '—';
        return '约 ' + Math.max(1, Math.round(min)) + ' 分钟';
    }

    function showResults(data) {
        // 隐藏欢迎气泡
        var welcomeBubble = document.getElementById('welcome-bubble');
        var shortcutCards = document.getElementById('shortcut-cards-row');
        if (welcomeBubble) welcomeBubble.style.display = 'none';
        if (shortcutCards) shortcutCards.style.display = 'none';

        var section = document.getElementById('results-section');
        section.hidden = false;

        document.getElementById('recommended-distance').textContent =
            (data.recommended_length_m || data.distance_m || 0).toFixed(0) + ' m';

        // 路线类型差异化展示：via → 绕行比，tour → 景点数，direct → 最短/重叠率
        var routeKind = data.route_kind || 'direct';
        var shortestCard = document.querySelector('#shortest-distance').closest('.summary-card');
        var shortestLabel = shortestCard ? shortestCard.querySelector('.summary-label') : null;
        var overlapCard = document.querySelector('#overlap-rate').closest('.summary-card');
        var overlapLabel = overlapCard ? overlapCard.querySelector('.summary-label') : null;

        if (routeKind === 'tour') {
            // 游览环线：最短距离 → 景点数，重叠率 → 环线标志
            var tourInfo = data.tour || {};
            var poiCount = (tourInfo.ordered_pois || data.pois || []).length;
            document.getElementById('shortest-distance').textContent = poiCount + ' 个';
            if (shortestLabel) shortestLabel.textContent = '游览景点';
            document.getElementById('overlap-rate').textContent = tourInfo.loop ? '环线' : '单程';
            if (overlapLabel) overlapLabel.textContent = '游览方式';
        } else if (routeKind === 'via') {
            // 途经路线：最短距离 → 途经点，重叠率 → 绕行比
            var viaInfo = data.via || {};
            document.getElementById('shortest-distance').textContent = viaInfo.name || '—';
            if (shortestLabel) shortestLabel.textContent = '途经点';
            var detour = data.detour_ratio;
            document.getElementById('overlap-rate').textContent =
                detour != null ? '+' + (detour * 100).toFixed(0) + '%' : '—';
            if (overlapLabel) overlapLabel.textContent = '绕行比';
        } else {
            // 直接路线：保持原有逻辑
            document.getElementById('shortest-distance').textContent =
                (data.shortest_length_m || data.shortest_distance_m || 0).toFixed(0) + ' m';
            if (shortestLabel) shortestLabel.textContent = '最短距离';
            document.getElementById('overlap-rate').textContent =
                ((data.overlap_rate || 0) * 100).toFixed(0) + '%';
            if (overlapLabel) overlapLabel.textContent = '重叠率';
        }

        // 预计用时：优先用后端 duration_min，缺失时按模式兜底速度估算（fail-soft）
        var durationEl = document.getElementById('estimated-duration');
        if (durationEl) durationEl.textContent = estimateDurationText(data);

        var poiList = document.getElementById('poi-items');
        poiList.innerHTML = '';
        var pois = data.pois || [];
        // 游览环线：优先用 tour.ordered_pois 显示有序编号
        if (routeKind === 'tour' && data.tour && data.tour.ordered_pois) {
            pois = data.tour.ordered_pois;
        }
        if (pois.length === 0) {
            poiList.innerHTML = '<li style="background:#FAF8F5;color:#A8A5A2;">暂无途经景点</li>';
        } else {
            pois.forEach(function (poi, i) {
                var li = document.createElement('li');
                if (routeKind === 'tour') {
                    li.textContent = (i + 1) + '. ' + (poi.name || poi.id || '未知');
                } else {
                    li.textContent = poi.name || poi.id || '未知';
                }
                poiList.appendChild(li);
            });
        }

        var explanation = data.explanation || '已为您规划好路线';
        document.getElementById('explanation-text').textContent = explanation;

        // 路况提示：若有生效的路况事件，在解释下方显示提醒
        var roadNoticeEl = document.getElementById('road-conditions-notice');
        if (roadNoticeEl) {
            var rcCount = data.road_conditions_applied || 0;
            if (rcCount > 0) {
                roadNoticeEl.style.display = 'block';
                roadNoticeEl.textContent = '⚠️ 当前有 ' + rcCount + ' 条路况事件生效，路线已自动绕行';
            } else {
                roadNoticeEl.style.display = 'none';
            }
        }

        // 跟进建议
        showSuggestions(data);

        // 自动滚到结果区：让摘要卡顶部对齐可视区（结果卡片在用户气泡之前，
        // 短面板上若直接滚到底会把结果滚出可视区，手机端尤其明显）
        var chatContent = document.getElementById('chat-content');
        if (chatContent) {
            setTimeout(function () {
                var contentRect = chatContent.getBoundingClientRect();
                var sectionRect = section.getBoundingClientRect();
                var target = chatContent.scrollTop + (sectionRect.top - contentRect.top) - 8;
                chatContent.scrollTo({ top: Math.max(0, target), behavior: 'smooth' });
            }, 100);
        }
    }

    function showSuggestions(data) {
        var area = document.getElementById('suggestions-area');
        var chips = document.getElementById('suggestions-chips');
        if (!area || !chips) return;

        var suggestions = (data.suggestions && data.suggestions.length > 0)
            ? data.suggestions        // 后端 LLM 动态生成的建议
            : buildSuggestions(data); // 兜底：规则建议
        if (suggestions.length === 0) { area.hidden = true; return; }

        chips.innerHTML = '';
        suggestions.forEach(function (s) {
            var chip = document.createElement('button');
            chip.className = 'suggestion-chip';
            chip.textContent = s.label;
            chip.title = s.query;
            chip.addEventListener('click', function () {
                document.getElementById('nl-input').value = s.query;
                document.getElementById('submit-btn').click();
            });
            chips.appendChild(chip);
        });
        area.hidden = false;
    }

    function buildSuggestions(data) {
        var suggestions = [];
        var constraints = data.constraints || {};
        var recLen = data.recommended_length_m || data.distance_m || 0;
        var shortLen = data.shortest_length_m || data.shortest_distance_m || 0;
        var mode = (data && data.mode && TRAVEL_MODES[data.mode]) ? data.mode : state.travelMode;

        // 偏好类建议
        if (mode === 'drive') {
            if (constraints.distance !== 'short') {
                suggestions.push({ label: '⚡ 最快到达', query: '帮我规划最快到达的路线' });
            }
        } else {
            if (constraints.slope !== 'avoid') {
                suggestions.push({ label: '🪜 走更平坦的路', query: '帮我找一条更平坦的路线' });
            }
        }
        if (constraints.scenery !== 'high') {
            suggestions.push({ label: '🌸 想看风景好的路', query: '走风景更好的路线' });
        }
        if (mode !== 'drive' && recLen > shortLen * 1.3 && constraints.distance !== 'short') {
            suggestions.push({ label: '⚡ 我要最短路径', query: '帮我规划最短路径' });
        }

        // 校园生活场景建议（基于起终点推断）
        var start = data.start && data.start.name ? data.start.name : '';
        var end = data.end && data.end.name ? data.end.name : '';
        var routeText = (start + end) || '';

        // 不重复已有的偏好建议
        var existingLabels = suggestions.map(function (s) { return s.label; });
        function addIfNew(label, query) {
            if (existingLabels.indexOf(label) === -1) {
                suggestions.push({ label: label, query: query });
                existingLabels.push(label);
            }
        }

        // 吃饭场景
        if (routeText.indexOf('食堂') === -1 && routeText.indexOf('餐') === -1) {
            addIfNew('🍜 去附近食堂', '从' + (end || '这里') + '去最近的食堂');
        }
        // 学习场景
        if (routeText.indexOf('图书馆') === -1 && routeText.indexOf('教') === -1) {
            addIfNew('📚 去图书馆', '从' + (end || '这里') + '去总图书馆');
        }
        // 赏樱/景点场景
        if (routeText.indexOf('樱') === -1) {
            addIfNew('🌸 去樱花大道', '从' + (end || '这里') + '去樱花大道');
        }
        // 校门场景
        if (routeText.indexOf('门') === -1) {
            addIfNew('🚪 去最近校门', '从' + (end || '这里') + '去最近的校门');
        }

        // 兜底
        if (suggestions.length === 0) {
            if (mode === 'drive') {
                suggestions.push({ label: '⚡ 换条更快的路线', query: '换一条更快到达的路线' });
            } else {
                suggestions.push({ label: '🪜 换条更平坦的', query: '换一条更平坦的路线' });
            }
            suggestions.push({ label: '🌸 换条风景更好的', query: '换一条风景更好的路线' });
        }
        return suggestions.slice(0, 4);
    }

    function collapseResults() {
        var section = document.getElementById('results-section');
        section.classList.toggle('collapsed');
    }

    function showLoading(text, subtext) {
        state.loading = true;
        var section = document.getElementById('loading-section');
        section.hidden = false;
        document.getElementById('loading-text').textContent = text || '正在处理…';
        document.getElementById('loading-subtext').textContent = subtext || '请稍候';
    }

    function hideLoading() {
        state.loading = false;
        stopLoadingMessages();
        document.getElementById('loading-section').hidden = true;
    }

    function showError(title, message) {
        var section = document.getElementById('error-section');
        document.getElementById('error-title').textContent = title || '唔，出错了';
        document.getElementById('error-message').textContent = message || '抱歉，出了点意外状况';
        section.hidden = false;
    }

    function hideError() {
        document.getElementById('error-section').hidden = true;
    }

    async function apiRequest(endpoint, data, method) {
        var url = API_BASE + endpoint;
        var httpMethod = method || 'POST';
        var options = {
            method: httpMethod,
            headers: { 'Content-Type': 'application/json' },
        };
        // GET 请求不带 body（否则部分服务器/缓存层会拒绝）
        if (httpMethod !== 'GET') {
            options.body = JSON.stringify(data);
        }

        var response;
        try {
            response = await fetch(url, options);
        } catch (e) {
            throw new Error('网络请求失败，请检查网络连接');
        }

        var result;
        try {
            result = await response.json();
        } catch (e) {
            throw new Error('服务器响应格式错误');
        }

        if (!response.ok) {
            var errMsg = (result && result.message) || ('请求失败 (' + response.status + ')');
            var errCode = result && result.error;
            var userMsg = mapError(errCode, errMsg);
            var err = new Error(userMsg);
            err.code = errCode;  // 带上错误码，供调用方判断是否对话式引导
            throw err;
        }

        return result.data || result;
    }

    function mapError(code, msg) {
        var errorMap = {
            'missing_query': '嗯？你还没告诉我你想去哪呢～试试输入「从珞珈门到樱顶」',
            'missing_endpoints': '需要起点和终点才能规划路线哦，在地图上选点或者打字告诉我吧',
            'missing_poi_names': '起点和终点得有个名字才行～',
            'poi_not_found': '抱歉，我没找到这个地方😅 试试换个说法？比如「教五」就是「第五教学楼」',
            'route_not_found': '这条路线走不通…可能是路网数据还不够全，试试换个目的地？',
            'network_not_initialized': '地图还没加载完，稍等一下下就好～',
            'network_load_failed': '地图数据加载失败了，刷新一下页面试试？',
            'parse_failed': '我没太理解你的意思…试试简单一点的说法，比如「从珞珈门到樱顶」',
            'parse_validation_error': '输入格式有点问题，试试更简洁的描述？',
            'route_computation_failed': '路线计算出错了，可能是网络不太好，再试一次？',
            'nearest_node_failed': '这个位置我没法定位，换个附近的地点试试？',
            'unsupported_task': '这个功能我暂时还不会，试试问路或者查景点吧～',
            'internal_error': '出了点小问题，稍等一下再试就好',
        };
        return errorMap[code] || msg || '出了点意外，再试一次吧';
    }

    // 可"对话式引导"的错误码：信息不完整 / 输入无法理解，用气泡友好提示而非报错弹窗
    var GUIDEABLE_ERRORS = [
        'missing_query', 'missing_endpoints', 'missing_poi_names',
        'poi_not_found', 'same_poi', 'parse_failed',
        'parse_validation_error', 'unsupported_task',
    ];
    function isGuideableError(code) {
        return GUIDEABLE_ERRORS.indexOf(code) !== -1;
    }

    // GPS 指代表达识别
    // 起点：「从我这/我这里/我的位置/当前位置…去/到/出发」；终点：「到我这(来)/来我的位置」
    var LOC_START_RE = /从\s*(我这(?:儿|里)?|我的位置|当前位置|我现在的?位置|我这边)/;
    var LOC_BARE_RE = /^(我这(?:儿|里)?|我的位置|当前位置)[^，。,.]{0,6}(去|到|出发)/;
    var LOC_END_RE = /(?:到|去)\s*(我这(?:儿|里)?(?:来)?|我的位置|当前位置)(?:来)?$/;

    function detectLocationRefs(text) {
        return {
            asStart: LOC_START_RE.test(text) || LOC_BARE_RE.test(text),
            asEnd: LOC_END_RE.test(text),
        };
    }

    // 确保有定位：优先用已有的 state.userLocation（页面加载自动跟踪已赋值），
    // 如果还没有（比如用户拒绝了权限），则主动启动跟踪并等第一次回调。
    function ensureUserLocation() {
        if (state.userLocation) return Promise.resolve(state.userLocation);

        return new Promise(function (resolve, reject) {
            // 启动跟踪（如果还没启动），等第一次 renderUserLocation 后 resolve
            var resolved = false;
            var origRender = renderUserLocation;
            // 临时包装 renderUserLocation，第一次被调时 resolve
            renderUserLocation = function (lng, lat, accuracy, isManual) {
                origRender(lng, lat, accuracy, isManual);
                if (!resolved) { resolved = true; resolve(state.userLocation); }
            };
            // 启动跟踪
            startTracking(true);
            // 10 秒后还没 resolve → 恢复原函数并 reject
            setTimeout(function () {
                if (!resolved) {
                    resolved = true;
                    renderUserLocation = origRender;  // 恢复原函数
                    reject(new Error('定位超时，请点右上角 ◎ 按钮允许定位后再试。'));
                }
            }, 10000);
        });
    }

    async function handleNlSubmit(query) {
        hideError();
        cancelRouteAccept();  // 新请求到来 → 上一条路线不再计为采纳
        state._autoLocated = false;  // 每轮新查询重置自动定位标记
        startLoadingMessages(query);
        hideWelcomeElements();
        showUserBubble(query);

        // 显示"思考中"气泡，结果回来后替换内容
        var thinkingBubble = showChatBubble(query, '🌸 正在为你规划路线…');

        state.requestSeq += 1;
        var mySeq = state.requestSeq;

        try {
            // 上下文 = 真实对话消息流（最近 4 轮）+ 在途规划槽位。
            // 闲聊/景点查询不会清空规划槽位（见 addConversationTurn）。
            var context = null;
            if (state.conversationHistory.length > 0 || state.lastIntent) {
                context = { history: state.conversationHistory.slice(-8) };
                if (state.lastIntent) {
                    context.previous_intent = state.lastIntent;
                    context.last_ambiguity = state.lastIntent.ambiguity;
                    context.start = state.lastIntent.start;
                    context.end = state.lastIntent.end;
                    context.constraints = state.lastIntent.constraints;
                    context.weights = state.lastIntent.weights;
                }
            }

            // GPS 指代表达：识别「从我这到X / 到我这来」，现场补一次定位
            var requestBody = {
                query: query,
                context: context,
                travel_mode: state.travelMode,
                whu_uid: getUid(),  // 画像学习用匿名 ID
            };
            var locRefs = detectLocationRefs(query);
            if (locRefs.asStart || locRefs.asEnd) {
                var loc = await ensureUserLocation();
                if (mySeq !== state.requestSeq) return;
                var coordRef = { lng: loc.lng, lat: loc.lat, name: '我的位置' };
                if (locRefs.asStart) requestBody.coord_start = coordRef;
                if (locRefs.asEnd) requestBody.coord_end = coordRef;
            }
            // 兜底：用户之前已定位过（点过◎或之前轮次），且没显式说起点 → 自动附起点
            // 这样用户说"去珞珈山"时，如果之前已定位过，后端直接拿到坐标，不再追问
            if (!requestBody.coord_start && state.userLocation && !query._auto_located) {
                requestBody.coord_start = {
                    lng: state.userLocation.lng, lat: state.userLocation.lat, name: '我的位置',
                };
            }

            var result = await apiRequest('/api/chat', requestBody);

            if (mySeq !== state.requestSeq) return;

            var taskType = result.task_type;

            if (taskType === 'chat') {
                hideWelcomeElements();
                clearRouteResult();
                updateChatBubble(thinkingBubble, result.reply || result.message || '嗯…这个问题有点难，换个问法试试？');
                addConversationTurn(query, result);
                stopLoadingMessages();
                hideLoading();
                return;
            }

            // clarify（response_kind）：Agent 主动追问 → 渲染可点选项
            if (result.response_kind === 'clarify' && result.clarify) {
                // 层 2：如果后端追问的是起点（"从哪出发""你在哪儿"等），且没标记过自动定位 → 自动定位后重发
                var clarifyMsg = (result.clarify && result.clarify.question) || result.message || '';
                var NEED_START_RE = /起点|从哪|你在哪|在哪儿|哪里出发|告诉我起点|出发地/;
                if (NEED_START_RE.test(clarifyMsg) && !state._autoLocated) {
                    try {
                        console.log('[AUTO_LOC] clarify 追问起点 → 自动定位并重发');
                        state._autoLocated = true;
                        var autoLoc = await ensureUserLocation();
                        if (mySeq !== state.requestSeq) return;
                        // 重发原始 query
                        handleNlSubmit(query);
                        return;  // 已重发，不再往下渲染
                    } catch (autoErr) {
                        console.warn('[AUTO_LOC] 自动定位失败，显示 clarify 让用户手动选:', autoErr.message);
                        state._autoLocated = false;  // 失败重置，下次还能再试
                        // fallthrough 渲染 clarify 选项
                    }
                }
                hideWelcomeElements();
                clearRouteResult();
                updateChatBubble(thinkingBubble, clarifyMsg || '能再具体一点吗？');
                renderClarifyOptions(result.clarify.options || []);
                addConversationTurn(query, result);
                stopLoadingMessages();
                hideLoading();
                return;
            }

            // candidates（response_kind）：目标型需求 → 渲染候选卡片
            if (result.response_kind === 'candidates') {
                hideWelcomeElements();
                clearRouteResult();
                var cands = result.candidates || [];
                updateChatBubble(thinkingBubble, result.message || (cands.length ? '帮你找到这些地点，点一个我帮你规划路线～' : '校内没找到匹配的地点，换个说法试试？'));
                if (cands.length) renderCandidateCards(cands);
                addConversationTurn(query, result);
                stopLoadingMessages();
                hideLoading();
                return;
            }

            if (taskType === 'help' || taskType === 'unknown') {
                hideWelcomeElements();
                clearRouteResult();
                updateChatBubble(thinkingBubble, result.message || '有什么可以帮你的？');
                addConversationTurn(query, result);
                stopLoadingMessages();
                hideLoading();
                return;
            }

            if (taskType === 'poi_query') {
                hideWelcomeElements();
                clearRouteResult();
                var poi = result.poi;
                updateChatBubble(thinkingBubble, result.message || (poi ? poi.description : '找到相关信息了～'));
                if (poi && state.map) {
                    focusPoiOnMap(poi);
                }
                addConversationTurn(query, result);
                stopLoadingMessages();
                hideLoading();
                return;
            }

            // path_planning → 渲染路线 + 对话反馈
            if (result.recommended && result.recommended.length > 0) {
                syncModeFromServer(result);
                renderRoute(result);
                showResults(result);
                scheduleRouteAccept(result);  // 埋点：曝光 + 20s 采纳判定
                // 用后端 explanation 作为对话反馈，没有则兜底文案
                var reply = result.explanation || buildRouteSummary(result);
                updateChatBubble(thinkingBubble, reply);
                addConversationTurn(query, result);
            } else {
                clearRouteResult();
                updateChatBubble(thinkingBubble, '唔，这条路我没能规划出来😅 试试换个目的地？比如「从珞珈门到樱顶」');
            }
        } catch (err) {
            if (mySeq !== state.requestSeq) return;
            // 所有错误都走对话气泡，不弹错误窗
            hideWelcomeElements();
            clearRouteResult();
            var errMsg = (err && err.message) || '出了点小问题，再试一次吧～';
            updateChatBubble(thinkingBubble, errMsg);
        } finally {
            if (mySeq === state.requestSeq) {
                hideLoading();
            }
        }
    }

    // 显示用户输入气泡（靠右，主题色）
    function showUserBubble(query) {
        if (!query) return;
        var chatContent = document.getElementById('chat-content');
        if (!chatContent) return;

        var bubble = document.createElement('div');
        bubble.className = 'chat-bubble-row';
        bubble.innerHTML =
            '<div class="chat-bubble chat-bubble-user">' +
                '<div class="chat-bubble-body">' +
                    '<p class="chat-bubble-text">' + escapeHtml(query) + '</p>' +
                '</div>' +
            '</div>';
        chatContent.appendChild(bubble);
    }

    // 在对话流中显示聊天气泡，返回气泡元素供后续 updateChatBubble 替换内容
    function showChatBubble(query, reply) {
        var chatContent = document.getElementById('chat-content');
        if (!chatContent) return null;

        var bubble = document.createElement('div');
        bubble.className = 'chat-bubble-row';
        bubble.innerHTML =
            '<div class="chat-bubble chat-bubble-reply">' +
                '<div class="chat-bubble-avatar">🌸</div>' +
                '<div class="chat-bubble-body">' +
                    '<p class="chat-bubble-text">' + escapeHtml(reply) + '</p>' +
                '</div>' +
            '</div>';
        chatContent.appendChild(bubble);

        setTimeout(function () {
            chatContent.scrollTo({ top: chatContent.scrollHeight, behavior: 'smooth' });
        }, 100);

        return bubble;
    }

    // 替换已有气泡内容（用于"思考中"→最终回复）
    function updateChatBubble(bubble, text) {
        if (!bubble) return;
        var textEl = bubble.querySelector('.chat-bubble-text');
        if (textEl) {
            textEl.textContent = text;
        }
        var chatContent = document.getElementById('chat-content');
        if (chatContent) {
            setTimeout(function () {
                chatContent.scrollTo({ top: chatContent.scrollHeight, behavior: 'smooth' });
            }, 100);
        }
    }

    // 路线摘要兜底文案（后端 explanation 缺失时用）
    function buildRouteSummary(data) {
        var dist = (data.recommended_length_m || data.distance_m || 0).toFixed(0);
        var dur = estimateDurationText(data);
        var pois = data.pois || [];
        var poiNames = pois.slice(0, 3).map(function (p) { return p.name; }).join('、');
        var reply = '为你规划好了路线，约 ' + dist + ' 米，' + dur + '。';
        if (poiNames) {
            reply += ' 沿途经过 ' + poiNames;
            if (pois.length > 3) reply += ' 等 ' + pois.length + ' 个地点';
            reply += '。';
        }
        return reply;
    }

    // 候选 POI 卡片（response_kind=candidates）：点击卡片 → 以该点为终点发起规划
    function renderCandidateCards(candidates) {
        var chatContent = document.getElementById('chat-content');
        if (!chatContent || !candidates.length) return;

        var row = document.createElement('div');
        row.className = 'chat-bubble-row';
        var wrap = document.createElement('div');
        wrap.className = 'candidate-cards';
        row.appendChild(wrap);
        chatContent.appendChild(row);

        candidates.slice(0, 6).forEach(function (p) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'candidate-card';

            var nameEl = document.createElement('span');
            nameEl.className = 'candidate-name';
            nameEl.textContent = p.name || '';
            btn.appendChild(nameEl);

            var cat = p.category_label || p.subcategory_label || p.subcategory || '';
            if (cat) {
                var catEl = document.createElement('span');
                catEl.className = 'candidate-cat';
                catEl.textContent = cat;
                btn.appendChild(catEl);
            }

            if (p.distance_m != null) {
                var distEl = document.createElement('span');
                distEl.className = 'candidate-dist';
                distEl.textContent = p.distance_m >= 1000
                    ? (p.distance_m / 1000).toFixed(1) + 'km'
                    : Math.round(p.distance_m) + 'm';
                btn.appendChild(distEl);
            }

            btn.addEventListener('click', function () {
                // 防连点：点击后整组卡片禁用
                wrap.querySelectorAll('.candidate-card').forEach(function (b) { b.disabled = true; });
                trackEvent('candidate_click', { name: p.name, subcategory: p.subcategory || null });
                handleNlSubmit('去' + (p.name || ''));
            });
            wrap.appendChild(btn);
        });

        // 地图上同步标注候选点，方便用户看着地图挑
        if (state.map) {
            var bounds = [];
            candidates.slice(0, 6).forEach(function (p, i) {
                var lng = p.lon != null ? p.lon : p.lng;
                var lat = p.lat;
                if (lng == null || lat == null) return;
                var pillHtml = '<div class="whu-map-pill whu-map-pill-poi">' + (i + 1) + '. ' + (p.name || '') + '</div>';
                var marker = divMarker(gcjToLatLng(lng, lat), pillHtml, [0, 0], [0, 0], p.name || '')
                    .addTo(state.map);
                // 点地图标记 = 选这个候选（跟点卡片一样）
                marker.on('click', function () {
                    trackEvent('candidate_click_map', { name: p.name, subcategory: p.subcategory || null });
                    handleNlSubmit('去' + (p.name || ''));
                });
                state.poiMarkers.push(marker);
                bounds.push(marker.getLatLng());
            });
            if (bounds.length > 1) {
                state.map.fitBounds(bounds, { padding: [40, 40] });
            } else if (bounds.length === 1) {
                state.map.setView(bounds[0], 17);
            }
        }

        setTimeout(function () {
            chatContent.scrollTo({ top: chatContent.scrollHeight, behavior: 'smooth' });
        }, 100);
    }

    // 澄清选项（response_kind=clarify）：点击选项 → 把选项文本作为新 query 提交
    function renderClarifyOptions(options) {
        var chatContent = document.getElementById('chat-content');
        if (!chatContent || !options.length) return;

        var row = document.createElement('div');
        row.className = 'chat-bubble-row';
        var wrap = document.createElement('div');
        wrap.className = 'clarify-chips';
        row.appendChild(wrap);
        chatContent.appendChild(row);

        options.slice(0, 4).forEach(function (opt) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'clarify-chip';
            btn.textContent = opt;
            btn.addEventListener('click', function () {
                wrap.querySelectorAll('.clarify-chip').forEach(function (b) { b.disabled = true; });
                trackEvent('clarify_answer', { answer: opt });
                handleNlSubmit(opt);
            });
            wrap.appendChild(btn);
        });

        setTimeout(function () {
            chatContent.scrollTo({ top: chatContent.scrollHeight, behavior: 'smooth' });
        }, 100);
    }

    function hideWelcomeElements() {
        var welcomeBubble = document.getElementById('welcome-bubble');
        var shortcutCards = document.getElementById('shortcut-cards-row');
        if (welcomeBubble) welcomeBubble.style.display = 'none';
        if (shortcutCards) shortcutCards.style.display = 'none';
    }

    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    async function handleShortcutMode(mode) {
        hideError();

        state.requestSeq += 1;
        var mySeq = state.requestSeq;

        var modeConfig = {
            distance_first: {
                loadingText: '正在规划最短路径…',
                loadingSubtext: '优先考虑距离',
                userQuery: '帮我规划最短路径',
            },
            scenery_first: {
                loadingText: '正在规划风景路线…',
                loadingSubtext: '优先考虑景观',
                userQuery: '帮我找一条风景好的路线',
            },
            slope_avoid: {
                loadingText: '正在规划平坦路线…',
                loadingSubtext: '优先考虑坡度',
                userQuery: '帮我找一条平坦的路线',
            },
        };

        var cfg = modeConfig[mode] || {};

        var startName = '珞珈门';
        var endName = '樱顶';
        if (state.lastIntent && state.lastIntent.start && state.lastIntent.end) {
            startName = state.lastIntent.start.name || '珞珈门';
            endName = state.lastIntent.end.name || '樱顶';
        }

        // 快捷模式也走对话流：用户气泡 + 思考气泡
        hideWelcomeElements();
        showUserBubble(cfg.userQuery || ('从' + startName + '到' + endName));
        var thinkingBubble = showChatBubble('', '🌸 正在为你规划路线…');

        var tm = TRAVEL_MODES[state.travelMode] || TRAVEL_MODES.walk;
        showLoading(tm.loadingText, cfg.loadingSubtext || tm.loadingSub);

        try {
            var parsePayload = {
                start: { name: startName },
                end: { name: endName },
                input_method: 'shortcut',
                travel_mode: state.travelMode,
            };
            if (mode) parsePayload.mode = mode;

            var parseResult = await apiRequest('/api/parse', parsePayload);
            if (mySeq !== state.requestSeq) return;

            var routePayload = parseResult || {};
            routePayload.travel_mode = state.travelMode;

            var routeResult = await apiRequest('/api/route', routePayload);
            if (mySeq !== state.requestSeq) return;

            if (routeResult.recommended && routeResult.recommended.length > 0) {
                renderRoute(routeResult);
                showResults(routeResult);
                // 快捷模式后端不返回 explanation，用兜底摘要
                updateChatBubble(thinkingBubble, buildRouteSummary(routeResult));
            } else {
                clearRouteResult();
                updateChatBubble(thinkingBubble, '唔，这条路我没能规划出来😅 试试换个目的地？');
            }
        } catch (err) {
            if (mySeq !== state.requestSeq) return;
            clearRouteResult();
            var errMsg = (err && err.message) || '出了点小问题，再试一次吧～';
            updateChatBubble(thinkingBubble, errMsg);
        } finally {
            if (mySeq === state.requestSeq) {
                hideLoading();
                flushPendingModeRecompute();
            }
        }
    }

    // ========== 快捷键系统 ==========

    // 给按钮加 0.35s 闪动反馈（快捷键触发后视觉确认）
    function flashButton(el) {
        if (!el) return;
        el.classList.remove('flash');
        // 强制 reflow 再加 class，确保动画重新播放
        void el.offsetWidth;
        el.classList.add('flash');
        setTimeout(function () { el.classList.remove('flash'); }, 400);
    }

    function showKbdHelp() {
        var sec = document.getElementById('kbd-help-section');
        if (sec) sec.hidden = false;
    }
    function hideKbdHelp() {
        var sec = document.getElementById('kbd-help-section');
        if (sec) sec.hidden = true;
    }

    // 全局键盘快捷键
    function handleGlobalKeydown(e) {
        // 输入框/textarea 聚焦时：只放行 Esc，其余字母不拦截（用户在打字）
        var tag = (e.target.tagName || '').toLowerCase();
        var inInput = (tag === 'input' || tag === 'textarea' || e.target.isContentEditable);

        // Esc：关闭所有弹窗（输入框内也生效）
        if (e.key === 'Escape') {
            hideError();
            hideKbdHelp();
            return;
        }

        // 输入框内：不拦截其余按键（Enter/Shift+Enter 已有专门处理）
        if (inInput) return;

        // 数字键 1/2/3：切出行方式（输入框失焦时才触发）
        if (e.key === '1') { triggerTravelMode('walk'); e.preventDefault(); return; }
        if (e.key === '2') { triggerTravelMode('bike'); e.preventDefault(); return; }
        if (e.key === '3') { triggerTravelMode('drive'); e.preventDefault(); return; }

        // 字母快捷键：偏好 chip + 功能键
        var key = e.key.toUpperCase();
        if (key === 'F') { triggerChip('scenery_first'); e.preventDefault(); return; }
        if (key === 'S') { triggerChip('slope_avoid'); e.preventDefault(); return; }
        if (key === 'D') { triggerChip('distance_first'); e.preventDefault(); return; }
        if (key === 'R') { var rb = document.getElementById('reset-btn'); flashButton(rb); handleReset(); e.preventDefault(); return; }
        if (e.key === '?' || (e.shiftKey && e.key === '/')) { var hb = document.getElementById('help-btn'); flashButton(hb); showKbdHelp(); e.preventDefault(); return; }

        // / 聚焦输入框
        if (e.key === '/') {
            var inp = document.getElementById('nl-input');
            if (inp) { inp.focus(); inp.select(); e.preventDefault(); }
            return;
        }
    }

    // 快捷键触发出行方式切换
    function triggerTravelMode(mode) {
        var btn = document.querySelector('.travel-mode-btn[data-travel-mode="' + mode + '"]');
        flashButton(btn);
        setTravelMode(mode);
    }

    // 快捷键触发偏好路线（风景/平坦/最短），不再依赖页面上的 chip 元素
    function triggerChip(chipMode, sourceEl) {
        // 驾车模式下 S（平坦优先）无效
        if (chipMode === 'slope_avoid' && state.travelMode === 'drive') return;
        if (sourceEl) flashButton(sourceEl);
        state.activeMode = chipMode;
        handleShortcutMode(chipMode);
    }

    function bindEvents() {
        var nlInput = document.getElementById('nl-input');
        var charCount = document.getElementById('char-count');
        var submitBtn = document.getElementById('submit-btn');
        var quickChips = document.querySelectorAll('.quick-chip');
        var errorCloseBtn = document.getElementById('error-close-btn');
        var resetBtn = document.getElementById('reset-btn');

        if (nlInput) {
            // 字数统计 + auto-resize
            nlInput.addEventListener('input', function () {
                var len = nlInput.value.length;
                if (charCount) charCount.textContent = len;
                if (len >= 200 && charCount) charCount.style.color = '#C76B7A';
                else if (charCount) charCount.style.color = '';
                // auto-resize
                nlInput.style.height = 'auto';
                nlInput.style.height = Math.min(nlInput.scrollHeight, 100) + 'px';
            });

            nlInput.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    if (submitBtn) submitBtn.click();
                }
            });
        }

        if (submitBtn) {
            submitBtn.addEventListener('click', function () {
                var query = nlInput.value.trim();
                if (!query) {
                    showError('嗯？还没说去哪呢', '告诉我你想从哪走到哪吧～比如「从珞珈门到樱顶」');
                    nlInput.focus();
                    return;
                }
                handleNlSubmit(query);
                // 提交后清空输入框 + 重置字数统计与高度
                nlInput.value = '';
                nlInput.style.height = 'auto';
                if (charCount) charCount.textContent = '0';
            });
        }

        // 快捷 Chip 按钮
        quickChips.forEach(function (chip) {
            chip.addEventListener('click', function () {
                quickChips.forEach(function (c) { c.classList.remove('active'); });
                chip.classList.add('active');
                var mode = chip.getAttribute('data-mode');
                state.activeMode = mode;
                handleShortcutMode(mode);
            });
        });

        // 出行方式分段选择器（步行 / 骑行 / 驾车）：只切状态，不自动发请求（有路线结果时自动重算）
        var travelModeBtns = document.querySelectorAll('.travel-mode-btn');
        travelModeBtns.forEach(function (btn) {
            btn.addEventListener('click', function () {
                var travelMode = btn.getAttribute('data-travel-mode');
                setTravelMode(travelMode);
            });
        });

        // 快捷小卡片（经典路线、赏樱路线）
        var miniCards = document.querySelectorAll('.mini-card');
        miniCards.forEach(function (card) {
            card.addEventListener('click', function () {
                var displayText = card.getAttribute('data-display-text');
                if (nlInput && displayText) {
                    nlInput.value = displayText;
                    nlInput.dispatchEvent(new Event('input'));
                }
                if (submitBtn) submitBtn.click();
            });
        });

        if (errorCloseBtn) {
            errorCloseBtn.addEventListener('click', hideError);
        }

        if (resetBtn) {
            resetBtn.addEventListener('click', handleReset);
        }

        // 帮助按钮 → 快捷键帮助弹窗
        var helpBtn = document.getElementById('help-btn');
        if (helpBtn) {
            helpBtn.addEventListener('click', function () {
                flashButton(helpBtn);
                showKbdHelp();
            });
        }

        // 上报路况按钮
        var roadReportBtn = document.getElementById('road-report-btn');
        if (roadReportBtn) {
            roadReportBtn.addEventListener('click', function () {
                flashButton(roadReportBtn);
                openRoadReport();
            });
        }
        var rrCancel = document.getElementById('rr-cancel-btn');
        if (rrCancel) rrCancel.addEventListener('click', closeRoadReport);
        var rrLogout = document.getElementById('rr-logout-btn');
        if (rrLogout) rrLogout.addEventListener('click', adminLogout);
        var rrPick = document.getElementById('rr-pick-btn');
        if (rrPick) rrPick.addEventListener('click', startPickLocation);
        var rrForm = document.getElementById('road-report-form');
        if (rrForm) rrForm.addEventListener('submit', handleRoadReportSubmit);

        // 管理员登录弹窗
        var adminLoginCancel = document.getElementById('admin-login-cancel');
        if (adminLoginCancel) adminLoginCancel.addEventListener('click', closeAdminLogin);
        var adminLoginForm = document.getElementById('admin-login-form');
        if (adminLoginForm) adminLoginForm.addEventListener('submit', handleAdminLogin);

        // 快捷键帮助弹窗关闭按钮
        var kbdHelpClose = document.getElementById('kbd-help-close');
        if (kbdHelpClose) {
            kbdHelpClose.addEventListener('click', hideKbdHelp);
        }

        // 全局键盘快捷键
        document.addEventListener('keydown', handleGlobalKeydown);

        // 底部快捷键提示条：点击 kbd 直接执行对应动作（不依赖焦点，鼠标可用）
        document.querySelectorAll('#kbd-hint-bar kbd[data-kbd]').forEach(function (k) {
            k.addEventListener('click', function () {
                var action = k.getAttribute('data-kbd');
                var active = document.activeElement;
                if (active && active.id !== 'nl-input') active.blur();
                if (action.indexOf('mode:') === 0) {
                    triggerTravelMode(action.slice(5));
                } else if (action.indexOf('chip:') === 0) {
                    triggerChip(action.slice(5), k);
                } else if (action === 'reset') {
                    flashButton(k);
                    handleReset();
                } else if (action === 'focus') {
                    var inp = document.getElementById('nl-input');
                    if (inp) inp.focus();
                } else if (action === 'help') {
                    showKbdHelp();
                }
            });
        });
    }

    function showWelcomeHint() {
        var welcomeBubble = document.getElementById('welcome-bubble');
        var shortcutCards = document.getElementById('shortcut-cards-row');
        if (welcomeBubble) welcomeBubble.style.display = '';
        if (shortcutCards) shortcutCards.style.display = '';
        var results = document.getElementById('results-section');
        if (results) results.hidden = true;
        var sa = document.getElementById('suggestions-area');
        if (sa) sa.hidden = true;
    }

    function restoreRecentBubbles() {
        var history = state.conversationHistory;
        if (!history || history.length < 2) return;
        var chatContent = document.getElementById('chat-content');
        if (!chatContent) return;
        // 有历史对话：隐藏欢迎引导
        hideWelcomeElements();
        // 最近 3 轮（6 条消息）
        var recent = history.slice(-6);
        recent.forEach(function (msg) {
            if (msg.role === 'user') {
                showUserBubble(msg.content);
            } else {
                showChatBubble('', msg.content || '');
            }
        });
    }

    function init() {
        state.sessionId = generateSessionId();
        loadContext();
        loadTravelMode();  // 读取持久化的出行方式偏好（非法值回退 walk）
        bindEvents();
        syncTravelModeUI();  // 同步选择器选中态 / 图例 / 驾车隐藏平坦 chip
        showWelcomeHint();
        initMap();
        restoreRecentBubbles();

        // 暴露公开函数给欢迎卡片等模块调用
        window.submitNaturalLanguageQuery = handleNlSubmit;

        // 页面加载自动定位（延迟 1.5s 等地图 + AMap SDK 就绪，静默失败不打扰用户）
        setTimeout(autoLocateSilent, 1500);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
/* ========================================================================
   珞珈智行 · 冷启动欢迎卡片（方案 1）交互逻辑
   · Append-only IIFE，命名空间：window.WelcomeColdStart
   · 不修改任何原有函数、不覆盖原有全局变量
   · 所有事件绑定使用事件委托 + once/debounce
   · Fail-soft：任何错误不影响原 app.js 核心功能
   ======================================================================== */
(function () {
    'use strict';

    // =============== 常量配置（对应 spec 所有硬编码数字，改一处全联动） ===============
    var LS_KEY_SEEN = 'whu_welcome_seen';
    var LS_KEY_FOREVER = 'whu_welcome_dont_show_forever';
    var DEBOUNCE_MS = 1500; // shortcut 连点防抖窗口（对应 T-017 14.11：1.5s 内连点只发第一次）
    var AUTO_SHOW_DELAY_MS = 150; // DOMContentLoaded 后延迟弹卡（不阻塞首屏 Leaflet 地图加载）
    var HIGHLIGHT_PULSE_CLASS = 'highlight';
    var SIDEBAR_HIGHLIGHT_CLASS = 'flash-highlight';
    var ANIM_OUT_DURATION_MS = 200; // 退场动画总时长（略长于 CSS 180ms，保险）
    var INPUT_ID = 'nl-input'; // 输入框 id（如果前几轮不是这个名字，JS 会自动降级找第一个 input[type=text]/textarea，不崩）

    // =============== 内部状态（闭包私有，不暴露） ===============
    var sessionWelcomeShown = false; // localStorage 不可用时的内存兜底（仅当前标签页）
    var lastShortcutAt = 0; // shortcut 防抖时间戳
    var animating = false; // 防止入场退场动画叠加 + 连续点 X 和 start 造成重复关

    // =============== DOM 引用（缓存一次，懒初始化） ===============
    var $overlay = null;
    var $card = null;
    var $helpBtn = null;
    var $shortcuts = null;
    var $input = null;
    var $resultArea = null;
    var $sidebar = null;
    var $dontShowCheckbox = null;

    function initDomRefs() {
        $overlay = document.getElementById('welcome-overlay');
        $card = $overlay ? $overlay.querySelector('.welcome-card') : null;
        $helpBtn = document.getElementById('help-btn');
        $shortcuts = $overlay ? $overlay.querySelector('.welcome-shortcuts') : null;
        // 输入框：先找 INPUT_ID，找不到就降级找 input[type=text]，再找不到找 textarea（fail-soft）
        $input = document.getElementById(INPUT_ID)
            || document.querySelector('input[type="text"]')
            || document.querySelector('textarea');
        // 结果区：先找 id=result-area，再找 id=route-result
        $resultArea = document.getElementById('result-area') || document.getElementById('route-result');
        // 侧边栏：先找 .poi-sidebar，再找 #poi-sidebar
        $sidebar = document.querySelector('.poi-sidebar') || document.getElementById('poi-sidebar');
        $dontShowCheckbox = document.getElementById('welcome_dont_show');
        // 只要 overlay/helpbtn/shortcuts 三大件在就继续，其他 DOM（input/sidebar/resultArea）找不到没关系，用降级分支
        return !!($overlay && $card && $helpBtn && $shortcuts);
    }

    // =============== localStorage 工具（安全封装，任何抛错自动降级，fail-soft 核心！） ===============
    function safeLsGet(key) {
        try { return window.localStorage.getItem(key); }
        catch (e) { return null; } // 隐私模式/QuotaExceededError 直接当没存
    }
    function safeLsSet(key, value) {
        try { window.localStorage.setItem(key, value); return true; }
        catch (e) { return false; } // 失败就当没写，用内存变量兜底
    }

    // =============== 公开方法：show / close / isOpen ===============
    function show(options) {
        options = options || {};
        var ignoreLs = !!options.ignoreLocalStorage;

        if (!$overlay) return; // DOM 不存在直接静默 fail-soft
        if (animating) return; // 动画中不响应，避免叠图

        // 若卡片已显示：？按钮唤回高亮脉冲（E-07：点了没反应的焦虑 → 给视觉反馈）
        if (isOpen()) {
            $overlay.classList.remove(HIGHLIGHT_PULSE_CLASS);
            // 强制 reflow 重启动画（防止浏览器把两次 class 操作合并）
            void $overlay.offsetWidth;
            $overlay.classList.add(HIGHLIGHT_PULSE_CLASS);
            setTimeout(function () {
                if ($overlay) $overlay.classList.remove(HIGHLIGHT_PULSE_CLASS);
            }, 650); // 动画 600ms + 50ms buffer
            return;
        }

        animating = true;

        // 显示（先移除 hidden 才能播动画）
        $overlay.classList.remove('hidden');
        $overlay.setAttribute('aria-hidden', 'false');

        // 触发入场动画（下一帧再加 .opening，确保 CSS transition 生效，老浏览器不会白屏）
        if (window.requestAnimationFrame) {
            window.requestAnimationFrame(function () {
                if ($overlay) $overlay.classList.add('opening');
            });
        } else {
            // 老浏览器无 rAF，直接加（fail-soft，无动画也能显示）
            setTimeout(function () { if ($overlay) $overlay.classList.add('opening'); }, 16);
        }

        // 动画结束清理（最长 400ms：卡片 280ms + stagger 300ms buffer）
        setTimeout(function () {
            animating = false;
            if ($overlay) $overlay.classList.remove('opening');
        }, 420);

        // 如果不是 ignoreLocalStorage 模式（= 非 ?按钮手动唤回），先标记内存兜底 seen，避免隐私模式闪弹
        if (!ignoreLs) {
            sessionWelcomeShown = true;
        }
    }

    function close(options) {
        options = options || {};
        var markSeen = options.markSeen !== false; // 默认 true：除了调试外都写 seen

        if (!$overlay) return;
        if (animating) return;
        if (!isOpen()) return;

        animating = true;

        // 1. 先写 localStorage（如果需要）
        if (markSeen) {
            safeLsSet(LS_KEY_SEEN, 'true');
            sessionWelcomeShown = true;
            // 如果勾选了"以后不再显示"，再写 forever 标记
            if ($dontShowCheckbox && $dontShowCheckbox.checked) {
                safeLsSet(LS_KEY_FOREVER, 'true');
            }
        }

        // 2. 触发退场动画
        $overlay.classList.add('closing');

        // 3. 动画结束切 .hidden（ANIM_OUT_DURATION_MS 200ms）
        setTimeout(function () {
            animating = false;
            if ($overlay) {
                $overlay.classList.remove('closing');
                $overlay.classList.add('hidden');
                $overlay.setAttribute('aria-hidden', 'true');
            }
        }, ANIM_OUT_DURATION_MS);
    }

    function isOpen() {
        return !!($overlay
            && !$overlay.classList.contains('hidden')
            && !$overlay.classList.contains('closing'));
    }

    // =============== Shortcut-card 数据流分发（3 种 action 走完全不同链路） ===============
    function handleShortcutClick(e) {
        var card = e.target.closest('.shortcut-card');
        if (!card || !($shortcuts && $shortcuts.contains(card))) return;

        // 防抖：DEBOUNCE_MS 1.5s 内只跑第一次（T-017 14.11）
        var now = Date.now();
        if (now - lastShortcutAt < DEBOUNCE_MS) {
            e.preventDefault();
            return;
        }
        lastShortcutAt = now;

        var action = card.getAttribute('data-action');
        var displayText = card.getAttribute('data-display-text') || '';

        // 先填输入框（模拟用户手输，占位 + 方便改 + 多轮上下文继承）
        if ($input && displayText) {
            $input.value = displayText;
        }

        // 分发：所有 action 统一走 Agent（自然语言 query → /api/chat）
        switch (action) {
            case 'path_planning':
            case 'poi_query':
            case 'recommend_poi':
                // 统一复用 handleNlSubmit（走 Agent 循环，含多轮上下文）
                close({ markSeen: true });
                setTimeout(function () {
                    if (typeof window.submitNaturalLanguageQuery === 'function') {
                        window.submitNaturalLanguageQuery(displayText);
                    } else if ($input) {
                        var ev;
                        if (typeof KeyboardEvent === 'function') {
                            ev = new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', which: 13, keyCode: 13, bubbles: true });
                        } else {
                            ev = document.createEvent('Event');
                            ev.initEvent('keydown', true, true);
                            ev.key = 'Enter'; ev.which = 13; ev.keyCode = 13;
                        }
                        $input.dispatchEvent(ev);
                    }
                    if ($resultArea && typeof $resultArea.scrollIntoView === 'function') {
                        try { $resultArea.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) { $resultArea.scrollIntoView(); }
                    }
                }, ANIM_OUT_DURATION_MS + 20);
                break;

            default:
                close({ markSeen: true });
        }
    }

    // =============== 事件绑定（全部挂在 overlay 级别，防止 DOM 变更后失效） ===============
    function bindEvents() {
        // ① 关闭按钮统一事件委托（X / mask / 开始使用 —— 所有带 data-close-welcome 的）
        $overlay.addEventListener('click', function (e) {
            var closer = e.target.closest('[data-close-welcome="true"]');
            if (closer) {
                e.preventDefault();
                close({ markSeen: true });
            }
        });
        // ①+ 关键！点卡片内部区域必须阻止冒泡到 mask，否则点哪里都关（最常见 bug，Constraint 6）
        // 但 data-close-welcome 元素（X / 开始使用）在卡片内部，必须允许冒泡到 overlay 触发关闭
        $card.addEventListener('click', function (e) {
            if (e.target.closest('[data-close-welcome="true"]')) {
                return; // 关闭按钮不阻止冒泡，让 overlay 的委托监听器处理
            }
            e.stopPropagation();
        });

        // ② 键盘 Esc 关（仅在卡片显示时有效，不影响输入框；T-017 14.13：Esc 不关 input 内容 —— 这里只关卡，不动 input.value，所以天然满足）
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && isOpen()) {
                close({ markSeen: true });
            }
        });

        // ③ ？帮助按钮唤回（强制 ignoreLocalStorage，不写任何 LS 标记）
        $helpBtn.addEventListener('click', function () {
            show({ ignoreLocalStorage: true });
        });

        // ④ shortcut-card 点击数据流分发（事件委托挂在 .welcome-shortcuts 上，后续加卡片只要加 HTML 不用改 JS）
        $shortcuts.addEventListener('click', handleShortcutClick);
    }

    // =============== 自动弹出判断（DOMContentLoaded 后调用，G-03 老用户不打扰） ===============
    function shouldAutoShow() {
        // 优先级：forever（用户明确不要）→ ls_seen（看过一次）→ session_seen（内存兜底）→ 默认弹
        if (safeLsGet(LS_KEY_FOREVER) === 'true') return false;
        if (safeLsGet(LS_KEY_SEEN) === 'true') return false;
        if (sessionWelcomeShown) return false;
        return true;
    }

    // =============== 初始化入口（fail-soft 第一关：三大件 DOM 找不到就静默 return，不影响原功能） ===============
    function init() {
        if (!initDomRefs()) {
            // DOM 没加（Task 2 没合并 / 或用户没加欢迎卡片 DOM）→ 直接 return，静默降级不报错
            return;
        }
        bindEvents();

        // 按判断结果决定是否自动弹卡
        if (shouldAutoShow()) {
            // 延迟 AUTO_SHOW_DELAY_MS 再弹：避免卡首屏 Leaflet 地图的加载（AUTO_SHOW_DELAY_MS = 150ms）
            setTimeout(function () { show({ ignoreLocalStorage: false }); }, AUTO_SHOW_DELAY_MS);
        }

        // 公开命名空间到 window（供 ?按钮调试 / 测试脚本用 / 后续多轮上下文用）
        window.WelcomeColdStart = {
            show: show,
            close: close,
            isOpen: isOpen,
            shouldAutoShow: shouldAutoShow,
            // 方便测试/调试的工具方法（清 localStorage，测试自动弹卡）
            _debugClearLs: function () {
                try {
                    window.localStorage.removeItem(LS_KEY_SEEN);
                    window.localStorage.removeItem(LS_KEY_FOREVER);
                } catch (e) { /* 忽略 */ }
                sessionWelcomeShown = false;
            }
        };
    }

    // =============== 挂载到 DOMContentLoaded 或立即执行（DOM 已就绪的情况） ===============
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        // DOM 已经解析完了（比如 app.js 被异步 defer/async 加载，或者动态插入）→ 等一帧再 init 稳一点
        if (window.requestAnimationFrame) {
            window.requestAnimationFrame(init);
        } else {
            setTimeout(init, 16);
        }
    }
})();
/* ========================================================================
   珞珈智行 · T-024 快捷按钮权重优先级（前端高亮 + 清除逻辑）
   · Append-only IIFE，不修改任何原有函数
   · 高亮类名：.shortcut-active
   · 清除触发：收到 /api/parse 或 /api/chat 返回 weights != null 且 input_method != shortcut 时
   ======================================================================== */
(function () {
    'use strict';

    var SHORTCUT_ACTIVE_CLASS = 'shortcut-active';
    var SHORTCUT_BTN_SELECTOR = '.shortcut-btn';

    // =============== 公开函数（挂 window，供测试/外部调用） ===============
    function addShortcutHighlight(btn) {
        if (!btn) return;
        // 先清除所有按钮高亮（单选行为）
        clearShortcutHighlight();
        btn.classList.add(SHORTCUT_ACTIVE_CLASS);
        // 兼容原代码同时也加 .active（防止其他逻辑依赖 .active）
        btn.classList.add('active');
    }

    function clearShortcutHighlight() {
        var btns = document.querySelectorAll(SHORTCUT_BTN_SELECTOR);
        btns.forEach(function (b) {
            b.classList.remove(SHORTCUT_ACTIVE_CLASS);
            b.classList.remove('active');
        });
    }

    window.addShortcutHighlight = addShortcutHighlight;
    window.clearShortcutHighlight = clearShortcutHighlight;

    // =============== 事件增强：点击快捷按钮即高亮（捕获阶段绑定，append-only 不影响原监听） ===============
    document.addEventListener('click', function (e) {
        var btn = e.target.closest(SHORTCUT_BTN_SELECTOR);
        if (btn) {
            addShortcutHighlight(btn);
        }
    }, true);

    // =============== Fetch 猴子补丁：拦截响应后判断是否清除高亮 ===============
    var _origFetch = window.fetch;
    if (typeof _origFetch === 'function') {
        window.fetch = function (url, options) {
            var promise = _origFetch.apply(this, arguments);
            // 只对本域 API 感兴趣
            var urlStr = String(url);
            var isParseApi = urlStr.indexOf('/api/parse') !== -1;
            var isChatApi = urlStr.indexOf('/api/chat') !== -1;
            if (!isParseApi && !isChatApi) {
                return promise;
            }
            // 克隆响应体，确保原逻辑也能拿到完整数据
            return promise.then(function (response) {
                var cloned = response.clone();
                cloned.json().then(function (payload) {
                    var data = payload && (payload.data || payload);
                    if (!data) return;
                    var weights = data.weights;
                    var inputMethod = data.input_method;
                    // 核心判断：weights != null 且 input_method !== shortcut → 清除高亮
                    // 含义：NL 解析出了显式偏好（LLM 解析权重），快捷按钮预设不再是当前偏好来源
                    if (weights !== null && weights !== undefined && inputMethod !== 'shortcut') {
                        clearShortcutHighlight();
                    }
                    // input_method=shortcut 时保留高亮（说明当前就是快捷按钮生效）
                }).catch(function () {
                    // JSON 解析失败静默忽略，不影响原逻辑
                });
                return response;
            });
        };
    }
})();
/* ========================================================================
   珞珈智行 · T-022 场景 3 候选 POI 交互卡片（候选点选择闭环）
   · Append-only IIFE，命名空间：window.showCandidateCards
   · 不修改任何原有函数、不覆盖原有全局变量
   · 卡片点击 → 自动触发路线规划（复用现有提交流程）
   · Fail-soft：任何错误不影响原 app.js 核心功能
   ======================================================================== */
(function () {
    'use strict';

    var CONTAINER_ID = 'candidates-panel';
    var BRAND_PINK = '#E8929C';
    var BRAND_GOLD = '#D4915C';

    // =============== 内部状态 ===============
    var currentStartName = '';
    var $container = null;

    // =============== 工具函数 ===============
    function escapeHTML(str) {
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
    }

    function renderStars(score) {
        score = Math.max(0, Math.min(5, parseInt(score, 10) || 3));
        var filled = '';
        var empty = '';
        for (var i = 0; i < score; i++) { filled += '★'; }
        for (var i = score; i < 5; i++) { empty += '☆'; }
        return '<span style="color:' + BRAND_GOLD + ';letter-spacing:1px;">' + filled + '</span>'
            + '<span style="color:#CCC;letter-spacing:1px;">' + empty + '</span>';
    }

    function typeBadgeHTML(poiType) {
        var labelMap = { scenery: '赏景', landmark: '地标', study: '学习', gate: '校门', dining: '食堂', service: '服务' };
        var colorMap = {
            scenery: '#E8929C', landmark: '#D4915C', study: '#6A9FB5',
            gate: '#8B7E74', dining: '#C79A63', service: '#7BA37B'
        };
        var label = labelMap[poiType] || poiType || 'POI';
        var bg = colorMap[poiType] || '#A8A5A2';
        return '<span style="display:inline-block;background:' + bg + ';color:#FFF;font-size:11px;'
            + 'padding:2px 8px;border-radius:10px;margin-left:6px;">' + escapeHTML(label) + '</span>';
    }

    // =============== 创建容器 DOM ===============
    function ensureContainer() {
        var el = document.getElementById(CONTAINER_ID);
        if (el) {
            el.innerHTML = '';
            return el;
        }
        el = document.createElement('div');
        el.id = CONTAINER_ID;
        el.style.cssText =
            'margin:16px 20px;padding:0;font-family:"Noto Serif SC","PingFang SC","Microsoft YaHei",serif;';

        var resultsSec = document.getElementById('results-section');
        if (resultsSec && resultsSec.parentNode) {
            resultsSec.parentNode.insertBefore(el, resultsSec);
        } else {
            var inputSec = document.querySelector('.input-section');
            if (inputSec && inputSec.parentNode) {
                inputSec.parentNode.insertBefore(el, inputSec.nextSibling);
            } else {
                var mainEl = document.querySelector('.app-main') || document.querySelector('main');
                if (mainEl) {
                    mainEl.appendChild(el);
                } else {
                    document.body.appendChild(el);
                }
            }
        }
        return el;
    }

    // =============== 候选 POI 点击处理 ===============
    function onCandidateClick(candidateName) {
        var cards = $container ? $container.querySelectorAll('.candidate-card') : [];
        cards.forEach(function (card) {
            card.style.boxShadow = '';
            card.style.transform = '';
        });

        var query = '从' + currentStartName + '到' + candidateName;
        var input = document.getElementById('nl-input')
            || document.querySelector('input[type="text"]')
            || document.querySelector('textarea');

        if (input) {
            input.value = query;
        }

        var submitBtn = document.getElementById('submit-btn');
        if (submitBtn) {
            setTimeout(function () {
                submitBtn.click();
            }, 80);
        } else {
            if (input) {
                var ev;
                if (typeof KeyboardEvent === 'function') {
                    ev = new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', which: 13, keyCode: 13, bubbles: true });
                } else {
                    ev = document.createEvent('Event');
                    ev.initEvent('keydown', true, true);
                    ev.key = 'Enter'; ev.which = 13; ev.keyCode = 13;
                }
                input.dispatchEvent(ev);
            }
        }

        if ($container) {
            $container.style.transition = 'opacity 0.4s ease';
            $container.style.opacity = '0.3';
            setTimeout(function () {
                if ($container) {
                    $container.innerHTML = '';
                    $container.style.opacity = '1';
                }
            }, 1200);
        }
    }

    // =============== 渲染候选卡片 ===============
    function renderCards(candidates, startName) {
        $container = ensureContainer();
        currentStartName = startName || '';

        if (!candidates || candidates.length === 0) {
            $container.innerHTML =
                '<div style="background:#FDF0F2;border:1px dashed ' + BRAND_PINK + ';border-radius:12px;'
                + 'padding:20px 24px;text-align:center;color:#C76B7A;font-size:14px;">'
                + '🔍 未找到匹配的候选 POI，'
                + '请尝试其他关键词或类型。'
                + '</div>';
            return;
        }

        var html =
            '<div style="margin-bottom:10px;display:flex;align-items:center;gap:8px;">'
            + '<span style="font-size:13px;color:#A8A5A2;font-weight:500;">📍 候选目的地'
            + '（点击卡片自动规划路线）</span>'
            + '<span style="font-size:11px;color:#CCC;">— 从 '
            + escapeHTML(currentStartName) + ' 出发</span>'
            + '</div>'
            + '<div style="display:flex;flex-wrap:wrap;gap:10px;">';

        candidates.forEach(function (c) {
            var starsHTML = renderStars(c.scenery_score || 3);
            var badgeHTML = typeBadgeHTML(c.type || 'landmark');
            var distText = (c.distance_m != null) ? (c.distance_m >= 1000
                ? (c.distance_m / 1000).toFixed(1) + ' km'
                : Math.round(c.distance_m) + ' m') : '—';
            var desc = (c.description || '').substring(0, 60);

            html +=
                '<div class="candidate-card" data-candidate-name="' + escapeHTML(c.name) + '"'
                + ' style="flex:1 1 200px;min-width:180px;max-width:280px;'
                + 'background:#FFF;border:1.5px solid #F0EDE8;border-radius:14px;'
                + 'padding:14px 16px;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,0.06);'
                + 'transition:all 0.2s ease;position:relative;overflow:hidden;'
                + 'box-sizing:border-box;"'
                + ' onmouseenter="this.style.boxShadow=\'0 4px 16px rgba(232,146,156,0.25)\';'
                + 'this.style.borderColor=\'' + BRAND_PINK + '\';this.style.transform=\'translateY(-2px)\';"'
                + ' onmouseleave="this.style.boxShadow=\'0 2px 8px rgba(0,0,0,0.06)\';'
                + 'this.style.borderColor=\'#F0EDE8\';this.style.transform=\'\';">'
                + '<div style="position:absolute;left:0;top:0;bottom:0;width:4px;'
                + 'background:linear-gradient(180deg,' + BRAND_PINK + ',' + BRAND_GOLD + ');'
                + 'border-radius:4px 0 0 4px;"></div>'
                + '<div style="padding-left:8px;">'
                + '<div style="font-size:15px;font-weight:600;color:#3D322B;'
                + 'margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
                + escapeHTML(c.name) + badgeHTML + '</div>'
                + '<div style="font-size:12px;margin-bottom:4px;">'
                + '<span style="color:#A8A5A2;">景色 </span>' + starsHTML
                + '<span style="color:#A8A5A2;margin-left:4px;font-size:11px;">'
                + (c.scenery_score || 3) + '/5</span>'
                + '</div>'
                + '<div style="font-size:12px;color:#8B7E74;margin-bottom:4px;">'
                + '📏 路网距离：' + distText + '</div>'
                + (desc
                    ? '<div style="font-size:11px;color:#B5B0AB;line-height:1.4;">'
                    + escapeHTML(desc) + (c.description && c.description.length > 60 ? '...' : '')
                    + '</div>'
                    : '')
                + '</div>'
                + '</div>';
        });

        html += '</div>';
        $container.innerHTML = html;

        $container.addEventListener('click', function (e) {
            var card = e.target.closest('.candidate-card');
            if (!card) return;
            var name = card.getAttribute('data-candidate-name');
            if (name) {
                onCandidateClick(name);
            }
        });
    }

    // =============== 公开命名空间 ===============
    window.showCandidateCards = function (candidates, startName) {
        try {
            renderCards(candidates, startName);
        } catch (e) {
            console.warn('showCandidateCards 渲染失败:', e);
        }
    };

    window.hideCandidateCards = function () {
        try {
            var el = document.getElementById(CONTAINER_ID);
            if (el) {
                el.innerHTML = '';
            }
        } catch (e) { /* 静默忽略 */ }
    };
})();
/* =============== PWA 安装引导（独立模块） =============== */
(function () {
    var LS_DISMISS = 'whu_walker:install_dismissed_at';
    var deferredPrompt = null;
    var banner = null, btn = null, sub = null;

    function isStandalone() {
        return (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches)
            || window.navigator.standalone === true;
    }
    function isIos() {
        return /iphone|ipad|ipod/i.test(navigator.userAgent)
            || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    }
    function recentlyDismissed() {
        try {
            var t = parseInt(localStorage.getItem(LS_DISMISS) || '0', 10);
            return t && (Date.now() - t < 30 * 24 * 3600 * 1000); // 30 天内不再打扰
        } catch (e) { return false; }
    }
    function showBanner(text) {
        if (isStandalone() || recentlyDismissed() || !banner) return;
        if (text && sub) sub.textContent = text;
        banner.hidden = false;
    }
    function hideBanner(persist) {
        if (banner) banner.hidden = true;
        if (persist) {
            try { localStorage.setItem(LS_DISMISS, String(Date.now())); } catch (e) {}
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        banner = document.getElementById('install-banner');
        btn = document.getElementById('install-btn');
        sub = document.getElementById('install-banner-sub');
        if (!banner) return;

        document.getElementById('install-dismiss').addEventListener('click', function () {
            hideBanner(true);
        });
        btn.addEventListener('click', function () {
            if (deferredPrompt) {
                deferredPrompt.prompt();
                deferredPrompt.userChoice.then(function () {
                    deferredPrompt = null;
                    hideBanner(true);
                });
            } else if (isIos()) {
                // iOS 无原生弹窗：展开文字步骤
                if (sub) sub.textContent = '点底部分享图标「□↑」→ 选「添加到主屏幕」';
                btn.textContent = '知道了';
                btn.addEventListener('click', function () { hideBanner(true); }, { once: true });
            }
        });

        // Android / Chrome / Edge：捕获系统安装事件
        window.addEventListener('beforeinstallprompt', function (e) {
            e.preventDefault();
            deferredPrompt = e;
            // 延迟一会儿，避免首屏就弹
            setTimeout(function () { showBanner(); }, 2500);
        });
        window.addEventListener('appinstalled', function () { hideBanner(true); });

        // iOS Safari：手动引导（仅非 standalone 且首次）
        if (isIos() && !isStandalone()) {
            setTimeout(function () {
                showBanner('点底部分享图标「□↑」→「添加到主屏幕」');
            }, 3000);
        }
    });
})();
