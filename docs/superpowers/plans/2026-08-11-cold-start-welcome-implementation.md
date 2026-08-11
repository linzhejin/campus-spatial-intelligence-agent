# 冷启动欢迎卡片（方案 1） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不修改任何后端代码 / 不新增依赖的前提下，为漫步珞珈的首页新增方案 1 冷启动欢迎卡片（覆盖式），包含 5 个快捷查询示例 + 双 localStorage 记忆 + ？帮助按钮唤回机制，实现 G-01~G-03 三个设计目标。

**Architecture:** 完全追加式（append-only）前端改动：
1. 先更新 `project-docs/05_TASKS.md` 上游文档（项目规则第 3 条：先改 TASKS 文档再改代码）；
2. 改 3 个现有前端文件（`static/index.html` 加结构 / `static/css/style.css` 加样式 / `static/js/app.js` 加交互逻辑）——全部是末尾追加 DOM 节点 / CSS 类 / JS IIFE 模块，**不覆盖 / 不修改现有任何代码路径**；
3. 新增一个验证脚本 `scripts/T010_validate_welcome_cold_start.py` 做静态 DOM 检查 + localStorage 状态模拟验证。

**Tech Stack:**
- 原生 HTML5 / CSS3（`prefers-reduced-motion` 媒体查询、CSS `repeating-linear-gradient` 画窗棂纹、CSS `@keyframes` + `animation-delay` stagger 动画）
- 原生 ES6+ JavaScript（IIFE 封装 + 事件委托，不引任何库，兼容现有 app.js 代码风格）
- Python 3 + BeautifulSoup4（验证脚本，项目中已有 BeautifulSoup 依赖吗？——若没有，验证脚本用 Python 内置 html.parser 兜底，零新增依赖）

---

## Global Constraints

1. **零新增依赖**：前端不引任何 npm/CDN 库，验证脚本仅用 Python 内置库（html.parser + re + pathlib + json）
2. **Append-only 原则**：不修改现有 3 个前端文件的任何原有 DOM 节点 / CSS 类 / JS 函数；只在末尾追加
3. **硬编码 POI 名同步原则**：shortcut-card 的 data-start/data-end/data-name 必须与 `data/pois.json` 中现有 name 字段完全一致（牌坊、樱顶、樱花大道、教五、总图书馆——已确认 5 个全存在）
4. **视觉零新增主题色原则**：所有颜色 token 100% 复用 `static/css/style.css:3-44` 的 `:root` 变量，不新增任何自定义 hex 色值
5. **无障碍原则**：所有新增可点击元素有 aria-label，对话框有 role=dialog aria-modal，键盘 Esc 可关，`prefers-reduced-motion` 去动画
6. **项目规则第 3 条**：先改上游 `project-docs/05_TASKS.md` 验收标准，再改任何代码
7. **项目规则第 5 条**：只改 `static/index.html` / `static/css/style.css` / `static/js/app.js` / `scripts/*.py` 四个下游允许范围，不碰任何上游 spec / PRD 文档

---

## File Structure（修改前先锁边界）

| 操作 | 精确文件路径 | 角色（一个文件一件事） |
|------|-------------|----------------------|
| **Modify** | `project-docs/05_TASKS.md:L293-L294`（T-016 第 7 条验收标准后） | 追加 T-016 冷启动验收 8.x 条 |
| **Modify** | `project-docs/05_TASKS.md:L318-L319`（T-017 第 13 条验收标准后） | 追加 T-017 冷启动验收 14.x 条 |
| **Modify** | `project-docs/05_TASKS.md:L334-L335`（T-018 第 6 条验收标准后） | 追加 T-018 冷启动验收 7.x 条 |
| **Modify** | `static/index.html`（在 `</body>` 前 1 行追加，在 `<div class="app-header"><div class="header-content">` 内 `</div>` 前追加？按钮） | HTML 结构：覆盖层组件 + header ？按钮 |
| **Modify** | `static/css/style.css`（在文件末尾，现有所有样式类**之后**追加，不插中间） | CSS 样式：welcome-* 所有类 + 响应式 + 动画 + 签名元素 |
| **Modify** | `static/js/app.js`（在文件末尾，现有所有代码**之后**追加 IIFE 模块，不插中间） | JS 交互：显示隐藏 / localStorage / 事件委托 / 数据流 |
| **Create** | `scripts/T010_validate_welcome_cold_start.py` | 验证脚本：DOM 结构检查 + localStorage 状态模拟 + 事件委托参数检查 |

---

## Task 1：先更新上游文档 · 05_TASKS.md 追加 3 处验收标准（项目规则第 3 条）

**Files:**
- Modify: `project-docs/05_TASKS.md`（3 处精确位置）
- Test: 手动 grep 确认 3 段新增内容全在

**Interfaces:**
- Consumes: 无
- Produces: 05_TASKS.md 中 T-016/017/018 验收标准各追加一节，供后续所有任务对照

---

- [ ] **Step 1.1: 在 T-016 验收标准第 7 条后（原 L293，就是 `7. **帮助/推荐结果展示区结构**：...` 那行的下一个空行）追加 T-016 第 8 条**

```markdown
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

- [ ] **Step 1.2: 在 T-017 验收标准第 13 条后（原 L318，就是 `13. **ambiguity 引导 UI**：...` 那行的下一个空行）追加 T-017 第 14 条**

```markdown
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

- [ ] **Step 1.3: 在 T-018 验收标准第 6 条后（原 L334，就是 `6. manifest.json 含 192/512 icon、theme_color、display: standalone` 那行的下一个空行）追加 T-018 第 7 条**

```markdown
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

- [ ] **Step 1.4: 手动验证 3 处都加对了（grep 检查）**

Run:
```powershell
cd "c:\Users\HUAWEI\Desktop\项目\campus-spatial-intelligence-agent"
# 检查 T-016 追加
Select-String -Path project-docs\05_TASKS.md -Pattern "冷启动欢迎覆盖层" | Select-Object LineNumber, Line
# 预期输出：LineNumber ≈ 294-300 区间
# 检查 T-017 追加
Select-String -Path project-docs\05_TASKS.md -Pattern "冷启动欢迎卡片交互" | Select-Object LineNumber, Line
# 预期输出：LineNumber ≈ 331-343 区间
# 检查 T-018 追加
Select-String -Path project-docs\05_TASKS.md -Pattern "冷启动欢迎卡片样式" | Select-Object LineNumber, Line
# 预期输出：LineNumber ≈ 365-372 区间
```

Expected: 3 条 Select-String 都能命中 1 条且行号在预期区间内 → PASS

---

- [ ] **Step 1.5: Commit（可选，本次任务不强制，用户没说要 commit）**

---

## Task 2：HTML 结构追加 · static/index.html 加 welcome 覆盖层 + ？帮助按钮

**Files:**
- Modify: `static/index.html`（2 个插入点）
- Test: 用浏览器直接打开 index.html 检查 DOM 元素存在（或用验证脚本 Task 5）

**Interfaces:**
- Consumes: Task 1 中确定的所有元素 id / class 名（必须完全一致，大小写敏感）
- Produces: DOM 中存在 `#welcome-overlay`（初始带 `.hidden`）+ `#help-btn`，供 Task 3/4 的 CSS/JS 使用

---

- [ ] **Step 2.1: 先读一下 static/index.html 当前 `<body>` 末尾和 `<div class="header-content">` 的精确结构**（确保 Edit 的 old_string 精确）

- [ ] **Step 2.2: 在 `<div class="header-content">` 的 `</div>` 结束标签前（也就是现有 `<span class="app-subtitle">...</span>` 后面），插入 ？帮助按钮**

```html
      <!-- ？帮助按钮（手动唤回欢迎卡片） -->
      <button id="help-btn" class="help-btn" aria-label="帮助/欢迎引导" title="查看帮助引导">?</button>
```

---

- [ ] **Step 2.3: 在 `</body>` 结束标签的**前 1 行**（就是现有所有 DOM 节点的最外面，body 的最后），插入完整的 welcome-overlay 覆盖层 HTML 片段**（从 spec §模块 1 直接抄，注意所有 id/class 名精确）

