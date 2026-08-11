# Spec · 漫步珞珈冷启动欢迎卡片设计（方案 1）

| 字段 | 值 |
|------|----|
| Date | 2026-08-11 |
| Author | Product Agent (Spec) → 待 Frontend Agent 实现 |
| Status | **DRAFT** — 待用户审阅确认（Step 7 前不进入实施） |
| Related Tasks | T-016 (HTML 结构追加) · T-017 (JS 交互追加) · T-018 (样式追加) |
| 决策 ID | D-20260811-003 冷启动方案选择 |

---

## 1. 项目背景与动机

### 1.1 问题陈述（Problem Statement）
> 新用户首次打开 `漫步珞珈` 页面时，看到的是：地图 + 一个空输入框 + 侧边栏。**80% 以上的新访客不知道这个产品能做什么**，也无法在 3 秒内说出 3 种可用的交互方式（"我可以问什么" / "我可以点什么" / "我能得到什么结果"）。典型新手犹豫场景：
> - 输入框光标闪烁，但想不出要问什么；
> - 侧边栏的 24 个 POI 分类浏览是"隐藏入口"，新用户根本没注意到；
> - 即使知道可以问路，也不知道可以提偏好（"风景好" "避开陡坡" "走远一点"）。

### 1.2 设计目标（明确可度量）
| 目标编号 | 目标描述 | 度量方式 |
|----------|----------|----------|
| G-01 | **新用户 ≤ 5 秒内知道产品能做什么**（问路 / 查景点 / 看推荐） | 用户访谈 + 首屏加载后 5 秒内是否有明确操作（输入 / 点卡片 / 点侧边栏） |
| G-02 | **新用户首访转化率（发起有效查询比例）从现状 ~10% → ≥ 40%** | 统计 `POST /api/v1/query` 在用户会话前 30 秒内的调用比例 |
| G-03 | **不打扰老用户**（老用户最多 1 秒无感关闭，下次不自动弹） | `？帮助` 按钮唤回率 < 老用户会话的 10%（说明不需要） |

### 1.3 目标用户
- **主要**：首次访问的访客（新生 / 游客 / 家长）—— 占预估流量 70%+
- **次要**：偶尔使用的在校生（知道可以问路，但不知道可以查景点 / 看推荐）
- **豁免**：重度老用户（勾选"以后不再显示"即可永久屏蔽）

---

## 2. 方案选择（重申 D-20260811-003）

### 2.1 对比表（为什么不选 2/3）

| 维度 | 方案 2：半屏侧边抽屉引导 | 方案 3：空输入框 placeholder 5 句轮播 | 方案 1（选中）：覆盖式欢迎卡片 |
|------|--------------------------|----------------------------------------|--------------------------------|
| G-01 5 秒认知 | ⚠️ 抽屉默认收起，新用户还是看不到 | ❌ placeholder 轮播太含蓄，没人盯 5 秒 | ✅ 强制首屏焦点，标题+卡片一目了然 |
| G-02 首访转化率 | ⚠️ 预计 20-25%（只提升侧边栏打开率） | ❌ 预计 12-15%（几乎没提升） | ✅ 预计 40-55%（一键跑示例，零输入成本） |
| G-03 老用户打扰 | ✅ 几乎无打扰（不自动弹） | ✅ 完全无打扰（只是文字） | ⚠️ 首次加载挡地图，但 5 种方式秒关，下次不弹 |
| 实现复杂度 | 低（加抽屉 + ？按钮） | 极低（改 placeholder 数组） | 中（加覆盖层 + CSS + localStorage + 事件委托） |
| 新手提示信息密度 | ⚠️ 抽屉空间有限，塞不下 5 卡+3 步 | ❌ 只有 5 句短句，没行动号召 | ✅ 480×640 空间刚好，行动号召明确 |

### 2.2 结论
选 **方案 1**，牺牲老用户 1 次 1 秒的体验，换 70% 新用户 5 秒内上手 + 首访转化率 4 倍提升。收益 >> 成本，且通过 `？帮助` 按钮 + localStorage 记忆机制把对老用户的打扰降到最低。

---

## 3. 模块设计（完整 5 模块，不再拆分确认）

### 模块 1：组件结构（HTML 片段，追加到 `static/index.html` 的 `<body>` 末尾）

