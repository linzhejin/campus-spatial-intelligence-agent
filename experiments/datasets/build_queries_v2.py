"""
E1 测试集 v2-draft 生成器（目标 200 条，方案 v1 §五 的 L1-L7 分层）。

产出：
  experiments/datasets/queries_v2.json      — 200 条 query（含 20 组多轮对话）
  experiments/datasets/gold_intents_v2.json — 每条 query 的 gold TaskIntent

设计要点：
  1. 起终点 POI 全部从真实 pois.json 采样，并经 find_poi 解析为规范名，
     保证 E2 路径实验"每条 path_planning gold 都可解析、可算路径"；
  2. 权重 gold 采用 prompt v1.1 的区间中值约定（单维 0.65、双维 0.45+0.10、
     强烈单维 0.80），标注为 draft，需 3 人独立标注取平均做最终校准；
  3. 模板做口语化变换（"咋走/麻烦问下/扫个共享单车"），50% 以上条目不落在
     few-shot 句式上；
  4. 随机采样使用固定 seed=20260911，完全可复现；
  5. L5 多轮条目的 query 字段为对话首句（兼容旧 runner），明细在 conversation 字段。

用法：
  python experiments/datasets/build_queries_v2.py
"""
from __future__ import annotations

import json
import random
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from spatial.poi import load_pois, find_poi  # noqa: E402

RNG = random.Random(20260911)
OUT_DIR = Path(__file__).resolve().parent

DEFAULT_CONS = {"distance": "medium", "slope": "normal", "scenery": "normal"}

# ---------------------------------------------------------------------------
# 偏好表达库：偏好类型 → (表达短语库, 约束维度, 约束值, 权重强度)
# ---------------------------------------------------------------------------
PREF_LIB = {
    "distance_short": (["走最近的路", "哪条路最短", "赶时间，要最快的路线", "越近越好"],
                       "distance", "short", "strong"),
    "distance_relaxed": (["不着急，慢慢走", "时间宽裕，随便逛逛"], "distance", "relaxed", "normal"),
    "slope": (["避开陡坡", "膝盖不好不想爬坡", "走平缓一点的路", "别走台阶"],
              "slope", "avoid", "normal"),
    "scenery": (["走风景好的路", "想一路看风景", "挑景色漂亮的路", "走适合拍照的路线"],
                "scenery", "high", "normal"),
}

PATH_TEMPLATES = [
    "从{a}到{b}",
    "从{a}去{b}怎么走",
    "{a}到{b}咋走",
    "我想从{a}走到{b}",
    "麻烦问下{a}去{b}的路",
    "帮我规划一条{a}到{b}的路线",
    "{a}出发去{b}",
    "从{a}到{b}的步行路线",
]

MODE_CLAUSES = {
    "bike": ["骑车", "骑自行车", "扫个共享单车", "骑单车"],
    "drive": ["开车", "打车", "自驾过去", "驾车"],
    "walk": ["走路", "步行", "走着去", ""],
}

# ---------------------------------------------------------------------------
# 权重约定（draft；最终由 3 人标注平均校准）
# ---------------------------------------------------------------------------
def weights_for(active: list[tuple[str, str, str]]) -> dict | None:
    """active: [(dim, level, strength), ...] → gold weights。"""
    if not active:
        return None
    strong = [a for a in active if a[2] == "strong"]
    if len(active) == 1:
        dim = active[0][0]
        val = 0.8 if active[0][2] == "strong" else 0.65
        rest = round((1 - val) / 2, 3)
        w = {"distance": rest, "slope": rest, "scenery": rest}
        w[dim] = round(val, 3)
        return w
    if len(active) == 2:
        w = {"distance": 0.1, "slope": 0.1, "scenery": 0.1}
        for dim, _lvl, strength in active:
            w[dim] = 0.45 if strength == "normal" else 0.5
        return w
    # 3 个活跃维度
    w = {"distance": round(1 / 3, 3), "slope": round(1 / 3, 3), "scenery": round(1 / 3, 3)}
    if strong:
        w[strong[0][0]] = 0.5
        others = [d for d in ("distance", "slope", "scenery") if d != strong[0][0]]
        w[others[0]] = 0.25
        w[others[1]] = 0.25
    return w


def constraints_for(active: list[tuple[str, str, str]]) -> dict:
    cons = dict(DEFAULT_CONS)
    for dim, level, _s in active:
        cons[dim] = level
    return cons


