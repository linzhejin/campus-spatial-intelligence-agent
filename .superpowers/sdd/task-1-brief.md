# Task 1 Brief · 冷启动欢迎卡片

> **任务位置**：整个实施计划的 **第 1 个任务**（前置条件：Preflight 基线已绿，05_TASKS.md 存在）
> **目标**：严格遵守项目规则第 3 条（先改 TASKS 文档再改代码），在 `project-docs/05_TASKS.md` 中为 T-016 / T-017 / T-018 三个前端 Task **追加 26 条验收标准**（6 + 13 + 7）。这些验收标准将作为后续 Task 2/3/4/5/6 实现时的对标依据。
> **修改范围**：仅修改 **1 个文件**（`project-docs/05_TASKS.md`），在 3 个精确行号位置**追加 markdown 段落**，**不修改、不删除任何原有文本**，保证 append-only。

---

## 全局约束（必须严格遵守）

1. **Append-only 原则**：不修改任何原有 05_TASKS.md 的文字，只能在 3 个指定位置**追加**验收标准段落；原有标题、编号、格式不能动。
2. **Exact 行号原则**：3 个插入位置必须与下文的精确行号描述 100% 一致，不能插错地方。
3. **零新增上游文档修改**：只改 05_TASKS.md，不改任何 `docs/superpowers/specs/` 下的 spec 文档（项目规则第 5 条：禁止修改上游 PRD/spec）。
4. **精确格式**：追加的验收标准必须沿用原 05_TASKS.md 的统一格式：
   - 一级条目：`  N. **名称**：描述`（2 空格缩进，`N. **粗体标题**：描述`，末尾可带冒号）
   - 子条目：`     N.M 描述`（5 空格缩进）
   - 子子条目：`        N.M.N 描述`（8 空格缩进）

---

## 插入位置 1：T-016 验收标准末尾（追加 T-016 第 8 条 · 6 小条）

### 精确插入点（OLD_STRING）
原有 T-016 验收标准的第 7 条是最后一条：
```
  7. **帮助/推荐结果展示区结构**：结果区包含帮助/推荐结果子结构——功能说明列表 + 推荐景点卡片网格 + 示例 Query 快捷 Chip 按钮（点击自动填入输入框并提交）

```

**必须紧贴** 第 7 条描述的**下一个空行**（即 `  7. **帮助/推荐结果展示区结构**：…` 这一行结束后的换行处）**之后**追加，不能插在第 7 条中间，也不能和 T-017 的标题挨在一起（中间要有空行，和原有风格一致）。

### 要追加的 Exact 内容
```
  8. **冷启动欢迎覆盖层**：
     8.1 `<body>` 末尾存在 `id="welcome-overlay"` 的全页面级覆盖层，包含遮罩（`.welcome-mask`）+ 卡片本体（`.welcome-card`）
     8.2 卡片内按顺序包含 4 部分：标题区（`.welcome-header`）、5 个 shortcut-card（`.welcome-shortcuts` 下 5 个 `.shortcut-card`）、3 步新手提示（`.welcome-tips`）、底部操作区（`.welcome-footer`：开始使用按钮 + 不再显示复选框）
     8.3 5 个 shortcut-card 均具备完整 `data-*` 参数：
         - 3 张 path_planning 类：`data-action="path_planning"` + `data-start` + `data-end` + `data-constraints` + `data-display-text`
         - 1 张 poi_query 类：`data-action="poi_query"` + `data-name` + `data-display-text`
         - 1 张 recommend_poi 类：`data-action="recommend_poi"` + `data-display-text`
     8.4 卡片最顶部存在 class="welcome-card-topbar" 的樱顶窗棂纹装饰条
     8.5 覆盖层的对话框满足基础无障碍：`.welcome-card` 有 `role="dialog"` + `aria-modal="true"` + `aria-label`；2 个关闭按钮（X / 开始使用）有 `aria-label`；遮罩和对话框有 `data-close-welcome="true"` 统一选择器
     8.6 header 的 `.header-content` 最右侧存在 `id="help-btn"` + `aria-label="帮助/欢迎引导"` 的圆形？按钮

```