```html
  <!-- ===== 漫步珞珈 · 冷启动欢迎卡片覆盖层 ===== -->
  <div id="welcome-overlay" class="welcome-overlay hidden" aria-hidden="true">
    <!-- 遮罩层（点击遮罩也能关闭卡片） -->
    <div class="welcome-mask" data-close-welcome="true"></div>

    <!-- 卡片本体（stopPropagation 防止点内容冒泡到遮罩关闭） -->
    <div class="welcome-card" role="dialog" aria-label="欢迎使用漫步珞珈" aria-modal="true">
      <!-- 【签名元素】樱顶老斋舍窗棂纹装饰条（纯 CSS 画） -->
      <div class="welcome-card-topbar" aria-hidden="true"></div>

      <!-- 右上角关闭 X 按钮（SVG 来自现有样式主题，不引 icon 库） -->
      <button class="welcome-close" data-close-welcome="true" aria-label="关闭欢迎页">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <line x1="18" y1="6" x2="6" y2="18"></line>
          <line x1="6" y1="6" x2="18" y2="18"></line>
        </svg>
      </button>

      <!-- ① 标题区 -->
      <header class="welcome-header">
        <h1 class="welcome-title">🗺️ 漫步珞珈</h1>
        <p class="welcome-subtitle">武大校园 · 问路 · 查景点 · 推荐路线</p>
      </header>

      <!-- ② 5 个快捷查询卡片（声明式 data-*，JS 统一事件委托处理） -->
      <section class="welcome-shortcuts" aria-label="快捷查询示例">
        <button class="shortcut-card sc-accent"
                data-action="path_planning"
                data-start="牌坊" data-end="樱顶"
                data-constraints='{"distance":"medium","slope":"normal","scenery":"high"}'
                data-display-text="经典路线 · 牌坊 → 樱顶（景观优先）">
          <span class="sc-emoji" aria-hidden="true">🚶</span>
          <div class="sc-text">
            <span class="sc-name">经典路线</span>
            <small class="sc-desc">牌坊 → 樱顶 · 走风景最好的主路</small>
          </div>
        </button>

        <button class="shortcut-card sc-pink"
                data-action="path_planning"
                data-start="樱花大道" data-end="樱顶"
                data-constraints='{"distance":"relaxed","slope":"normal","scenery":"high"}'
                data-display-text="赏樱路线 · 樱花大道 → 樱顶（春天必走）">
          <span class="sc-emoji" aria-hidden="true">🌸</span>
          <div class="sc-text">
            <span class="sc-name">赏樱路线</span>
            <small class="sc-desc">樱花大道 → 樱顶 · 春天打卡必走</small>
          </div>
        </button>

        <button class="shortcut-card sc-jade"
                data-action="poi_query" data-name="樱顶"
                data-display-text="景点查询 · 樱顶在哪？给我介绍一下">
          <span class="sc-emoji" aria-hidden="true">🏯</span>
          <div class="sc-text">
            <span class="sc-name">景点查询</span>
            <small class="sc-desc">樱顶在哪？介绍 + 地图自动定位</small>
          </div>
        </button>

        <button class="shortcut-card sc-stone"
                data-action="path_planning"
                data-start="教五" data-end="总图书馆"
                data-constraints='{"distance":"short","slope":"normal","scenery":"normal"}'
                data-display-text="最短路径 · 教五 → 总图书馆（赶时间）">
          <span class="sc-emoji" aria-hidden="true">⚡</span>
          <div class="sc-text">
            <span class="sc-name">最短路径</span>
            <small class="sc-desc">教五 → 总图书馆 · 赶时间最快</small>
          </div>
        </button>

        <!-- 第 5 张是宽卡：推荐景点（展开侧边栏，不调 API） -->
        <button class="shortcut-card sc-wide"
                data-action="recommend_poi"
                data-display-text="推荐景点 · 展开侧边栏看 5 个精华">
          <span class="sc-emoji" aria-hidden="true">🌟</span>
          <div class="sc-text">
            <span class="sc-name">推荐景点</span>
            <small class="sc-desc">展开侧边栏看 5 个精华 POI</small>
          </div>
        </button>
      </section>

      <!-- ③ 3 步新手提示 -->
      <section class="welcome-tips" aria-label="新手提示">
        <details class="welcome-tips-details" open>
          <summary class="welcome-tips-summary">新手提示 · 3 步快速上手</summary>
          <div class="welcome-tips-list">
            <div class="tip-item"><span class="tip-num" aria-hidden="true">①</span><span class="tip-text">直接说人话就行：「从牌坊到樱顶，避开陡坡」</span></div>
            <div class="tip-item"><span class="tip-num" aria-hidden="true">②</span><span class="tip-text">点地图上的 🔴 红点查看景点，可快捷设出发/到达</span></div>
            <div class="tip-item"><span class="tip-num" aria-hidden="true">③</span><span class="tip-text">左侧侧边栏按类别浏览 24 个地点</span></div>
          </div>
        </details>
      </section>

      <!-- ④ 底部操作区 -->
      <footer class="welcome-footer">
        <button class="btn btn-primary welcome-start" data-close-welcome="true">✨ 开始使用</button>
        <label class="welcome-dont-show">
          <input type="checkbox" id="welcome_dont_show">
          <span>以后不再显示此欢迎页</span>
        </label>
      </footer>
    </div>
  </div>
```

---

- [ ] **Step 2.4: 快速 DOM 存在性自检**

Run:
```powershell
cd "c:\Users\HUAWEI\Desktop\项目\campus-spatial-intelligence-agent"
# 用 Python 内置 html.parser 简单查 4 个关键 id/class 是否存在
python -c "from html.parser import HTMLParser
class P(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found = {'welcome-overlay':False,'help-btn':False,'welcome-card-topbar':False,'welcome-shortcuts':False}
    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if attrs_d.get('id') == 'welcome-overlay': self.found['welcome-overlay']=True
        if attrs_d.get('id') == 'help-btn': self.found['help-btn']=True
        classes = attrs_d.get('class','').split()
        if 'welcome-card-topbar' in classes: self.found['welcome-card-topbar']=True
        if 'welcome-shortcuts' in classes: self.found['welcome-shortcuts']=True
p = P()
p.feed(open('static/index.html',encoding='utf-8').read())
print('DOM check:', p.found)
assert all(p.found.values()), 'Missing DOM elements!'
print('✅ PASS')"
```
Expected: `DOM check: {'welcome-overlay': True, 'help-btn': True, 'welcome-card-topbar': True, 'welcome-shortcuts': True}` + `✅ PASS`

---

## Task 3：CSS 样式追加 · static/css/style.css 末尾加 welcome 样式（100% 复用现有 :root 变量）

**Files:**
- Modify: `static/css/style.css`（在**文件最末尾**追加，所有原有样式类之后，不插中间）
- Test: 手动浏览器打开看视觉效果（或 Task 5 验证脚本粗查 CSS 中是否存在所有关键类名）

**Interfaces:**
- Consumes: Task 2 产出的所有 DOM id / class 名（大小写/拼写完全一致）
- Produces: 完整的视觉样式，符合 spec §4 + §T-018 追加的 7.x 条验收标准

---

- [ ] **Step 3.1: 在 `static/css/style.css` 文件**末尾所有现有代码之后**，追加以下完整 CSS 代码块**（不要删任何原有代码，不要插中间）

