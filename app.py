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
        CORS(app, origins=os.getenv("CORS_ORIGINS", "").split(","))
        app.config["DEBUG"] = False
    else:
        CORS(app)
        app.config["DEBUG"] = True

    from api.routes import api_bp
    app.register_blueprint(api_bp)

    @app.route("/")
    def serve_index():
        return send_from_directory(app.static_folder, "index.html")

    @app.route("/js/config.js")
    def serve_config_js():
        config_data = {
            "amapKey": config.AMAP_KEY or "",
            "apiBase": "",
            "mapCenter": [config.MAP_CENTER["lng"], config.MAP_CENTER["lat"]],
            "mapZoom": config.MAP_ZOOM,
        }
        js = "window.WHU_WALKER_CONFIG = " + json.dumps(config_data, ensure_ascii=False) + ";"
        return Response(js, mimetype="application/javascript; charset=utf-8")

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