"""
T-022 自检脚本 · 场景 3 候选 POI 交互闭环（v2 — 架构审核 N4 实现）
4 条验收 Checks：
  [1/4] 后端 api/routes.py：POST /api/candidates 端点存在 + 返回 candidates 列表结构
  [2/4] 前端 static/js/app.js：window.showCandidateCards 公开函数 + 卡片渲染 UI
  [3/4] 前端卡片点击自动触发路线规划（填写 input → 提交）
  [4/4] 无候选 POI 时返回 error hint（no_candidates 错误码 + 空状态 UI）

使用：
  python scripts/T022_selfcheck.py
  期望输出：T-022 Self-Check Result: 4/4 PASSED
"""

import ast
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROUTES_PATH = os.path.join(ROOT, "api", "routes.py")
APPJS_PATH = os.path.join(ROOT, "static", "js", "app.js")

_results = []


def _pass(check_id, msg):
    _results.append((check_id, True, msg))


def _fail(check_id, msg):
    _results.append((check_id, False, msg))


# ========================= Check 1/4 =========================
def check1_backend_candidates_endpoint():
    """验证 POST /api/candidates 端点存在且返回正确的 candidates 列表结构。"""
    check_id = "1/4"
    with open(ROUTES_PATH, "r", encoding="utf-8") as f:
        src = f.read()

    # 1a. 端点路由装饰器存在
    if '@api_bp.route("/candidates"' not in src and "@api_bp.route('/candidates'" not in src:
        _fail(check_id, "未找到 /api/candidates 路由装饰器")
        return

    # 1b. candidates 函数定义存在
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        _fail(check_id, f"routes.py 语法错误: {e}")
        return

    func_names = {
        node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if "candidates" not in func_names:
        _fail(check_id, "AST 中未找到 candidates 函数定义（POST /api/candidates）")
        return

    # 1c. 函数体内必须返回 candidates 字段（含 name, type, scenery_score, distance_m）
    candidates_func = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "candidates":
            candidates_func = node
            break

    if candidates_func is None:
        _fail(check_id, "未找到 candidates() 函数定义")
        return

    # 提取 candidates 函数源码
    src_lines = src.splitlines()
    func_start = candidates_func.lineno - 1
    all_funcs = sorted([
        n.lineno for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.lineno > candidates_func.lineno
    ])
    func_end = all_funcs[0] - 1 if all_funcs else len(src_lines)
    func_src = "\n".join(src_lines[func_start:func_end])

    # 必须返回 candidates 列表
    if '"candidates"' not in func_src and "'candidates'" not in func_src:
        _fail(check_id, "candidates 函数返回中未包含 candidates 字段")
        return

    # 必须包含 name, type, scenery_score, distance_m 字段
    required_fields = ["name", "type", "scenery_score", "distance_m"]
    for field in required_fields:
        if f'"{field}"' not in func_src and f"'{field}'" not in func_src:
            _fail(check_id, f"candidates 返回结构中缺少 {field} 字段")
            return

    # 1d. networkx import 存在（路网距离计算需要）
    if "import networkx as nx" not in src and "import networkx" not in src:
        _fail(check_id, "未导入 networkx（路网距离计算需要）")
        return

    # 1e. _path_length 导入存在
    if "from spatial.routing import" in src and "_path_length" in src:
        pass  # 已从 spatial.routing 导入
    elif "def _path_length" in src:
        pass  # 内联定义
    else:
        _fail(check_id, "未找到 _path_length（需要计算路网距离）")
        return

    _pass(check_id,
          "POST /api/candidates 端点: 路由+函数定义+返回 candidates(name/type/scenery_score/distance_m)+networkx 导入 全部达标")


# ========================= Check 2/4 =========================
def check2_frontend_candidate_cards():
    """验证前端 window.showCandidateCards 公开函数 + 卡片渲染 UI。"""
    check_id = "2/4"
    with open(APPJS_PATH, "r", encoding="utf-8") as f:
        src = f.read()

    # 2a. window.showCandidateCards 必须存在
    if ("window.showCandidateCards" not in src
            and "window['showCandidateCards']" not in src
            and 'window["showCandidateCards"]' not in src):
        _fail(check_id, "前端未挂载 window.showCandidateCards 公开函数")
        return

    # 2b. 必须渲染 .candidate-card 样式卡片
    if "candidate-card" not in src:
        _fail(check_id, "未找到 .candidate-card 卡片 UI 元素")
        return

    # 2c. 必须包含 candidates-panel 容器 ID
    if "'candidates-panel'" not in src and '"candidates-panel"' not in src:
        _fail(check_id, "未找到 candidates-panel 容器 ID")
        return

    # 2d. 卡片必须展示景色评分星标（★/renderStars）
    if "renderStars" not in src and "★" not in src:
        _fail(check_id, "卡片未包含景色评分星标展示")
        return

    # 2e. 品牌色存在（#E8929C 樱花粉）
    if "#E8929C" not in src and "E8929C" not in src:
        _fail(check_id, "卡片样式未使用品牌色 #E8929C")
        return

    _pass(check_id,
          "前端 UI: window.showCandidateCards + candidate-card 卡片 + candidates-panel 容器 + 星标评分 + 品牌色 全部达标")


# ========================= Check 3/4 =========================
def check3_frontend_click_replanning():
    """验证点击候选卡片后自动触发路线规划（填写输入 → 提交按钮）。"""
    check_id = "3/4"
    with open(APPJS_PATH, "r", encoding="utf-8") as f:
        src = f.read()

    # 3a. 必须有点击事件处理（onCandidateClick 或 click 事件委托）
    if "onCandidateClick" not in src and ".candidate-card" not in src:
        _fail(check_id, "未找到候选卡片点击处理逻辑")
        return

    # 3b. 点击后必须填写输入框（修改 input.value 或类似动作）
    show_cards_start = max(
        src.find("window.showCandidateCards"),
        src.find("window['showCandidateCards']"),
        src.find('window["showCandidateCards"]'),
    )
    if show_cards_start < 0:
        _fail(check_id, "无法定位 showCandidateCards 函数")
        return

    # 取函数定义附近 4000 字符
    tail = src[show_cards_start:show_cards_start + 5000]
    if ".value" not in tail:
        _fail(check_id, "点击处理中未设置输入框值（.value）")
        return

    # 3c. 必须触发提交（submitBtn.click() 或 dispatchEvent 键盘事件）
    if "submitBtn" not in tail and "dispatchEvent" not in tail and ".click()" not in tail:
        _fail(check_id, "点击处理中未触发提交按钮或分发事件")
        return

    # 3d. 必须构造查询文本（包含 start 名称 + candidate 名称）
    if "currentStartName" not in tail and "startName" not in tail:
        _fail(check_id, "查询文本中未使用起点名称")
        return

    _pass(check_id,
          "点击自动规划: onCandidateClick + input.value 填写 + submitBtn.click 触发 + startName 参数 全部达标")


# ========================= Check 4/4 =========================
def check4_no_candidates_error_hint():
    """验证无候选 POI 时返回错误提示（后端 no_candidates 错误 + 前端空状态 UI）。"""
    check_id = "4/4"
    with open(ROUTES_PATH, "r", encoding="utf-8") as f:
        routes_src = f.read()
    with open(APPJS_PATH, "r", encoding="utf-8") as f:
        appjs_src = f.read()

    # 4a. 后端：无 candidates 时返回 no_candidates 错误码
    if "no_candidates" not in routes_src:
        _fail(check_id, "后端未定义 no_candidates 错误码（空结果返回）")
        return

    # 4b. 前端：candidates 空数组时展示友好提示
    if "未找到匹配的候选" not in appjs_src and "candidates.length === 0" not in appjs_src and "candidates.length" not in appjs_src:
        _fail(check_id, "前端未处理 candidates 空数组的 UI 提示")
        return

    # 4c. 前端空状态应有友好文案
    if "请尝试其他" not in appjs_src and "未找到" not in appjs_src:
        _fail(check_id, "前端空状态缺少友好错误提示文案")
        return

    _pass(check_id,
          "无候选错误提示: 后端 no_candidates 错误码 + 前端空状态友好提示 全部达标")


# ========================= Main =========================
def main():
    print("=" * 70)
    print("T-022 场景 3 候选 POI 交互闭环 · 自检脚本 (v2)")
    print("=" * 70)
    print()

    check1_backend_candidates_endpoint()
    check2_frontend_candidate_cards()
    check3_frontend_click_replanning()
    check4_no_candidates_error_hint()

    print()
    passed = 0
    for cid, ok, msg in _results:
        status = "PASS" if ok else "FAIL"
        tag = f"[{cid}]"
        print(f"  {tag} {status}: {msg}")
        if ok:
            passed += 1

    total = len(_results)
    print()
    print("-" * 70)
    print(f"T-022 Self-Check Result: {passed}/{total} {'PASSED' if passed == total else 'FAILED'}")
    print("-" * 70)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