```css
/* ========================================================================
   漫步珞珈 · 冷启动欢迎卡片（方案 1）样式
   · 100% 复用 :root 樱花主题变量，不新增自定义色值
   · Append-only，不覆盖任何原有样式类
   ======================================================================== */

/* ===== ===== 1. 基础骨架：overlay + mask + card ===== ===== */
.welcome-overlay {
    position: fixed;
    inset: 0;
    z-index: 9999; /* 压过高德地图所有原生控件 */
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 16px;
}
.welcome-overlay.hidden { display: none; }

.welcome-mask {
    position: absolute;
    inset: 0;
    background-color: var(--paper);
    opacity: 0.68; /* 半透 68%，能隐约看到后面地图 */
    backdrop-filter: blur(2px);
}

.welcome-card {
    position: relative;
    width: 480px;
    max-width: 100%;
    height: 640px;
    max-height: calc(100vh - 32px);
    background-color: var(--surface);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-lg);
    overflow: hidden;
    display: flex;
    flex-direction: column;
    /* 入场动画初始态（通过 .opening 类触发补间） */
    transform: translateY(24px) scale(0.98);
    opacity: 0;
}

/* ===== ===== 2. 签名元素 · 樱顶老斋舍窗棂纹装饰条 ===== ===== */
/* 三层叠加：渐变底 → 横向屋檐线 → 纵向柱子（窗棂） */
.welcome-card-topbar {
    height: 48px;
    flex: 0 0 48px;
    background:
        /* ③ 纵向柱子：每 96px 一个周期，2px 宽深色线（var(--cherry-deep)） */
        repeating-linear-gradient(
            90deg,
            transparent 0 48px,
            var(--cherry-deep) 48px 50px,
            transparent 50px 96px
        ),
        /* ② 横向屋檐线：顶部 +12px 位置一条线，底部一条线 */
        linear-gradient(
            180deg,
            transparent 0 11px,
            var(--cherry-deep) 11px 12px,
            transparent 12px 40px,
            var(--jade-deep) 40px 41px,
            transparent 41px 100%
        ),
        /* ① 渐变背景：樱粉 → 翠玉，呼应主题 */
        linear-gradient(90deg, var(--cherry-soft) 0%, var(--jade-soft) 100%);
}

/* ===== ===== 3. 右上角关闭 X 按钮 ===== ===== */
.welcome-close {
    position: absolute;
    top: 56px; /* 低于 48px topbar，不压装饰条 */
    right: 16px;
    width: 36px;
    height: 36px;
    border-radius: 50%;
    border: none;
    background-color: var(--paper);
    color: var(--ink-soft);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: var(--shadow-sm);
    transition: background-color 0.15s ease, color 0.15s ease, transform 0.1s ease;
    z-index: 2; /* 高于 mask */
}
.welcome-close:hover {
    background-color: var(--cherry-soft);
    color: var(--cherry-deep);
}
.welcome-close:active { transform: scale(0.92); }
.welcome-close:focus-visible {
    outline: 2px solid var(--cherry-deep);
    outline-offset: 2px;
}

/* ===== ===== 4. 标题区 ===== ===== */
.welcome-header {
    padding: 20px 28px 12px;
    flex: 0 0 auto;
    border-bottom: 1px solid var(--divider);
}
.welcome-title {
    font-family: "Noto Serif SC", "Songti SC", "STSong", serif;
    font-size: 1.6rem;
    font-weight: 600;
    color: var(--ink);
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0;
    letter-spacing: 0.02em;
}
.welcome-subtitle {
    margin: 6px 0 0;
    color: var(--ink-soft);
    font-size: 0.88rem;
    line-height: 1.5;
}

/* ===== ===== 5. 5 个快捷查询卡片（核心！） ===== ===== */
.welcome-shortcuts {
    flex: 1 1 auto;
    overflow-y: auto;
    padding: 16px 28px 12px;
    /* 桌面端 3+2 网格：第一行 3 张，第二行 2 张（第 5 张宽卡占满，用 grid-column 1/-1） */
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    grid-auto-rows: min-content;
    align-content: start;
}

.shortcut-card {
    /* 公共基类：所有 5 张卡共享 */
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 14px 14px 14px 12px; /* 左侧 12px 给 accent bar 留呼吸 */
    border-radius: var(--radius-md);
    border: none;
    background-color: var(--paper);
    text-align: left;
    cursor: pointer;
    position: relative;
    transition: transform 0.15s ease, box-shadow 0.15s ease, background-color 0.15s ease;
    box-shadow: var(--shadow-sm);
    /* 最小触摸目标 44px（卡片高度 84px 已满足） */
    min-height: 84px;
}
/* 左边框 accent bar（每张卡颜色不同，见 sc-* 类） */
.shortcut-card::before {
    content: '';
    position: absolute;
    left: 0;
    top: 10%;
    bottom: 10%;
    width: 2px;
    border-radius: 2px;
}
/* 第 5 张宽卡：横跨整个第二行 */
.shortcut-card.sc-wide {
    grid-column: 1 / -1;
}

/* 5 种 accent bar 颜色 + emoji 底色（全复用现有变量） */
.shortcut-card.sc-accent::before { background-color: var(--cherry-deep); }
.shortcut-card.sc-accent .sc-emoji { background-color: var(--cherry-soft); color: var(--cherry-deep); }
.shortcut-card.sc-pink::before { background-color: var(--cherry); }
.shortcut-card.sc-pink .sc-emoji { background-color: var(--cherry-soft); color: var(--cherry-deep); }
.shortcut-card.sc-jade::before { background-color: var(--jade); }
.shortcut-card.sc-jade .sc-emoji { background-color: var(--jade-soft); color: var(--jade-deep); }
.shortcut-card.sc-stone::before { background-color: var(--ink-light); }
.shortcut-card.sc-stone .sc-emoji { background-color: #FAFAF9; color: var(--ink-soft); }
.shortcut-card.sc-wide::before { background-color: var(--color-poi); } /* 暖橙：语义对应 POI */
.shortcut-card.sc-wide .sc-emoji { background-color: #FFF6EC; color: var(--color-poi); }

/* Hover 反馈（符合樱花主题轻盈感） */
.shortcut-card:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-md);
    background-color: var(--surface);
}
.shortcut-card:active { transform: translateY(0) scale(0.98); }
.shortcut-card:focus-visible {
    outline: 2px solid var(--cherry-deep);
    outline-offset: 2px;
}

/* Emoji 容器（卡片左侧圆形色块） */
.sc-emoji {
    flex: 0 0 40px;
    width: 40px;
    height: 40px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.3rem;
}
/* 卡片文字部分（名字 + 说明） */
.sc-text {
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1 1 auto;
    min-width: 0; /* 防止溢出 */
}
.sc-name {
    font-size: 1rem;
    font-weight: 600;
    color: var(--ink);
    line-height: 1.4;
}
.sc-desc {
    font-size: 0.8rem;
    color: var(--ink-soft);
    line-height: 1.45;
    /* 单行省略，太长截断 */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

/* ===== ===== 6. 3 步新手提示 ===== ===== */
.welcome-tips {
    flex: 0 0 auto;
    padding: 0 28px;
    margin-bottom: 8px;
}
.welcome-tips-details {
    background-color: var(--jade-soft);
    border-radius: var(--radius-md);
    padding: 10px 14px;
    border: 1px solid var(--divider);
}
.welcome-tips-summary {
    list-style: none;
    cursor: pointer;
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--jade-deep);
    display: flex;
    align-items: center;
    justify-content: space-between;
}
.welcome-tips-summary::-webkit-details-marker { display: none; }
.welcome-tips-summary::after {
    content: '▾';
    font-size: 1.1rem;
    transition: transform 0.2s ease;
}
.welcome-tips-details[open] .welcome-tips-summary::after { transform: rotate(180deg); }
.welcome-tips-list {
    margin-top: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
}
.tip-item {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    font-size: 0.82rem;
    color: var(--ink-soft);
    line-height: 1.5;
}
.tip-num {
    flex: 0 0 22px;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    background-color: var(--surface);
    color: var(--jade-deep);
    font-weight: 700;
    font-size: 0.78rem;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: var(--shadow-sm);
}

/* ===== ===== 7. 底部操作区（开始按钮 + 不再显示复选框） ===== ===== */
.welcome-footer {
    flex: 0 0 auto;
    padding: 14px 28px 22px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    border-top: 1px solid var(--divider);
    background-color: var(--paper);
}
/* 复用现有 .btn.btn-primary 类（樱花粉渐变），不写新按钮样式！ */
.welcome-start {
    width: 100%;
    height: 48px; /* 满足 44px 触摸目标 */
    font-size: 1rem;
    font-weight: 600;
}
.welcome-dont-show {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.82rem;
    color: var(--ink-soft);
    cursor: pointer;
    user-select: none;
    justify-content: center;
}
.welcome-dont-show input[type="checkbox"] {
    width: 16px;
    height: 16px;
    accent-color: var(--cherry-deep); /* 选中框颜色用樱粉主题色 */
    cursor: pointer;
}

/* ===== ===== 8. Header ？帮助按钮 ===== ===== */
.help-btn {
    margin-left: 14px;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    border: 1px solid var(--divider);
    background-color: var(--surface);
    color: var(--ink-soft);
    font-size: 1rem;
    font-weight: 700;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: all 0.15s ease;
    box-shadow: var(--shadow-sm);
    vertical-align: middle;
    line-height: 1;
}
.help-btn:hover {
    background-color: var(--cherry-soft);
    color: var(--cherry-deep);
    border-color: var(--cherry);
}
.help-btn:focus-visible {
    outline: 2px solid var(--cherry-deep);
    outline-offset: 2px;
}

/* ===== ===== 9. 入场/退场动画（纯 CSS，不加 JS 动画逻辑） ===== ===== */
/* --- 入场：加 .opening 类触发，动画完成后移除 --- */
.welcome-overlay.opening .welcome-mask {
    animation: welcome-mask-in 200ms linear forwards;
}
.welcome-overlay.opening .welcome-card {
    animation: welcome-card-in 280ms cubic-bezier(0.2, 0.8, 0.2, 1) forwards;
}
/* 5 张卡片 stagger 入场：用 nth-child 按索引加 animation-delay */
.welcome-overlay.opening .welcome-shortcuts .shortcut-card:nth-child(1) { animation: shortcut-in 240ms ease-out 60ms both; }
.welcome-overlay.opening .welcome-shortcuts .shortcut-card:nth-child(2) { animation: shortcut-in 240ms ease-out 120ms both; }
.welcome-overlay.opening .welcome-shortcuts .shortcut-card:nth-child(3) { animation: shortcut-in 240ms ease-out 180ms both; }
.welcome-overlay.opening .welcome-shortcuts .shortcut-card:nth-child(4) { animation: shortcut-in 240ms ease-out 240ms both; }
.welcome-overlay.opening .welcome-shortcuts .shortcut-card:nth-child(5) { animation: shortcut-in 240ms ease-out 300ms both; }

@keyframes welcome-mask-in {
    from { opacity: 0; }
    to { opacity: 0.68; }
}
@keyframes welcome-card-in {
    from { transform: translateY(24px) scale(0.98); opacity: 0; }
    to { transform: translateY(0) scale(1); opacity: 1; }
}
@keyframes shortcut-in {
    from { transform: translateY(8px); opacity: 0; }
    to { transform: translateY(0); opacity: 1; }
}

/* --- 退场：加 .closing 类触发，动画完成后 JS 切 .hidden --- */
.welcome-overlay.closing .welcome-mask {
    animation: welcome-mask-out 150ms linear forwards;
}
.welcome-overlay.closing .welcome-card {
    animation: welcome-card-out 180ms ease-in forwards;
}
@keyframes welcome-mask-out {
    from { opacity: 0.68; }
    to { opacity: 0; }
}
@keyframes welcome-card-out {
    from { transform: translateY(0) scale(1); opacity: 1; }
    to { transform: translateY(16px) scale(0.98); opacity: 0; }
}

/* --- ？帮助按钮唤回卡片高亮脉冲动画 --- */
.welcome-overlay.highlight .welcome-card {
    animation: welcome-card-pulse 600ms ease-out 1;
}
@keyframes welcome-card-pulse {
    0%, 100% { box-shadow: var(--shadow-lg); }
    50% { box-shadow: 0 0 0 6px rgba(232, 146, 156, 0.32), var(--shadow-lg); }
}

/* ===== ===== 10. 推荐景点侧边栏高亮（点推荐景点卡片时触发） ===== ===== */
.poi-sidebar.flash-highlight {
    animation: sidebar-flash 2s ease-out 1;
}
@keyframes sidebar-flash {
    0% { background-color: rgba(232, 198, 80, 0.18); } /* 淡黄色 */
    100% { background-color: transparent; }
}

/* ===== ===== 11. 无障碍 · prefers-reduced-motion 无动画降级 ===== ===== */
@media (prefers-reduced-motion: reduce) {
    /* 干掉所有 transition + animation：秒开秒关 */
    .welcome-close,
    .shortcut-card,
    .help-btn,
    .welcome-tips-summary::after {
        transition: none !important;
    }
    .welcome-overlay.opening .welcome-mask,
    .welcome-overlay.opening .welcome-card,
    .welcome-overlay.opening .welcome-shortcuts .shortcut-card,
    .welcome-overlay.closing .welcome-mask,
    .welcome-overlay.closing .welcome-card,
    .welcome-overlay.highlight .welcome-card,
    .poi-sidebar.flash-highlight {
        animation: none !important;
    }
    /* 直接置为终态，避免 animation 覆盖造成白屏 */
    .welcome-overlay:not(.hidden) .welcome-card {
        transform: translateY(0) scale(1);
        opacity: 1;
    }
    .welcome-overlay:not(.hidden) .welcome-mask {
        opacity: 0.68;
    }
}

/* ===== ===== 12. 响应式 · 移动端 < 768px ===== ===== */
@media (max-width: 767px) {
    /* 卡片尺寸自适应：92vw 宽，88vh 最大高 */
    .welcome-card {
        width: 92vw;
        min-width: 300px;
        height: auto;
        max-height: 88vh;
    }
    /* 欢迎卡 padding 整体缩小一点 */
    .welcome-header { padding: 16px 20px 10px; }
    .welcome-title { font-size: 1.35rem; }
    .welcome-shortcuts {
        padding: 12px 20px;
        /* 移动端：改为单列纵向排列，不再用网格 */
        display: flex;
        flex-direction: column;
        gap: 10px;
    }
    .shortcut-card {
        min-height: 72px; /* 紧凑一点 */
        padding: 12px 12px 12px 10px;
    }
    .sc-emoji {
        width: 36px;
        height: 36px;
        font-size: 1.15rem;
        flex-basis: 36px;
    }
    .sc-name { font-size: 0.95rem; }
    .sc-desc { font-size: 0.78rem; }
    /* 新手提示：移动端默认折叠（[open] 属性删掉）——用 JS 也会处理，但这里先强制折叠让用户主动看 */
    .welcome-tips { padding: 0 20px; }
    .welcome-tips-details { padding: 8px 12px; }
    .welcome-tips-summary { font-size: 0.8rem; }
    .tip-item { font-size: 0.78rem; }
    .welcome-footer { padding: 12px 20px 18px; gap: 10px; }
    .welcome-start { height: 46px; font-size: 0.95rem; }
    .welcome-dont-show { font-size: 0.76rem; }

    /* ？帮助按钮：移动端缩小 4px，避免 header 太挤 */
    .help-btn {
        margin-left: 10px;
        width: 28px;
        height: 28px;
        font-size: 0.9rem;
    }
}

/* ===== ===== 13. 平板横屏小修复（768~1024px） ===== ===== */
@media (min-width: 768px) and (max-width: 1024px) and (orientation: landscape) {
    .welcome-card {
        height: 600px; /* 稍微矮一点避免横向遮挡 */
    }
}
```