# ---------------------------------------------------------------------------
# POI 采样
# ---------------------------------------------------------------------------
def build_poi_pool():
    """规范名可解析、名字长度适中的 POI 池（排除区域点和宿舍，保证问路语义自然）。"""
    pool = []
    for p in load_pois():
        if p.get("type") in ("dorm", "area"):
            continue
        name = p.get("name", "")
        if not 2 <= len(name) <= 14:
            continue
        resolved = find_poi(name)
        if not resolved or resolved.get("name") != name:
            continue  # 只保留自身即规范名的 POI
        pool.append(name)
    # 去重保序
    seen = set()
    uniq = []
    for n in pool:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


def sample_pair(pool: list[str]) -> tuple[str, str]:
    a, b = RNG.sample(pool, 2)
    return a, b


def make_path_gold(a: str, b: str, active, mode: str = "walk") -> dict:
    return {
        "task_type": "path_planning",
        "start": {"name": a, "type": "poi"},
        "end": {"name": b, "type": "poi"},
        "constraints": constraints_for(active),
        "weights": weights_for(active),
        "mode": mode,
    }


# ---------------------------------------------------------------------------
# 主生成
# ---------------------------------------------------------------------------
def main() -> int:
    pool = build_poi_pool()
    print(f"POI 池: {len(pool)} 个可解析规范名 POI")

    queries: list[dict] = []
    gold: dict[str, dict] = {}
    seq = {"n": 0}

    def add(qid: str, category: str, text: str, g: dict):
        seq["n"] += 1
        queries.append({"id": qid, "query": text, "category": category, "gold": g})
        gold[qid] = g

    def pref_tail(active) -> str:
        if not active:
            return ""
        phrases = []
        for dim, _lvl, _s in active:
            key = {"distance": "distance_short", "slope": "slope", "scenery": "scenery"}[dim]
            if dim == "distance" and _lvl == "relaxed":
                key = "distance_relaxed"
            phrases.append(RNG.choice(PREF_LIB[key][0]))
        return "，".join(phrases)

    def path_query(a, b, active, mode):
        q = RNG.choice(PATH_TEMPLATES).format(a=a, b=b)
        if mode != "walk" or RNG.random() < 0.25:
            choices = [c for c in MODE_CLAUSES[mode] if c]
            clause = RNG.choice(choices)
            if clause and "从" in q:
                q = q.replace("从", clause + "从", 1)
            elif clause:
                q = clause + "，" + q
        tail = pref_tail(active)
        if tail:
            q += "，" + tail
        return q

    # ---- L1 简单路径 35 ----
    for i in range(1, 36):
        a, b = sample_pair(pool)
        qid = f"L1_{i:03d}"
        add(qid, "L1_simple", path_query(a, b, [], "walk"), make_path_gold(a, b, []))

    # ---- L2 单维偏好 55（distance 20 / slope 18 / scenery 17）----
    specs = (
        [("distance", "short", "strong")] * 12
        + [("distance", "relaxed", "normal")] * 8
        + [("slope", "avoid", "normal")] * 18
        + [("scenery", "high", "normal")] * 17
    )
    RNG.shuffle(specs)
    for i, (dim, lvl, strength) in enumerate(specs, start=1):
        a, b = sample_pair(pool)
        active = [(dim, lvl, strength)]
        add(f"L2_{i:03d}", "L2_single_pref", path_query(a, b, active, "walk"),
            make_path_gold(a, b, active))

    # ---- L3 多维组合 40 ----
    combo_sets = [
        [("slope", "avoid", "normal"), ("scenery", "high", "normal")],
        [("distance", "short", "strong"), ("slope", "avoid", "normal")],
        [("distance", "short", "strong"), ("scenery", "high", "normal")],
        [("distance", "relaxed", "normal"), ("scenery", "high", "normal")],
        [("distance", "short", "strong"), ("slope", "avoid", "normal"), ("scenery", "high", "normal")],
    ]
    combo_weights = [12, 10, 8, 6, 4]
    idx = 0
    for combo, cnt in zip(combo_sets, combo_weights):
        for _ in range(cnt):
            idx += 1
            a, b = sample_pair(pool)
            add(f"L3_{idx:03d}", "L3_multi_pref", path_query(a, b, combo, "walk"),
                make_path_gold(a, b, combo))

    # ---- L4 出行方式 25（bike 10 / drive 8 / walk 7）----
    modes = [("bike", 10), ("drive", 8), ("walk", 7)]
    idx = 0
    for mode, cnt in modes:
        for _ in range(cnt):
            idx += 1
            a, b = sample_pair(pool)
            add(f"L4_{idx:03d}", "L4_mode", path_query(a, b, [], mode),
                make_path_gold(a, b, [], mode))

    # ---- L6 非路径 15（poi_query 7 / help 4 / chat 4）----
    poi_query_targets = ["樱顶", "凌波门", "老图书馆", "万林艺术博物馆", "樱花大道", "宋卿体育馆", "总图"]
    poi_query_tmpl = ["{x}在哪", "{x}在哪里啊", "{x}是什么地方", "请问{x}在哪儿", "{x}怎么去", "找一下{x}", "{x}位置"]
    for i, target in enumerate(poi_query_targets, start=1):
        resolved = find_poi(target)
        canon = resolved["name"] if resolved else target
        text = RNG.choice(poi_query_tmpl).format(x=target)
        add(f"L6_{i:03d}", "L6_poi_query", text, {
            "task_type": "poi_query",
            "start": {"name": canon, "type": "poi"},
            "end": None,
            "constraints": dict(DEFAULT_CONS),
            "weights": None,
            "mode": "walk",
        })
    help_queries = ["你能做什么", "这个系统有啥功能", "怎么用你", "推荐几个景点"]
    for j, text in enumerate(help_queries, start=8):
        add(f"L6_{j:03d}", "L6_help", text, {
            "task_type": "help", "start": None, "end": None,
            "constraints": dict(DEFAULT_CONS), "weights": None, "mode": "walk",
        })
    chat_queries = ["今天天气怎么样", "樱花什么时候开", "桂园食堂好吃吗", "你是谁"]
    for j, text in enumerate(chat_queries, start=12):
        add(f"L6_{j:03d}", "L6_chat", text, {
            "task_type": "chat", "start": None, "end": None,
            "constraints": dict(DEFAULT_CONS), "weights": None, "mode": "walk",
        })

    # ---- L7 鲁棒性 10（别名 5 / 口语化 3 / 校外 2）----
    alias_pairs = [
        ("牌坊", "樱顶"),
        ("教五", "总图"),
        ("老斋舍", "宋卿体育馆"),
        ("樱花大道", "凌波门"),
        ("珞珈门", "枫园"),
    ]
    for i, (al, dst) in enumerate(alias_pairs, start=1):
        a_poi = find_poi(al)
        b_poi = find_poi(dst)
        a = a_poi["name"] if a_poi else al
        b = b_poi["name"] if b_poi else dst
        text = f"从{al}到{dst}"
        add(f"L7_{i:03d}", "L7_alias", text, make_path_gold(a, b, []))
    colloquial = [
        ("嗯…我想问下，{a}咋去{b}啊", [],),
        ("{a}到{b}远不远，给条好走的道", [("slope", "avoid", "normal")]),
        ("快快快，{a}去{b}", [("distance", "short", "strong")]),
    ]
    for j, (tmpl, active) in enumerate(colloquial, start=6):
        a, b = sample_pair(pool)
        add(f"L7_{j:03d}", "L7_colloquial", tmpl.format(a=a, b=b),
            make_path_gold(a, b, active))
    external = ["怎么去华中师范大学", "华中科技大学离这多远"]
    for j, text in enumerate(external, start=9):
        add(f"L7_{j:03d}", "L7_external", text, {
            "task_type": "unknown", "start": None, "end": None,
            "constraints": dict(DEFAULT_CONS), "weights": None, "mode": "walk",
        })

    # ---- L5 多轮对话 20 组 ----
    # 每个 pattern 返回 (turns_text_gold)；槽位 gold 按承接后的完整意图标注
    def tg(text, g):
        return {"query": text, "gold": g}

    def make_conversations():
        cases = []

        def new_case(cid, a, b, turns, pref=None, c=None):
            active = pref or []
            cases.append({"id": cid, "poi_pair": (a, b), "turns": turns, "active": active, "extra": c})

        # p1: 只给起点 → 补终点 → 加偏好（4 组）
        for k in range(4):
            a, b = sample_pair(pool)
            pref = RNG.choice([
                [("slope", "avoid", "normal")],
                [("scenery", "high", "normal")],
                [("distance", "short", "strong")],
            ])
            tail = pref_tail(pref)
            turns = [
                tg(f"从{a}出发", {
                    "task_type": "path_planning",
                    "start": {"name": a, "type": "poi"}, "end": None,
                    "constraints": dict(DEFAULT_CONS), "weights": None, "mode": "walk",
                    "ambiguity": "请指定终点"}),
                tg(f"去{b}", {
                    "task_type": "path_planning",
                    "start": {"name": a, "type": "poi"},
                    "end": {"name": b, "type": "poi"},
                    "constraints": dict(DEFAULT_CONS), "weights": None, "mode": "walk"}),
                tg(tail, {
                    "task_type": "path_planning",
                    "start": {"name": a, "type": "poi"},
                    "end": {"name": b, "type": "poi"},
                    "constraints": constraints_for(pref),
                    "weights": weights_for(pref), "mode": "walk"}),
            ]
            new_case(f"L5_{len(cases)+1:03d}", a, b, turns, pref)

        # p2: 只给终点 → 补起点（3 组）
        for _ in range(3):
            a, b = sample_pair(pool)
            turns = [
                tg(f"想去{b}", {
                    "task_type": "path_planning", "start": None,
                    "end": {"name": b, "type": "poi"},
                    "constraints": dict(DEFAULT_CONS), "weights": None, "mode": "walk",
                    "ambiguity": "请指定起点"}),
                tg(f"从{a}", {
                    "task_type": "path_planning",
                    "start": {"name": a, "type": "poi"},
                    "end": {"name": b, "type": "poi"},
                    "constraints": dict(DEFAULT_CONS), "weights": None, "mode": "walk"}),
            ]
            new_case(f"L5_{len(cases)+1:03d}", a, b, turns)

        # p3: 已规划 → 调整约束（4 组）
        for _ in range(4):
            a, b = sample_pair(pool)
            pref = RNG.choice([
                [("slope", "avoid", "normal")],
                [("scenery", "high", "normal")],
                [("distance", "short", "strong")],
                [("slope", "avoid", "normal"), ("scenery", "high", "normal")],
            ])
            turns = [
                tg(f"从{a}到{b}", make_path_gold(a, b, [])),
                tg("换成" + pref_tail(pref) + "的路线", make_path_gold(a, b, pref)),
            ]
            new_case(f"L5_{len(cases)+1:03d}", a, b, turns, pref)

        # p4: 已规划 → 改出行方式（3 组）
        for _ in range(3):
            a, b = sample_pair(pool)
            turns = [
                tg(f"从{a}到{b}", make_path_gold(a, b, [], "walk")),
                tg(RNG.choice(["还是骑车去吧", "改成骑车", "我想骑单车过去"]),
                   make_path_gold(a, b, [], "bike")),
            ]
            new_case(f"L5_{len(cases)+1:03d}", a, b, turns)

        # p5: 已规划 → 修正终点（3 组）
        for _ in range(3):
            a, b = sample_pair(pool)
            c = RNG.choice([p for p in pool if p not in (a, b)])
            turns = [
                tg(f"从{a}到{b}", make_path_gold(a, b, [])),
                tg(f"不对，去{c}", make_path_gold(a, c, [])),
            ]
            new_case(f"L5_{len(cases)+1:03d}", a, b, turns, c=c)

        # p6: 规划 → 景点查询（不得污染槽位）→ 回到原规划加偏好（3 组）
        for _ in range(3):
            a, b = sample_pair(pool)
            x = RNG.choice(["万林艺术博物馆", "樱花大道", "凌波门"])
            x_poi = find_poi(x)
            x_name = x_poi["name"] if x_poi else x
            pref = [("slope", "avoid", "normal")]
            turns = [
                tg(f"从{a}到{b}", make_path_gold(a, b, [])),
                tg(f"{x}在哪", {
                    "task_type": "poi_query",
                    "start": {"name": x_name, "type": "poi"}, "end": None,
                    "constraints": dict(DEFAULT_CONS), "weights": None, "mode": "walk"}),
                tg("那继续，" + pref_tail(pref), make_path_gold(a, b, pref)),
            ]
            new_case(f"L5_{len(cases)+1:03d}", a, b, turns, pref)

        return cases

    for case in make_conversations():
        first_text = case["turns"][0]["query"]
        first_gold = case["turns"][0]["gold"]
        queries.append({
            "id": case["id"],
            "query": first_text,
            "category": "L5_multiturn",
            "gold": first_gold,
            "conversation": case["turns"],
        })
        for ti, turn in enumerate(case["turns"], start=1):
            gold[f"{case['id']}#t{ti}"] = turn["gold"]
        gold[case["id"]] = first_gold

    # ---- 落盘 ----
    assert len(queries) == 200, f"期望 200 条，实际 {len(queries)}"
    q_doc = {
        "version": "v2-draft",
        "generated_at": str(date.today()),
        "seed": 20260911,
        "description": (
            "200 条分层测试集草稿（L1-L7）。起终点均解析为 POI 规范名；"
            "权重 gold 为 prompt 区间中值约定，需 3 人独立标注平均后定稿；"
            "L5 为多轮对话，逐轮 gold 的 key 为 <id>#t<n>。"
        ),
        "queries": queries,
    }
    g_doc = {"version": "v2-draft", "generated_at": str(date.today()), "intents": gold}
    (OUT_DIR / "queries_v2.json").write_text(
        json.dumps(q_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "gold_intents_v2.json").write_text(
        json.dumps(g_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    # 分层统计
    from collections import Counter
    cnt = Counter(q["category"] for q in queries)
    print(f"完成：{len(queries)} 条 query，{len(gold)} 条 gold（含多轮逐轮）")
    for k in sorted(cnt):
        print(f"  {k}: {cnt[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