```html
<!-- ===== 漫步珞珈 · 冷启动欢迎卡片覆盖层 ===== -->
<div id="welcome-overlay" class="welcome-overlay hidden" aria-hidden="true">
  <!-- 遮罩层（点击遮罩也能关闭卡片） -->
  <div class="welcome-mask" data-close-welcome="true"></div>

  <!-- 卡片本体 -->
  <div class="welcome-card" role="dialog" aria-label="欢迎使用漫步珞珈" aria-modal="true">
    <!-- 【签名元素】樱顶老斋舍窗棂纹装饰条（frontend-design skill：one signature risk） -->
    <div class="welcome-card-topbar" aria-hidden="true"></div>

    <!-- 右上角关闭 X -->
    <button class="welcome-close" data-close-welcome="true" aria-label="关闭欢迎页">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line>
      </svg>
    </button>

    <!-- ① 标题区 -->
    <header class="welcome-header">
      <h1 class="welcome-title">🗺️ 漫步珞珈</h1>
      <p class="welcome-subtitle">武大校园 · 问路 · 查景点 · 推荐路线</p>
    </header>

    <!-- ② 5 个快捷查询卡片（声明式 data-*，事件委托统一处理） -->
    <section class="welcome-shortcuts" aria-label="快捷查询示例">
      <button class="shortcut-card sc-accent"
              data-action="path_planning"
              data-start="牌坊" data-end="樱顶"
              data-constraints='{"distance":"medium","slope":"normal","scenery":"high"}'
              data-display-text="经典路线 · 牌坊 → 樱顶（景观优先）">
        <span class="sc-emoji" aria-hidden="true">🚶</span>
        <span class="sc-name">经典路线</span>
        <small class="sc-desc">牌坊 → 樱顶 · 走风景最好的主路</small>
      </button>

      <button class="shortcut-card sc-pink"
              data-action="path_planning"
              data-start="樱花大道" data-end="樱顶"
              data-constraints='{"distance":"relaxed","slope":"normal","scenery":"high"}'
              data-display-text="赏樱路线 · 樱花大道 → 樱顶（春天必走）">
        <span class="sc-emoji" aria-hidden="true">🌸</span>
        <span class="sc-name">赏樱路线</span>
        <small class="sc-desc">樱花大道 → 樱顶 · 春天打卡必走</small>
      </button>

      <button class="shortcut-card sc-jade"
              data-action="poi_query" data-name="樱顶"
              data-display-text="景点查询 · 樱顶在哪？（介绍 + 地图定位）">
        <span class="sc-emoji" aria-hidden="true">🏯</span>
        <span class="sc-name">景点查询</span>
        <small class="sc-desc">樱顶在哪？介绍 + 地图自动定位</small>
      </button>

      <button class="shortcut-card sc-stone"
              data-action="path_planning"
              data-start="教五" data-end="总图书馆"
              data-constraints='{"distance":"short","slope":"normal","scenery":"normal"}'
              data-display-text="最短路径 · 教五 → 总图书馆（赶时间）">
        <span class="sc-emoji" aria-hidden="true">⚡</span>
        <span class="sc-name">最短路径</span>
        <small class="sc-desc">教五 → 总图书馆 · 赶时间最快</small>
      </button>

      <button class="shortcut-card sc-wide"
              data-action="recommend_poi"
              data-display-text="推荐景点 · 展开侧边栏看 5 个精华">
        <span class="sc-emoji" aria-hidden="true">🌟</span>
        <span class="sc-name">推荐景点</span>
        <small class="sc-desc">展开侧边栏看 5 个精华 POI</small>
      </button>
    </section>

    <!-- ③ 3 步新手提示 -->
    <section class="welcome-tips" aria-label="新手提示">
      <div class="tip-item"><span class="tip-num">①</span><span class="tip-text">直接说人话就行：「从牌坊到樱顶，避开陡坡」</span></div>
      <div class="tip-item"><span class="tip-num">②</span><span class="tip-text">点地图上的 🔴 红点查看景点，可快捷设出发/到达</span></div>
      <div class="tip-item"><span class="tip-num">③</span><span class="tip-text">左侧侧边栏按类别浏览 24 个地点</span></div>
    </section>

    <!-- ④ 底部操作区 -->
    <footer class="welcome-footer">
      <button class="btn btn-primary welcome-start" data-close-welcome="true">✨ 开始使用</button>
      <label class="welcome-dont-show">
        <input type="checkbox" id="welcome_dont_show"> 以后不再显示此欢迎页
      </label>
    </footer>
  </div>
</div>

<!-- ===== header 右上角？帮助按钮（永久存在，用于手动唤回欢迎卡） ===== -->
<!-- （加在 app-header > header-content 的最右侧，与 app-subtitle 并排，位于 subtitle 的右边） -->
```