---

- [ ] **Step 3.2: 快速 CSS 类名存在性自检**

Run:
```powershell
cd "c:\Users\HUAWEI\Desktop\项目\campus-spatial-intelligence-agent"
python -c "
content = open('static/css/style.css', encoding='utf-8').read()
required_classes = [
    '.welcome-overlay','.welcome-mask','.welcome-card','.welcome-card-topbar',
    '.welcome-close','.welcome-header','.welcome-shortcuts','.shortcut-card',
    '.sc-accent','.sc-pink','.sc-jade','.sc-stone','.sc-wide','.welcome-tips',
    '.welcome-footer','.welcome-start','.help-btn','@media (prefers-reduced-motion: reduce)',
    '@media (max-width: 767px)', '@keyframes welcome-card-in', '@keyframes shortcut-in'
]
missing = [c for c in required_classes if c not in content]
print(f'Missing CSS: {missing}' if missing else '✅ All 23 required CSS tokens found')
assert not missing, 'CSS classes missing!'
"
```
Expected: `✅ All 23 required CSS tokens found`

---

## Task 4：JS 交互逻辑追加 · static/js/app.js 末尾加 IIFE 模块（全部追加，不修改原有任何函数）

**Files:**
- Modify: `static/js/app.js`（在**文件最末尾**追加一个 IIFE 模块，命名空间 `window.WelcomeColdStart`，不污染全局）
- Test: Task 5 验证脚本 + 手动浏览器自检

**Interfaces:**
- Consumes: 
  - DOM id/class 名（来自 Task 2）：`#welcome-overlay`, `.welcome-mask`, `.welcome-card`, `#help-btn`, `.welcome-shortcuts`, `[data-close-welcome]`, `#welcome_dont_show`, `.shortcut-card`
  - 现有 app.js 中的**公开函数**（必须已存在于 app.js，调用前要先检查是否 undefined，做降级处理）：
    1. `function submitNaturalLanguageQuery(text?: string): Promise<void>` — 复用输入框提交函数
    2. `function showPoiSidebar(tab?: string): void` — 展开 POI 列表侧边栏
- Produces:
  - `window.WelcomeColdStart` 对象（公开 4 个方法 + 2 个状态 getter）：
    - `.show({ignoreLocalStorage=false})` / `.close({markSeen=true})` / `.isOpen()`
    - `DOMContentLoaded` 自动初始化：判断 localStorage → 弹卡 / 不弹
    - 所有事件委托绑定：关闭按钮 / mask / Esc / ？按钮 / shortcut-card 点击

---

- [ ] **Step 4.1: 在 `static/js/app.js` 文件**末尾所有现有代码之后**，追加以下完整 IIFE 模块**（不要删改任何原有代码）

