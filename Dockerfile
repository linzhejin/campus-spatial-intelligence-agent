# ===== 漫步珞珈 (WHU-Walker) Fly.io 部署 =====
# 精简镜像：去掉 osmnx/geopandas 等重型 GIS 依赖
# 路网缓存已打包进镜像，运行时无需下载 OSM

FROM python:3.11-slim

WORKDIR /app

# 装精简生产依赖（利用 Docker 层缓存）
COPY requirements-prod.txt .
RUN pip install --no-cache-dir -r requirements-prod.txt

# 复制项目代码 + 路网缓存
COPY . .

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=3)" || exit 1

# Fly.io internal_port = 8080
EXPOSE 8080

CMD ["gunicorn", "app:app", "--workers", "1", "--timeout", "60", "--bind", "0.0.0.0:8080"]
