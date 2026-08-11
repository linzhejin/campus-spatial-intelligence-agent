# Task 2 Brief · 冷启动欢迎卡片

> **任务位置**：实施计划 **第 2 个任务**（前置条件：Task 1 DONE，`static/index.html` 存在）
> **目标**：在 `static/index.html` 中**追加两个 DOM 节点**（不修改任何原有 DOM！100% append-only）：① 在 header 的 `.header-content` 最右侧追加 `#help-btn` 圆形 ？按钮；② 在 `</body>` 前 1 行追加完整的 `#welcome-overlay` 欢迎卡片覆盖层结构（含遮罩 + 卡片 + 5 shortcut-card + 3 步提示 + 底部操作区）。
> **修改范围**：仅修改 **1 个文件**（`static/index.html`），Edit 必须基于 Read 的最新内容做 old_string/new_string，不允许硬写行号。

---

## 全局约束（必须严格遵守）

1. **Append-only 原则**：**绝对不能**删除 / 修改 `static/index.html` 现有任何 DOM 节点、属性、类名、id。只能在两个插入点**追加**新 DOM。
2. **先读后改原则**：第一步必须 Read 完整 `static/index.html`，用文本匹配找到两个插入点的 exact old_string，再做 Edit。禁止硬猜行号，禁止基于假设的 old_string 做 Edit（会导致 Edit tool FAIL）。
3. **精确 DOM ID/Class 命名原则**：所有新增 DOM 的 id/class/data-* 属性必须和下文 Exact 代码片段 **100% 一致**（大小写敏感，连 `-` `_` 都不能错）。因为后续 Task 3（CSS）和 Task 4（JS）都是按这些 exact 名字写的，错一个字符就全断。
4. **POI 名同步原则**：5 个 shortcut-card 的 `data-start`/`data-end`/`data-name` 硬编码值必须是下文 Exact 片段里的值（牌坊、樱顶、樱花大道、教五、总图书馆）——这 5 个已确认存在于 `data/pois.json`。**严禁擅自改任何 POI 名硬编码**。
5. **无障碍原则**：所有按钮的 aria-label 属性值严格与 Exact 代码一致，role=dialog aria-modal 不能漏。

---

## 插入点 1：header 的 ？帮助按钮

### 操作说明
1. 先 Read `static/index.html`，定位 `<div class="app-header"><div class="header-content">...</div></div>` 这部分 DOM。
2. `.header-content` 的**现有最后一个子节点**通常是 `<span class="app-subtitle">...</span>`（如果前几轮有改动，以实际读到的为准）。
3. **在现有最后一个子节点之后、`</div>`（header-content 的结束标签）之前**，追加 `<button id="help-btn" ...>`。
4. old_string 应该是：现有最后一个子节点（例如 `<span class="app-subtitle">...</span>`）+ 换行 + `</div>`（header-content 的结束）。new_string = old_string 在中间插入 ？按钮 DOM。

### Exact 要追加的 ？按钮代码（一字不能改）
```html
      <!-- ？帮助按钮（手动唤回欢迎卡片） -->
      <button id="help-btn" class="help-btn" aria-label="帮助/欢迎引导" title="查看帮助引导">?</button>
```

---

## 插入点 2：</body> 前的完整欢迎卡片覆盖层

### 操作说明
1. 在同一份 `static/index.html` 中，找到最后一个 `</body>` 结束标签（注意可能是 `<body>` 配套的，不会有多个）。
2. **在 `</body>` 的前一行**（即 body 内所有现有 DOM 节点之后）追加完整覆盖层代码。
3. old_string = `</body>` 或 现有最后一个节点 + `</body>`，new_string = 现有内容 + 完整 welcome-overlay + `</body>`。