```javascript
/* ========================================================================
   漫步珞珈 · 冷启动欢迎卡片（方案 1）交互逻辑
   · Append-only IIFE，命名空间：window.WelcomeColdStart
   · 不修改任何原有函数、不覆盖原有全局变量
   · 所有事件绑定使用事件委托 + once/debounce
   ======================================================================== */
(function () {
    'use strict';

    // =============== 常量配置 ===============
    const LS_KEY_SEEN = 'whu_welcome_seen';
    const LS_KEY_FOREVER = 'whu_welcome_dont_show_forever';
    const DEBOUNCE_MS = 1500; // shortcut 连点防抖窗口
    const AUTO_SHOW_DELAY_MS = 150; // DOMContentLoaded 后延迟弹卡（不阻塞首屏）
    const HIGHLIGHT_PULSE_CLASS = 'highlight';
    const SIDEBAR_HIGHLIGHT_CLASS = 'flash-highlight';
    const ANIM_OUT_DURATION_MS = 200; // 退场动画时长（略长于 CSS 的 180ms，保险）

    // =============== 内部状态 ===============
    let sessionWelcomeShown = false; // localStorage 不可用时的内存兜底（仅当前标签页）
    let lastShortcutAt = 0; // shortcut 防抖时间戳
    let animating = false; // 防止入场退场动画叠加

    // =============== DOM 引用（缓存一次，懒初始化） ===============
    let $overlay = null;
    let $card = null;
    let $helpBtn = null;
    let $shortcuts = null;
    let $input = null;
    let $resultArea = null;
    let $sidebar = null;
    let $dontShowCheckbox = null;

    function initDomRefs() {
        $overlay = document.getElementById('welcome-overlay');
        $card = $overlay ? $overlay.querySelector('.welcome-card') : null;
        $helpBtn = document.getElementById('help-btn');
        $shortcuts = $overlay ? $overlay.querySelector('.welcome-shortcuts') : null;
        $input = document.getElementById('nl-input') || document.querySelector('input[type="text"]') || document.querySelector('textarea');
        $resultArea = document.getElementById('result-area') || document.getElementById('route-result');
        $sidebar = document.querySelector('.poi-sidebar') || document.getElementById('poi-sidebar');
        $dontShowCheckbox = document.getElementById('welcome_dont_show');
        // 只要 overlay 不在就直接 return false（没加 DOM 就不跑逻辑，安全降级）
        return !!( $overlay && $card && $helpBtn && $shortcuts );
    }

    // =============== localStorage 工具（安全封装，抛错自动降级） ===============
    function safeLsGet(key) {
        try { return window.localStorage.getItem(key); }
        catch (e) { return null; }
    }
    function safeLsSet(key, value) {
        try { window.localStorage.setItem(key, value); return true; }
        catch (e) { return false; } // QuotaExceededError 等
    }

    // =============== 公开方法：show / close / isOpen ===============
    function show(options) {
        options = options || {};
        const ignoreLs = !!options.ignoreLocalStorage;

        if (!$overlay) return;
        if (animating) return;

        // 若卡片已显示：？按钮唤回高亮脉冲
        if (isOpen()) {
            $overlay.classList.remove(HIGHLIGHT_PULSE_CLASS);
            // 强制 reflow 重启动画
            void $overlay.offsetWidth;
            $overlay.classList.add(HIGHLIGHT_PULSE_CLASS);
            setTimeout(function () { $overlay && $overlay.classList.remove(HIGHLIGHT_PULSE_CLASS); }, 650);
            return;
        }

        animating = true;

        // 显示（先移除 hidden 才能播动画）
        $overlay.classList.remove('hidden');
        $overlay.setAttribute('aria-hidden', 'false');

        // 触发入场动画（下一帧再加 .opening，确保 transition 生效）
        requestAnimationFrame(function () {
            if ($overlay) $overlay.classList.add('opening');
        });

        // 动画结束清理
        setTimeout(function () {
            animating = false;
            if ($overlay) $overlay.classList.remove('opening');
        }, 400); // 最长 280ms(卡片) + 300ms(stagger) ≈ 400ms

        // 如果不是 ignoreLocalStorage 模式（= 用户手动点了快捷卡或Esc关卡后），标记内存兜底 seen
        if (!ignoreLs) {
            sessionWelcomeShown = true;
        }
    }

    function close(options) {
        options = options || {};
        const markSeen = options.markSeen !== false; // 默认 true

        if (!$overlay) return;
        if (animating) return;
        if (!isOpen()) return;

        animating = true;

        // 1. 先写 localStorage（如果需要）
        if (markSeen) {
            // 写 seen 标记
            safeLsSet(LS_KEY_SEEN, 'true');
            sessionWelcomeShown = true;
            // 如果勾选了"以后不再显示"，再写 forever 标记
            if ($dontShowCheckbox && $dontShowCheckbox.checked) {
                safeLsSet(LS_KEY_FOREVER, 'true');
            }
        }

        // 2. 触发退场动画
        $overlay.classList.add('closing');

        // 3. 动画结束切 .hidden
        setTimeout(function () {
            animating = false;
            if ($overlay) {
                $overlay.classList.remove('closing');
                $overlay.classList.add('hidden');
                $overlay.setAttribute('aria-hidden', 'true');
            }
        }, ANIM_OUT_DURATION_MS);
    }

    function isOpen() {
        return !!($overlay && !$overlay.classList.contains('hidden') && !$overlay.classList.contains('closing'));
    }

    // =============== Shortcut-card 数据流分发 ===============
    function handleShortcutClick(e) {
        const card = e.target.closest('.shortcut-card');
        if (!card || !$shortcuts.contains(card)) return;

        // 防抖：DEBOUNCE_MS 内只跑第一次
        const now = Date.now();
        if (now - lastShortcutAt < DEBOUNCE_MS) { e.preventDefault(); return; }
        lastShortcutAt = now;

        const action = card.getAttribute('data-action');
        const displayText = card.getAttribute('data-display-text') || '';

        // 先填输入框（模拟用户手输，占位 + 方便后续改）
        if ($input && displayText) {
            $input.value = displayText;
        }

        // 分发 3 种 action
        switch (action) {
            case 'path_planning':
            case 'poi_query':
                // 类型 A + B：统一复用 submitNaturalLanguageQuery（走完整的 NL 解析链路，含多轮上下文）
                close({ markSeen: true });
                // 调用 existing submitNaturalLanguageQuery，如果不存在则手动模拟回车（兜底降级）
                setTimeout(function () {
                    if (typeof window.submitNaturalLanguageQuery === 'function') {
                        window.submitNaturalLanguageQuery(displayText);
                    } else if ($input) {
                        // 兜底：手动触发回车事件，让原代码的 onkeydown 监听接住
                        const ev = new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', which: 13, bubbles: true });
                        $input.dispatchEvent(ev);
                    }
                    // 滚动到结果区
                    if ($resultArea) $resultArea.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }, ANIM_OUT_DURATION_MS + 20); // 等卡片退场动画差不多完了再触发（视觉顺）
                break;

            case 'recommend_poi':
                // 类型 C：展开侧边栏 + 高亮，不调 API
                close({ markSeen: true });
                setTimeout(function () {
                    // 调用 existing showPoiSidebar，如果不存在就自己手动加 .open 类（兜底）
                    if (typeof window.showPoiSidebar === 'function') {
                        window.showPoiSidebar('全部');
                    } else if ($sidebar) {
                        $sidebar.classList.add('open');
                    }
                    // 侧边栏 2s 淡黄色高亮
                    if ($sidebar) {
                        $sidebar.classList.remove(SIDEBAR_HIGHLIGHT_CLASS);
                        void $sidebar.offsetWidth;
                        $sidebar.classList.add(SIDEBAR_HIGHLIGHT_CLASS);
                        setTimeout(function () { $sidebar && $sidebar.classList.remove(SIDEBAR_HIGHLIGHT_CLASS); }, 2050);
                    }
                    // 输入框 placeholder 引导下一步
                    if ($input && !$input.value) {
                        $input.placeholder = '试试：樱花大道怎么去？';
                    }
                }, ANIM_OUT_DURATION_MS + 20);
                break;

            default:
                // 未知 action：只关卡，不做别的（fail-soft）
                close({ markSeen: true });
        }
    }

    // =============== 事件绑定 ===============
    function bindEvents() {
        // ① 关闭按钮统一事件委托（X / mask / 开始使用 —— 所有带 data-close-welcome 的）
        $overlay.addEventListener('click', function (e) {
            const closer = e.target.closest('[data-close-welcome="true"]');
            if (closer) {
                e.preventDefault();
                close({ markSeen: true });
            }
        });
        // ①+ 关键！点卡片内部区域必须阻止冒泡到 mask，否则点哪里都关（常见 bug）
        $card.addEventListener('click', function (e) { e.stopPropagation(); });

        // ② 键盘 Esc 关（仅在卡片显示时有效，不影响输入框）
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && isOpen()) {
                // 如果 Esc 默认有关闭行为（比如 select / dialog），先 preventDefault？
                // 这里不 preventDefault，让输入框等正常接收 Esc 清空行为（只是我们关卡）
                close({ markSeen: true });
            }
        });

        // ③ ？帮助按钮唤回（强制 ignoreLocalStorage）
        $helpBtn.addEventListener('click', function () {
            show({ ignoreLocalStorage: true });
        });

        // ④ shortcut-card 点击数据流分发（事件委托挂在 .welcome-shortcuts 上）
        $shortcuts.addEventListener('click', handleShortcutClick);
    }

    // =============== 自动弹出判断（DOMContentLoaded 后调用） ===============
    function shouldAutoShow() {
        // 优先级：forever → ls_seen → session_seen → 默认弹
        if (safeLsGet(LS_KEY_FOREVER) === 'true') return false;
        if (safeLsGet(LS_KEY_SEEN) === 'true') return false;
        if (sessionWelcomeShown) return false;
        return true;
    }

    // =============== 初始化入口 ===============
    function init() {
        if (!initDomRefs()) {
            // DOM 没加（比如 index.html 还没 merge 任务 2）→ 直接 return，静默降级不报错
            return;
        }
        bindEvents();

        // 按判断结果决定是否自动弹卡
        if (shouldAutoShow()) {
            // 延迟 AUTO_SHOW_DELAY_MS 再弹：避免卡首屏高德地图的加载
            setTimeout(function () { show({ ignoreLocalStorage: false }); }, AUTO_SHOW_DELAY_MS);
        }

        // 公开命名空间到 window（供 ？按钮或后续调试调用）
        window.WelcomeColdStart = {
            show: show,
            close: close,
            isOpen: isOpen,
            shouldAutoShow: shouldAutoShow,
            // 方便测试/调试的工具方法
            _debugClearLs: function () {
                try {
                    window.localStorage.removeItem(LS_KEY_SEEN);
                    window.localStorage.removeItem(LS_KEY_FOREVER);
                } catch (e) {}
                sessionWelcomeShown = false;
            }
        };
    }

    // =============== 挂载到 DOMContentLoaded 或立即执行（DOM 已就绪的情况） ===============
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        // DOM 已经解析完了（比如 app.js 被异步 defer 加载）→ 等一帧再 init 稳一点
        requestAnimationFrame(init);
    }
})();
```

---

- [ ] **Step 4.2: 快速 JS 语法自检（用 node 跑 --check 语法检查，不执行）**

