# Task 6 Brief · 冷启动欢迎卡片 · Final QA Review

> **任务位置**：Subagent-Driven 最终自检阶段（Gate Review 前的强制 13 项清单）
> **目标**：A 代码规范 5 条 + B 验收覆盖 5 条 + C 手动浏览器模拟 3 条 = 13 checks，**13/13 才允许进入 Gate Review 提交产物**。任何一条不通过，必须回退到对应 Task 修复，不能硬过关。
> **修改范围**：**只读操作（验证）**。如果 A/B/C 发现问题 → 对应 Task 回退修复。如果全通过 → 新增 `.superpowers/sdd/task-6-report.md` 和 `T010-gate-evidence/` 截图证据。
> **要求**：必须有 evidence（脚本输出 + 至少 2 张截图/脚本模拟日志），不能只是口头说 OK。

---

## 13 项 Checklist（Implementer 必须一条一条核对，任何一条 NO 都必须修）

### Group A: 代码规范 & 全局约束（5 条）
- [ ] **A1 · Append-only 原则**：4 个改动文件（05_TASKS.md / static/index.html / static/css/style.css / static/js/app.js），新增的 welcome / T-016/017/018 相关内容**全部在文件末尾或指定位置追加，没有修改/删除任何原有代码/文字**。
  - How：分别 Read 每个文件的「新增内容之前的最后 20 行」和「新增内容之后的第一行」，确认原有内容一字没改（特别是原 app.js 的 `submitNaturalLanguageQuery` / `showPoiSidebar` / 原 index.html 的 `<body>` 结构 / 原 style.css 的 `:root` 变量 / 原 05_TASKS.md 的 T-016 原 7 条）。
- [ ] **A2 · CSS 裸色值控制**：style.css 新增 welcome 代码块里，除 4 个设计特例（#FAFAF9 / #FFF6EC / rgba(232,198,80,0.18) + rgba(232,146,156,0.32) + rgba(230,245,255,0.9) 唤回阴影 + rgba(52,140,170,0.35) 青蓝高光 inset）之外，**所有颜色都是 `var(--xxx)`**，没有任何硬编码的 `#` / `rgb(` / `rgba(`。
  - How：用 Python 正则统计新增 welcome 代码块里 `#` + `rgb(` + `rgba(` 的总数，必须 = 4~7（和 spec 的 4 个设计特例数相当）。
- [ ] **A3 · JS 零污染全局**：app.js 新增 welcome 代码块是 IIFE 封装，内部所有 `var/const/let/function` 都是闭包私有，对外只暴露**唯一**的全局对象 `window.WelcomeColdStart`（show/close/isOpen/shouldAutoShow + _debugClearLs），没有其他全局赋值。
  - How：用 Python 正则搜 app.js 新增的 welcome 代码块里（`/* ============ 漫步珞珈 · 冷启动` 到 IIFE 结尾 `})();`），除了 `window.WelcomeColdStart = {...}`，有没有 `window.xxx =` 或顶层 `var xxx =`（注意 IIFE 里面 var 的是私有，不算）。
- [ ] **A4 · Fail-soft 全链路**：任何失败路径（DOM 不存在 / localStorage 抛错 / 公开函数不存在 / shortcut action 未知）都不会崩，静默降级或走兜底分支，**不会影响原有问路核心功能**。
  - How：人工读代码的 4 处 fail-soft：① `initDomRefs()` 找不到 overlay → `return`；② `safeLsGet/safeLsSet` → try/catch；③ 调 `submitNaturalLanguageQuery` / `showPoiSidebar` 前面的 `typeof ... === 'function'`；④ `switch(action)` 的 `default:` 只 close 不报错。
- [ ] **A5 · StopPropagation 必写**：`$card.addEventListener('click', function(e){ e.stopPropagation(); })` 写了，防止点卡片内部任意位置冒泡到 mask 的 data-close-welcome=true 导致误关。
  - How：用 re.search 搜 app.js。

