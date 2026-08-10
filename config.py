"""
漫步珞珈 (WHU-Walker) V1 全局配置
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ===== LLM 配置（DEC-006: DeepSeek V4-Flash）=====
# 兼容 OpenAI SDK：仅改 base_url + model id，无需替换 SDK
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
OPENAI_API_KEY = DEEPSEEK_API_KEY  # 兼容 openai SDK 字段名
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")  # DeepSeek V4-Flash 别名

# ===== 高德地图配置 =====
AMAP_KEY = os.getenv("AMAP_KEY", "")

# ===== 武汉大学校园范围 (经纬度边界) =====
WHU_BBOX = {
    "north": 30.5480,
    "south": 30.5280,
    "east":  114.3750,
    "west":  114.3500,
}

# 地图默认中心点 (武大核心区)
MAP_CENTER = {"lat": 30.5365, "lng": 114.3630}
MAP_ZOOM = 16

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

# ===== POI 数据（V1 硬编码，V2 迁移到文件）=====
WHU_POIS = {
    "牌坊":    {"lat": 30.5362, "lon": 114.3625, "type": "landmark", "desc": "武大主入口，标志性建筑"},
    "樱园":    {"lat": 30.5375, "lon": 114.3630, "type": "scenery",  "desc": "樱花大道核心区，每年三月樱花盛开"},
    "樱顶":    {"lat": 30.5378, "lon": 114.3635, "type": "scenery",  "desc": "樱花大道顶端，可俯瞰校园"},
    "老图书馆": {"lat": 30.5376, "lon": 114.3633, "type": "scenery",  "desc": "武大标志性建筑，百年历史"},
    "行政楼":   {"lat": 30.5385, "lon": 114.3640, "type": "scenery",  "desc": "武大标志性建筑群"},
    "梅园":    {"lat": 30.5380, "lon": 114.3630, "type": "scenery",  "desc": "梅花盛开之处，安静清幽"},
    "桂园":    {"lat": 30.5355, "lon": 114.3615, "type": "scenery",  "desc": "桂花飘香，秋季最美"},
    "图书馆":   {"lat": 30.5395, "lon": 114.3635, "type": "study",    "desc": "总图书馆，自习好去处"},
    "教五":    {"lat": 30.5360, "lon": 114.3610, "type": "study",    "desc": "第五教学楼"},
    "万林艺术馆": {"lat": 30.5368, "lon": 114.3628, "type": "scenery","desc": "现代艺术博物馆"},
    "枫园":    {"lat": 30.5400, "lon": 114.3650, "type": "scenery",  "desc": "留学生教育学院附近"},
    "珞珈山":   {"lat": 30.5415, "lon": 114.3660, "type": "scenery",  "desc": "校园最高点，环山路适合散步"},
}

# ===== 文件路径 =====
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
ROAD_NETWORK_CACHE = os.path.join(DATA_DIR, "whu_road_network.graphml")
