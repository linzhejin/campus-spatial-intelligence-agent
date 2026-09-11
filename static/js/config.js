(function () {
    'use strict';

    var DEFAULT_CENTER = [114.3630, 30.5365];  // GCJ-02（与高德瓦片/POI/路径天然对齐）
    var DEFAULT_ZOOM = 16;
    var DEFAULT_API_BASE = '';
    // 天地图 Key（https://console.tianditu.gov.cn/ 申请，免费）。
    // 留空时默认使用高德矢量瓦片（GCJ-02，国内手机端秒开，中文标注，高缩放全覆盖，无需 Key）。
    // 图层控件可切换 OpenStreetMap / 卫星影像 / 天地图（配 Key 后）。
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
