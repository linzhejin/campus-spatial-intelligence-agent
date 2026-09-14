"""珞珈智行 — 用户画像（长期记忆）。

按前端生成的 whu_uid 持久化每个用户的偏好权重，
用 EMA（指数移动平均）从用户实际接受的路径权重中缓慢学习：
    prior ← (1−α)·prior + α·本次采纳权重

设计取舍：
- 只在用户"采纳"（前端 telemetry 上报 route_accept）时更新，拒绝/重规划不学习，
  避免被 LLM 自己的选择循环强化。
- α=0.3：约 5~8 次采纳后明显体现个人偏好，又不会因单次异常抖动。
- 样本量 < MIN_SAMPLES 时不注入（先验不可靠，宁缺毋滥）。
- 存储为 JSON 文件，单进程 gunicorn 多线程下用锁保护；多 worker 各自读写
  可能丢更新，但画像只是软先验，可容忍（正式规模化再换 SQLite/Redis）。
"""

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_PROFILES_PATH = Path(__file__).parent.parent / "data" / "user_profiles.json"
_lock = threading.Lock()
_cache = None  # {uid: profile}

EMA_ALPHA = 0.3
MIN_SAMPLES = 3          # 至少 3 次采纳才把画像注入 prompt
_WEIGHT_KEYS = ("distance", "slope", "scenery")


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_PROFILES_PATH.read_text(encoding="utf-8"))
            if not isinstance(_cache, dict):
                _cache = {}
        except Exception:
            _cache = {}
    return _cache


def _save():
    try:
        _PROFILES_PATH.write_text(
            json.dumps(_cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        logger.warning("用户画像保存失败: %s", e)


def get_profile(uid: str) -> dict | None:
    """读取画像；不存在返回 None。"""
    if not uid:
        return None
    with _lock:
        return _load().get(uid)


def _valid_weights(w) -> bool:
    return (isinstance(w, dict)
            and all(isinstance(w.get(k), (int, float)) for k in _WEIGHT_KEYS))


def record_route_feedback(uid: str, applied_weights: dict, accepted: bool = True) -> dict | None:
    """记录一次路径反馈并做 EMA 更新。返回更新后的画像。

    applied_weights: 路径实际使用的权重（route payload 的 applied_weights 字段）。
    accepted=False 时不更新权重（仅计数 exposure，留作后续负反馈扩展）。
    """
    if not uid or not _valid_weights(applied_weights):
        return None
    # 归一化，防御上游权重和不为 1
    total = sum(float(applied_weights[k]) for k in _WEIGHT_KEYS)
    if total <= 0:
        return None
    w = {k: float(applied_weights[k]) / total for k in _WEIGHT_KEYS}

    with _lock:
        profiles = _load()
        p = profiles.setdefault(uid, {
            "weights": {"distance": 0.5, "slope": 0.2, "scenery": 0.3},  # 与系统默认一致
            "accepted_count": 0,
            "exposure_count": 0,
        })
        p["exposure_count"] = p.get("exposure_count", 0) + 1
        if accepted:
            p["accepted_count"] = p.get("accepted_count", 0) + 1
            for k in _WEIGHT_KEYS:
                p["weights"][k] = round((1 - EMA_ALPHA) * p["weights"][k] + EMA_ALPHA * w[k], 4)
        _save()
        return dict(p)


def build_profile_message(uid: str) -> str | None:
    """生成注入 LLM 的用户画像消息；样本不足或无画像返回 None。"""
    p = get_profile(uid)
    if not p or p.get("accepted_count", 0) < MIN_SAMPLES:
        return None
    w = p["weights"]
    return (
        f"该用户的历史偏好（{p['accepted_count']} 次采纳中学得，作为默认倾向，"
        f"用户本次显式要求优先）：distance={w['distance']:.2f}, "
        f"slope={w['slope']:.2f}, scenery={w['scenery']:.2f}。"
        f"用户没有表达偏好时，weights 参考这组值。"
    )
