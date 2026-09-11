"""
E1 意图解析指标计算器

输入：一组 (prediction, gold) 配对，prediction / gold 可为 TaskIntent 对象或 dict。
输出：以下指标
  - task_type_accuracy:      4 类 (path_planning/poi_query/help/chat) 精确匹配准确率
  - constraint_macro_f1:     distance/slope/scenery 三个约束维度各自的 macro-F1
  - weight_mae:              权重向量平均绝对误差（仅当 gold weights != null 的配对参与）
  - weight_cosine_sim:       预测/gold 权重向量余弦相似度（同上样本集）
  - poi_f1:                  start/end POI 名称匹配 F1
  - parse_failure_rate:      兜底/解析失败比例 (task_type=unknown 或 ambiguity 含"解析失败")

各函数签名：func(pairs: list[tuple]) -> value，pairs 元素为 (prediction, gold)。
优先使用 sklearn 计算 F1；sklearn 不可用时回退到手写实现。
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Optional

# 尝试导入 sklearn（可选，缺失时回退手写 F1）
try:
    from sklearn.metrics import f1_score as _sk_f1_score
    _HAS_SKLEARN = True
except Exception:  # pragma: no cover - sklearn 是可选依赖
    _HAS_SKLEARN = False


# ---------------------------------------------------------------------------
# 工具：兼容 TaskIntent 对象 / dict 的字段读取
# ---------------------------------------------------------------------------
def _get(obj, attr: str, default=None):
    """从 TaskIntent(pydantic) 或 dict 中读取字段，统一接口。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(attr, default)
    # pydantic v1 兼容
    if hasattr(obj, attr):
        return getattr(obj, attr, default)
    return default


def _poi_name(ref) -> Optional[str]:
    """从 PoiRef / dict 中取规范化的 name。"""
    if ref is None:
        return None
    name = _get(ref, "name", None)
    if not name:
        return None
    return str(name).strip()


def _weight_vec(w) -> Optional[dict]:
    """归一化权重 dict 为 {distance, slope, scenery} 三键；None/空返回 None。"""
    if w is None:
        return None
    if isinstance(w, dict):
        if not w:
            return None
        return {
            "distance": float(w.get("distance", 0.0)),
            "slope": float(w.get("slope", 0.0)),
            "scenery": float(w.get("scenery", 0.0)),
        }
    return None


# ---------------------------------------------------------------------------
# 指标 1：task_type 精确匹配准确率
# ---------------------------------------------------------------------------
TASK_TYPES = ("path_planning", "poi_query", "help", "chat", "unknown")


def task_type_accuracy(pairs: list[tuple]) -> float:
    """4 类 (path_planning/poi_query/help/chat) 精确匹配准确率。
    unknown 也计入分母（视为错误），分子只算严格相等的配对。"""
    if not pairs:
        return 0.0
    correct = 0
    for pred, gold in pairs:
        p = _get(pred, "task_type")
        g = _get(gold, "task_type")
        if p == g:
            correct += 1
    return correct / len(pairs)


# ---------------------------------------------------------------------------
# 指标 2：constraint macro-F1（按 distance/slope/scenery 维度分别算）
# ---------------------------------------------------------------------------
def _macro_f1(y_true: list, y_pred: list) -> float:
    """单维度 macro-F1。优先 sklearn，缺失时手写（按类别求 F1 后算术平均）。"""
    labels = sorted(set(y_true) | set(y_pred))
    if not labels:
        return 1.0
    if _HAS_SKLEARN:
        # average='macro' 对未出现的 label 自动置 0
        return float(_sk_f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0))

    # 手写 macro-F1
    f1s = []
    for lab in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == lab and p == lab)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != lab and p == lab)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == lab and p != lab)
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
        f1s.append(f1)
    return sum(f1s) / len(f1s) if f1s else 0.0


def constraint_macro_f1(pairs: list[tuple]) -> dict:
    """对 distance/slope/scenery 三个约束维度分别计算 macro-F1，返回 dict。"""
    if not pairs:
        return {"distance": 0.0, "slope": 0.0, "scenery": 0.0}

    result = {}
    for dim in ("distance", "slope", "scenery"):
        y_true, y_pred = [], []
        for pred, gold in pairs:
            g_con = _get(gold, "constraints") or {}
            p_con = _get(pred, "constraints") or {}
            y_true.append(g_con.get(dim, "normal"))
            y_pred.append(p_con.get(dim, "normal"))
        result[dim] = _macro_f1(y_true, y_pred)
    return result


# ---------------------------------------------------------------------------
# 指标 3：weight_mae 权重向量平均绝对误差
# ---------------------------------------------------------------------------
def weight_mae(pairs: list[tuple]) -> float:
    """仅当 gold weights != null 的配对参与。

    MAE = (1 / (3N)) × Σ_i Σ_k |w_p[i,k] - w_g[i,k]|
    （三个维度各自绝对误差的平均，取值区间 [0, 2/3]；2026-09 修正：
    旧实现漏除维度数 3，数值整体偏大 3 倍）
    另在汇总中报告参与样本数 weight_n。
    """
    sample = [(p, g) for p, g in pairs if _weight_vec(_get(g, "weights")) is not None]
    if not sample:
        return 0.0
    total = 0.0
    for pred, gold in sample:
        g_w = _weight_vec(_get(gold, "weights"))
        p_w = _weight_vec(_get(pred, "weights")) or {"distance": 0.0, "slope": 0.0, "scenery": 0.0}
        for k in ("distance", "slope", "scenery"):
            total += abs(p_w.get(k, 0.0) - g_w.get(k, 0.0))
    return total / (3.0 * len(sample))


