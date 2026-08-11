(function () {
    'use strict';

    var DEFAULT_CENTER = [114.3630, 30.5365];
    var DEFAULT_ZOOM = 16;
    var DEFAULT_API_BASE = '';
    var DEFAULT_AMAP_KEY = '';

    var cfg = {
        AMAP_KEY: DEFAULT_AMAP_KEY,
        API_BASE_URL: DEFAULT_API_BASE,
        DEFAULT_CENTER: DEFAULT_CENTER,
        MAP_ZOOM: DEFAULT_ZOOM,
        amapKey: DEFAULT_AMAP_KEY,
        apiBase: DEFAULT_API_BASE,
        mapCenter: DEFAULT_CENTER,
        mapZoom: DEFAULT_ZOOM,
    };

    window.WHU_WALKER_CONFIG = cfg;
    window.AMAP_KEY = cfg.AMAP_KEY;
    window.API_BASE_URL = cfg.API_BASE_URL;
    window.DEFAULT_CENTER = cfg.DEFAULT_CENTER;
})();