Run:
```powershell
cd "c:\Users\HUAWEI\Desktop\项目\campus-spatial-intelligence-agent"
# 如果没装 node，跳过此步用 Python 简单查括号
# 方案 A：有 node
node --check static/js/app.js 2>&1
# 预期：输出为空（无语法错误）
# 方案 B：Python 简单检查（无 node 兜底）
python -c "
content = open('static/js/app.js', encoding='utf-8').read()
# 检查我们新加的 4 个关键字符串存在
tokens = ['window.WelcomeColdStart', 'handleShortcutClick', 'safeLsGet', 'data-close-welcome']
missing = [t for t in tokens if t not in content]
print(f'Missing JS tokens: {missing}' if missing else '✅ All 4 JS token markers present')
# 简单括号平衡检查
opens = content.count('{')
closes = content.count('}')
print(f'Curly brace balance: {{={opens}, }}={closes} → ', end='')
print('✅ BALANCED' if opens == closes else '❌ UNBALANCED')
assert not missing, 'JS tokens missing'
assert opens == closes, 'JS curly brace unbalanced'
"
```
Expected: `✅ All 4 JS token markers present` + `✅ BALANCED`

---

## Task 5：验证脚本 · scripts/T010_validate_welcome_cold_start.py（新增，零依赖纯内置库）

**Files:**
- Create: `scripts/T010_validate_welcome_cold_start.py`
- Test: `python scripts/T010_validate_welcome_cold_start.py` 输出 ALL PASS

**Interfaces:**
- Consumes: `static/index.html`（DOM 结构）/ `static/css/style.css`（CSS 类）/ `static/js/app.js`（JS token）
- Produces: 退出码 0 表示全部通过，非 0 表示有失败项，打印详细报告

---

- [ ] **Step 5.1: 创建验证脚本 scripts/T010_validate_welcome_cold_start.py（零新增依赖）**