# ---------------------------------------------------------------------------
# 指标 4：weight_cosine_sim 权重向量余弦相似度
# ---------------------------------------------------------------------------
def weight_cosine_sim(pairs: list[tuple]) -> float:
    """仅当 gold weights != null 的配对参与。返回平均余弦相似度。"""
    sample = [(p, g) for p, g in pairs if _weight_vec(_get(g, "weights")) is not None]
    if not sample:
        return 0.0
    sims = []
    for pred, gold in sample:
        g_w = _weight_vec(_get(gold, "weights"))
        p_w = _weight_vec(_get(pred, "weights")) or {"distance": 0.0, "slope": 0.0, "scenery": 0.0}
        keys = ("distance", "slope", "scenery")
        dot = sum(p_w[k] * g_w[k] for k in keys)
        norm_p = math.sqrt(sum(p_w[k] ** 2 for k in keys))
        norm_g = math.sqrt(sum(g_w[k] ** 2 for k in keys))
        if norm_p == 0 or norm_g == 0:
            sims.append(0.0)
        else:
            sims.append(dot / (norm_p * norm_g))
    return sum(sims) / len(sims)


# ---------------------------------------------------------------------------
# 指标 5：poi_f1（start/end POI 名称匹配 F1）
# ---------------------------------------------------------------------------
def poi_f1(pairs: list[tuple]) -> float:
    """把 start / end 两个槽位合并统计 POI 名称匹配 F1。

    - gold 槽位有 POI：预测同名 -> TP；预测缺/不同 -> FN
    - gold 槽位无 POI：预测有 POI -> FP
    """
    tp = fp = fn = 0
    for pred, gold in pairs:
        for slot in ("start", "end"):
            g_ref = _get(gold, slot)
            p_ref = _get(pred, slot)
            g_name = _poi_name(g_ref)
            p_name = _poi_name(p_ref)
            if g_name:  # gold 有 POI
                if p_name == g_name:
                    tp += 1
                else:
                    fn += 1
            else:  # gold 无 POI
                if p_name:
                    fp += 1
    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    if prec + rec == 0:
        return 0.0
    return 2 * prec * rec / (prec + rec)


# ---------------------------------------------------------------------------
# 指标 6：parse_failure_rate 兜底/解析失败比例
# ---------------------------------------------------------------------------
def parse_failure_rate(pairs: list[tuple]) -> float:
    """task_type=unknown 或 ambiguity 含"解析失败"即视为解析失败。"""
    if not pairs:
        return 0.0
    fail = 0
    for pred, _gold in pairs:
        tt = _get(pred, "task_type")
        amb = _get(pred, "ambiguity") or ""
        if tt == "unknown":
            fail += 1
        elif "解析失败" in str(amb):
            fail += 1
    return fail / len(pairs)


# ---------------------------------------------------------------------------
# 汇总：一次计算全部 E1 指标
# ---------------------------------------------------------------------------
def weight_sample_count(pairs: list[tuple]) -> int:
    """gold weights != null 的配对数（权重指标的实际分母，便于论文中报告）。"""
    return sum(1 for _p, g in pairs if _weight_vec(_get(g, "weights")) is not None)


def evaluate_all(pairs: list[tuple]) -> dict:
    """计算全部 E1 指标，返回扁平 dict（constraint_macro_f1 展开为 3 个键）。"""
    cf1 = constraint_macro_f1(pairs)
    return {
        "n_pairs": len(pairs),
        "weight_n": weight_sample_count(pairs),
        "task_type_accuracy": round(task_type_accuracy(pairs), 4),
        "constraint_macro_f1_distance": round(cf1["distance"], 4),
        "constraint_macro_f1_slope": round(cf1["slope"], 4),
        "constraint_macro_f1_scenery": round(cf1["scenery"], 4),
        "constraint_macro_f1_avg": round((cf1["distance"] + cf1["slope"] + cf1["scenery"]) / 3, 4),
        "weight_mae": round(weight_mae(pairs), 4),
        "weight_cosine_sim": round(weight_cosine_sim(pairs), 4),
        "poi_f1": round(poi_f1(pairs), 4),
        "parse_failure_rate": round(parse_failure_rate(pairs), 4),
    }


if __name__ == "__main__":
    # 自检：用一组构造的 (pred, gold) 验证各指标
    g1 = {"task_type": "path_planning", "start": {"name": "珞珈门", "type": "poi"},
          "end": {"name": "樱顶", "type": "poi"},
          "constraints": {"distance": "medium", "slope": "avoid", "scenery": "normal"},
          "weights": {"distance": 0.2, "slope": 0.6, "scenery": 0.2}, "mode": "walk"}
    p1 = {"task_type": "path_planning", "start": {"name": "珞珈门", "type": "poi"},
          "end": {"name": "樱顶", "type": "poi"},
          "constraints": {"distance": "medium", "slope": "avoid", "scenery": "high"},
          "weights": {"distance": 0.3, "slope": 0.5, "scenery": 0.2}, "mode": "walk"}
    g2 = {"task_type": "help", "start": None, "end": None,
          "constraints": {"distance": "medium", "slope": "normal", "scenery": "normal"},
          "weights": None, "mode": "walk"}
    p2 = {"task_type": "chat", "start": None, "end": None, "ambiguity": None,
          "constraints": {"distance": "medium", "slope": "normal", "scenery": "normal"},
          "weights": None, "mode": "walk"}
    demo = [(p1, g1), (p2, g2)]
    import json
    print(json.dumps(evaluate_all(demo), ensure_ascii=False, indent=2))
