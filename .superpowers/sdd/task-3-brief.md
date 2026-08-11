# Task 3 Brief · 冷启动欢迎卡片

> **任务位置**：实施计划 **第 3 个任务**（前置条件：Task 2 DONE，`static/css/style.css` 存在）
> **目标**：在 `static/css/style.css` 的**文件最末尾（现有所有样式类之后）**追加完整的欢迎卡片样式代码块。要求：100% append-only（不删改原有任何一行 CSS）、100% 复用 style.css 开头定义的 `:root` CSS 变量、严禁新增任何自定义 hex 色值、樱顶窗棂纹三层渐变必须 exact、符合 T-018 验收第 7 条 7.1~7.7 全部要求。
> **修改范围**：仅修改 **1 个文件**（`static/css/style.css`），Edit 必须基于 Read 到的文件最新末尾内容做 old_string/new_string。

---

## 全局约束（必须严格遵守）

1. **Append-only 原则 + 末尾追加**：**绝对不能**删改 / 重写 style.css 里原有任何样式（包括 :root、header、地图、输入框、按钮等已有类）。必须**只在文件最后一行之后**追加，不能把新样式插入到原有样式中间（否则会造成：① 原有样式的覆盖顺序被打乱 ② CSS 特异性冲突 ③ 未来维护时不知道 welcome 样式在哪）。
2. **先读后改原则**：第一步必须 Read 完整 `static/css/style.css`，确认文件最后一行的内容是什么（可能是 `}` 闭合或空行），old_string 必须包含文件最后 1~2 行的精确内容（例如最后一行的 `}` 或空行），再做 Edit 追加。
3. **零新增主题色原则（最关键！）**：新增的 welcome CSS 代码中，**严禁出现任何自定义 hex/rgb/hsl 色值字面量**（例如 `#FF0000`、`rgb(255,0,0)` 都不行）。所有颜色必须使用开头 `:root` 里定义的变量（`var(--xxx)` 形式），除了以下 3 处**特例**：
   - `.sc-stone .sc-emoji` 的 `background-color: #FAFAF9`（这个是极浅的灰，`--paper` 太暗了一点点，没有对应变量，保留这个特例）
   - `.sc-wide .sc-emoji` 的 `background-color: #FFF6EC`（这个是暖橙浅色底，`--color-poi` 是字色，没有对应浅底变量，保留这个特例）
   - `.welcome-tips` 侧边栏高亮的 `rgba(232,198,80,0.18)`（淡黄色，没有对应 --paper 系变量，保留这个特例）
   除了这 3 处以外，**任何地方出现裸 hex/rgb 值就算任务失败**（哪怕和变量值一样也不行，必须用 var()，保证未来主题色一改，welcome 一起变）。
4. **Class/ID 命名 100% 匹配 DOM**：所有 `.welcome-*` / `.shortcut-card` / `.sc-accent` 等类名，`#help-btn` / `#welcome-overlay` 等 ID，**必须和 Task 2 生成的 DOM 里的名字逐字符一致**（大小写敏感，`--` `_` 不能错）。class 拼写错一个，样式就挂了。
5. **樱顶窗棂纹签名元素 Exact 原则**：`.welcome-card-topbar` 的 background 三层叠加顺序必须和 Exact 代码完全一样（从上到下：③ 竖线柱子 `repeating-linear-gradient(90deg, transparent 0 48px, var(--cherry-deep) 48px 50px, transparent 50px 96px)` → ② 横线屋檐线 → ① 渐变底色）。顺序反了的话，线会被底挡住看不见。

---

## Step-by-Step 操作步骤

- [ ] **Step 3.1: 先 Read 完整 `static/css/style.css`，确认文件最后一行内容是什么**
  - 必须拿到 exact 末尾内容才能做 Edit。old_string 通常是文件最后 1~2 行（比如最后一个 `}` 或空行），new_string = old_string + "\n" + 下面的 Exact CSS 代码块。

- [ ] **Step 3.2: 做 Edit，在 style.css 末尾追加以下完整 CSS 代码块（一字不能改！包括注释、@keyframes 名、cubic-bezier 参数、换行顺序）**