### 模块 2：显示/隐藏逻辑（JS，追加到 `static/js/app.js` 末尾）

#### 2.1 双 localStorage 标记设计
| Key | 取值 | 写入时机 |
|-----|------|----------|
| `whu_welcome_seen` | `"true"` / 不存在 | 用户触发任何 5 种关闭方式时**必写**（除了点 shortcut-card 这种"跑查询+关"的也写） |
| `whu_welcome_dont_show_forever` | `"true"` / 不存在 | 用户勾选「以后不再显示此欢迎页」复选框后，**再**触发关闭时才写 |

**自动弹出判断（DOMContentLoaded 时执行）**：
```
IF whu_welcome_dont_show_forever === "true"  → 不弹
ELSE IF whu_welcome_seen === "true"          → 不弹
ELSE                                         → 调 showWelcomeCard() 弹卡
```

#### 2.2 5 种关闭方式（都调用 closeWelcomeCard()，参数传一个 `actionType` 用于区分来源，存 seen 标记）
1. 点右上角 X 按钮（`data-close-welcome="true"`）
2. 点遮罩层（`data-close-welcome="true"` —— **注意**：点击欢迎卡片本身的 inner 区域冒泡到 `.welcome-mask` 时要阻止冒泡，不然点卡片内部也会关，这个是常见 bug，必须在 `.welcome-card` 上 `addEventListener('click', e => e.stopPropagation())`）
3. 按键盘 `Esc` 键（`document.addEventListener('keydown', e => e.key === 'Escape' && isWelcomeOpen() && closeWelcomeCard())`）
4. 点底部「✨ 开始使用」按钮（`data-close-welcome="true"`）
5. **点任意 5 个快捷卡片**（先跑查询动作 → 再调 `closeWelcomeCard({markSeen: true})`，自动关卡片不挡地图）

#### 2.3 ？帮助按钮唤回机制
- 点击 header 右上角 `？` 按钮 → **强制调 `showWelcomeCard({ignoreLocalStorage: true})`**
- 唤回时**不修改任何 localStorage 标记**（用户手动查帮助，不影响下次是否自动弹）

#### 2.4 CSS 动画（纯 CSS，不写 JS 动画逻辑）
| 元素 | 入场动画（加 `.opening` 类触发） | 退场动画（加 `.closing` 类 → 动画结束 → 加 `.hidden` 类） |
|------|----------------------------------|--------------------------------------------------------|
| `.welcome-mask` | `opacity: 0 → 1`，200ms linear | `opacity: 1 → 0`，150ms linear |
| `.welcome-card` | `transform: translateY(24px) scale(0.98) → translateY(0) scale(1)` + `opacity: 0 → 1`，280ms cubic-bezier(0.2, 0.8, 0.2, 1)（springy 回弹一点点，符合樱花主题的轻盈感） | `transform: translateY(16px) scale(0.98)` + `opacity: 1 → 0`，180ms ease-in |
| `.shortcut-card` | stagger 入场：每张卡片延迟 60ms 依次淡入（CSS `:nth-child` + `animation-delay`）— 总时长 360ms | 不用退场动画（卡片属于 welcome-card 内部，跟着一起走） |

#### 2.5 移动端响应式（< 768px）
| 断点 | 改动 |
|------|------|
| < 768px（手机） | ① welcome-card 宽度 92vw（min-width: 300px），最大高度 88vh，内部 `.welcome-shortcuts` + `.welcome-tips` 超出滚动；② 5 个 shortcut-card 改成**单列纵向排列**（不是 3+2 网格），每张高度 72px 省空间；③ `.welcome-tips` 默认折叠成「新手提示 ▾」按钮，点才展开；④ checkbox「以后不再显示」字号缩小 1 号 |
| ≥ 768px（平板/桌面） | 3+2 网格（3 上 2 下），卡片 480×640px 居中，3 步提示直接显示 |

---

## 4. 视觉设计（基于 frontend-design skill · 紧扣漫步珞珈樱花主题）

