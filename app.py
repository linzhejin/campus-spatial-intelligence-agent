"""
漫步珞珈 (WHU-Walker) — Flask 应用入口

注册 API 蓝图、配置 CORS、统一错误处理、dev/prod 模式切换。
"""
import json
import logging
import os
import threading
import time
from flask import Flask, send_from_directory, jsonify, Response, request
from flask_cors import CORS

import config

logger = logging.getLogger(__name__)


def _cache_max_age(path: str) -> int:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".html",):
        return 0
    if ext in (".js", ".css", ".json", ".svg", ".png", ".ico", ".webmanifest"):
        return 3600
    if ext in (".woff2", ".woff", ".ttf", ".eot"):
        return 2592000
    return 60


def create_app() -> Flask:
    basedir = os.path.abspath(os.path.dirname(__file__))

    app = Flask(
        __name__,
        static_folder=os.path.join(basedir, "static"),
        static_url_path="",
    )

    if os.getenv("FLASK_ENV", "development") == "production":
        origins_raw = os.getenv("CORS_ORIGINS", "")
        origins = [o.strip() for o in origins_raw.split(",") if o.strip()]
        if origins:
            CORS(app, origins=origins)
        else:
            render_url = os.getenv("RENDER_EXTERNAL_URL", "https://your-app.onrender.com")
            CORS(app, origins=[render_url])
        app.config["DEBUG"] = False
        app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB
    else:
        CORS(app)
        app.config["DEBUG"] = True
        app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB dev

    from api.routes import api_bp
    app.register_blueprint(api_bp)

    @app.route("/")
    def serve_index():
        resp = send_from_directory(app.static_folder, "index.html")
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp

    @app.route("/health")
    @app.route("/api/health")
    def health_check():
        return jsonify({
            "status": "ok",
            "project": "漫步珞珈",
            "timestamp": int(time.time()),
        })

    @app.after_request
    def _add_cache_headers(resp):
        path = request.path
        if path.startswith("/static/") or path.startswith("/icons/") or path.startswith("/css/") or path.startswith("/js/"):
            resp.headers["Cache-Control"] = f"public, max-age={_cache_max_age(path)}"
        return resp

    if os.getenv("FLASK_ENV", "development") == "production":
        def _warmup_network():
            try:
                from spatial.network import load_or_download_network
                logger.info("[warmup] 预热路网中...")
                t0 = time.time()
                load_or_download_network()
                logger.info("[warmup] 路网预热完成，耗时 %.1fs", time.time() - t0)
            except Exception as e:
                logger.warning("[warmup] 路网预热失败 (不影响运行): %s", e)

        threading.Thread(target=_warmup_network, daemon=True).start()

    @app.route("/js/config.js")
    def serve_config_js():
        config_data = {
            "amapKey": config.AMAP_KEY or "",
            "amapSecurityCode": config.AMAP_SECURITY_CODE or "",
            "apiBase": "",
            "mapCenter": [config.MAP_CENTER["lng"], config.MAP_CENTER["lat"]],
            "mapZoom": config.MAP_ZOOM,
        }
        js = "window.WHU_WALKER_CONFIG = " + json.dumps(config_data, ensure_ascii=False) + ";"
        return Response(js, mimetype="application/javascript; charset=utf-8")

    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"error": "bad_request", "message": "请求参数不合法"}), 400

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "not_found", "message": "资源不存在"}), 404

    @app.errorhandler(500)
    def internal_error(e):
        return jsonify({"error": "internal_error", "message": "服务器内部错误"}), 500

    return app


app = create_app()

if __name__ == "__main__":
    # Glitch/Cloud 等平台注入 PORT 环境变量，本地开发用 FLASK_PORT
    port = int(os.getenv("PORT", os.getenv("FLASK_PORT", "5000")))
    debug = os.getenv("FLASK_ENV", "development") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)