---

## 插入位置 2：T-017 验收标准末尾（追加 T-017 第 14 条 · 13 小条）

### 精确插入点（OLD_STRING）
原有 T-017 验收标准的第 13 条是最后一条：
```
  13. **ambiguity 引导 UI**：后端返回 `ambiguity != null` 时，在输入框下方显示引导气泡 + 若 ambiguity 文本含候选（如 POI 候选）则同时渲染可点击 Chip，用户点击 Chip 后无需手打字直接作为下一轮补全提交，并自动带上上一轮 context 实现字段合并

```

**必须紧贴** 第 13 条描述的**下一个空行**之后追加，中间要有空行（和后面的 T-018 标题隔开）。

### 要追加的 Exact 内容
```
  14. **冷启动欢迎卡片交互**：
     14.1 首次访问（localStorage 无任何 welcome 相关键）→ DOMContentLoaded 后 150ms 内自动弹出欢迎卡片
     14.2 正常关闭卡片后刷新 → 不自动弹出（验证 `localStorage.getItem('whu_welcome_seen') === 'true'`）
     14.3 勾选「以后不再显示此欢迎页」复选框后关闭 → `localStorage.getItem('whu_welcome_dont_show_forever') === 'true'` 生效，后续刷新永不自动弹（不考虑清 localStorage 的情况）
     14.4 5 种关闭方式全部有效：点右上角 X / 点遮罩层空白区 / 按键盘 Esc 键 / 点底部「✨ 开始使用」/ 点任意 5 个 shortcut-card
     14.5 点任意 shortcut-card 后：① 输入框 `value` 自动填入 `data-display-text` 的文本；② 自动调用现有 `submitNaturalLanguageQuery()`（不是独立 fetch）；③ 欢迎卡片关闭；④ 滚动到结果区
     14.6 点击 3 张 `data-action="path_planning"` 卡片 → 正确走路径规划流程，start/end/constraints 与 data-* 参数一致
     14.7 点击 1 张 `data-action="poi_query"`（景点查询 · 樱顶）卡片 → 后端返回 `task_type=poi_query start={"name":"樱顶"} end=null`，结果区显示 POI 详情，地图 panTo + 弹 InfoWindow
     14.8 点击 1 张 `data-action="recommend_poi"`（推荐景点）卡片 → 展开 POI 列表侧边栏 + 切换到「全部/推荐」标签 + 侧边栏顶部 2s 淡黄色高亮；不发起任何网络请求
     14.9 点击 header 右上角 `#help-btn`（？按钮）→ **强制**显示欢迎卡片，无论 localStorage 状态如何，且不修改 / 写入任何 localStorage 标记
     14.10 隐私模式降级：localStorage `setItem` 抛错（模拟 QuotaExceededError）→ 用内存变量 `sessionWelcomeShown` 兜底，当前标签页内只弹一次；刷新页面再弹一次（UX 降级可接受）
     14.11 防抖：1.5s 内连续点击多张 shortcut-card → 只触发第 1 次查询，后面点击忽略
     14.12 无障碍：系统设置 `prefers-reduced-motion: reduce` → 欢迎卡秒开秒关，无入场 stagger、无 springy 回弹、无 mask fade（transition 全 0s）
     14.13 Esc 关卡不影响输入：欢迎卡弹开时用户已在输入框打了 10 个字 → 按 Esc 关卡 → 输入框内容原封不动保留

```

---

## 插入位置 3：T-018 验收标准末尾（追加 T-018 第 7 条 · 7 小条）

### 精确插入点（OLD_STRING）
原有 T-018 验收标准的第 6 条是最后一条：
```
  6. manifest.json 含 192/512 icon、theme_color、display: standalone