Exact 代码：
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

/* 5 种 accent bar 颜色 + emoji 底色（全复用现有变量，除了 3 个特例保留裸色） */
.shortcut-card.sc-accent::before { background-color: var(--cherry-deep); }
.shortcut-card.sc-accent .sc-emoji { background-color: var(--cherry-soft); color: var(--cherry-deep); }
.shortcut-card.sc-pink::before { background-color: var(--cherry); }
.shortcut-card.sc-pink .sc-emoji { background-color: var(--cherry-soft); color: var(--cherry-deep); }
.shortcut-card.sc-jade::before { background-color: var(--jade); }
.shortcut-card.sc-jade .sc-emoji { background-color: var(--jade-soft); color: var(--jade-deep); }
.shortcut-card.sc-stone::before { background-color: var(--ink-light); }
.shortcut-card.sc-stone .sc-emoji { background-color: #FAFAF9; color: var(--ink-soft); } /* 特例 1 */
.shortcut-card.sc-wide::before { background-color: var(--color-poi); } /* 暖橙：语义对应 POI */
.shortcut-card.sc-wide .sc-emoji { background-color: #FFF6EC; color: var(--color-poi); } /* 特例 2 */

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
    0% { background-color: rgba(232, 198, 80, 0.18); } /* 特例 3：淡黄色高亮 */
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

- [ ] **Step 3.3: 运行以下 CSS token 自检脚本（23 项检查，必跑）**

  Run:
  ```powershell
  cd "c:\Users\HUAWEI\Desktop\项目\campus-spatial-intelligence-agent"
  python -c "
  import re
  content = open('static/css/style.css', encoding='utf-8').read()
  required_tokens = [
      (r'\.welcome-overlay\s*\{', 'welcome-overlay 骨架'),
      (r'z-index:\s*9999', 'z-index ≥ 9999 压过高德'),
      (r'\.welcome-card-topbar[\s\S]{0,300}height:\s*48px', 'topbar 48px 高'),
      (r'repeating-linear-gradient\s*\(\s*90deg[\s\S]{0,100}transparent\s+0\s+48px[\s\S]{0,80}var\(--cherry-deep\)\s+48px\s+50px', 'topbar ③ 柱子 repeating-gradient'),
      (r'linear-gradient\s*\(\s*180deg[\s\S]{0,100}--cherry-deep[\s\S]{0,100}--jade-deep', 'topbar ② 屋檐线横线'),
      (r'linear-gradient\s*\(\s*90deg[\s\S]{0,60}--cherry-soft[\s\S]{0,60}--jade-soft', 'topbar ① 渐变底'),
      (r'\.shortcut-card::before.*\{[\s\S]{0,60}width:\s*2px[\s\S]{0,60}border-radius:\s*2px', 'accent bar ::before 2px left'),
      (r'sc-accent::before[\s\S]{0,60}var\(--cherry-deep\)', 'sc-accent 樱粉左框'),
      (r'sc-pink::before[\s\S]{0,60}var\(--cherry\)', 'sc-pink 浅樱粉左框'),
      (r'sc-jade::before[\s\S]{0,60}var\(--jade\)', 'sc-jade 珞珈绿左框'),
      (r'sc-stone::before[\s\S]{0,60}var\(--ink-light\)', 'sc-stone 中灰左框'),
      (r'sc-wide::before[\s\S]{0,60}var\(--color-poi\)', 'sc-wide 暖橙左框'),
      (r'\.welcome-title[\s\S]{0,150}Noto Serif SC', 'welcome-title Noto Serif SC 标题字体'),
      (r'@keyframes\s+welcome-card-in[\s\S]{0,200}280ms\s+cubic-bezier\s*\(\s*0\.2\s*,\s*0\.8\s*,\s*0\.2\s*,\s*1\s*\)', '入场卡片 springy 280ms'),
      (r'shortcut-card:nth-child\(1\)[\s\S]{0,120}60ms.*both', '5 卡 stagger 第 1 张 60ms'),
      (r'@keyframes\s+welcome-card-out[\s\S]{0,150}180ms\s+ease-in', '退场卡片 180ms ease-in'),
      (r'@media\s*\(prefers-reduced-motion:\s*reduce\)', '无障碍 prefers-reduced-motion 降级'),
      (r'@media\s*\(max-width:\s*767px\)', '移动端 < 768px 断点'),
      (r'\.welcome-shortcuts[\s\S]{0,50}flex-direction:\s*column', '移动端 shortcut-card 单列 flex（column）'),
      (r'\.help-btn\s*\{', '？按钮 .help-btn 类存在'),
      (r'flash-highlight[\s\S]{0,200}sidebar-flash[\s\S]{0,200}rgba\s*\(\s*232\s*,\s*198\s*,\s*80\s*,\s*0\.18\s*\)', '侧边栏 flash 淡黄色高亮'),
      (r'@keyframes\s+welcome-card-pulse[\s\S]{0,200}0\s+0\s+0\s+6px\s+rgba\s*\(\s*232\s*,\s*146\s*,\s*156\s*,\s*0\.32\s*\)', '唤回高亮脉冲（樱粉阴影）'),
      (r'min-height:\s*(44|46|48|72|84)px', '最小触摸目标 ≥ 44px（至少有一个满足）'),
  ]
  ok_total = 0
  fail_total = 0
  for pattern, name in required_tokens:
      found = bool(re.search(pattern, content, flags=re.IGNORECASE | re.DOTALL))
      if found:
          ok_total += 1
          print(f'  ✅ {name}')
      else:
          fail_total += 1
          print(f'  ❌ {name}')
  print()
  print(f'✅ CSS CHECK PASSED ({ok_total}/{len(required_tokens)})' if fail_total == 0 else f'❌ CSS CHECK FAILED ({fail_total} FAIL)')
  assert fail_total == 0, 'CSS tokens missing!'
  "
  ```
  Expected: 23/23 ✅，最终输出 `✅ CSS CHECK PASSED (23/23)`。

- [ ] **Step 3.4: Self-review 4 条（Implementer 自审）**
  1. **裸色值检查**：全文搜索 `#` 开头的 hex 色值在新增 welcome CSS 代码块里，确认除了 3 个特例（`#FAFAF9` / `#FFF6EC` / `rgba(232,198,80,0.18)` + 唤回阴影 `rgba(232,146,156,0.32)`）之外没有别的裸色值（所有颜色都是 `var(...)`）。
  2. **顺序检查**：新增的 CSS 代码块确实只在 style.css 的**文件最末尾**追加（没有插在原有任何样式之间，通过 Read 最后 10 行看一眼）。
  3. **类名匹配检查**：把新增 welcome CSS 里的所有类/id 列出来（.welcome-*、#help-btn、.sc-*、@keyframes 名），对照 Task 2 report 里的 DOM 确认全部匹配（没有拼写错误的 class/id）。
  4. **樱顶签名元素检查**：`background` 的三层叠加顺序对不对（③ 柱子在上 → ② 横线在中 → ① 渐变底在下）？（顺序反了就看不到线）

---

## Implementer Report 要求

完成后写 `.superpowers/sdd/task-3-report.md`，模板：

```markdown
# Task 3 Report

| 项 | 值 |
|----|----|
| Status | DONE / BLOCKED |
| 改动文件（1 个） | static/css/style.css（末尾追加 welcome 代码块，append-only） |
| Step 3.3 CSS token 23 项自检结果 | ✅ 23/23 PASS / ❌ 失败 N 项 |
| 裸色值检查（新增代码块内 hex/rgb 数） | 裸色值共 N 个（3 个特例 ≤4 算 PASS；>4 算 FAIL，得改） |
| Self-review 4 条结果 | 1. 裸色仅特例: YES/NO；2. 追加在文件末尾: YES/NO；3. class/id 全匹配: YES/NO；4. topbar 三层顺序对: YES/NO |
| Concerns | 有写，没有 N/A |
```

返回时只给 Status + Step 3.3 结果 + Concerns。