### Group B: 验收标准全量覆盖（5 条，对应 Task 1 写的 26 条）
- [ ] **B1 · T-016 覆盖**：05_TASKS.md T-016 新增的 8.1~8.6 共 6 条 DOM 要求（overlay id/class/hidden/aria/topbar subtitle shortcuts tips footer 五骨架 / help-btn / 5 shortcut-card 比例 3:1:1 + POI 真实）全被 T010 脚本 B 节 13 条覆盖，且全部 PASS。
  - Evidence：贴 T010 脚本 HTML section 的输出截图 / 日志。
- [ ] **B2 · T-017 覆盖**：05_TASKS.md T-017 新增的 14.1~14.13 共 13 条交互要求（自动弹卡 / 双 LS / 5 关闭方式 / 填 value / 3 数据流分支 / ？按钮唤回 / localStorage 隐私模式兜底 / 1.5s 防抖 / 动画叠加 / Esc 不关 input）全被 T010 脚本 D 节 14 条覆盖，且全部 PASS。
  - Evidence：贴 T010 脚本 JS section 的输出截图 / 日志。
- [ ] **B3 · T-018 覆盖**：05_TASKS.md T-018 新增的 7.1~7.7 共 7 条视觉要求（樱顶窗棂纹签名 / 毛玻璃质感 / 入场 280ms+stagger / 响应式 / 移动端 ≤480px / shortcut hover 微交互 / 无障碍高对比）全被 T010 脚本 C 节 24 条覆盖，且全部 PASS。
  - Evidence：贴 T010 脚本 CSS section 的输出截图 / 日志。
- [ ] **B4 · 26 条总数对应**：T-016 6 条 + T-017 13 条 + T-018 7 条 = 26 条，T010 脚本的 HTML/CSS/JS 51 条都能**映射回**这 26 条（1 条验收标准 → 多条脚本检查是允许的，但不能有任何一条验收标准没被脚本覆盖）。
  - （Implementer 只需在 report 写：6→13 / 13→14 / 7→24，总共 26→51，没有孤儿）
- [ ] **B5 · G-02~G-11 Guardrails Fail-soft 全兜底**：Spec 列的 10 条 Guardrails（G-02 已有 seen 不弹 / G-03 forever 不弹 / G-04 遮罩误关靠 stopPropagation / G-05 Esc 不误关 input / G-06 localStorage 隐私模式靠 sessionSeen / G-07 shortcut 防抖 / G-08 动画叠加靠 animating / G-09 公开函数不存在靠 typeof 检查 / G-10 POI 真实防解析失败 / G-11 JS 出错靠 IIFE 包裹 + initDomRefs return），每一条都在 A4 / D 节 / A5 中找到对应实现。
  - （Implementer 只需在 report 写：10/10 guardrails 全有实现依据，映射 A5→G-04；A4→G-05/06/09/11；D-6→G-02/03；D-11→G-07；D-12→G-08；B-9/10→G-10）

### Group C: 手动浏览器交互模拟测试（3 条，Subagent-Driven 要求 evidence）
> 有 Playwright 就真实点（推荐）；没有就写一个 Node.js 轻量脚本用 jsdom 模拟（也没有就用 Python 的正则逻辑+代码走读模拟，总之必须有 3 条 evidence，不能只说 OK）。
> 注意：如果用 Playwright，**不要真的启动高德地图 API（会抛网络错然后白屏）**，直接 `page.goto` 静态 HTML + 注入 `window.submitNaturalLanguageQuery` / `window.showPoiSidebar` 两个 mock，然后测卡片逻辑即可。

- [ ] **C1 · 首次访问 → 自动弹 → 点 X → 刷新 → 不再弹**（T-017 14.1 / 14.2）
  - Steps:
    1. `page.evaluate(() => { localStorage.clear(); window.sessionWelcomeShown = false; })` 清干净；
    2. 刷新 / 重新加载（或重新 evaluate `window.WelcomeColdStart.shouldAutoShow()`）；
    3. 断言 `WelcomeColdStart.isOpen() === true`（或 overlay 无 .hidden）；
    4. 点 #welcome-x-btn（X 按钮），断言 `WelcomeColdStart.isOpen() === false`；
    5. `assert localStorage.getItem('whu_welcome_seen') === 'true'`；
    6. `assert WelcomeColdStart.shouldAutoShow() === false`（下一次不再自动弹）。
  - Evidence：日志 or 截图。
