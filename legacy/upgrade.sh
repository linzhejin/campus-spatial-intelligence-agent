#!/usr/bin/env bash
# =====================================================================
# 珞珈智行 — 服务器升级 & 端口修复脚本
#
# 解决问题：
#   1. 运营商封禁 5000 端口 → 配置 Nginx 反向代理 80→5000
#   2. v2 全 Agent 架构代码未部署
#
# 用法（在服务器上执行）：
#   bash upgrade.sh
# =====================================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="whu-walker"

echo "=============================================="
echo "  珞珈智行 · 升级 & 端口修复"
echo "  目录: $APP_DIR"
echo "=============================================="
echo ""

# ---------- Step 1: 拉取最新代码 ----------
echo "[1/5] 拉取最新代码..."
cd "$APP_DIR"
git fetch --all
git reset --hard origin/main   # 强制同步到远程 main（避免本地脏文件冲突）
echo "  ✓ 代码已更新到 $(git log --oneline -1)"

# ---------- Step 2: 安装 Nginx（如果没有）----------
echo ""
echo "[2/5] 检查 Nginx..."
if ! command -v nginx &>/dev/null; then
    echo "  Nginx 未安装，正在安装..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq nginx
    echo "  ✓ Nginx 安装完成"
else
    echo "  ✓ Nginx 已安装 ($(nginx -v 2>&1))"
fi

# ---------- Step 3: 生成 Nginx 反向代理配置 ----------
echo ""
echo "[3/5] 配置 Nginx 反向代理 (80 → 5000)..."

# 获取服务器公网 IP
SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || curl -s ipinfo.io/ip 2>/dev/null || hostname -I | awk '{print $1}')
echo "  服务器 IP: $SERVER_IP"

NGINX_CONF="/etc/nginx/sites-available/whu-walker"
sudo tee "$NGINX_CONF" > /dev/null <<NGINXEOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name ${SERVER_IP} _;

    # 关闭 HTTP 默认的 308 重定向（如有）
    # return 301 https://\$host\$request_uri;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }
}
NGINXEOF

# 启用站点配置
sudo ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/whu-walker

# 禁用默认站点（如果存在且冲突）
if [ -f /etc/nginx/sites-enabled/default ]; then
    sudo rm -f /etc/nginx/sites-enabled/default
    echo "  已禁用 Nginx 默认站点"
fi

# 测试配置
if sudo nginx -t 2>&1; then
    echo "  ✓ Nginx 配置测试通过"
else
    echo "  ✗ Nginx 配置错误，请检查 $NGINX_CONF"
    exit 1
fi

# ---------- Step 4: 安装 Python 依赖 ----------
echo ""
echo "[4/5] 更新 Python 依赖..."
cd "$APP_DIR"
if [ -d venv ]; then
    source venv/bin/activate
else
    python3 -m venv venv
    source venv/bin/activate
fi
pip install -r requirements.txt -q
echo "  ✓ Python 依赖已更新"

# ---------- Step 5: 重启所有服务 ----------
echo ""
echo "[5/5] 重启服务..."

# 重启 gunicorn
sudo systemctl daemon-reload
sudo systemctl restart "${SERVICE_NAME}"
sleep 2

# 重启 Nginx
sudo systemctl restart nginx
sleep 1

# ---------- 验证 ----------
echo ""
echo "=============================================="
echo "  验证..."
echo "=============================================="

GUNICORN_OK=false
NGINX_OK=false
HEALTH_OK=false

if sudo systemctl is-active --quiet "${SERVICE_NAME}"; then
    echo "  ✓ Gunicorn (${SERVICE_NAME}) 运行中"
    GUNICORN_OK=true
else
    echo "  ✗ Gunicorn 未运行，查看日志: sudo journalctl -u ${SERVICE_NAME} -f"
fi

if sudo systemctl is-active --quiet nginx; then
    echo "  ✓ Nginx 运行中"
    NGINX_OK=true
else
    echo "  ✗ Nginx 未运行，查看日志: sudo journalctl -u nginx -f"
fi

# 健康检查
sleep 1
HEALTH=$(curl -s http://127.0.0.1:5000/health 2>/dev/null || echo "")
if [ -n "$HEALTH" ] && echo "$HEALTH" | grep -q '"status":"ok"'; then
    echo "  ✓ 后端健康检查通过"
    HEALTH_OK=true
else
    echo "  ✗ 后端健康检查失败"
fi

# 80 端口测试（从本机）
PORT80=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:80/health 2>/dev/null || echo "000")
if [ "$PORT80" = "200" ]; then
    echo "  ✓ Nginx 80端口 → 5000端口 代理正常"
else
    echo "  ✗ 80端口代理测试返回 $PORT80"
fi

echo ""
echo "=============================================="
if $GUNICORN_OK && $NGINX_OK && $HEALTH_OK && [ "$PORT80" = "200" ]; then
    echo "  ✅ 升级完成，全部正常！"
    echo ""
    echo "  手机端访问（推荐）:  http://${SERVER_IP}"
    echo "  旧地址仍可用:        http://${SERVER_IP}:5000"
else
    echo "  ⚠️  部分服务异常，请检查上方日志"
fi
echo ""
echo "  快速检查命令："
echo "    sudo systemctl status whu-walker"
echo "    sudo systemctl status nginx"
echo "    sudo journalctl -u whu-walker -f"
echo "=============================================="
