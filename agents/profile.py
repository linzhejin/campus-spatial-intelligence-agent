"""珞珈智行 — 用户画像（长期记忆）。

按前端生成的 whu_uid 持久化每个用户的偏好权重，
用 EMA（指数移动平均）从用户实际开始或完成导航的显式休闲偏好中缓慢学习：
    prior ← (1−α)·prior + α·本次采纳权重

设计取舍：
- 路线曝光、停留时间和单纯点选不学习；只有明确选择休闲策略后开始/完成导航才学习。
- 同一个 route_id 最多学习一次，避免开始与到达被重复计数。
- α=0.3：约 5~8 次采纳后明显体现个人偏好，又不会因单次异常抖动。
- 样本量 < MIN_SAMPLES 时不注入（先验不可靠，宁缺毋滥）。
- 存储为 JSON 文件，单进程 gunicorn 多线程下用锁保护；多 worker 各自读写
  可能丢更新，但画像只是软先验，可容忍（正式规模化再换 SQLite/Redis）。
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from agents.routing_policy import STRATEGY_WEIGHTS
from spatial.routing import DEFAULT_WEIGHTS

logger = logging.getLogger(__name__)

_PROFILES_PATH = Path(__file__).parent.parent / "data" / "user_profiles.json"
_lock = threading.Lock()
_cache = None  # {uid: profile}

EMA_ALPHA = 0.3
MIN_SAMPLES = 3          # 至少 3 次采纳才把画像注入 prompt
_WEIGHT_KEYS = ("distance", "slope", "scenery")
CONFIRM_SIGNALS = {"navigation_started", "navigation_completed"}
TRACKED_SIGNALS = {
    "route_shown", "strategy_selected", "strategy_abandoned", *CONFIRM_SIGNALS,
}
LEARNABLE_STRATEGIES = {"recommended", "scenery", "flat", "custom"}
LEARNABLE_SOURCES = {"button", "explicit_nl"}
RECOMMENDED_WEIGHTS = STRATEGY_WEIGHTS["recommended"]


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


def _normalized_weights(applied_weights) -> dict | None:
    if not _valid_weights(applied_weights):
        return None
    total = sum(float(applied_weights[k]) for k in _WEIGHT_KEYS)
    if total <= 0:
        return None
    return {k: float(applied_weights[k]) / total for k in _WEIGHT_KEYS}


def _strategy_profile(profiles: dict, uid: str) -> dict:
    return profiles.setdefault(uid, {
        "weights": dict(RECOMMENDED_WEIGHTS),
        "accepted_count": 0,
        "exposure_count": 0,
        "selected_count": 0,
        "abandoned_count": 0,
        "confirmed_route_ids": [],
        "last_updated_at": None,
        "last_signal_source": None,
    })


def _apply_ema(profile: dict, weights: dict) -> None:
    profile["accepted_count"] = profile.get("accepted_count", 0) + 1
    current = profile.setdefault("weights", dict(RECOMMENDED_WEIGHTS))
    for key in _WEIGHT_KEYS:
        current[key] = round((1 - EMA_ALPHA) * float(current[key]) + EMA_ALPHA * weights[key], 4)


def record_strategy_signal(
    uid: str,
    route_id: str,
    strategy: str,
    strategy_source: str,
    applied_weights: dict,
    signal: str,
) -> dict | None:
    """Record a route signal and learn once from confirmed explicit leisure use."""
    if not uid or not route_id or signal not in TRACKED_SIGNALS:
        return None
    weights = _normalized_weights(applied_weights)
    if weights is None:
        return None

    with _lock:
        profiles = _load()
        p = _strategy_profile(profiles, uid)
        if signal == "route_shown":
            p["exposure_count"] = p.get("exposure_count", 0) + 1
        elif signal == "strategy_selected":
            p["selected_count"] = p.get("selected_count", 0) + 1
        elif signal == "strategy_abandoned":
            p["abandoned_count"] = p.get("abandoned_count", 0) + 1

        learnable = (
            signal in CONFIRM_SIGNALS
            and strategy in LEARNABLE_STRATEGIES
            and strategy_source in LEARNABLE_SOURCES
        )
        if learnable:
            confirmed = p.setdefault("confirmed_route_ids", [])
            if route_id not in confirmed:
                _apply_ema(p, weights)
                confirmed.append(route_id)
                del confirmed[:-100]
                p["last_updated_at"] = datetime.now(timezone.utc).isoformat()
                p["last_signal_source"] = signal
        _save()
        return dict(p)


def record_route_feedback(uid: str, applied_weights: dict, accepted: bool = True) -> dict | None:
    """旧版兼容入口；线上 telemetry 不再调用它。

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
            "weights": dict(DEFAULT_WEIGHTS),
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
        f"该用户的历史偏好（{p['accepted_count']} 次采纳中学得，仅在本次有偏好需求时参考，"
        f"用户本次显式要求优先）：distance={w['distance']:.2f}, "
        f"slope={w['slope']:.2f}, scenery={w['scenery']:.2f}。"
        f"普通通勤和赶时间请求不使用画像权重，统一走纯最短路径 1.00/0.00/0.00。"
    )