- [ ] **C2 · 勾选"以后不再显示" → 关 → 刷 → 不弹 → 点？按钮仍能唤回高亮脉冲**（T-017 14.3 / 14.9 / E-07）
  - Steps:
    1. 清 LS；弹卡；`page.evaluate(() => document.getElementById('welcome_dont_show').checked = true)`；
    2. 点"开始使用"关；
    3. `assert localStorage.getItem('whu_welcome_dont_show_forever') === 'true'`；
    4. `assert WelcomeColdStart.shouldAutoShow() === false`；
    5. 点 `#help-btn` → 断言 `WelcomeColdStart.isOpen() === true`（？按钮 ignoreLocalStorage 唤回）；
    6. 再点 `#help-btn`（已显示时点唤回高亮）→ 断言 overlay.classList.contains('highlight') === true（或 650ms 内有 pulse class）。
  - Evidence：日志 or 截图。
- [ ] **C3 · 5 shortcut-card 3 类数据流分别正确**（T-017 14.5~14.8）
  - 三个子用例（都要测）：
    a. **路径规划（#card-sakura，data-action=path_planning）**：点卡片 → 断言 input.value = "从牌坊到樱花大道怎么走" → 提交 submitNaturalLanguageQuery 的 mock 被调用一次（参数相同）→ 不调 showPoiSidebar。
    b. **景点查询（#card-yingding，data-action=poi_query）**：点卡片 → 断言 input.value = "樱顶在哪里，有什么好逛的" → submitNaturalLanguageQuery mock 被调一次。
    c. **推荐 POI（#card-summer，data-action=recommend_poi）**：点卡片 → 断言 input.value = "我是新来的，有什么值得逛的地方推荐吗？" → showPoiSidebar mock 被调一次参数 '全部' → submitNaturalLanguageQuery mock **没被调** → 无 fetch/axios/post 负断言。
    d. 附加 C3-d（防抖 C3-d：）1.5s 内连点两次路径卡 → submitNaturalLanguageQuery mock 只被调用 1 次（不是 2 次）。
  - Evidence：日志 or 截图。

---

## 如何执行（Implementer 按推荐顺序选 1）

Option A (Best): Playwright（如果环境有）—— 真实点击
```powershell
# 写一个临时脚本（不要 commit）.superpowers/sdd/_tmp_c3_playwright.py / js，page.goto('file:///C:/.../static/index.html')，先注入两个 mock：
#   window.submitNaturalLanguageQuery = () => window.__submit_calls.push(arguments); window.__submit_calls = [];
#   window.showPoiSidebar = () => window.__sidebar_calls.push(arguments); window.__sidebar_calls = [];
# 然后按 C1/C2/C3 顺序跑。3 条都过，截图放 T010-gate-evidence/。
```

Option B (次佳): Node.js + jsdom 模拟（环境有 npm 就行）
```bash
# 临时 npm i jsdom 不要 commit，造一个 DOM 环境，注入 mock 公开函数，跑 welcome IIFE，测 shouldAutoShow / close / 点击 shortcut 的副作用（input.value + mock calls）。
```

Option C (兜底): 纯 Python 代码走读 + 逻辑模拟（零依赖，最容易跑通，C1-C3 的副作用逻辑我们已经用 D-1~D-14 token 间接保证了，这里再做 3 个逻辑断言）
```powershell
# 写一个临时脚本（不要 commit）：
# C1 模拟：if shouldAutoShow 逻辑有 forever → false，再 if seen_ls → false，再 sessionSeen → false，否则 true（正则验证顺序对）+ close 函数里有 safeLsSet(LS_KEY_SEEN, 'true')（正则匹配）
# C2 模拟：close 函数里 if ($dontShowCheckbox && $dontShowCheckbox.checked) safeLsSet(LS_KEY_FOREVER, 'true')（正则匹配）+ helpBtn listener show({ignoreLocalStorage:true})（正则匹配）
# C3 模拟：switch 3 分支里分别有 submit / submit / showPoiSidebar + 无 API 调用（负正则）
# 这个 Option 的证据就是正则断言结果表。
```

任选一种即可，3/3 PASS 就行。

---

## Implementer Report 要求

