(function () {
    'use strict';

    var CFG = (function () {
        var fallback = {
            AMAP_KEY: '',
            API_BASE_URL: '',
            DEFAULT_CENTER: [114.3630, 30.5365],
            MAP_ZOOM: 16,
        };
        var w = window.WHU_WALKER_CONFIG || {};
        return {
            AMAP_KEY: w.AMAP_KEY || w.amapKey || fallback.AMAP_KEY,
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

    var state = {
        map: null,
        recommendedLine: null,
        shortestLine: null,
        poiMarkers: [],
        loading: false,
        sessionId: null,
        conversationHistory: [],
        activeMode: null,
        loadingTimer: null,  // 轮播加载语定时器
    };

    // ========== 轮播加载语（有人味儿） ==========
    var LOADING_MESSAGES = [
        { text: '正在理解你的需求…', sub: '嗯，让我想想怎么走最好' },
        { text: '正在查地图…', sub: '珞珈山的路我都熟' },
        { text: '正在计算最佳路线…', sub: '帮你避开那些不好走的路' },
        { text: '正在找沿途的好风景…', sub: '这条路樱花季特别美' },
        { text: '快好了…', sub: '稍等一下下' },
    ];

    function startLoadingMessages() {
        var idx = 0;
        var textEl = document.getElementById('loading-text');
        var subEl = document.getElementById('loading-subtext');
        showLoading(LOADING_MESSAGES[0].text, LOADING_MESSAGES[0].sub);

        state.loadingTimer = setInterval(function () {
            idx = (idx + 1) % LOADING_MESSAGES.length;
            var msg = LOADING_MESSAGES[idx];
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
                state.conversationHistory = JSON.parse(raw);
            }
        } catch (e) {
            state.conversationHistory = [];
        }
    }

    function saveContext() {
        try {
            localStorage.setItem(getContextKey(), JSON.stringify(state.conversationHistory.slice(-3)));
        } catch (e) {}
    }

    function addConversationTurn(query, result) {
        state.conversationHistory.push({
            query: query,
            parse_result: {
                constraints: result.constraints,
                weights: result.weights,
            },
            timestamp: Date.now(),
        });
        if (state.conversationHistory.length > 10) {
            state.conversationHistory = state.conversationHistory.slice(-10);
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

    function initMap() {
        if (typeof AMap === 'undefined') {
            setTimeout(initMap, 300);
            return;
        }

        if (state.map) return;

        var placeholder = document.getElementById('map-placeholder');
        if (placeholder) placeholder.style.display = 'none';

        try {
            state.map = new AMap.Map('map-container', {
                zoom: MAP_ZOOM,
                center: [MAP_CENTER[0], MAP_CENTER[1]],
                mapStyle: 'amap://styles/whitesar',
                viewMode: '2D',
            });

            state.map.addControl(new AMap.Scale());
            state.map.addControl(new AMap.ToolBar({ position: 'RB' }));
        } catch (e) {
            console.error('地图初始化失败:', e);
            showError('地图加载失败', '无法初始化地图组件，请刷新页面重试');
        }
    }

    function renderRoute(routeData) {
        if (!state.map) return;

        clearMap();

        var recommended = routeData.recommended || [];
        var shortest = routeData.shortest || [];

        if (recommended.length > 0) {
            var recPath = toGcj02Path(recommended.map(function (c) {
                return { lng: c.lng, lat: c.lat };
            }));

            state.recommendedLine = new AMap.Polyline({
                path: recPath,
                strokeColor: '#E8929C',
                strokeWeight: 6,
                strokeOpacity: 0.85,
                strokeStyle: 'solid',
                lineJoin: 'round',
                lineCap: 'round',
                zIndex: 50,
            });
            state.recommendedLine.setMap(state.map);
        }

        if (shortest.length > 0) {
            var shortPath = toGcj02Path(shortest.map(function (c) {
                return { lng: c.lng, lat: c.lat };
            }));

            state.shortestLine = new AMap.Polyline({
                path: shortPath,
                strokeColor: '#B5B0AB',
                strokeWeight: 4,
                strokeOpacity: 0.6,
                strokeStyle: 'dashed',
                lineJoin: 'round',
                lineCap: 'round',
                zIndex: 40,
            });
            state.shortestLine.setMap(state.map);
        }

        var pois = routeData.pois || [];
        pois.forEach(function (poi) {
            var lng = poi.lng || poi.lon;
            var lat = poi.lat;
            if (lng == null || lat == null) return;

            // POI 坐标在 data/pois.json 中存储为 GCJ-02，无需转换
            var marker = new AMap.Marker({
                position: [lng, lat],
                title: poi.name || '',
                content: '<div style="background:#D4915C;color:white;padding:2px 8px;border-radius:10px;font-size:11px;box-shadow:0 2px 6px rgba(0,0,0,0.2);">' +
                    (poi.name || 'POI') + '</div>',
                zIndex: 100,
            });
            marker.setMap(state.map);
            state.poiMarkers.push(marker);
        });

        var allOverlays = [];
        if (state.recommendedLine) allOverlays.push(state.recommendedLine);
        if (state.shortestLine) allOverlays.push(state.shortestLine);
        state.poiMarkers.forEach(function (m) { allOverlays.push(m); });

        if (allOverlays.length > 0) {
            state.map.setFitView(allOverlays, false, [40, 40, 40, 40]);
        }
    }

    function clearMap() {
        if (state.recommendedLine) {
            state.recommendedLine.setMap(null);
            state.recommendedLine = null;
        }
        if (state.shortestLine) {
            state.shortestLine.setMap(null);
            state.shortestLine = null;
        }
        state.poiMarkers.forEach(function (m) { m.setMap(null); });
        state.poiMarkers = [];
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
        document.getElementById('shortest-distance').textContent =
            (data.shortest_length_m || data.shortest_distance_m || 0).toFixed(0) + ' m';
        document.getElementById('overlap-rate').textContent =
            ((data.overlap_rate || 0) * 100).toFixed(0) + '%';

        var poiList = document.getElementById('poi-items');
        poiList.innerHTML = '';
        var pois = data.pois || [];
        if (pois.length === 0) {
            poiList.innerHTML = '<li style="background:#FAF8F5;color:#A8A5A2;">暂无途经景点</li>';
        } else {
            pois.forEach(function (poi) {
                var li = document.createElement('li');
                li.textContent = poi.name || poi.id || '未知';
                poiList.appendChild(li);
            });
        }

        var explanation = data.explanation || '已为您规划好路线';
        document.getElementById('explanation-text').textContent = explanation;

        // 跟进建议
        showSuggestions(data);

        // 自动滚到结果区
        var chatContent = document.getElementById('chat-content');
        if (chatContent) {
            setTimeout(function () {
                chatContent.scrollTo({ top: chatContent.scrollHeight, behavior: 'smooth' });
            }, 100);
        }
    }

    function showSuggestions(data) {
        var area = document.getElementById('suggestions-area');
        var chips = document.getElementById('suggestions-chips');
        if (!area || !chips) return;

        var suggestions = buildSuggestions(data);
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

        // 如果没避开陡坡 → 建议避开
        if (constraints.slope !== 'avoid') {
            suggestions.push({ label: '🪜 走更平坦的路', query: '帮我找一条更平坦的路线' });
        }
        // 如果没优先风景 → 建议看风景
        if (constraints.scenery !== 'high') {
            suggestions.push({ label: '🌸 想看风景好的路', query: '走风景更好的路线' });
        }
        // 如果推荐比最短长不少 → 建议最短
        if (recLen > shortLen * 1.3 && constraints.distance !== 'short') {
            suggestions.push({ label: '⚡ 我要最短路径', query: '帮我规划最短路径' });
        }
        // 总有一条默认
        if (suggestions.length === 0) {
            suggestions.push({ label: '🪜 换条更平坦的', query: '换一条更平坦的路线' });
            suggestions.push({ label: '🌸 换条风景更好的', query: '换一条风景更好的路线' });
        }
        // 最多 4 条
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

    async function apiRequest(endpoint, data) {
        var url = API_BASE + endpoint;
        var options = {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        };

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
            throw new Error(userMsg);
        }

        return result.data || result;
    }

    function mapError(code, msg) {
        var errorMap = {
            'missing_query': '嗯？你还没告诉我你想去哪呢～试试输入「从牌坊到樱顶」',
            'missing_endpoints': '需要起点和终点才能规划路线哦，在地图上选点或者打字告诉我吧',
            'missing_poi_names': '起点和终点得有个名字才行～',
            'poi_not_found': '抱歉，我没找到这个地方😅 试试换个说法？比如「教五」就是「第五教学楼」',
            'route_not_found': '这条路线走不通…可能是路网数据还不够全，试试换个目的地？',
            'network_not_initialized': '地图还没加载完，稍等一下下就好～',
            'network_load_failed': '地图数据加载失败了，刷新一下页面试试？',
            'parse_failed': '我没太理解你的意思…试试简单一点的说法，比如「从牌坊到樱顶」',
            'parse_validation_error': '输入格式有点问题，试试更简洁的描述？',
            'route_computation_failed': '路线计算出错了，可能是网络不太好，再试一次？',
            'nearest_node_failed': '这个位置我没法定位，换个附近的地点试试？',
            'unsupported_task': '这个功能我暂时还不会，试试问路或者查景点吧～',
            'internal_error': '出了点小问题，稍等一下再试就好',
        };
        return errorMap[code] || msg || '出了点意外，再试一次吧';
    }

    async function handleNlSubmit(query) {
        hideError();
        startLoadingMessages();

        try {
            var context = state.conversationHistory.length > 0
                ? state.conversationHistory
                : null;

            var result = await apiRequest('/api/chat', {
                query: query,
                context: context,
            });

            var taskType = result.task_type;

            // chat → 在对话流中显示闲聊回复
            if (taskType === 'chat') {
                hideWelcomeElements();
                showChatBubble(query, result.reply || result.message || '嗯…这个问题有点难，换个问法试试？');
                addConversationTurn(query, result);
                stopLoadingMessages();
                hideLoading();
                return;
            }

            // help / unknown → 显示引导消息
            if (taskType === 'help' || taskType === 'unknown') {
                hideWelcomeElements();
                showChatBubble(query, result.message || '有什么可以帮你的？');
                addConversationTurn(query, result);
                stopLoadingMessages();
                hideLoading();
                return;
            }

            // poi_query → 显示景点信息
            if (taskType === 'poi_query') {
                hideWelcomeElements();
                var poi = result.poi;
                showChatBubble(query, result.message || (poi ? poi.description : '找到相关信息了～'));
                addConversationTurn(query, result);
                stopLoadingMessages();
                hideLoading();
                return;
            }

            // path_planning → 渲染路线
            if (result.recommended && result.recommended.length > 0) {
                renderRoute(result);
                showResults(result);
                addConversationTurn(query, result);
            } else {
                showError('路线规划失败', '未能生成有效的路线，请尝试更明确的需求描述');
            }
        } catch (err) {
            showError('规划出错', err.message);
        } finally {
            hideLoading();
        }
    }

    // 在对话流中显示聊天气泡
    function showChatBubble(query, reply) {
        var chatContent = document.getElementById('chat-content');
        if (!chatContent) return;

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

        // 自动滚动到底部
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

        var modeConfig = {
            distance_first: {
                start: '牌坊',
                end: '樱顶',
                loadingText: '正在规划最短路径…',
                loadingSubtext: '优先考虑距离',
            },
            scenery_first: {
                start: '牌坊',
                end: '樱顶',
                loadingText: '正在规划风景路线…',
                loadingSubtext: '优先考虑景观',
            },
            slope_first: {
                start: '牌坊',
                end: '樱顶',
                loadingText: '正在规划平坦路线…',
                loadingSubtext: '优先考虑坡度',
            },
        };

        var cfg = modeConfig[mode];
        if (!cfg) return;

        showLoading(cfg.loadingText, cfg.loadingSubtext);

        try {
            var parseResult = await apiRequest('/api/parse', {
                start: { name: cfg.start },
                end: { name: cfg.end },
                mode: mode,
                input_method: 'shortcut',
            });

            var routeResult = await apiRequest('/api/route', parseResult);

            if (routeResult.recommended && routeResult.recommended.length > 0) {
                renderRoute(routeResult);
                showResults(routeResult);
            } else {
                showError('路线规划失败', '未能生成有效的路线');
            }
        } catch (err) {
            showError('规划出错', err.message);
        } finally {
            hideLoading();
        }
    }

    function bindEvents() {
        var nlInput = document.getElementById('nl-input');
        var charCount = document.getElementById('char-count');
        var submitBtn = document.getElementById('submit-btn');
        var quickChips = document.querySelectorAll('.quick-chip');
        var errorCloseBtn = document.getElementById('error-close-btn');

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
                    showError('嗯？还没说去哪呢', '告诉我你想从哪走到哪吧～比如「从牌坊到樱顶」');
                    nlInput.focus();
                    return;
                }
                handleNlSubmit(query);
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

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') {
                hideError();
            }
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

    function init() {
        state.sessionId = generateSessionId();
        loadContext();
        bindEvents();
        showWelcomeHint();
        initMap();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
/* ========================================================================
   漫步珞珈 · 冷启动欢迎卡片（方案 1）交互逻辑
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
    var AUTO_SHOW_DELAY_MS = 150; // DOMContentLoaded 后延迟弹卡（不阻塞首屏高德地图加载）
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

        // 分发 3 种 action
        switch (action) {
            case 'path_planning':
            case 'poi_query':
                // ===== 类型 A + B：统一复用 submitNaturalLanguageQuery（走完整 NL 解析链路，含多轮上下文）=====
                close({ markSeen: true });
                // 等卡片退场动画差不多完了再触发，视觉顺一点
                setTimeout(function () {
                    // 优先调公开函数（typeof 安全检查！）
                    if (typeof window.submitNaturalLanguageQuery === 'function') {
                        window.submitNaturalLanguageQuery(displayText);
                    } else if ($input) {
                        // 兜底：手动触发回车事件，让原代码的 onkeydown 监听接住（fail-soft 绝对不能崩）
                        var ev;
                        if (typeof KeyboardEvent === 'function') {
                            ev = new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', which: 13, keyCode: 13, bubbles: true });
                        } else {
                            // 老浏览器 IE 兜底（不支持 KeyboardEvent 构造器）
                            ev = document.createEvent('Event');
                            ev.initEvent('keydown', true, true);
                            ev.key = 'Enter'; ev.which = 13; ev.keyCode = 13;
                        }
                        $input.dispatchEvent(ev);
                    }
                    // 滚动到结果区（有就滚，没有就算 fail-soft 不报错）
                    if ($resultArea && typeof $resultArea.scrollIntoView === 'function') {
                        try { $resultArea.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) { /* scrollIntoView options 老浏览器不支持，忽略 */ $resultArea.scrollIntoView(); }
                    }
                }, ANIM_OUT_DURATION_MS + 20);
                break;

            case 'recommend_poi':
                // ===== 类型 C：展开侧边栏 + 侧边栏顶部 2s 淡黄色高亮，不发起任何网络请求！ =====
                close({ markSeen: true });
                setTimeout(function () {
                    // 优先调公开函数
                    if (typeof window.showPoiSidebar === 'function') {
                        window.showPoiSidebar('全部');
                    } else if ($sidebar) {
                        // 兜底：手动加 .open 类（如果有这个类的话；没有也不会崩）
                        $sidebar.classList.add('open');
                    }
                    // 侧边栏 2s 淡黄色高亮（Fail-soft：sidebar 找不到就忽略）
                    if ($sidebar) {
                        $sidebar.classList.remove(SIDEBAR_HIGHLIGHT_CLASS);
                        void $sidebar.offsetWidth; // reflow 强制重启动画
                        $sidebar.classList.add(SIDEBAR_HIGHLIGHT_CLASS);
                        setTimeout(function () {
                            if ($sidebar) $sidebar.classList.remove(SIDEBAR_HIGHLIGHT_CLASS);
                        }, 2050); // 动画 2000ms + 50ms buffer
                    }
                    // 输入框 placeholder 引导下一步（仅当当前 placeholder 空的时候才加，不覆盖用户已有的提示）
                    if ($input && !$input.placeholder) {
                        $input.placeholder = '试试：樱花大道怎么去？';
                    }
                }, ANIM_OUT_DURATION_MS + 20);
                break;

            default:
                // 未知 action：只关卡，不做别的（fail-soft，不崩不报错）
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
        $card.addEventListener('click', function (e) {
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
            // 延迟 AUTO_SHOW_DELAY_MS 再弹：避免卡首屏高德地图的加载（AUTO_SHOW_DELAY_MS = 150ms）
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
   漫步珞珈 · T-024 快捷按钮权重优先级（前端高亮 + 清除逻辑）
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
   漫步珞珈 · T-022 场景 3 候选 POI 交互卡片（候选点选择闭环）
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