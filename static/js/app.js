(function () {
    'use strict';

    var API_BASE = '';
    var MAP_CENTER = [114.3630, 30.5365];
    var MAP_ZOOM = 16;
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
    };

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
                strokeColor: '#1976D2',
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
                strokeColor: '#9E9E9E',
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
                content: '<div style="background:#FF9800;color:white;padding:2px 8px;border-radius:10px;font-size:11px;box-shadow:0 2px 6px rgba(0,0,0,0.2);">' +
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
            poiList.innerHTML = '<li style="background:#f5f5f5;color:#999;">暂无途经景点</li>';
        } else {
            pois.forEach(function (poi) {
                var li = document.createElement('li');
                li.textContent = poi.name || poi.id || '未知';
                poiList.appendChild(li);
            });
        }

        var explanation = data.explanation || '已为您规划好路线';
        document.getElementById('explanation-text').textContent = explanation;
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
        document.getElementById('loading-section').hidden = true;
    }

    function showError(title, message) {
        var section = document.getElementById('error-section');
        document.getElementById('error-title').textContent = title || '出错了';
        document.getElementById('error-message').textContent = message || '抱歉，出现了未知错误';
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
            'missing_query': '请输入你的漫步需求',
            'missing_endpoints': '请提供起点和终点',
            'missing_poi_names': '起点和终点名称不能为空',
            'poi_not_found': '未找到指定的地点，请尝试其他名称',
            'route_not_found': '无法找到符合条件的路线，请尝试调整条件',
            'network_not_initialized': '路网尚未初始化，请稍后再试',
            'network_load_failed': '路网加载失败，请刷新页面重试',
            'parse_failed': '需求解析失败，请尝试更明确的表述',
            'parse_validation_error': '需求格式有误，请检查输入',
            'route_computation_failed': '路径计算失败，请重试',
            'nearest_node_failed': '地点定位失败，请尝试其他地点',
            'unsupported_task': '暂不支持此任务类型',
            'internal_error': '服务器内部错误，请稍后重试',
        };
        return errorMap[code] || msg || '未知错误';
    }

    async function handleNlSubmit(query) {
        hideError();
        showLoading('正在解析你的需求…', '与空间智能助手对话中');

        try {
            var context = state.conversationHistory.length > 0
                ? state.conversationHistory
                : null;

            var result = await apiRequest('/api/chat', {
                query: query,
                context: context,
            });

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
        var shortcutBtns = document.querySelectorAll('.shortcut-btn');
        var collapseBtn = document.getElementById('collapse-btn');
        var errorCloseBtn = document.getElementById('error-close-btn');

        if (nlInput) {
            nlInput.addEventListener('input', function () {
                var len = nlInput.value.length;
                charCount.textContent = len;
                if (len >= 200) {
                    charCount.style.color = '#E53935';
                } else {
                    charCount.style.color = '';
                }
            });

            nlInput.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    submitBtn.click();
                }
            });
        }

        if (submitBtn) {
            submitBtn.addEventListener('click', function () {
                var query = nlInput.value.trim();
                if (!query) {
                    showError('请输入需求', '请先输入你的漫步需求，例如"从牌坊到樱顶"');
                    nlInput.focus();
                    return;
                }
                handleNlSubmit(query);
            });
        }

        shortcutBtns.forEach(function (btn) {
            btn.addEventListener('click', function () {
                shortcutBtns.forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
                var mode = btn.getAttribute('data-mode');
                state.activeMode = mode;
                handleShortcutMode(mode);
            });
        });

        if (collapseBtn) {
            collapseBtn.addEventListener('click', collapseResults);
        }

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
        var results = document.getElementById('results-section');
        results.hidden = false;
        document.getElementById('recommended-distance').textContent = '—';
        document.getElementById('shortest-distance').textContent = '—';
        document.getElementById('overlap-rate').textContent = '—';
        document.getElementById('poi-items').innerHTML =
            '<li style="background:#E3F2FD;color:#1565C0;">输入需求或点击快捷按钮开始规划</li>';
        document.getElementById('explanation-text').textContent =
            '👋 欢迎来到漫步珞珈！输入自然语言描述或使用下方快捷按钮，即可为你规划武大校园的最优漫步路线。';
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