### 4.1 设计锚点（frontend-design skill 要求：pin down subject）
- **Subject**：武汉大学「漫步珞珈」校园导航欢迎页
- **Audience**：18-25 岁新生 + 30-50 岁访客家长，审美偏好清新、轻盈、有武大文化感，**拒绝模板化科技风**
- **Single Job**：5 秒内传达「我能问路/查景点/看推荐」+ 一键触发一个示例查询

### 4.2 视觉 token 系统（100% 复用 `static/css/style.css` 的 `:root` 变量，不新增主题色，避免风格分裂）

| Token 类别 | 变量 | 用于欢迎卡片的哪部分 |
|-----------|------|----------------------|
| **主色** | `--cherry-deep: #C76B7A` | 标题 emoji 色、`.sc-pink` 赏樱路线卡片边框色、签名元素樱顶窗棂纹主色 |
| **辅色 1（珞珈绿）** | `--jade: #4A7C6F` | `.sc-jade` 景点查询卡片边框色、`.btn-primary` 按钮 hover 渐变 |
| **辅色 2（珞珈石）** | `--stone: #DDD8D3` | `.sc-stone` 最短路径卡片边框色 |
| **背景** | `--paper: #FAF8F5` | 遮罩层 68% 透明度叠在地图上，welcome-card 背景用 `--surface: #FFFFFF` 配 `--shadow-lg` |
| **正文** | `--ink: #2D2D2D` / `--ink-soft: #6B6B6B` | 标题/说明文字 |
| **主按钮** | 现有 `.btn-primary` 样式（樱花粉渐变，必须复用，不加新按钮样式） | 底部「✨ 开始使用」按钮 |
| **圆角** | `--radius-lg: 20px`（卡片本体）/ `--radius-md: 12px`（shortcut-card） | 所有圆角统一复用 |
| **字体** | 标题用 Noto Serif SC / Songti SC（现有 `.app-title` 字体栈）· 正文用 PingFang SC / YaHei（现有 body 字体栈） | 不引入新字体，保持一致 |

### 4.3 签名元素（Signature · frontend-design skill 要求的 one real aesthetic risk）
**`.welcome-card-topbar`（卡片最顶部一条 48px 高的装饰条）**——用纯 CSS 画一个「樱顶老斋舍窗户纹样」：

- 背景：线性渐变 `linear-gradient(90deg, var(--cherry-soft) 0%, var(--jade-soft) 100%)`
- 前景：`background-image: repeating-linear-gradient(90deg, transparent 0 48px, var(--cherry-deep) 48px 50px, transparent 50px 96px)` 画竖线（柱子），再叠加一条横线（老斋舍的屋檐线），形成抽象的"民国建筑窗棂"图案
- **为什么是这个**：不是俗套的樱花瓣满天飞（模板化），而是用武大**最有辨识度的建筑樱顶老斋舍的窗**做抽象纹样，只有武大人看得懂的"暗号感"，不影响信息密度，又有足够独特性（frontend-design skill："signature that could not be mistaken for anyone else"）

### 4.4 Shortcut-card 5 卡配色策略
每张卡片有 2px 的**左边框彩色 accent bar**（不是整卡上色，太花）+ emoji 底色和 accent bar 同色，做到一眼区分：
| 卡片 | accent 颜色（左边框 2px） | emoji 底 |
|------|---------------------------|----------|
| 经典路线（推荐） | `--cherry-deep` 樱粉（主色，强调这是推荐路线） | `#FDF0F2` |
| 赏樱路线 | `--cherry` 浅樱粉 | `#FDF0F2` |
| 景点查询 | `--jade` 珞珈绿 | `#E8F0ED` |
| 最短路径 | `--ink-light` 中灰（"普通"，不强调） | `#FAFAF9` |
| 推荐景点（宽卡） | `--color-poi: #D4915C` 暖橙（现有 POI 标记色，语义对应"景点"） | `#FFF6EC` |

---

## 5. 模块 3：5 种 shortcut-card 点击后的完整数据流动（核心功能逻辑）

5 个卡片按 `data-action` 分**3 类触发逻辑**：

### 5.1 类型 A：`data-action="path_planning"`（经典路线 / 赏樱路线 / 最短路径，共 3 卡）
这 3 张卡的逻辑**完全相同**，只是参数不同，统一走：