```

**必须紧贴** 第 6 条描述的**下一个空行**之后追加，中间要有空行（和后面的 T-019 标题隔开）。

### 要追加的 Exact 内容
```
  7. **冷启动欢迎卡片样式**：
     7.1 桌面端（≥ 768px）：`.welcome-card` 固定尺寸 480×640px，屏幕水平垂直居中，border-radius = var(--radius-lg)；`.welcome-shortcuts` 为 3+2 grid（第一行 3 张第二行 2 张居中，推荐景点宽卡占满第二行全宽）
     7.2 移动端（< 768px）：`.welcome-card` 宽度 92vw（min-width 300px），最大高度 88vh；`.welcome-shortcuts` 改为单列 flex（5 张卡片纵向排列每张 72px 紧凑布局）；`.welcome-tips` 折叠成「新手提示 ▾」按钮点击才展开；「以后不再显示」复选框字号缩小为 0.8rem
     7.3 遮罩层 `.welcome-mask` 背景色为 `var(--paper)` 透明度 0.68，能隐约看到后面的地图（不遮挡信息），z-index ≥ 9999（压过高德地图的所有 UI 控件）
     7.4 5 张 shortcut-card 的 2px 左边框 accent bar 颜色正确：
         - 经典路线（sc-accent）→ `border-left: 2px solid var(--cherry-deep)`
         - 赏樱路线（sc-pink）→ `border-left: 2px solid var(--cherry)`
         - 景点查询（sc-jade）→ `border-left: 2px solid var(--jade)`
         - 最短路径（sc-stone）→ `border-left: 2px solid var(--ink-light)`
         - 推荐景点（sc-wide）→ `border-left: 2px solid var(--color-poi)`（暖橙）
     7.5 `.welcome-card-topbar`（签名元素 · 樱顶窗棂纹装饰条）高度 48px，使用 `linear-gradient` + `repeating-linear-gradient` 画出樱粉-翠玉渐变背景 + 竖线窗棂 + 横线屋檐三层叠加，无图片资源
     7.6 动画符合 spec：
         - 入场：遮罩 200ms 淡入，卡片 280ms cubic-bezier(0.2,0.8,0.2,1) springy 回弹，5 张 shortcut-card 使用 `:nth-child` + `animation-delay` 依次 stagger 入场（每张间隔 60ms，总 360ms）
         - 退场：遮罩 150ms 淡出，卡片 180ms ease-in 淡出下移
     7.7 字体完全复用现有栈：`.welcome-title` 使用 `font-family: "Noto Serif SC", "Songti SC", "STSong", serif`（与 `.app-title` 一致），正文所有文字使用现有 body 字体栈（PingFang SC / YaHei），不引入任何新字体引用

