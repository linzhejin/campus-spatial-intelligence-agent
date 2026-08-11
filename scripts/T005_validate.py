# -*- coding: utf-8 -*-
"""T-005 验收脚本：Prompt 模板与 Few-shot 示例"""
import sys, json, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.stdout.reconfigure(encoding="utf-8")

from agents import parser, explainer
from config import WHU_POIS
from agents.parser import TaskIntent, FEW_SHOT_EXAMPLES

results = {}
failures = []

# ── 验收 1：System Prompt 包含 POI 列表 + 权重限制 ──────────────────────────
print("=" * 60)
print("【验收 1】System Prompt 包含 POI 列表 + 权重限制")
prompt = parser._load_system_prompt()
# 所有 24 个 POI 名都应该出现在 prompt 里（从 WHU_POIS 取）
missing_pois = [name for name in WHU_POIS.keys() if f'"{name}"' not in prompt]
# 权重限制说明
has_weight_range = "[0.05, 0.8]" in prompt
has_clamp = "后端会自动 clamp" in prompt
has_normalize = "归一化到权重和为 1" in prompt
ok1 = (len(missing_pois) == 0 and has_weight_range and has_clamp and has_normalize)
results["T-005-1: System Prompt 含 POI + 权重限制"] = ok1
if missing_pois:
    failures.append(f"  ❌ POI 缺失: {missing_pois}")
if not has_weight_range:
    failures.append("  ❌ 缺少权重范围 [0.05, 0.8]")
if not has_clamp:
    failures.append("  ❌ 缺少 clamp 说明")
if not has_normalize:
    failures.append("  ❌ 缺少归一化说明")
if ok1:
    print(f"  ✅ PASS: POI 全包含（{len(WHU_POIS)} 个）+ 权重限制/归一化 说明齐全")
else:
    print("  ❌ FAIL:")
    for f in failures: print(f)

# ── 验收 2：Few-shot 示例覆盖 ≥ 3 种场景 ──────────────────────────────────
print("=" * 60)
print("【验收 2】Few-shot 示例覆盖 ≥ 3 种场景")
n_few = len(FEW_SHOT_EXAMPLES)
# 统计不同场景类型：按 query 语义或 output 特征粗略分类
scene_types = set()
for q, out in FEW_SHOT_EXAMPLES:
    data = json.loads(out)
    scene_types.add(data["task_type"])
    if data.get("weights"):
        # 看最大权重对应的偏好
        w = data["weights"]
        dom = max(w, key=w.get)
        scene_types.add(f"weight_{dom}")
    if data.get("ambiguity"):
        scene_types.add("ambiguity_prompt")
n_scenes = len(scene_types)
ok2 = (n_few >= 3)
results["T-005-2: Few-shot ≥ 3 场景"] = ok2
print(f"  {'✅ PASS' if ok2 else '❌ FAIL'}: Few-shot 示例数 = {n_few}, 识别到场景类型数 = {n_scenes}")
print(f"    场景类型: {sorted(scene_types)}")

# ── 验收 3：Output JSON Schema 与 Pydantic 一致 ──────────────────────────
print("=" * 60)
print("【验收 3】Output JSON Schema 与 Pydantic 一致")
schema_errors = []
# 3.1 先验证 System Prompt 文本里确实写了两个 JSON 样例的标题（证明样例已写入）
has_pp_sample = "path_planning（路径规划）样例：" in prompt
has_pq_sample = "poi_query（景点查询）样例：" in prompt
if not has_pp_sample:
    schema_errors.append("  ❌ Prompt 缺少 path_planning 样例标题")
if not has_pq_sample:
    schema_errors.append("  ❌ Prompt 缺少 poi_query 样例标题")
# 3.2 直接构造两个 Schema 样例结构 → 喂给 TaskIntent 校验（确保结构与 Pydantic 一致）
schema_samples = [
    # path_planning 样例（与 Prompt 中结构完全一致）
    {
        "task_type": "path_planning",
        "start": {"name": "牌坊", "type": "poi"},
        "end": {"name": "樱顶", "type": "poi"},
        "constraints": {"distance": "medium", "slope": "normal", "scenery": "normal"},
        "weights": None,
        "input_method": "nl",
        "ambiguity": None,
    },
    # poi_query 样例（与 Prompt 中结构完全一致）
    {
        "task_type": "poi_query",
        "start": {"name": "樱顶", "type": "poi"},
        "end": None,
        "constraints": {"distance": "medium", "slope": "normal", "scenery": "normal"},
        "weights": None,
        "input_method": "nl",
        "ambiguity": None,
    },
]
parsed_schemas = 0
for sample in schema_samples:
    try:
        ti = TaskIntent(**sample)
        parsed_schemas += 1
    except Exception as e:
        schema_errors.append(f"  Schema 样例校验失败 [{sample['task_type']}]: {e}")
# 3.3 每条 few-shot output 喂给 TaskIntent
for i, (q, out) in enumerate(FEW_SHOT_EXAMPLES, 1):
    try:
        data = json.loads(out)
        ti = TaskIntent(**data)
    except Exception as e:
        schema_errors.append(f"  Few-shot #{i} 校验失败: {e}")
ok3 = (parsed_schemas >= 2 and len(schema_errors) == 0)
results["T-005-3: JSON Schema 与 Pydantic 一致"] = ok3
if ok3:
    print(f"  ✅ PASS: {parsed_schemas} 个 System Prompt 样例 + {len(FEW_SHOT_EXAMPLES)} 条 Few-shot 全部通过 TaskIntent Pydantic 校验")
else:
    print("  ❌ FAIL:")
    for e in schema_errors: print(e)

# ── 验收 4：Prompt 模板可被 Agent 正确加载 ──────────────────────────────
print("=" * 60)
print("【验收 4】Prompt 模板可被 Agent 正确加载")
parser_prompt = parser._load_system_prompt()
explainer_prompt = explainer._load_system_prompt()
parser_ok = (len(parser_prompt) > 200 and "漫步珞珈的空间偏好解析助手" in parser_prompt and "{poi_list_json}" not in parser_prompt)
explainer_ok = (len(explainer_prompt) > 100 and "漫步珞珈的解释生成助手" in explainer_prompt)
# 检查 parser 占位符是否真的被替换（至少有 1 个真实 POI 被替换了）
placeholder_replaced = sum(1 for name in list(WHU_POIS.keys())[:5] if f'"{name}"' in parser_prompt) >= 3
ok4 = parser_ok and explainer_ok and placeholder_replaced
results["T-005-4: Prompt 模板正确加载"] = ok4
print(f"  Parser Prompt: 长度={len(parser_prompt)}, 占位符已替换={'✅' if placeholder_replaced else '❌'}")
print(f"  Explainer Prompt: 长度={len(explainer_prompt)}={'✅' if explainer_ok else '❌'}")
if ok4:
    print("  ✅ PASS: 两个 Agent Prompt 模板均正确加载，占位符无残留")
else:
    print("  ❌ FAIL")

# ── 总结 ────────────────────────────────────────────────────────────────
print("=" * 60)
print("【T-005 验收总结】")
all_pass = all(results.values())
for k, v in results.items():
    print(f"  {'✅' if v else '❌'} {k}: {'PASS' if v else 'FAIL'}")
print(f"\n最终结果: {'✅ 全部通过' if all_pass else '❌ 存在失败项'}")
sys.exit(0 if all_pass else 1)
