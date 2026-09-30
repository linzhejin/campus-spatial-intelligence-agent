#!/usr/bin/env bash
# =====================================================================
# 漫步珞珈 (WHU-Walker) — 腾讯云/阿里云一键部署脚本
#
# 用法（在服务器上）：
#   1. git clone https://github.com/linzhejin/campus-spatial-intelligence-agent.git
#   2. cd campus-spatial-intelligence-agent
#   3. bash deploy.sh
#
# 脚本自动完成：装系统依赖 → 建虚拟环境 → 装 Python 包 → 配置 .env
#               → 创建 systemd 服务 → 启动并常驻后台
# =====================================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_USER="$(whoami)"
SERVICE_NAME="whu-walker"
AGENT_SERVICE_NAME="whu-agent-worker"
VISION_SERVICE_NAME="whu-vision-worker"
PORT="5000"

echo "=============================================="
echo "  漫步珞珈 · 一键部署"
echo "  目录: $APP_DIR"
echo "  用户: $APP_USER"
echo "=============================================="
echo ""

# ---------- 1. 安装系统依赖 ----------
echo "[1/6] 安装系统依赖 (python3 / venv / pip / git)..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git

# ---------- 2. 创建虚拟环境 ----------
echo "[2/6] 创建虚拟环境..."
cd "$APP_DIR"
python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate

# ---------- 3. 安装 Python 依赖 ----------
echo "[3/6] 安装 Python 依赖..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# ---------- 4. 配置 .env ----------
echo "[4/6] 配置 .env..."
if [ ! -f .env ]; then
    cp .env.example .env
fi
# 强制切到生产模式（关闭 debug / reloader，稳定单进程）
sed -i 's/^FLASK_ENV=.*/FLASK_ENV=production/' .env

# 检查密钥是否还是占位符
if grep -qE "your-key-here|your-amap-key-here|sk-your-key-here" .env; then
    echo ""
    echo "  ⚠️  检测到 .env 里还是占位符，请先填入真实密钥："
    echo "      nano .env    # 修改 DEEPSEEK_API_KEY / AMAP_KEY / AMAP_SECURITY_CODE"
    echo "  填好后重新运行：  bash deploy.sh"
    echo ""
    exit 1
fi

# 新版对话以 PostgreSQL 保存任务状态；没有可用的持久数据库不能启用新聊天端点。
if ! grep -qE '^DATABASE_URL=postgres(ql)?://[^[:space:]]+' .env \
    || grep -qE '^DATABASE_URL=.*(replace-me|your-|CHANGE_ME)' .env; then
    echo "  ❌ 请先在 .env 中配置可连接的 PostgreSQL DATABASE_URL。"
    echo "     数据库需要预先创建；部署会运行版本化迁移，并启动 Agent worker。"
    exit 1
fi
if ! grep -qE '^SECRET_KEY=.{32,}' .env \
    || grep -qE '^SECRET_KEY=(replace-with-a-random-secret|secret|changeme|change-me)$' .env; then
    echo "  ❌ 请先在 .env 中配置至少 32 个字符的随机 SECRET_KEY。"
    exit 1
fi

echo "  检查 PostgreSQL 连接并应用数据库迁移..."
if ! venv/bin/python - <<'PY'
from dotenv import load_dotenv
load_dotenv(".env")
try:
    from storage.database import initialize
    initialize()
except Exception as error:
    print("PostgreSQL 检查失败：", type(error).__name__)
    raise SystemExit(1)
print("PostgreSQL 已连接，数据库迁移完成。")
PY
then
    echo "  请检查 DATABASE_URL、数据库账号权限和网络连通性后重试。"
    exit 1
fi

# ---------- 5. 创建 systemd 服务 ----------
echo "[5/6] 创建 systemd 服务..."
GUNICORN_BIN="$APP_DIR/venv/bin/gunicorn"
sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null <<EOF
[Unit]
Description=WHU Walker (漫步珞珈)
After=network.target

[Service]
User=${APP_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${GUNICORN_BIN} app:app --workers 2 --threads 4 --timeout 60 --max-requests 1000 --bind 0.0.0.0:${PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sed \
    -e "s|@APP_DIR@|${APP_DIR}|g" \
    -e "s|@APP_USER@|${APP_USER}|g" \
    ops/systemd/whu-agent-worker.service \
    | sudo tee "/etc/systemd/system/${AGENT_SERVICE_NAME}.service" > /dev/null

# Vision is opt-in: only install its service when a local checkpoint and all
# optional runtime packages are already available in the shared venv.
VISION_WORKER_ENABLED=0
if venv/bin/python - <<'PY'
from dotenv import load_dotenv
from importlib.util import find_spec
from pathlib import Path
import os
load_dotenv(".env")
model = os.getenv("VISION_MODEL_PATH", "")
ready = bool(model and Path(model).is_file()
             and find_spec("ultralytics") and find_spec("cv2"))
raise SystemExit(0 if ready else 1)
PY
then
    sed \
        -e "s|@APP_DIR@|${APP_DIR}|g" \
        -e "s|@APP_USER@|${APP_USER}|g" \
        ops/systemd/whu-vision-worker.service \
        | sudo tee "/etc/systemd/system/${VISION_SERVICE_NAME}.service" > /dev/null
    VISION_WORKER_ENABLED=1
else
    echo "  视觉 worker 未启用：需同时提供本地模型权重和已安装的视觉依赖。"
fi

# ---------- 6. 启动服务 ----------
echo "[6/6] 启动服务..."
sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}" "${AGENT_SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}" "${AGENT_SERVICE_NAME}"
if [ "${VISION_WORKER_ENABLED}" = "1" ]; then
    sudo systemctl enable --now "${VISION_SERVICE_NAME}"
    sudo systemctl restart "${VISION_SERVICE_NAME}"
fi
sudo systemctl is-active --quiet "${SERVICE_NAME}"
sudo systemctl is-active --quiet "${AGENT_SERVICE_NAME}"
if [ "${VISION_WORKER_ENABLED}" = "1" ]; then
    sudo systemctl is-active --quiet "${VISION_SERVICE_NAME}"
fi

echo ""
echo "=============================================="
echo "  ✅ 部署完成！"
echo ""
echo "  访问地址:  http://<你的服务器公网IP>:${PORT}"
echo ""
echo "  常用命令："
echo "    查看状态:  sudo systemctl status ${SERVICE_NAME}"
echo "    查看日志:  sudo journalctl -u ${SERVICE_NAME} -f"
echo "    重启服务:  sudo systemctl restart ${SERVICE_NAME}"
echo "    Agent worker: sudo systemctl status ${AGENT_SERVICE_NAME}"
if [ "${VISION_WORKER_ENABLED}" = "1" ]; then
    echo "    视觉 worker: sudo systemctl status ${VISION_SERVICE_NAME}"
fi
echo "=============================================="