```

---

## Step-by-Step 操作步骤（Implementer 必须按顺序执行）

> 注意：本任务纯文档修改，不需要跑 pytest / npm test 等单元测试，只需最后 Step 1.4 的 PowerShell 验证命令通过即可。

- [ ] **Step 1.1: 先读 05_TASKS.md 的 293 / 318 / 334 行附近，确认三个插入点的 OLD_STRING 与 brief 描述完全一致**
  - 打开 `project-docs/05_TASKS.md`，分别定位：
    1. T-016 验收标准第 7 条（帮助/推荐结果展示区结构）末尾空行（≈ L293-L294）
    2. T-017 验收标准第 13 条（ambiguity 引导 UI）末尾空行（≈ L318-L319）
    3. T-018 验收标准第 6 条（manifest.json…）末尾空行（≈ L334-L335）
  - 三个点的 OLD_STRING 必须和 brief 描述 100% 相同，才能进行下一步；如果行号因为之前的改动变化了，**以 OLD_STRING 文本匹配为准，不硬绑行号**。

- [ ] **Step 1.2: 在第一个插入点（T-016 第 7 条后）追加 Exact 内容（T-016 第 8 条 6 小条）**
  - 必须用「Edit tool（old_string=new_string）」的方式精确追加，old_string 就是第 7 条原文（带空行），new_string 是第 7 条原文 + 空行 + 要追加的 8.x 内容（保证 append-only，不删第 7 条任何字）。

- [ ] **Step 1.3: 在第二个插入点（T-017 第 13 条后）追加 Exact 内容（T-017 第 14 条 13 小条）**
  - 同上 Edit tool，old_string = 第 13 条原文（带空行），new_string = 第 13 条原文 + 空行 + 14.x 内容。

- [ ] **Step 1.4: 在第三个插入点（T-018 第 6 条后）追加 Exact 内容（T-018 第 7 条 7 小条）**
  - 同上 Edit tool，old_string = 第 6 条原文（带空行），new_string = 第 6 条原文 + 空行 + 7.x 内容。

- [ ] **Step 1.5: 运行以下精确 PowerShell 验证命令，确认 3 段内容都加对了位置**

  Run:
  ```powershell
  cd "c:\Users\HUAWEI\Desktop\项目\campus-spatial-intelligence-agent"
  Write-Host "===== Task 1 Verification: 05_TASKS.md 3 段追加检查 ====="
  $tasks = Get-Content project-docs\05_TASKS.md -Raw
  # Check 1: T-016 第 8 条（冷启动欢迎覆盖层）
  $check1 = [regex]::Match($tasks, "8\. \*\*冷启动欢迎覆盖层\*\*：[\s\S]{0,200}8\.6 header 的 `\.`header-content").Success
  # Check 2: T-017 第 14 条（冷启动欢迎卡片交互）
  $check2 = [regex]::Match($tasks, "14\. \*\*冷启动欢迎卡片交互\*\*：[\s\S]{0,400}14\.13 Esc").Success
  # Check 3: T-018 第 7 条（冷启动欢迎卡片样式）
  $check3 = [regex]::Match($tasks, "7\. \*\*冷启动欢迎卡片样式\*\*：[\s\S]{0,300}7\.7 字体完全复用现有栈").Success
  Write-Host ("T-016 追加 8.x: " + ($check1 ? "✅ PASS" : "❌ FAIL"))
  Write-Host ("T-017 追加 14.x: " + ($check2 ? "✅ PASS" : "❌ FAIL"))
  Write-Host ("T-018 追加 7.x : " + ($check3 ? "✅ PASS" : "❌ FAIL"))
  if(($check1 -and $check2 -and $check3)){ Write-Host "`n✅ ALL 3 TASKS MD SECTIONS APPENDED CORRECTLY" -ForegroundColor Green }
  else { Write-Host "`n❌ FAIL" -ForegroundColor Red; exit 1 }
  ```
  Expected: 3 条全部 PASS + 最终输出 ALL 3 ... CORRECTLY。

- [ ] **Step 1.6: （可选，不强制）提交 git commit：`chore(docs): add cold start welcome acceptance criteria T-016/017/018`**
  - 用户没有明确要求 commit，所以可跳过；如果要 commit，先 git add 再 commit。

- [ ] **Step 1.7: Self-review（Implementer 自己先过一遍，避免 reviewer 打回）**
  自审 3 条：
  1. 有没有删任何原有 TASKS 文档文字？（必须 100% append-only）
  2. 3 段追加的编号是否正确？（T-016 原 1-7 → 追加 8；T-017 原 1-13 → 追加 14；T-018 原 1-6 → 追加 7；编号不能和原有的重复）
  3. 缩进格式和原文档一致吗？（一级条目 2 空格 + N. **粗体** + 冒号；子条目 5 空格 + N.M）

---

## Implementer Report 要求

完成后，请把以下内容写到 `.superpowers/sdd/task-1-report.md`（你要创建这个文件），然后返回：

```markdown
# Task 1 Report

| 项 | 值 |
|----|----|
| Status | DONE / BLOCKED / NEEDS_CONTEXT |
| 改动文件（1 个） | project-docs/05_TASKS.md |
| Step 1.5 验证结果 | 3 PASS 全通过 ✅ / 失败 N 条 ❌（附失败详情） |
| Self-review 3 条结果 | 1. Append-only: YES/NO；2. 编号正确不重复: YES/NO；3. 缩进格式匹配: YES/NO |
| Concerns（疑虑/备注） | 有就写，没有写 N/A |
```

**报告里不要附 diff 内容或大段代码**，diff 由 Controller 生成后给 reviewer。
