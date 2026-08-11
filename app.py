"""
漫步珞珈 (WHU-Walker) — Flask 应用入口

注册 API 蓝图、配置 CORS、统一错误处理、dev/prod 模式切换。
"""
import json
import os
from flask import Flask, send_from_directory, jsonify, Response
from flask_cors import CORS

import config


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
            CORS(app, origins=["https://your-app.onrender.com"])
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
        return send_from_directory(app.static_folder, "index.html")

    @app.route("/health")
    def health_check():
        return jsonify({
            "status": "ok",
            "project": "漫步珞珈",
        })

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
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_ENV", "development") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)