### Exact 要追加的完整覆盖层代码（一字不能改！包括换行、缩进、SVG 路径、data-* 值）
```html
  <!-- ===== 漫步珞珈 · 冷启动欢迎卡片覆盖层 ===== -->
  <div id="welcome-overlay" class="welcome-overlay hidden" aria-hidden="true">
    <!-- 遮罩层（点击遮罩也能关闭卡片） -->
    <div class="welcome-mask" data-close-welcome="true"></div>

    <!-- 卡片本体（防止点击内容冒泡到遮罩误关，JS 里会加 stopPropagation） -->
    <div class="welcome-card" role="dialog" aria-label="欢迎使用漫步珞珈" aria-modal="true">
      <!-- 【签名元素】樱顶老斋舍窗棂纹装饰条（纯 CSS 画，无需图片） -->
      <div class="welcome-card-topbar" aria-hidden="true"></div>

      <!-- 右上角关闭 X 按钮 -->
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

        <!-- 第 5 张：宽卡推荐景点（展开侧边栏，不调 API） -->
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

      <!-- ③ 3 步新手提示（桌面端默认展开，移动端默认折叠，CSS + <details> 原生交互） -->
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

## Step-by-Step 操作步骤

- [ ] **Step 2.1: Read 完整 `static/index.html`**
  - 必须先全量 Read 到最新内容，才能做 Edit（Edit tool 要求 old_string 必须是文件里的精确最新内容）。

- [ ] **Step 2.2: 第一个 Edit · 追加 ？按钮到 header-content 末尾**
  - 用实际读到的 header-content 结束部分构造 old_string：必须包含现有的最后一个节点 + `</div>` 结束标签。
  - new_string = old_string 在最后一个节点之后、`</div>` 之前插入 Exact ？按钮代码。
  - 注意缩进对齐：按钮必须和前面的 app-subtitle 保持同级缩进（通常是 6 个空格，或与同级元素一致）。

- [ ] **Step 2.3: 第二个 Edit · 追加完整 welcome-overlay 到 </body> 前**
  - 用实际读到的 `</body>` 前的内容构造 old_string：如果 body 内最后是 `<script src="..."></script>`，old_string 就包含最后一个 script + `</body>`。
  - new_string = old_string 在 `</body>` 之前插入 Exact 覆盖层代码（注意缩进：整个覆盖层的第一行注释是 2 空格缩进，和 body 内其他节点同级）。

- [ ] **Step 2.4: 运行以下 Python DOM 存在性自检（必跑，确保 id/class 全对）**

  Run:
  ```powershell
  cd "c:\Users\HUAWEI\Desktop\项目\campus-spatial-intelligence-agent"
  python -c "from html.parser import HTMLParser
  class P(HTMLParser):
      def __init__(self):
          super().__init__()
          self.found = {'welcome-overlay':False,'help-btn':False,'welcome-mask':False,'welcome-card':False,'welcome-card-topbar':False,'welcome-shortcuts':False,'welcome-tips':False,'welcome-footer':False,'welcome_dont_show':False}
          self.shortcuts = []
          self.aria_ok = {'role_dialog':False, 'aria_modal':False}
      def handle_starttag(self, tag, attrs):
          a = dict(attrs)
          id_ = a.get('id',''); cls = a.get('class','').split()
          if id_ == 'welcome-overlay': self.found['welcome-overlay']=True
          if id_ == 'help-btn': self.found['help-btn']=True
          if 'welcome-mask' in cls: self.found['welcome-mask']=True
          if 'welcome-card' in cls:
              self.found['welcome-card']=True
              if a.get('role')=='dialog': self.aria_ok['role_dialog']=True
              if a.get('aria-modal')=='true': self.aria_ok['aria_modal']=True
          if 'welcome-card-topbar' in cls: self.found['welcome-card-topbar']=True
          if 'welcome-shortcuts' in cls: self.found['welcome-shortcuts']=True
          if 'welcome-tips' in cls: self.found['welcome-tips']=True
          if 'welcome-footer' in cls: self.found['welcome-footer']=True
          if id_ == 'welcome_dont_show': self.found['welcome_dont_show']=True
          if 'shortcut-card' in cls:
              self.shortcuts.append({
                  'action': a.get('data-action'),
                  'start': a.get('data-start'),
                  'end': a.get('data-end'),
                  'name': a.get('data-name'),
                  'display': bool(a.get('data-display-text'))
              })
  p = P()
  p.feed(open('static/index.html',encoding='utf-8').read())
  # 1) id/class 存在性 9 项
  print('--- (1/3) DOM id/class 存在性 ---')
  ok = True
  for k,v in p.found.items():
      status = '✅' if v else '❌'
      print(f'  {status} {k}: {v}')
      if not v: ok = False
  # 2) aria 2 项
  print('--- (2/3) Aria attributes ---')
  for k,v in p.aria_ok.items():
      status = '✅' if v else '❌'
      print(f'  {status} {k}: {v}')
      if not v: ok = False
  # 3) shortcut-card 5 张参数统计
  print('--- (3/3) 5 shortcut-card 参数 ---')
  print(f'  数量: {len(p.shortcuts)}（期望 5）→', '✅' if len(p.shortcuts)==5 else '❌')
  act_count = {}
  for s in p.shortcuts: act_count[s['action']] = act_count.get(s['action'], 0)+1
  print(f'  action 类型统计: path_planning={act_count.get(\"path_planning\",0)}, poi_query={act_count.get(\"poi_query\",0)}, recommend_poi={act_count.get(\"recommend_poi\",0)} →', '✅ (3/1/1)' if (act_count.get('path_planning'),act_count.get('poi_query'),act_count.get('recommend_poi'))==(3,1,1) else '❌')
  required_pois = {'牌坊','樱顶','樱花大道','教五','总图书馆'}
  html_pois = set()
  for s in p.shortcuts:
      if s['start']: html_pois.add(s['start'])
      if s['end']: html_pois.add(s['end'])
      if s['name']: html_pois.add(s['name'])
  print(f'  硬编码 POI 名集合匹配 pois.json: {html_pois == required_pois} →', '✅' if html_pois == required_pois else '❌')
  all_display = all(s['display'] for s in p.shortcuts)
  print(f'  全部 shortcut 有 data-display-text: {all_display} →', '✅' if all_display else '❌')
  if len(p.shortcuts)!=5 or (act_count.get('path_planning'),act_count.get('poi_query'),act_count.get('recommend_poi'))!=(3,1,1) or html_pois!=required_pois or not all_display: ok=False
  print()
  if ok:
      print('✅ DOM CHECK ALL PASSED（12/12）')
  else:
      print('❌ DOM CHECK FAILED，检查上方 ❌ 项')
      exit(1)
  "
  ```

  Expected: 3 部分 + 最终 `✅ DOM CHECK ALL PASSED（12/12）`。

- [ ] **Step 2.5: Self-review 5 条（Implementer 自审）**
  1. 有没有删改任何原有 DOM 节点？（必须 100% append-only）
  2. 所有 id/class 名和 brief 里 Exact 代码 **逐字符比对**了吗？（尤其 `#welcome-overlay` / `.welcome-card-topbar` / 5 种 `sc-*` / `data-close-welcome` —— 错一个字符 Task 3/4 全断）
  3. 5 张 shortcut-card 的 data-* 值是不是**一字不改**抄 Exact 代码的？（不能擅自改 POI 名、不能改 constraints JSON 格式、不能改 display-text 文案）
  4. 无障碍属性齐全吗？（`role=dialog aria-modal aria-label=欢迎...` + 关闭按钮 aria-label + ？按钮 aria-label 都要有）
  5. 缩进对齐是否和现有 index.html 一致？（不能出现不伦不类的 3 空格 5 空格混排）

---

## Implementer Report 要求

完成后写 `.superpowers/sdd/task-2-report.md`，内容模板：

```markdown
# Task 2 Report

| 项 | 值 |
|----|----|
| Status | DONE / BLOCKED / NEEDS_CONTEXT |
| 改动文件（1 个） | static/index.html |
| Step 2.4 DOM 自检结果 | ✅ 12/12 PASS / ❌ 失败 N 项（附 ❌ 详情） |
| 两个插入点的定位方式 | ？按钮：基于 old_string `<span class="app-subtitle">...</span></div>` 文本匹配（/ 或其他实际匹配文本）<br>覆盖层：基于 old_string `最后一个 <script>...</script></body>` 文本匹配（/ 或其他实际匹配文本） |
| Self-review 5 条结果 | 1. Append-only: YES/NO；2. id/class 逐字对: YES/NO；3. shortcut data-* 未改: YES/NO；4. 无障碍全: YES/NO；5. 缩进一致: YES/NO |
| Concerns | 有写，没有 N/A |
```

返回时只给：Status + Step 2.4 结果摘要 + Concerns。
