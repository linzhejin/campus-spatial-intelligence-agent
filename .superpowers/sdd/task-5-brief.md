# Task 5 Brief · 冷启动欢迎卡片

> **任务位置**：实施计划 **第 5 个任务**（前置条件：Task 2/3/4 DONE，4 个改动文件都已就位）
> **目标**：创建 `scripts/T010_validate_welcome_cold_start.py`（用 Python 内置 `html.parser` / `re` / `json` 纯 stdlib，绝对不新增第三方依赖！）。作为 T-010 验收的总入口脚本，一次性跑完全部静态检查（集成 Task 2/3/4 的所有分散自检，消除前几轮正则范围小导致的误报问题）。脚本必须返回 0 / non-zero exit code（ALL PASS 才是 0）。
> **修改范围**：只新增 1 个文件（`scripts/T010_validate_welcome_cold_start.py`）。

---

## 脚本结构要求（4 大 Section，按顺序执行）

脚本的结构必须固定（Section A → B → C → D），每一条检查必须打印 `[CheckName] 说明 ... ✅/❌`，最终 Section D 统计输出总表。

```
Section A: 基础文件存在性（3 checks）
  A-1. static/index.html 存在且可读
  A-2. static/css/style.css 存在且可读
  A-3. static/js/app.js 存在且可读

Section B: HTML 静态检查（T-016 DOM 完整性 + 无障碍，13 checks —— 合并 Task 2 自检 + 补充）
  B-1. id=welcome-overlay 存在
  B-2. overlay 初始含 class=hidden 且 aria-hidden=true（初始状态隐藏）
  B-3. overlay 内含至少一个 .welcome-mask[data-close-welcome="true"]（点击遮罩关，5 关闭方式之一）
  B-4. overlay 内含 .welcome-card
  B-5. .welcome-card 内含：#welcome_topbar / #welcome_subtitle / .welcome-shortcuts / .welcome-tips / .welcome-footer（5 大骨架 section 全齐）
  B-6. #help-btn（？按钮）存在且位于 overlay 之外、aria-label="打开欢迎面板"（T-016 8.6）
  B-7. 5 张 shortcut-card：
       · 总数恰好 5（不多不少）
       · 3 张 data-action="path_planning"（路径类，3 条）
       · 1 张 data-action="poi_query"（景点查询类，1 条）
       · 1 张 data-action="recommend_poi"（推荐类，1 条）
       · 比例恰好 3:1:1（T-016 8.5）
  B-8. 每张 shortcut-card 必须有 data-display-text 属性（必填，JS 用这个填输入框，不能为空）
  B-9. 路径类 3 张的起点/终点 POI 名必须是 data/pois.json 中真实存在的 POI.name 或 POI.aliases（防止虚构地点）
  B-10. 景点查询类 1 张的 POI 名必须在 data/pois.json 中真实存在（樱顶）
  B-11. X 关闭按钮含 data-close-welcome="true"（5 关闭方式之二）
  B-12. "开始使用"按钮含 data-close-welcome="true"（5 关闭方式之三）
  B-13. "以后不再显示" checkbox id=welcome_dont_show（T-017 14.3 forever 标记）

Section C: CSS 静态检查（T-018 视觉约束 + Task 3 全 23 token，修复原 Task 3 正则范围问题，24 checks）
  C-1. #welcome-overlay.hidden 的 display:none 或 opacity:0 + pointer-events:none（初始不显示）
  C-2. .welcome-card 含 backdrop-filter: blur(8px) + var(--card-bg) + 1px 描边（毛玻璃卡片质感，T-018 7.4）
  C-3. .welcome-topbar 的樱顶老斋舍窗棂纹：
       · position:relative 且 ::before 存在（content:""/position:absolute/left:0/top:0/bottom:0/width:4px）
       · 三层叠加顺序正确（从下往上：米黄底 → 朱红渐变 → 青蓝高光 accent bar，线性渐变顺序对吗？对的）
       · 三个特例裸色值必须是 #FAFAF9 + #FFF6EC + 朱红/青蓝渐变（T-018 7.2 设计签名元素，不能改）
  C-4. .welcome-title 字体大小至少 22px（移动端 ≥18px），颜色 var(--text-primary)
  C-5. .welcome-card 入场动画时间 ≤ 320ms（≤ 要求的 320ms，验收标准 7.3）
  C-6. .welcome-card 退场动画时间 ≤ 220ms（≤ 220ms，验收标准 7.3）
  C-7. shortcut-card 有 hover: scale 1.02~1.04 + shadow 加深（微交互，验收标准 7.6）
  C-8. 响应式：max-width ≤ 640px / max-height ≤ 90vh / 移动端 @media (max-width:480px) 存在
  C-9. 移动端 5 张卡的 flex-direction:column（不是 row，防止挤爆）
  C-10. 移动端 .shortcut-card 宽度 100%（全宽不并排）
  C-11. .welcome-tips 的 3 条 Tip 有 padding-left/bullet 样式（对齐一致）
  C-12. .flash-highlight 关键帧存在（供 Task 4 recommend_poi 高亮侧边栏 2s 用，验收标准 14.8）
  C-13. .opening / .closing 类含 opacity/transform 过渡（对应 JS 加的类）
  C-14. .highlight 类（？按钮唤回脉冲）存在，animation 0.6s
  C-15. .card-icon 存在（28~32px 圆角 + 渐变背景色）
  C-16. C-Token 原 23 项的修复后重跑（用更宽范围的 re.search，消除 Task3 4 项误报，23/23）

Section D: JS 静态检查（T-017 所有交互逻辑 token，14 checks —— 原 Task4 的 12 项 + 补充 2 项）
  D-1. 自动弹卡：DOMContentLoaded 或 readyState === 'loading' 分支存在
  D-2. shouldAutoShow() 逻辑：先 forever → 再 ls_seen → 再 sessionSeen → 默认 true（优先级对）
  D-3. localStorage 封装 safeLsGet / safeLsSet：全部 try/catch，不抛错（T-017 14.10）
  D-4. 写入 whu_welcome_seen：关闭时写 ls（T-017 14.2）
  D-5. 写入 whu_welcome_dont_show_forever：checkbox.checked 时写 ls（T-017 14.3）
  D-6. 5 种关闭方式：
       · data-close-welcome 事件委托（X / mask / 开始使用）
       · Esc keydown 仅当 isOpen 时关
       （共 5 条路径）
  D-7. 点 shortcut-card：
       · $input.value = displayText （填输入框，T-017 14.5）
       · typeof window.submitNaturalLanguageQuery === 'function' （安全检查，不能直接调）
       · setTimeout(ANIM_OUT_DURATION_MS + 20) （等退场动画完再触发）
  D-8. poi_query 分支：走 submitNaturalLanguageQuery（T-017 14.7，不能直接 open sidebar）
  D-9. recommend_poi 分支：**不出现** submitNaturalLanguageQuery / fetch / post（不调 API！T-017 14.8），而是 showPoiSidebar('全部') + 侧边栏 flash-highlight class 加/减 + setTimeout 2s 移除
  D-10. ？按钮唤回：必须传 ignoreLocalStorage:true，**不写任何 LS 标记**（T-017 14.9）
  D-11. 防抖：DEBOUNCE_MS=1500 + lastShortcutAt = Date.now() + if (now - last < DEBOUNCE) return（T-017 14.11）
  D-12. animating 防动画叠加：show/close 开头 `if (animating) return;`（T-017 14.12 快速连点）
  D-13. Esc 关卡**不碰** input.value（全文无 `Escape.*input\.value` 或 Esc 分支里无 `$input.value = ...`，T-017 14.13）
  D-14. 零污染全局：除了 `window.WelcomeColdStart = {...}` 之外，IIFE 内部无其他全局赋值（var 的都在闭包）

Section E: 输出汇总 + exit code
  E-1. 打印总表：
       HTML 检查: X/13 PASS
       CSS 检查 : Y/24 PASS
       JS 检查  : Z/14 PASS
       TOTAL    : (X+Y+Z)/(13+24+14) = T/51 PASS
  E-2. T == 51 时：打印 "🎉 T-010 WELCOME COLD START ACCEPTED · 51/51 ALL PASS" 并 sys.exit(0)
  E-3. T < 51 时：打印 "❌ T-010 FAILED · N checks failed. Failing checks: [name list]" 并 sys.exit(1)
```