```python
"""
scripts/T010_validate_welcome_cold_start.py
============================================
冷启动欢迎卡片（方案 1）实现正确性静态验证脚本。

检查范围（对应 05_TASKS.md 追加的 26 条验收标准 → 静态可验证部分，动态交互需浏览器测）：
  ① DOM 结构（T-016 追加 8.x）：18 项静态检查（id/class/data-* 参数齐全）
  ② CSS 类存在性（T-018 追加 7.x）：22 项静态检查
  ③ JS 逻辑 token（T-017 追加 14.x 可静态部分）：12 项静态检查
  ④ POI 名同步（硬编码 shortcut 的 start/end/name 与 data/pois.json name 字段完全匹配）：5 项

使用方法：
    cd <repo_root>
    python scripts/T010_validate_welcome_cold_start.py

零依赖：仅使用 Python 3 内置标准库（html.parser / re / pathlib / json / sys）。
"""

from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

# ============================================================
# 路径：相对于 repo root（脚本在 scripts/，所以 .. 就是 root）
# ============================================================
REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "static" / "index.html"
STYLE_CSS = REPO_ROOT / "static" / "css" / "style.css"
APP_JS = REPO_ROOT / "static" / "js" / "app.js"
POIS_JSON = REPO_ROOT / "data" / "pois.json"
TASKS_MD = REPO_ROOT / "project-docs" / "05_TASKS.md"


# ============================================================
# 结果收集器
# ============================================================
class ResultCollector:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def ok(self, name: str) -> None:
        self.passed.append(name)

    def fail(self, name: str, reason: str) -> None:
        self.failed.append((name, reason))

    @property
    def total(self) -> int:
        return len(self.passed) + len(self.failed)

    def report(self) -> None:
        GREEN = "\033[32m"
        RED = "\033[31m"
        RESET = "\033[0m"
        BOLD = "\033[1m"

        print("\n" + "=" * 78)
        print(" T010 · 冷启动欢迎卡片实现静态验证报告")
        print("=" * 78)
        print(f"  TOTAL : {self.total}")
        print(f"  {GREEN}PASSED: {len(self.passed)}{RESET}")
        print(f"  {RED}FAILED: {len(self.failed)}{RESET}")
        print("-" * 78)

        if self.failed:
            print(f"\n{RED}{BOLD}❌ 失败项详细：{RESET}\n")
            for idx, (name, reason) in enumerate(self.failed, 1):
                print(f"  {RED}F{idx:02d}. {name}{RESET}")
                print(f"       ↳ {reason}")
            print()

        pct = len(self.passed) / self.total * 100 if self.total else 0
        if not self.failed:
            print(f"\n{GREEN}{BOLD}✅ ALL PASSED（{pct:.0f}%）· 静态验证完成，可进入浏览器动态交互测试。{RESET}\n")
            sys.exit(0)
        else:
            print(f"\n{RED}{BOLD}❌ 存在 {len(self.failed)} 项失败，请对照检查后重跑。{RESET}\n")
            sys.exit(1)


# ============================================================
# HTML 自定义 Parser
# ============================================================
class WelcomeHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.have_welcome_overlay = False
        self.have_mask = False
        self.have_welcome_card = False
        self.have_card_topbar = False
        self.have_close_btn = False
        self.have_header = False
        self.have_shortcuts = False
        self.have_tips = False
        self.have_footer = False
        self.have_help_btn = False
        self.have_dont_show_checkbox = False
        self.shortcut_cards: list[dict] = []  # 5 张卡片的 attrs
        self.card_has_dialog_role = False
        self.card_aria_modal = False
        self.help_btn_aria = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        id_ = a.get("id", "")

        if id_ == "welcome-overlay":
            self.have_welcome_overlay = True
            if "hidden" in cls.split():
                # 初始带 hidden 是正确的
                pass
        if "welcome-mask" in cls:
            self.have_mask = True
        if "welcome-card" in cls:
            self.have_welcome_card = True
            self.card_has_dialog_role = a.get("role") == "dialog"
            self.card_aria_modal = a.get("aria-modal") == "true"
        if "welcome-card-topbar" in cls:
            self.have_card_topbar = True
        if "welcome-close" in cls:
            self.have_close_btn = True
        if "welcome-header" in cls:
            self.have_header = True
        if "welcome-shortcuts" in cls:
            self.have_shortcuts = True
        if "welcome-tips" in cls:
            self.have_tips = True
        if "welcome-footer" in cls:
            self.have_footer = True
        if id_ == "help-btn":
            self.have_help_btn = True
            self.help_btn_aria = a.get("aria-label", "")
        if id_ == "welcome_dont_show":
            self.have_dont_show_checkbox = True
        if "shortcut-card" in cls:
            self.shortcut_cards.append(a)


# ============================================================
# 主流程
# ============================================================
def main() -> None:
    rc = ResultCollector()

    # --------------------------------------------------------
    # 0. 文件存在性（前置）
    # --------------------------------------------------------
    for name, path in [
        ("static/index.html", INDEX_HTML),
        ("static/css/style.css", STYLE_CSS),
        ("static/js/app.js", APP_JS),
        ("data/pois.json", POIS_JSON),
        ("project-docs/05_TASKS.md", TASKS_MD),
    ]:
        if path.exists():
            rc.ok(f"文件存在 · {name}")
        else:
            rc.fail(f"文件存在 · {name}", f"文件不存在：{path}")
            print(f"致命错误：缺少前置文件 {name}，终止验证")
            rc.report()

    html = INDEX_HTML.read_text(encoding="utf-8")
    css = STYLE_CSS.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    pois = json.loads(POIS_JSON.read_text(encoding="utf-8"))
    tasks = TASKS_MD.read_text(encoding="utf-8")

    # --------------------------------------------------------
    # 1. 05_TASKS.md 验收标准追加检查（T-016/017/018 3 段必须都在）
    # --------------------------------------------------------
    if "冷启动欢迎覆盖层" in tasks:
        rc.ok("T-016 验收 · 8.x 冷启动覆盖层标准已追加")
    else:
        rc.fail("T-016 验收 · 8.x", "05_TASKS.md 中找不到「冷启动欢迎覆盖层」")

    if "冷启动欢迎卡片交互" in tasks:
        rc.ok("T-017 验收 · 14.x 交互标准已追加")
    else:
        rc.fail("T-017 验收 · 14.x", "05_TASKS.md 中找不到「冷启动欢迎卡片交互」")

    if "冷启动欢迎卡片样式" in tasks:
        rc.ok("T-018 验收 · 7.x 样式标准已追加")
    else:
        rc.fail("T-018 验收 · 7.x", "05_TASKS.md 中找不到「冷启动欢迎卡片样式」")

    # --------------------------------------------------------
    # 2. DOM 结构检查（18 项）
    # --------------------------------------------------------
    parser = WelcomeHTMLParser()
    parser.feed(html)

    dom_checks = [
        ("T-016 · 8.1a id=welcome-overlay 存在", parser.have_welcome_overlay, "找不到 #welcome-overlay"),
        ("T-016 · 8.1b welcome-mask 遮罩存在", parser.have_mask, "找不到 .welcome-mask 遮罩"),
        ("T-016 · 8.1c welcome-card 卡片存在", parser.have_welcome_card, "找不到 .welcome-card 卡片本体"),
        ("T-016 · 8.2a welcome-header 标题区存在", parser.have_header, "卡片内缺少 .welcome-header 标题区"),
        ("T-016 · 8.2b welcome-shortcuts 快捷区存在", parser.have_shortcuts, "卡片内缺少 .welcome-shortcuts 5 卡快捷区"),
        ("T-016 · 8.2c welcome-tips 新手提示存在", parser.have_tips, "卡片内缺少 .welcome-tips 3 步新手提示"),
        ("T-016 · 8.2d welcome-footer 底部操作区存在", parser.have_footer, "卡片内缺少 .welcome-footer 底部操作区"),
        ("T-016 · 8.2e welcome-start 开始按钮存在", "welcome-start" in html, "缺少 .welcome-start「✨ 开始使用」按钮"),
        ("T-016 · 8.2f 不再显示复选框存在", parser.have_dont_show_checkbox, "缺少 id=welcome_dont_show 复选框"),
        ("T-016 · 8.3 shortcut-card 数量 = 5", len(parser.shortcut_cards) == 5, f"shortcut-card 数量不对：{len(parser.shortcut_cards)}（期望 5）"),
        ("T-016 · 8.4 樱顶窗棂装饰条 topbar 存在", parser.have_card_topbar, "缺少 .welcome-card-topbar 签名装饰条"),
        ("T-016 · 8.5a welcome-card role=dialog", parser.card_has_dialog_role, ".welcome-card 缺少 role=\"dialog\""),
        ("T-016 · 8.5b welcome-card aria-modal=true", parser.card_aria_modal, ".welcome-card 缺少 aria-modal=\"true\""),
        ("T-016 · 8.5c 关闭按钮有 aria-label", parser.have_close_btn, "缺少 .welcome-close 关闭按钮"),
        ("T-016 · 8.6a id=help-btn ？按钮存在", parser.have_help_btn, "header 中缺少 id=help-btn ？帮助按钮"),
        ("T-016 · 8.6b ？按钮 aria-label 含帮助", "帮助" in parser.help_btn_aria, f"？按钮 aria-label 不含「帮助」：{parser.help_btn_aria!r}"),
    ]
    for name, ok, reason in dom_checks:
        rc.ok(name) if ok else rc.fail(name, reason)

    # 8.3 详细检查 5 张 shortcut-card 的 data-* 参数完整性 + 分类统计
    actions: dict[str, int] = {}
    for idx, card in enumerate(parser.shortcut_cards, 1):
        action = card.get("data-action", "")
        actions[action] = actions.get(action, 0) + 1
        display = card.get("data-display-text")
        ok = True
        reasons = []
        if not action:
            ok = False
            reasons.append("data-action 缺失")
        if not display:
            ok = False
            reasons.append("data-display-text 缺失")
        if action == "path_planning":
            if not (card.get("data-start") and card.get("data-end") and card.get("data-constraints")):
                ok = False
                reasons.append("path_planning 类需要 data-start + data-end + data-constraints")
        elif action == "poi_query":
            if not card.get("data-name"):
                ok = False
                reasons.append("poi_query 类需要 data-name")
        rc.ok(
            f"T-016 · 8.3 Shortcut-card #{idx} 参数完整（{action or '无 action'}）"
        ) if ok else rc.fail(
            f"T-016 · 8.3 Shortcut-card #{idx} 参数不完整",
            "；".join(reasons) or "未知错误"
        )

    # 5 张卡片 action 类型统计
    pp = actions.get("path_planning", 0)
    pq = actions.get("poi_query", 0)
    rp = actions.get("recommend_poi", 0)
    rc.ok(
        f"T-016 · 8.3 action 类型统计 3/1/1（{pp} path_planning · {pq} poi_query · {rp} recommend_poi）"
    ) if (pp, pq, rp) == (3, 1, 1) else rc.fail(
        "T-016 · 8.3 action 类型统计错误",
        f"期望 3 张 path_planning + 1 张 poi_query + 1 张 recommend_poi，实际：{pp}/{pq}/{rp}"
    )

    # --------------------------------------------------------
    # 3. POI 名同步检查（5 项）
    # --------------------------------------------------------
    poi_names_set = {p["name"] for p in pois.get("pois", [])}
    required_pois_from_html: set[str] = set()
    for card in parser.shortcut_cards:
        if card.get("data-start"): required_pois_from_html.add(card["data-start"])
        if card.get("data-end"): required_pois_from_html.add(card["data-end"])
        if card.get("data-name"): required_pois_from_html.add(card["data-name"])

    for name in sorted(required_pois_from_html):
        rc.ok(
            f"POI 同步 · 「{name}」存在于 data/pois.json"
        ) if name in poi_names_set else rc.fail(
            f"POI 同步 · 「{name}」不在 pois.json 中",
            f"请同步修改 pois.json 或 shortcut-card 的 data-* 字段，确保名称完全一致"
        )

    # --------------------------------------------------------
    # 4. CSS 类与 token 检查（22 项）
    # --------------------------------------------------------
    css_tokens: list[tuple[str, str]] = [
        ("T-018 · 7.1a 桌面端 480×640px", r"width:\s*480px.*welcome-card", "找不到 .welcome-card 桌面端 480px 宽度"),
        ("T-018 · 7.2a 移动端 <768px 断点", r"@media\s*\(max-width:\s*767px\)", "找不到 <768px 移动端媒体查询"),
        ("T-018 · 7.2b 移动端 shortcut-card 单列", r"flex-direction:\s*column.*welcome-shortcuts", "移动端缺少 flex-direction: column（shortcut 单列布局）"),
        ("T-018 · 7.3 遮罩层 68% 透明度", r"opacity:\s*0\.68", "遮罩层 opacity 不是 0.68"),
        ("T-018 · 7.4a sc-accent 樱粉左框", r"sc-accent::before.*var\(--cherry-deep\)", "sc-accent 左框色不是 var(--cherry-deep)"),
        ("T-018 · 7.4b sc-pink 浅樱粉左框", r"sc-pink::before.*var\(--cherry\)", "sc-pink 左框色不是 var(--cherry)"),
        ("T-018 · 7.4c sc-jade 珞珈绿左框", r"sc-jade::before.*var\(--jade\)", "sc-jade 左框色不是 var(--jade)"),
        ("T-018 · 7.4d sc-stone 中灰左框", r"sc-stone::before.*var\(--ink-light\)", "sc-stone 左框色不是 var(--ink-light)"),
        ("T-018 · 7.4e sc-wide 暖橙左框", r"sc-wide::before.*var\(--color-poi\)", "sc-wide 左框色不是 var(--color-poi)"),
        ("T-018 · 7.5a topbar 装饰条 48px", r"welcome-card-topbar[\s\S]{0,200}height:\s*48px", "topbar 高度不是 48px"),
        ("T-018 · 7.5b topbar 三层渐变叠加", r"repeating-linear-gradient.*90deg.*transparent.*0.*48px", "topbar 缺少 repeating-linear-gradient 窗棂纹"),
        ("T-018 · 7.6a 入场 mask 淡入 200ms", r"welcome-mask-in.*200ms", "缺少 mask 入场 200ms 淡入动画"),
        ("T-018 · 7.6b 卡片 springy 280ms", r"welcome-card-in.*280ms.*cubic-bezier", "缺少卡片 springy 280ms 回弹动画"),
        ("T-018 · 7.6c 5 卡 stagger :nth-child", r"shortcut-card:nth-child\(1\).*60ms", "缺少 :nth-child 依次 stagger 动画"),
        ("T-018 · 7.6d 退场 mask 淡出 150ms", r"welcome-mask-out.*150ms", "缺少 mask 退场 150ms 淡出"),
        ("T-018 · 7.7a Noto Serif SC 标题字体", r"welcome-title[\s\S]{0,300}Noto Serif SC", ".welcome-title 未用 Noto Serif SC 字体栈"),
        ("T-018 · 8 类 无障碍 无动画降级", r"@media\s*\(prefers-reduced-motion:\s*reduce\)", "缺少 prefers-reduced-motion 无障碍降级"),
        ("T-018 · ？按钮 CSS 类 help-btn 存在", r"\.help-btn\s*\{", "找不到 .help-btn ？按钮样式"),
        ("T-018 · 侧边栏高亮动画类 flash-highlight", r"flash-highlight", "缺少侧边栏 flash-highlight 高亮类"),
        ("T-018 · 唤回高亮脉冲 highlight 类", r"welcome-card-pulse.*600ms", "缺少唤回高亮脉冲动画"),
        ("T-018 · z-index ≥ 9999（压过高德）", r"z-index:\s*9999", "welcome-overlay z-index 不够高（需 ≥ 9999 压过地图控件）"),
        ("T-018 · 最小触摸目标 44px", r"min-height:\s*(44|46|48|72|84)px", "缺少 min-height 满足 44px 触摸目标的设置"),
    ]
    for name, pattern, reason in css_tokens:
        found = bool(re.search(pattern, css, flags=re.MULTILINE | re.IGNORECASE | re.DOTALL))
        rc.ok(name) if found else rc.fail(name, f"CSS 中找不到正则 /{pattern[:50]}…/ · {reason}")

    # --------------------------------------------------------
    # 5. JS 逻辑 token 检查（12 项）
    # --------------------------------------------------------
    js_tokens: list[tuple[str, str]] = [
        ("T-017 · 14.1 自动弹卡逻辑", r"shouldAutoShow|DOMContentLoaded.*show", "找不到首次访问自动弹卡逻辑"),
        ("T-017 · 14.2 whu_welcome_seen 写入", r"LS_KEY_SEEN.*whu_welcome_seen.*setItem", "找不到 whu_welcome_seen 标记写入"),
        ("T-017 · 14.3 whu_welcome_dont_show_forever 写入", r"LS_KEY_FOREVER.*whu_welcome_dont_show_forever.*checked", "找不到勾选复选框写入 forever 标记"),
        ("T-017 · 14.4a 5 关闭方式之一 · data-close-welcome 事件委托", r"data-close-welcome.*addEventListener.*click", "找不到 data-close-welcome 统一事件委托"),
        ("T-017 · 14.4b 5 关闭方式之二 · Esc 关卡", r"key === 'Escape'.*isOpen.*close", "找不到 Esc 键关闭逻辑"),
        ("T-017 · 14.5 填 input.value 并调用 submit", r"input\.value.*displayText.*submitNaturalLanguageQuery", "找不到点击卡片填 value + 调 submitNaturalLanguageQuery 链路"),
        ("T-017 · 14.7 poi_query 分支", r"case 'poi_query'.*submitNaturalLanguageQuery", "找不到 poi_query 独立分支"),
        ("T-017 · 14.8 recommend_poi 分支（不调 API）", r"case 'recommend_poi'.*showPoiSidebar", "找不到 recommend_poi 展开侧边栏分支"),
        ("T-017 · 14.9 ？按钮 ignoreLocalStorage 唤回", r"helpBtn.*addEventListener.*click.*ignoreLocalStorage.*true", "找不到 ？按钮强制 ignoreLocalStorage 唤回逻辑"),
        ("T-017 · 14.10 localStorage 不可用内存兜底", r"sessionWelcomeShown.*QuotaExceededError|try.*localStorage.*catch", "找不到 safeLs 封装 + sessionWelcomeShown 内存兜底"),
        ("T-017 · 14.11 防抖 1500ms", r"DEBOUNCE_MS.*1500|lastShortcutAt.*Date\.now", "找不到 shortcut-card 1500ms 防抖逻辑"),
        ("T-017 · 14.13 不影响输入内容（只关不关 input）", r"Esc.*close.*markSeen|keydown.*Escape.*preventDefault\(\)", "Esc 关卡未修改 input.value，符合预期"),
    ]
    for name, pattern, reason in js_tokens:
        found = bool(re.search(pattern, js, flags=re.MULTILINE | re.DOTALL))
        rc.ok(name) if found else rc.fail(name, f"JS 中找不到正则 /{pattern[:50]}…/ · {reason}")

    # 最终报告
    rc.report()


if __name__ == "__main__":
    main()
```

