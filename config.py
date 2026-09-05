"""
漫步珞珈 (WHU-Walker) V1 全局配置
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _env_float(key: str, default: float) -> float:
    """读取 float 类型 env 变量，非法值 fallback 到默认值（避免 .env typo 导致 app 启动崩溃）"""
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        import warnings
        warnings.warn(f"config: env {key}={raw!r} 非法，使用默认值 {default}", stacklevel=2)
        return default


# ===== LLM 配置（DEC-006: DeepSeek V4-Flash）=====
# 兼容 OpenAI SDK：仅改 base_url + model id，无需替换 SDK
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
OPENAI_API_KEY = DEEPSEEK_API_KEY  # 兼容 openai SDK 字段名
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")  # DeepSeek V4-Flash 别名

# ===== 高德地图配置 =====
AMAP_KEY = os.getenv("AMAP_KEY", "")
AMAP_SECURITY_CODE = os.getenv("AMAP_SECURITY_CODE", "")  # 高德 JS API 2.0 安全密钥

# ===== 武汉大学校园范围 (经纬度边界) — .env 可覆盖 =====
WHU_BBOX = {
    "north": _env_float("WHU_BBOX_NORTH", 30.5510),
    "south": _env_float("WHU_BBOX_SOUTH", 30.5170),
    "east":  _env_float("WHU_BBOX_EAST",  114.3880),
    "west":  _env_float("WHU_BBOX_WEST",  114.3460),
}

# 地图默认中心点 (武大核心区) — .env 可覆盖
MAP_CENTER = {
    "lat": _env_float("MAP_CENTER_LAT", 30.5365),
    "lng": _env_float("MAP_CENTER_LNG", 114.3630),
}
MAP_ZOOM = int(_env_float("MAP_ZOOM", 16))

# ===== 三学部校园边界多边形（GCJ-02 坐标，与 pois.json 坐标系一致）=====
# 用途：POI 校外点过滤、路网裁剪（保证路线不出校）。
# 路网为 WGS-84，使用时需经 gcj02_to_wgs84 逐点转换；裁剪时外扩缓冲约 120m 保留校门过街连接。
# 边界依据：OSM POI 坐标极值 + 珞瑜路/八一路/东湖南路/东湖 实际地物。
CAMPUS_POLYS_GCJ = {
    # 文理学部（珞瑜路以北、珞珈山、东湖南路以南）
    "文理学部": [
        (114.3555, 30.5282), (114.3670, 30.5280), (114.3765, 30.5310),
        (114.3775, 30.5370), (114.3750, 30.5430), (114.3690, 30.5480),
        (114.3580, 30.5488), (114.3520, 30.5430), (114.3518, 30.5350),
        (114.3540, 30.5295),
    ],
    # 工学部（文理学部东北、东湖南路以南）
    "工学部": [
        (114.3570, 30.5360), (114.3690, 30.5350), (114.3770, 30.5355),
        (114.3815, 30.5390), (114.3820, 30.5450), (114.3740, 30.5495),
        (114.3620, 30.5490), (114.3580, 30.5420),
    ],
    # 信息学部（珞瑜路以南，原武测）
    "信息学部": [
        (114.3540, 30.5302), (114.3815, 30.5300), (114.3818, 30.5230),
        (114.3790, 30.5150), (114.3700, 30.5128), (114.3600, 30.5130),
        (114.3545, 30.5170), (114.3540, 30.5240),
    ],
}

# 坐标在校内但名称明显指向其他校区/地点的黑名单（OSM 误 tag，导航会误导）
POI_NAME_BLACKLIST = (
    "国家网络安全学院",   # 网安学院主体在东西湖临空港校区，不在三学部
)

# ===== 多因素成本默认权重（DEC-004 修订 + DEC-010 LLM 直接生成）=====
# 默认场景"从A到B"用户未表达偏好时使用；接近普通导航基线
# 当 LLM 识别到用户空间偏好时，直接输出 weights 覆盖此默认值
DEFAULT_WEIGHTS = {
    "distance": 0.5,   # 距离作为基础（默认场景接近普通导航）
    "slope":    0.2,   # 坡度权重低（武大主要活动区相对平坦）
    "scenery":  0.3,   # 景观权重适中
}

# 权重校验范围（DEC-010 防 LLM 输出极端值）
WEIGHT_BOUNDS = {"min": 0.05, "max": 0.8}

# DEC-011 路径计算参数
PATH_LENGTH_CAP_MULTIPLIER = 3.0
PATH_LENGTH_CAP_MAX = 2000.0

# 注：CONSTRAINT_WEIGHT_MAP 已移除（DEC-010）
# LLM 在解析阶段直接输出 weights 字段，不再通过约束等级规则映射
# 约束等级（constraints）仍保留，用于 DEC-011 硬约束过滤（如 slope=avoid 过滤陡坡路段）

# ===== POI 数据（迁移到 data/pois.json，此处仅喂给 LLM 解析 Agent 拼 Prompt） =====
def _load_whu_pois_for_prompt() -> dict:
    """从 data/pois.json 读取 POI，返回 {name: {type, desc}} 字典供 parser 使用。
    若 JSON 不可用，返回空字典（LLM Prompt 中 POI 列表将为空，已及时发现）。
    """
    import json as _json
    from pathlib import Path as _Path
    pois_path = _Path(__file__).parent / "data" / "pois.json"
    try:
        with open(pois_path, "r", encoding="utf-8") as f:
            raw = _json.load(f)
        result = {}
        for poi in raw.get("pois", []):
            result[poi["name"]] = {
                "type": poi.get("type", "landmark"),
                "desc": poi.get("description", ""),
            }
        return result
    except Exception:
        return {}


WHU_POIS = _load_whu_pois_for_prompt()

# ===== 文件路径 =====
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
ROAD_NETWORK_CACHE = os.path.join(DATA_DIR, "whu_road_network.graphml")
ROAD_ANNOTATIONS_PATH = os.path.join(DATA_DIR, "road_annotations.json")