完成后写 `.superpowers/sdd/task-6-report.md`，模板：

```markdown
# Task 6 Report · Final QA Review（13/13 通过进入 Gate Review）

| 组 | 条数 | 通过数 | 备注 |
|----|------|--------|------|
| A 代码规范 | 5 | 5/5 | A1~A5 全过 |
| B 验收覆盖 | 5 | 5/5 | B1(T016→HTML) + B2(T017→JS) + B3(T018→CSS) + B4(26→51) + B5(10 guardrails) 全过 |
| C 手动测试 | 3 | 3/3 | 执行 Option ___（A Playwright / B jsdom / C Python 逻辑模拟）：C1/C2/C3 全过，证据在 T010-gate-evidence/ |
| **总计** | **13** | **13/13** | **✅ 满足 Gate Review 条件** |

## 详细 Check 证据
| CheckID | 结论 | 证据简述 |
|---------|------|----------|
| A1 Append-only | ✅ | 4 个文件最后 20 行原有内容 100% 未变 |
| A2 裸色值 | ✅ | 新增 welcome 代码块内 hex/rgba 共 6 个（4 设计特例 + 2 辅助），≤ 7 合规 |
| A3 零污染 | ✅ | 仅 window.WelcomeColdStart = {...}，无其他全局赋值 |
| A4 Fail-soft | ✅ | 4 处 fail-soft 全有代码依据 |
| A5 StopPropagation | ✅ | $card click → stopPropagation |
| B1 T016→HTML13 | ✅ | T010 HTML section 13/13 PASS（证据：脚本输出日志） |
| B2 T017→JS14 | ✅ | T010 JS section 14/14 PASS（证据：脚本输出日志） |
| B3 T018→CSS24 | ✅ | T010 CSS section 24/24 PASS（证据：脚本输出日志） |
| B4 26→51 无孤儿 | ✅ | 6→13 / 13→14 / 7→24，没有任何 T-016/017/018 验收标准没被脚本覆盖 |
| B5 10 guardrails | ✅ | A5→G04 / A4→G05/06/09/11 / D6→G02/03 / D11→G07 / D12→G08 / B9/B10→G10，10/10 全有实现依据 |
| C1 首次弹+X 关+不弹 | ✅ | Option C Python 逻辑模拟：shouldAutoShow 顺序对 + close 写 LS_SEEN ✅ |
| C2 forever+?唤回+高亮 | ✅ | Option C Python 逻辑模拟：close 写 LS_FOREVER + helpBtn ignoreLocalStorage + highlight class ✅ |
| C3 3 数据流 + 防抖 | ✅ | Option C Python 逻辑模拟：3 分支代码 token 全对 + recommend_poi 无 submit/fetch 负断言 + DEBOUNCE 1500ms 时间戳判断 ✅ |

## 产物 & 证据清单
- 代码改动文件 4 份 + 新增脚本 1 份（共 5 份文件）：
  1. project-docs/05_TASKS.md（append 3 段 26 条）
  2. static/index.html（append 2 段 DOM）
  3. static/css/style.css（append 1 段 CSS）
  4. static/js/app.js（append 1 段 IIFE）
  5. scripts/T010_validate_welcome_cold_start.py（新增，51 checks）
- SDD 文档 11 份：
  - spec.md / plan.md / progress.md
  - task-1-brief.md + task-1-report.md
  - task-2-brief.md + task-2-report.md
  - task-3-brief.md + task-3-report.md
  - task-4-brief.md + task-4-report.md
  - task-5-brief.md + task-5-report.md
  - task-6-brief.md + task-6-report.md
- 验证脚本证据 1 份（T010 51/51 ALL PASS 输出）
- 手动测试证据：___（Option C: 逻辑模拟脚本输出日志 / Option A: 截图）
```

然后把 Task 6 report 的路径 + 13/13 的结论（必须全部 ✅）返回给 Orchestrator（就是我），任何一条不通过 → 返回 Status = BLOCKED，告诉我哪一条没过，我决定回退哪个 Task 修。

**返回格式**：Status（DONE / BLOCKED） + Group A/B/C 各通过数（如 5/5, 5/5, 3/3） + 是否满足 Gate Review。