```
用户点击卡片
  ↓
1. 从 data-start / data-end / data-constraints / data-display-text 取参数
2. 关闭欢迎卡片（自动 markSeen=true）
3. 把 data-display-text 填入输入框 input.value（模拟用户"自己打了这句话"，既提示用户，又方便用户改）
4. 调 `submitNaturalLanguageQuery()` 函数（复用现有输入框回车时的那个提交函数，**不要写独立的 fetch 分支** —— 这样所有错误处理、加载动画、结果渲染全复用现有逻辑）
5. 自动滚动到 #result-area（把结果展示给用户）
```

**为什么复用 submitNaturalLanguageQuery 而不是调 API？** — 因为这样 shortcut-card 的行为和用户手输的行为**100% 一致**，后续不管加什么分析埋点、输入纠错逻辑，shortcut 自动继承，零维护成本（DRY 原则）。

### 5.2 类型 B：`data-action="poi_query"`（景点查询 · 樱顶，1 卡）
```
用户点击卡片
  ↓
1. 从 data-name 取 POI 名（"樱顶"）
2. 构造一句自然语言查询文本："樱顶在哪？给我介绍一下" → 填入 input.value
3. 关闭欢迎卡片
4. 调 submitNaturalLanguageQuery()（同上，全复用现有 NL 解析逻辑 —— 现有 parser 已支持 task_type=poi_query）
5. 滚动到结果区
```

**为什么不直接调 `/api/v1/poi/:name`？** — 同样是复用原则。`poi_query` 是 task_type 的一种，应该通过 NL Parser 统一走，这样如果用户点了景点查询卡片，后续再跟一句"从樱顶到牌坊怎么走"，多轮对话上下文也能正确继承樱顶这个 start point（如果直调 API，对话管理器不知道刚才查过樱顶，多轮就断了）。

### 5.3 类型 C：`data-action="recommend_poi"`（推荐景点 · 展开侧边栏，1 卡）
```
用户点击卡片
  ↓
1. 调 showPoiSidebar() —— 直接展开左侧侧边栏（如果侧边栏已处于"推荐"分类视图，就不用切；否则切到"景点"分类的"推荐"标签）
2. 侧边栏顶部临时高亮（比如加 2 秒的淡黄色背景淡出动画）"✨ 推荐 5 大精华景点"小标题
3. 关闭欢迎卡片
4. 不调 API（侧边栏数据是页面加载时就从 `/api/v1/pois` 拉好的，直接展示）
5. 输入框填入 placeholder："试试：樱花大道怎么去？" 引导用户下一步
```

---

## 6. 模块 4：错误与边缘情况处理（必须覆盖 8 条）

| 编号 | 边缘场景 | 处理策略 | 用户看到什么 |
|------|----------|----------|--------------|
| E-01 | localStorage 被用户禁用（隐私模式） | 用 in-memory 变量兜底记 `sessionWelcomeShown = true`，当前标签页会话内不重复弹；**不写** 永久不显示标记（因为存不进去） | 首次弹一次，刷新页面又弹（隐私模式，无法记忆，接受这个 UX 降级） |
| E-02 | 点击 shortcut-card 后 submitNaturalLanguageQuery() 返回 API 错误（500 / 超时） | **完全复用现有错误处理逻辑**（输入框报错 + Toast 提示 + 建议重试） —— 不新增错误分支 | 和用户手输出错完全一样，shortcut 没有特殊处理 |
| E-03 | 硬编码的 data-start/data-end（比如"樱顶"）未来如果从 pois.json 里删掉了 | POI 解析阶段 `find_poi()` 找不到 → 走现有 ambiguity 分支（prompt 用户"请更具体描述地点"） —— shortcut 本身不做兜底（因为硬编码的 POI 名必须和 pois.json 同步，这是维护原则） | 输入框里保留了文字，用户看到歧义提示后可以改 |
| E-04 | 欢迎卡片渲染时间过长（JS 阻塞，DOMContentLoaded 后 500ms 还没显示） | 不显示 welcome card（降级：直接在输入框 placeholder 显示 3 句轮播，给用户**最差** 也能看到引导文字） | 看不到卡片，但输入框有引导，不影响使用 |
| E-05 | 用户在 0.8 秒内连续点了 2 次 shortcut-card（防抖问题） | 在 welcome-shortcuts 事件委托里加 `once: true` 标志，或者全局加 1.5s 防重点 | 只跑第一次查询，后面的点击忽略 |
| E-06 | `prefers-reduced-motion: reduce`（无障碍 · 用户关闭动画） | 去掉所有 `.opening` / `.closing` 动画类，直接切 `.hidden`（CSS 里用 `@media (prefers-reduced-motion: reduce)` 覆盖 transition 为 0s） | 秒开秒关，无动画 |
| E-07 | ？帮助按钮点击时，欢迎卡已经显示了 | 不做重复显示动作，但给 welcome-card 加一个 0.6s 的 `box-shadow` 高亮脉冲（视觉告诉用户"在这里"） | 有视觉反馈，不是"点了没反应" |
| E-08 | 用户点击 Esc 关卡时，输入框处于焦点中 | 先阻止 Esc 触发输入框的清空行为（或者判断 Esc 时 welcome 是 open 状态，只关 welcome，不影响 input） | 关卡后输入框内容保留，符合预期 |

