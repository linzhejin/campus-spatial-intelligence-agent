"""珞珈智行 — 任务卡知识库检索注入。

把校园出行的领域经验（什么场景用什么工具、权重怎么设、有哪些坑）
写成任务卡存在 data/task_cards.json，Agent 每轮请求前按 query 关键词
召回最相关的 1~2 张，作为 system 消息注入 LLM 上下文。

定位：这是"静态记忆"——由开发者沉淀的校园先验；
用户个体的"动态记忆"在 agents/profile.py。
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_CARDS_PATH = Path(__file__).parent.parent / "data" / "task_cards.json"
_cards_cache = None

MAX_CARDS_PER_QUERY = 2  # 注入上限，避免稀释主提示词

# 月份 → 季节，用于 season_hint 加权
_SEASON_BY_MONTH = {
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "autumn", 10: "autumn", 11: "autumn",
    12: "winter", 1: "winter", 2: "winter",
}


def load_cards() -> list:
    """加载任务卡（进程内缓存）。"""
    global _cards_cache
    if _cards_cache is None:
        try:
            data = json.loads(_CARDS_PATH.read_text(encoding="utf-8"))
            _cards_cache = data.get("cards", [])
        except Exception as e:
            logger.warning("任务卡加载失败，按空知识库运行: %s", e)
            _cards_cache = []
    return _cards_cache


def retrieve_cards(query: str, limit: int = MAX_CARDS_PER_QUERY,
                   now: datetime = None) -> list:
    """按触发词命中数召回任务卡；季节匹配的卡 +1 分。

    纯关键词子串匹配——卡片数量少（<50），不需要向量检索。
    """
    if not query:
        return []
    season = _SEASON_BY_MONTH[(now or datetime.now()).month]
    scored = []
    for card in load_cards():
        hits = sum(1 for t in card.get("triggers", []) if t and t in query)
        if hits == 0:
            continue
        score = hits + (1 if card.get("season_hint") == season else 0)
        scored.append((score, card))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:limit]]


def build_knowledge_message(query: str, now: datetime = None) -> str | None:
    """生成注入 LLM 的知识消息文本；无命中返回 None。"""
    cards = retrieve_cards(query, now=now)
    if not cards:
        return None
    lines = ["校园场景经验（供决策参考，优先级低于用户显式要求）："]
    for c in cards:
        line = f"【{c['name']}】{c['advice']}"
        if c.get("default_weights"):
            w = c["default_weights"]
            line += (f" 参考权重 distance={w['distance']}, "
                     f"slope={w['slope']}, scenery={w['scenery']}。")
        lines.append(line)
    return "\n".join(lines)