---

## 关键实现细节（Implementer 必须严格遵守）

1. **纯 stdlib，绝对不新增第三方依赖**：只能 import `sys` / `os` / `re` / `json` / `pathlib` / `html.parser`，**严禁 import 任何其他库**（包括项目里的模块，本脚本独立运行）。
2. **CSS 类/ID 查询：用 `re.search(pattern, css, re.DOTALL | re.IGNORECASE)` 并带大跨度的 `{0,N}`（N=800~2000）消除 Task 3 的误报问题**，绝对不能再用 `{0,50}` 这种小范围。
3. **HTML 查询：用内置 `html.parser.HTMLParser` 子类**（不要用正则硬抠属性），但同时留正则 fallback 兜底（双重保险）。
4. **POI 真实性校验（B-9 / B-10）：脚本必须自己读 `data/pois.json`**，不要 import 项目的 `spatial/poi.py`（保持独立），抽出所有 name + 所有 aliases 做成一个集合，然后匹配 shortcut-card 中抽出来的 POI 名。
5. **Section C-3（樱顶窗棂纹三层顺序）验证：这是设计签名元素，绝对不能错**。必须同时验证：
   - `::before { content:""; position:absolute; left:0; top:0; bottom:0; width:4px }` 的结构存在（4px 朱红竖条）
   - 叠加的顺序：linear-gradient(#FFF6EC 米黄底) → 再 linear-gradient(to right, #E8636F 朱红主 + rgba(232,198,80,0.18) 米黄高光 + rgba(232,146,156,0.32) 朱红渐变叠纹) → 最后 `box-shadow: inset 2px 0 0 rgba(52,140,170,0.35)` 青蓝高光
6. **Exit code 必须非零当且仅当有检查失败**（CI 友好，后续集成到 Makefile）。
7. **Windows 中文友好**：print 前 `sys.stdout.reconfigure(encoding='utf-8')`，防止 GBK 控制台乱码。
8. **命名一致性**：所有检查项名字必须以 `A-1/A-2/B-1/B-2...` 开头（便于后续 diff 排查），绝对不能改编号。

---

## Step-by-Step 操作步骤

- [ ] **Step 5.1: 创建 `scripts/T010_validate_welcome_cold_start.py`，内容严格按 Section A~E 结构写**（Implementer 自己实现，不再给 Exact 代码，这是这次 SDD 唯一需要动脑的 Task）
- [ ] **Step 5.2: 运行脚本 3 次直到 51/51 ALL PASS**
  Run:
  ```powershell
  cd "c:\Users\HUAWEI\Desktop\项目\campus-spatial-intelligence-agent"
  python scripts/T010_validate_welcome_cold_start.py
  ```
  Expected final output:
  ```
  🎉 T-010 WELCOME COLD START ACCEPTED · 51/51 ALL PASS
  ```
  （`echo $LASTEXITCODE` 或 `echo %ERRORLEVEL%` 必须是 0）

- [ ] **Step 5.3: 负向冒烟（可选但推荐）**：手动改 HTML 一个属性（比如把 overlay 的 class=hidden 去掉），重跑脚本，预期 exit code 非 0 且明确报 B-2 失败 → 还原回原样（**必须还原！** 否则 Task 6 过不去）。

- [ ] **Step 5.4: Self-review 5 条（Implementer 自审）**
  1. **纯 stdlib？** 脚本 import 列表里有没有出现项目模块 / 第三方库？没有才能过。
  2. **POI 真实性校验（B-9 B-10）对吗？** 自己把 data/pois.json 打开核对 4 张路径 + 1 张景点查询的 POI，是不是都在 name/aliases 集合里？
  3. **窗棂纹三层顺序（C-3）对吗？** 三个色值必须精确匹配（米黄 #FFF6EC + 朱红 #E8636F + 青蓝 inset 2px rgba(52,140,170,0.35)），不能有偏差，这是设计签名。
  4. **Exit code 正确吗？** ALL PASS → 0；有任何失败 → 1。（可以故意加一条必失败的断言快速验证 exit code，验证完删掉）
  5. **检查编号一致吗？** A-1~A-3 / B-1~B-13 / C-1~C-16/C-Token1~23？不，C 要求是 C-1~C-16 加 C-Token 合并为 **24 条**（Implementer 自己决定怎么合并到 24 条，总数对就行，编号保持 C-1~C-24）。

---

## Implementer Report 要求

完成后写 `.superpowers/sdd/task-5-report.md`，模板：

```markdown
# Task 5 Report

| 项 | 值 |
|----|----|
| Status | DONE / BLOCKED / NEEDS_CONTEXT |
| 改动文件（1 个，新增） | scripts/T010_validate_welcome_cold_start.py |
| 脚本 import 列表（stdlib only?） | 列所有 import；是否全是 stdlib：是/否 |
| Step 5.2 最终运行结果 | 🎉 51/51 ALL PASS / ❌ N 失败（列失败编号和原因） |
| Exit code | 0 / 1 |
| Step 5.3 负向冒烟（可选） | 验证通过：故意破 1 条 → exit code 非零 → 还原成功。未执行也可以。 |
| Self-review 5 条结果 | 1. Pure stdlib: YES/NO；2. B-9/10 POI 真实性: YES/NO；3. C-3 窗棂纹三层顺序正确: YES/NO；4. Exit code 正确: YES/NO；5. C 总条数 24: YES/NO |
| Concerns | 有写，没有 N/A |
```

返回时只给 Status + Step 5.2 结果（51/51 PASS 才允许 DONE） + Concerns。