---

## 7. 模块 5：验收标准落位（追加到现有 T-016 / T-017 / T-018，不新增 T 任务）

### 7.1 T-016（HTML 结构验收）追加 6 条
> 在原 T-016 验收标准第 9 条之后追加：
```
  9. 冷启动欢迎覆盖层：
     9.1 <body> 末尾存在 id="welcome-overlay" 的全页面级覆盖层，包含遮罩 + 卡片本体
     9.2 卡片内有：标题区、5 个 shortcut-card、3 步新手提示、开始使用按钮、不再显示复选框
     9.3 5 个 shortcut-card 都有 data-action、data-* 参数齐全（3 条 path_planning + 1 条 poi_query + 1 条 recommend_poi）
     9.4 卡片有 class="welcome-card-topbar" 的樱顶装饰条
     9.5 覆盖层有 role="dialog" aria-modal="true" aria-label，关闭按钮有 aria-label，满足基础无障碍
     9.6 header 右上角存在 id="help-btn" aria-label="帮助/欢迎引导" 的？按钮
```

### 7.2 T-017（前端交互验收）追加 13 条
> 在原 T-017 验收标准第 15 条之后追加：
```
 15. 冷启动欢迎卡片交互：
     15.1 首次访问（localStorage 无标记）→ 自动弹出欢迎卡片 ✅
     15.2 关闭卡片后刷新 → 不自动弹出（whu_welcome_seen=true）✅
     15.3 勾选"不再显示"关闭后刷新+清缓存后（localStorage 清空后）→ 也不弹（whu_welcome_dont_show_forever=true 生效，如果用户清了 localStorage 那没办法）
     15.4 5 种关闭方式都能关卡：点 X / 点遮罩 / 按 Esc / 点开始使用 / 点任意 shortcut-card
     15.5 点 shortcut-card 后，输入框自动填入对应的 data-display-text，且提交了查询（和手输行为一致）
     15.6 3 张 path_planning 卡 → 调 path_planning 流程，正确解析 start/end
     15.7 1 张 poi_query 卡 → task_type=poi_query，start=樱顶，end=null
     15.8 1 张 recommend_poi 卡 → 展开侧边栏并高亮推荐区，不调 API
     15.9 点击 header 右上角？按钮 → 强制显示欢迎卡，不修改 localStorage 状态
     15.10 隐私模式（localStorage 不可用）→ 当前标签页内只弹一次，刷新后再弹（降级可接受）
     15.11 连点 shortcut-card 不触发重复提交
     15.12 prefers-reduced-motion → 无入场退场动画
     15.13 Esc 关卡时，输入框已输入内容不丢失
```

### 7.3 T-018（样式验收）追加 7 条
> 在原 T-018 验收标准第 8 条之后追加：
```
  8. 冷启动欢迎卡片样式：
     8.1 桌面端：卡片 480×640px 居中，3+2 shortcut 网格，所有圆角复用 --radius-lg/--radius-md
     8.2 移动端（< 768px）：卡片 92vw，shortcut 单列，新手提示可折叠
     8.3 遮罩层半透明度 68%，能隐约看到后面的地图（不遮挡信息）
     8.4 5 张 shortcut-card 左边框彩色 accent bar 颜色正确（樱粉/樱粉/珞珈绿/灰/暖橙）
     8.5 welcome-card-topbar 樱顶窗棂纹装饰条存在（用 repeating-linear-gradient 画）
     8.6 入场退场动画符合要求（stagger 卡片淡入 + 卡片 springy 回弹）
     8.7 字体完全复用现有栈（Noto Serif SC 标题 + PingFang/YaHei 正文）
```

