import re
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PARSE_PATH = BASE / "agents" / "prompts" / "parse_system.txt"
EXPLAIN_PATH = BASE / "agents" / "prompts" / "explain_system.txt"

def count_parse_fewshot(text):
    # 匹配 "输入："..." 或 输入："..." 格式
    pattern = r'输入："[^"]*"\s*\n输出：\{'
    return len(re.findall(pattern, text))

def count_explain_fewshot(text):
    pattern = r'示例 \d+'
    return len(re.findall(pattern, text))

def validate_parse():
    results = {}
    text = PARSE_PATH.read_text(encoding="utf-8")

    # 1. 任务类型说明：需包含4种类型
    results["1_任务类型说明"] = bool(
        (re.search(r'任务类型判断|任务类型限定', text)) and
        re.search(r'"path_planning"', text) and
        re.search(r'"poi_query"', text) and
        re.search(r'"help"', text) and
        re.search(r'"unknown"', text)
    )

    # 2. 约束等级说明：需包含三因素等级描述
    results["2_约束等级说明"] = bool(
        re.search(r'约束等级', text) and
        re.search(r'short', text) and
        re.search(r'medium', text) and
        re.search(r'relaxed', text) and
        re.search(r'avoid', text) and
        re.search(r'any', text) and
        re.search(r'high', text) and
        re.search(r'normal', text)
    )

    # 3. 权重生成规则：需说明 null 和范围
    results["3_权重生成规则"] = bool(
        re.search(r'权重.*生成规则|权重（软成本）', text) and
        re.search(r'weights.*null|weights 必须为 null', text) and
        re.search(r'\[0\.05,?\s*0\.8\]', text)
    )

    results["4_POI列表占位"] = "{poi_list_json}" in text

    results["5_输出JSON格式"] = bool(
        re.search(r'输出格式', text) and
        re.search(r'"task_type"', text) and
        re.search(r'"constraints"', text) and
        re.search(r'"weights"', text) and
        re.search(r'"ambiguity"', text)
    )

    results["6_FewShot>=5"] = count_parse_fewshot(text) >= 5
    results["6_FewShot实际数量"] = count_parse_fewshot(text)

    ambiguity_covered = (
        '请指定起点' in text and
        '请指定终点' in text and
        '无法识别' in text
    )
    results["额外_ambiguity触发规则"] = ambiguity_covered

    task_types_covered = (
        '"path_planning"' in text and
        '"poi_query"' in text and
        '"help"' in text and
        '"unknown"' in text
    )
    results["额外_4种task_type覆盖"] = task_types_covered

    return results

def validate_explain():
    results = {}
    text = EXPLAIN_PATH.read_text(encoding="utf-8")

    # 1. 解释生成规则：需说明解释生成的基本规则
    results["1_解释生成规则"] = "解释" in text and "助手" in text

    # 2. 必含3要素：显式回应约束 + 列出途经景点 + 距离对比
    results["2_必含3要素"] = bool(
        (re.search(r'显式|必须.*回应|逐一回应', text)) and
        (re.search(r'列出|途经.*景点', text)) and
        (re.search(r'距离对比|最短路径.*差异|与最短', text))
    )

    results["3_<=150字限制"] = "≤150" in text or "<=150" in text or "150 字" in text

    results["4_FewShot>=2"] = count_explain_fewshot(text) >= 2
    results["4_FewShot实际数量"] = count_explain_fewshot(text)

    # 模板兜底：含降级/degraded 相关说明
    template_fallback = "降级" in text or "模板兜底" in text
    results["额外_模板兜底说明"] = template_fallback

    return results

def main():
    print("=" * 60)
    print("T-005 Prompt 验收验证")
    print("=" * 60)

    parse_res = validate_parse()
    print("\n【parse_system.txt 验收 6/6】")
    parse_pass = 0
    for i in range(1, 7):
        key = f"{i}_"
        item = {k: v for k, v in parse_res.items() if k.startswith(key)}
        for k, v in item.items():
            if isinstance(v, bool):
                status = "✓ PASS" if v else "✗ FAIL"
                if v:
                    parse_pass += 1
                print(f"  {status}  {k}")
            else:
                print(f"      统计: {k} = {v}")

    for k, v in parse_res.items():
        if k.startswith("额外"):
            status = "✓" if v else " "
            print(f"    {status} [额外] {k}: {v}")

    explain_res = validate_explain()
    print("\n【explain_system.txt 验收 4/4】")
    explain_pass = 0
    for i in range(1, 5):
        key = f"{i}_"
        item = {k: v for k, v in explain_res.items() if k.startswith(key)}
        for k, v in item.items():
            if isinstance(v, bool):
                status = "✓ PASS" if v else "✗ FAIL"
                if v:
                    explain_pass += 1
                print(f"  {status}  {k}")
            else:
                print(f"      统计: {k} = {v}")

    for k, v in explain_res.items():
        if k.startswith("额外"):
            status = "✓" if v else " "
            print(f"    {status} [额外] {k}: {v}")

    print("\n" + "=" * 60)
    print(f"汇总: Parse {parse_pass}/6  Explain {explain_pass}/4")
    overall = parse_pass == 6 and explain_pass == 4
    print(f"整体结果: {'Status DONE ✓' if overall else 'Status FAIL ✗'}")
    print("=" * 60)

    return {"parse_pass": parse_pass, "explain_pass": explain_pass, "overall": overall}

if __name__ == "__main__":
    main()
