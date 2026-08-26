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
ExecStart=${GUNICORN_BIN} app:app --workers 2 --threads 4 --timeout 60 --keepalive 5 --max-requests 1000 --max-requests-jitter 50 --preload --bind 0.0.0.0:${PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# ---------- 6. 启动服务 ----------
echo "[6/6] 启动服务..."
sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"

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
echo "=============================================="
