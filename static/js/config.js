(function () {
    'use strict';

    var DEFAULT_CENTER = [114.3630, 30.5365];  // GCJ-02（app.js 初始化时转为 WGS-84）
    var DEFAULT_ZOOM = 16;
    var DEFAULT_API_BASE = '';
    // 天地图 Key（https://console.tianditu.gov.cn/ 申请，免费）。
    // 留空时使用 OpenStreetMap 标准底图（WGS-84，开发与国内访问均可）。
    var DEFAULT_TIANDITU_KEY = '';

    var cfg = {
        tiandituKey: DEFAULT_TIANDITU_KEY,
        TIANDITU_KEY: DEFAULT_TIANDITU_KEY,
        API_BASE_URL: DEFAULT_API_BASE,
        DEFAULT_CENTER: DEFAULT_CENTER,
        MAP_ZOOM: DEFAULT_ZOOM,
        apiBase: DEFAULT_API_BASE,
        mapCenter: DEFAULT_CENTER,
        mapZoom: DEFAULT_ZOOM,
    };

    window.WHU_WALKER_CONFIG = cfg;
    window.TIANDITU_KEY = cfg.TIANDITU_KEY;
    window.API_BASE_URL = cfg.API_BASE_URL;
    window.DEFAULT_CENTER = cfg.DEFAULT_CENTER;
})();