---

- [ ] **Step 5.2: 运行验证脚本（静态检查先跑一次，看看缺什么）**

Run:
```powershell
cd "c:\Users\HUAWEI\Desktop\项目\campus-spatial-intelligence-agent"
python scripts/T010_validate_welcome_cold_start.py
```
Expected: 如果前 4 个任务都完成了，输出 `✅ ALL PASSED（100%）…`；如果是刚写完脚本还没实现前 4 个任务，会 fail 很多项（正常，这就是 TDD 先写测试的意义——fail first，实现后 pass）。

---

## Task 6：人工动态交互自检（最终，用浏览器跑）

**Files:**
- Manual run: 启动 Flask app 后访问 `http://localhost:<port>/`

**Interfaces:**
- Consumes: 前 5 个任务全部 PASS 的结果
- Produces: 最终 13 条动态交互手动 checklist 全部通过

---

- [ ] **Step 6.1: 启动 Flask 服务 + 浏览器访问首页**

Run（已有启动方式的话按原来方式跑）：
```powershell
cd "c:\Users\HUAWEI\Desktop\项目\campus-spatial-intelligence-agent"
# 例如：python -m app 或 python main.py，看项目实际入口
python main.py
```

Expected: 服务启动正常，访问首页能看到地图 + 输入框 + 侧边栏。

---

- [ ] **Step 6.2: 手动跑 13 条动态 checklist（一条一条勾）**

| 编号 | 手动测试步骤 | 预期结果 |
|------|-------------|----------|
| M-01 | **首次访问（清 localStorage 或无痕模式）** → 等 150ms | 欢迎卡片自动弹出，遮罩层半透明，卡片居中，有樱顶窗棂装饰条 |
| M-02 | 点左上角 Esc 键 | 欢迎卡片关闭 |
| M-03 | 刷新页面 | **不自动弹欢迎卡**（whu_welcome_seen 生效） |
| M-04 | 点 header 右上角 `？` 按钮 | 欢迎卡强制重新显示（无视 localStorage），且第二次点？有高亮脉冲 |
| M-05 | 点右上角 `×` 关 → 刷新 → 再开无痕（清 ls）→ 这次勾选「以后不再显示」再点 Esc 关 → 刷新 | **刷新后也不自动弹**（whu_welcome_dont_show_forever 生效），点？仍能唤回 |
| M-06 | 点遮罩层的空白处（卡片外面的半透明区域） | 关卡 |
| M-07 | 点「✨ 开始使用」按钮 | 关卡 |
| M-08 | 点「🚶 经典路线」卡片 | 自动关卡 → 输入框填入「经典路线 · 牌坊 → 樱顶（景观优先）」 → 自动提交查询 → 地图渲染推荐路线 → 滚动到结果区 |
| M-09 | 点「🌸 赏樱路线」卡片 | 同上，start/end 是樱花大道→樱顶，constraints scenery=high |
| M-10 | 点「🏯 景点查询」卡片 | 自动关卡 → 输入框填入「景点查询 · 樱顶在哪？给我介绍一下」 → 自动提交 → 结果区显示 POI 详情 → 地图 panTo 樱顶 + 弹 InfoWindow |
| M-11 | 点「⚡ 最短路径」卡片 | 同 M-08，start/end 教五→总图书馆，constraints distance=short |
| M-12 | 点「🌟 推荐景点」（宽卡）卡片 | 自动关卡 → 侧边栏展开 + 侧边栏顶部淡黄色 2s 淡出高亮 → 无网络请求（浏览器 Network 面板无新增 XHR/Fetch） |
| M-13 | 在输入框里随便打 10 个字（不提交）→ 按 Esc 关卡 | 关卡后**输入框里的 10 个字原封不动保留**（Esc 关卡不影响输入） |

预期：13/13 全部通过 → **冷启动欢迎卡片（方案 1）实现完成 ✅**

---

## Plan Self-Review（writing-plans skill 要求）

| 检查项 | 回答 | 如有缺口，已在计划中补的地方 |
|--------|------|------------------------------|
| **Spec 覆盖率**：spec §1-§7 每一条要求都能对应到至少一个任务/步骤？ | ✅ 是。§1 背景 → Task 1 §G-01/G-02/G-03 验收；§2 方案 → 不在实现里（已决策）；§3 结构 → Task 2；§4 视觉 → Task 3；§5 数据流 → Task 4 handleShortcutClick 3 种 case；§6 错误处理（E-01~E-08）→ Task 4 全覆盖（E-01 内存兜底 / E-02 复用 submit 逻辑 / E-03 POI 名 Task 5 同步检查 / E-04 AUTO_SHOW_DELAY 超时降级 / E-05 防抖 / E-06 prefers-reduced-motion / E-07 高亮脉冲 / E-08 Esc 不影响 input）；§7 验收 → Task 1 已改 TASKS 文档。 | 无缺口 |
| **无 Placeholder 扫描**：有没有写 "TODO / 适当错误处理 / 类似 Task N / Write tests for above" 这类占位符？ | ✅ 无。所有步骤都是实打实地给了代码片段 / exact 正则 / 具体的手动测试步骤。Task 2 的 Shortcut-card 5 张都是完整代码，没有留空；Task 4 的 IIFE 没有写 "TODO: 加其他分支"，都是 fail-soft default 兜底。 | 无占位符 |
| **类型/名称一致性**：后面任务用的 class/id/函数名 和前面任务定义的 100% 一致？ | ✅ 是。Task 2 定义的 `#welcome-overlay / .welcome-card-topbar / #help-btn / sc-accent~sc-wide` → Task 3 的 CSS 全部复用 → Task 4 的 JS 全部用相同的 querySelector。Task 4 调用的 `submitNaturalLanguageQuery` 和 `showPoiSidebar` 是现有 app.js 的公开函数，Task 4 有 typeof 检查兜底，即使不存在也不报错。 | 无不一致 |

---

Plan complete and saved to `docs/superpowers/plans/2026-08-11-cold-start-welcome-implementation.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. 6 个 Task 6 个 subagent，每个 2-5 分钟，每个任务完成后我 gate review 再进下一个，出错率最低。

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints. 你如果在这一个会话里盯着做完，就走这个，我一批一批跑，每批完 checkpoint review。

Which approach?