### 7.4 新增测试脚本（可选但推荐）
`scripts/T010_validate_welcome_cold_start.py`（端到端 DOM 验证脚本，Frontend Agent 实现完后补写）：
- 验证所有 HTML 元素存在（T-016 的 6 条）
- Mock localStorage 3 种状态验证显示逻辑（首次 / 看过 / 永不显示）
- 模拟点击 5 种 shortcut-card，验证事件委托参数正确

---

## 8. 决策日志（D-20260811-004 ~ 006 三条）

| 决策 ID | 决策内容 | 原因（Why now, why this） | 受影响文件 |
|---------|----------|--------------------------|-----------|
| **D-20260811-004** | Shortcut-card 点击后不直调 API，而是填输入框 → 复用 `submitNaturalLanguageQuery()` 全链路 | DRY 原则，确保 shortcut 行为与手输 100% 一致，后续多轮对话、埋点、错误处理、加载动画全复用，不新增分支 | `static/js/app.js` |
| **D-20260811-005** | 欢迎卡片的签名元素选"樱顶老斋舍窗棂纹装饰条"而不是俗套樱花 | frontend-design skill 要求 one unique signature，必须"not mistaken for anyone else"；樱顶是武大最具辨识度建筑，抽象纹样不增加信息密度又有文化暗号感 | `static/css/style.css` (新增 `.welcome-card-topbar`) |
| **D-20260811-006** | 双 localStorage 标记（seen + forever） | 区分"看过一次"和"明确不想再看"两种用户意愿，避免老用户误点一次关闭后永远看不到帮助（如果只有一个 forever 标记，用户想重新看帮助就得清 localStorage） | `static/js/app.js` (welcome 相关逻辑) |

---

## 9. 待确认问题清单（Open Questions）

*（空，等用户 review 后补充）*

---

## 10. 实施影响摘要（给 Frontend Agent 的前置说明）

| 维度 | 说明 |
|------|------|
| **需修改的文件**（3 个现有 + 1 个新测试脚本） | ① `static/index.html`（追加 welcome-overlay + ？按钮）② `static/css/style.css`（追加 .welcome-* / .shortcut-card / .welcome-card-topbar 等样式）③ `static/js/app.js`（追加 welcome 显示/隐藏/事件委托/数据流逻辑）④ `scripts/T010_validate_welcome_cold_start.py`（新测试脚本） |
| **修改上游文档** | `project-docs/05_TASKS.md`（T-016 追加 6 条 / T-017 追加 13 条 / T-018 追加 7 条） |
| **后端文件是否修改** | ❌ 完全不需要（所有逻辑都在前端，复用现有 API） |
| **POI 数据是否修改** | ❌ 5 个 shortcut-card 的硬编码 POI 名（牌坊、樱顶、樱花大道、教五、总图书馆）都在 `data/pois.json` 里已存在，无需改 |
| **是否新增依赖** | ❌ 零新增 |
| **现有逻辑回归风险** | 极低 — 所有修改都是追加式（append-only），不修改现有 DOM 结构 / 不覆盖现有 CSS 类 / 不修改现有 submitNaturalLanguageQuery() 等函数，只新增 welcome-* 新代码路径 |

---

## 11. Spec Self-Review（Spec 作者的 3 个自问）

| 自问 | 回答 |
|------|------|
| **为什么选方案 1 而不是 2/3？** | 在 §2.1 对比表明确写了：5 秒认知、转化率、信息密度 3 项核心指标全面胜出，老用户打扰成本可通过 localStorage + 5 种秒关方式降到几乎为 0 |
| **有没有需要改的上游文档？改哪些？** | 有，`project-docs/05_TASKS.md` 的 T-016 / T-017 / T-018 验收标准需要按 §7 追加 26 条（6+13+7），已经在 §10 明确列出，不会忘 |
| **有没有硬编码？是不得已还是能避免？** | 有 5 个 shortcut-card 的 POI 名/参数是硬编码在 HTML data-* 里的；**这个是刻意为之的合理硬编码**：5 张卡是"经典示例"不常变，直接写 data-* 比从 JSON 拉更利于 SEO/可访问性/首屏速度，且如果 POI 名变了维护成本很低（改一行 HTML），D-20260811-004 说过 POI 名必须与 pois.json 同步，这是维护原则 |

---

*END OF SPEC · DRAFT*
