# Task 4 Brief · 冷启动欢迎卡片

> **任务位置**：实施计划 **第 4 个任务**（前置条件：Task 2/3 DONE，`static/js/app.js` 存在）
> **目标**：在 `static/js/app.js` **文件最末尾（所有现有代码之后）**，追加一个 IIFE 封装的冷启动欢迎卡片交互模块（命名空间 `window.WelcomeColdStart`，不污染全局）。实现显示/隐藏逻辑（双 localStorage + 内存兜底）、5 种关闭方式（含 Esc / Mask stopPropagation）、？按钮 ignoreLocalStorage 唤回高亮、shortcut-card 事件委托 + 1500ms 防抖、3 类 action 数据流分发（path_planning/poi_query 走 submitNaturalLanguageQuery 复用 / recommend_poi 展开侧边栏）、DOMContentLoaded 自动初始化。
> **修改范围**：仅修改 **1 个文件**（`static/js/app.js`），Edit 必须基于 Read 到的最新末尾内容追加。

---

## 全局约束（必须严格遵守）

1. **Append-only 原则**：绝对不能修改 / 删除 `static/js/app.js` 里的任何原有代码（包括现有的 `submitNaturalLanguageQuery` / `showPoiSidebar` 等公开函数、原有的 `addEventListener` 绑定、原有的全局变量）。**只能在文件最后一行之后追加 IIFE 模块**。
2. **先读后改原则**：第一步必须 Read 完整 app.js，拿到最后 1~2 行的 exact 内容当 old_string。
3. **零污染全局原则**：所有函数/常量/状态变量封装在 IIFE 闭包里，只对外暴露一个 `window.WelcomeColdStart` 对象（含 show/close/isOpen/shouldAutoShow/_debugClearLs），严禁新增其他全局变量。
4. **Fail-soft 原则（最关键！）**：因为这个 welcome 模块是**追加式的增强功能**，不是核心功能，所以**任何地方出问题都不能影响 app.js 原有功能（问路/解析/渲染路线）**。具体要求：
   - 所有 DOM query（`getElementById` / `querySelector`）找不到元素时，立即 return 静默降级，不能抛错；
   - 调用现有公开函数 `submitNaturalLanguageQuery` / `showPoiSidebar` 时，必须先做 `typeof window.xxx === 'function'` 检查，不存在就走兜底降级（比如手动触发 input 的 Enter 事件）；
   - `localStorage.setItem/getItem` 全部包在 `try/catch` 里，QuotaExceededError（隐私模式）必须用内存变量兜底，不能崩。
5. **DOM/Class/ID 名 100% 匹配**：所有查询用的 id/class/css 类名必须和 Task 2 DOM + Task 3 CSS 逐字符一致（包括 `.hidden` / `.opening` / `.closing` / `.highlight` 这几个状态类）。
6. **StopPropagation 必加**：`.welcome-card` 的 click 监听必须 `e.stopPropagation()`，否则点击卡片内部任意位置会冒泡到 `.welcome-mask`（data-close-welcome=true），导致点哪里都关卡片（最常见的 overlay bug）。

---

## Step-by-Step 操作步骤

- [ ] **Step 4.1: Read 完整 `static/js/app.js`，确认文件最后 1~2 行 exact 内容，构造 Edit old_string/new_string，在文件末尾追加以下 Exact JS IIFE 代码块（一字不能改！包括注释、常量名、正则、ANIM_OUT_DURATION_MS 数字）**

Exact 代码：
```javascript
/* ========================================================================
   漫步珞珈 · 冷启动欢迎卡片（方案 1）交互逻辑
   · Append-only IIFE，命名空间：window.WelcomeColdStart
   · 不修改任何原有函数、不覆盖原有全局变量
   · 所有事件绑定使用事件委托 + once/debounce
   · Fail-soft：任何错误不影响原 app.js 核心功能
   ======================================================================== */
(function () {
    'use strict';

    // =============== 常量配置（对应 spec 所有硬编码数字，改一处全联动） ===============
    var LS_KEY_SEEN = 'whu_welcome_seen';
    var LS_KEY_FOREVER = 'whu_welcome_dont_show_forever';
    var DEBOUNCE_MS = 1500; // shortcut 连点防抖窗口（对应 T-017 14.11：1.5s 内连点只发第一次）
    var AUTO_SHOW_DELAY_MS = 150; // DOMContentLoaded 后延迟弹卡（不阻塞首屏高德地图加载）
    var HIGHLIGHT_PULSE_CLASS = 'highlight';
    var SIDEBAR_HIGHLIGHT_CLASS = 'flash-highlight';
    var ANIM_OUT_DURATION_MS = 200; // 退场动画总时长（略长于 CSS 180ms，保险）
    var INPUT_ID = 'nl-input'; // 输入框 id（如果前几轮不是这个名字，JS 会自动降级找第一个 input[type=text]/textarea，不崩）

    // =============== 内部状态（闭包私有，不暴露） ===============
    var sessionWelcomeShown = false; // localStorage 不可用时的内存兜底（仅当前标签页）
    var lastShortcutAt = 0; // shortcut 防抖时间戳
    var animating = false; // 防止入场退场动画叠加 + 连续点 X 和 start 造成重复关

    // =============== DOM 引用（缓存一次，懒初始化） ===============
    var $overlay = null;
    var $card = null;
    var $helpBtn = null;
    var $shortcuts = null;
    var $input = null;
    var $resultArea = null;
    var $sidebar = null;
    var $dontShowCheckbox = null;

    function initDomRefs() {
        $overlay = document.getElementById('welcome-overlay');
        $card = $overlay ? $overlay.querySelector('.welcome-card') : null;
        $helpBtn = document.getElementById('help-btn');
        $shortcuts = $overlay ? $overlay.querySelector('.welcome-shortcuts') : null;
        // 输入框：先找 INPUT_ID，找不到就降级找 input[type=text]，再找不到找 textarea（fail-soft）
        $input = document.getElementById(INPUT_ID)
            || document.querySelector('input[type="text"]')
            || document.querySelector('textarea');
        // 结果区：先找 id=result-area，再找 id=route-result
        $resultArea = document.getElementById('result-area') || document.getElementById('route-result');
        // 侧边栏：先找 .poi-sidebar，再找 #poi-sidebar
        $sidebar = document.querySelector('.poi-sidebar') || document.getElementById('poi-sidebar');
        $dontShowCheckbox = document.getElementById('welcome_dont_show');
        // 只要 overlay/helpbtn/shortcuts 三大件在就继续，其他 DOM（input/sidebar/resultArea）找不到没关系，用降级分支
        return !!($overlay && $card && $helpBtn && $shortcuts);
    }

    // =============== localStorage 工具（安全封装，任何抛错自动降级，fail-soft 核心！） ===============
    function safeLsGet(key) {
        try { return window.localStorage.getItem(key); }
        catch (e) { return null; } // 隐私模式/QuotaExceededError 直接当没存
    }
    function safeLsSet(key, value) {
        try { window.localStorage.setItem(key, value); return true; }
        catch (e) { return false; } // 失败就当没写，用内存变量兜底
    }

    // =============== 公开方法：show / close / isOpen ===============
    function show(options) {
        options = options || {};
        var ignoreLs = !!options.ignoreLocalStorage;

        if (!$overlay) return; // DOM 不存在直接静默 fail-soft
        if (animating) return; // 动画中不响应，避免叠图

        // 若卡片已显示：？按钮唤回高亮脉冲（E-07：点了没反应的焦虑 → 给视觉反馈）
        if (isOpen()) {
            $overlay.classList.remove(HIGHLIGHT_PULSE_CLASS);
            // 强制 reflow 重启动画（防止浏览器把两次 class 操作合并）
            void $overlay.offsetWidth;
            $overlay.classList.add(HIGHLIGHT_PULSE_CLASS);
            setTimeout(function () {
                if ($overlay) $overlay.classList.remove(HIGHLIGHT_PULSE_CLASS);
            }, 650); // 动画 600ms + 50ms buffer
            return;
        }

        animating = true;

        // 显示（先移除 hidden 才能播动画）
        $overlay.classList.remove('hidden');
        $overlay.setAttribute('aria-hidden', 'false');

        // 触发入场动画（下一帧再加 .opening，确保 CSS transition 生效，老浏览器不会白屏）
        if (window.requestAnimationFrame) {
            window.requestAnimationFrame(function () {
                if ($overlay) $overlay.classList.add('opening');
            });
        } else {
            // 老浏览器无 rAF，直接加（fail-soft，无动画也能显示）
            setTimeout(function () { if ($overlay) $overlay.classList.add('opening'); }, 16);
        }

        // 动画结束清理（最长 400ms：卡片 280ms + stagger 300ms buffer）
        setTimeout(function () {
            animating = false;
            if ($overlay) $overlay.classList.remove('opening');
        }, 420);

        // 如果不是 ignoreLocalStorage 模式（= 非 ?按钮手动唤回），先标记内存兜底 seen，避免隐私模式闪弹
        if (!ignoreLs) {
            sessionWelcomeShown = true;
        }
    }

    function close(options) {
        options = options || {};
        var markSeen = options.markSeen !== false; // 默认 true：除了调试外都写 seen

        if (!$overlay) return;
        if (animating) return;
        if (!isOpen()) return;

        animating = true;

        // 1. 先写 localStorage（如果需要）
        if (markSeen) {
            safeLsSet(LS_KEY_SEEN, 'true');
            sessionWelcomeShown = true;
            // 如果勾选了"以后不再显示"，再写 forever 标记
            if ($dontShowCheckbox && $dontShowCheckbox.checked) {
                safeLsSet(LS_KEY_FOREVER, 'true');
            }
        }

        // 2. 触发退场动画
        $overlay.classList.add('closing');

        // 3. 动画结束切 .hidden（ANIM_OUT_DURATION_MS 200ms）
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
        return !!($overlay
            && !$overlay.classList.contains('hidden')
            && !$overlay.classList.contains('closing'));
    }

    // =============== Shortcut-card 数据流分发（3 种 action 走完全不同链路） ===============
    function handleShortcutClick(e) {
        var card = e.target.closest('.shortcut-card');
        if (!card || !($shortcuts && $shortcuts.contains(card))) return;

        // 防抖：DEBOUNCE_MS 1.5s 内只跑第一次（T-017 14.11）
        var now = Date.now();
        if (now - lastShortcutAt < DEBOUNCE_MS) {
            e.preventDefault();
            return;
        }
        lastShortcutAt = now;

        var action = card.getAttribute('data-action');
        var displayText = card.getAttribute('data-display-text') || '';

        // 先填输入框（模拟用户手输，占位 + 方便改 + 多轮上下文继承）
        if ($input && displayText) {
            $input.value = displayText;
        }

        // 分发 3 种 action
        switch (action) {
            case 'path_planning':
            case 'poi_query':
                // ===== 类型 A + B：统一复用 submitNaturalLanguageQuery（走完整 NL 解析链路，含多轮上下文）=====
                close({ markSeen: true });
                // 等卡片退场动画差不多完了再触发，视觉顺一点
                setTimeout(function () {
                    // 优先调公开函数（typeof 安全检查！）
                    if (typeof window.submitNaturalLanguageQuery === 'function') {
                        window.submitNaturalLanguageQuery(displayText);
                    } else if ($input) {
                        // 兜底：手动触发回车事件，让原代码的 onkeydown 监听接住（fail-soft 绝对不能崩）
                        var ev;
                        if (typeof KeyboardEvent === 'function') {
                            ev = new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', which: 13, keyCode: 13, bubbles: true });
                        } else {
                            // 老浏览器 IE 兜底（不支持 KeyboardEvent 构造器）
                            ev = document.createEvent('Event');
                            ev.initEvent('keydown', true, true);
                            ev.key = 'Enter'; ev.which = 13; ev.keyCode = 13;
                        }
                        $input.dispatchEvent(ev);
                    }
                    // 滚动到结果区（有就滚，没有就算 fail-soft 不报错）
                    if ($resultArea && typeof $resultArea.scrollIntoView === 'function') {
                        try { $resultArea.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) { /* scrollIntoView options 老浏览器不支持，忽略 */ $resultArea.scrollIntoView(); }
                    }
                }, ANIM_OUT_DURATION_MS + 20);
                break;

            case 'recommend_poi':
                // ===== 类型 C：展开侧边栏 + 侧边栏顶部 2s 淡黄色高亮，不发起任何网络请求！ =====
                close({ markSeen: true });
                setTimeout(function () {
                    // 优先调公开函数
                    if (typeof window.showPoiSidebar === 'function') {
                        window.showPoiSidebar('全部');
                    } else if ($sidebar) {
                        // 兜底：手动加 .open 类（如果有这个类的话；没有也不会崩）
                        $sidebar.classList.add('open');
                    }
                    // 侧边栏 2s 淡黄色高亮（Fail-soft：sidebar 找不到就忽略）
                    if ($sidebar) {
                        $sidebar.classList.remove(SIDEBAR_HIGHLIGHT_CLASS);
                        void $sidebar.offsetWidth; // reflow 强制重启动画
                        $sidebar.classList.add(SIDEBAR_HIGHLIGHT_CLASS);
                        setTimeout(function () {
                            if ($sidebar) $sidebar.classList.remove(SIDEBAR_HIGHLIGHT_CLASS);
                        }, 2050); // 动画 2000ms + 50ms buffer
                    }
                    // 输入框 placeholder 引导下一步（仅当当前 placeholder 空的时候才加，不覆盖用户已有的提示）
                    if ($input && !$input.placeholder) {
                        $input.placeholder = '试试：樱花大道怎么去？';
                    }
                }, ANIM_OUT_DURATION_MS + 20);
                break;

            default:
                // 未知 action：只关卡，不做别的（fail-soft，不崩不报错）
                close({ markSeen: true });
        }
    }

    // =============== 事件绑定（全部挂在 overlay 级别，防止 DOM 变更后失效） ===============
    function bindEvents() {
        // ① 关闭按钮统一事件委托（X / mask / 开始使用 —— 所有带 data-close-welcome 的）
        $overlay.addEventListener('click', function (e) {
            var closer = e.target.closest('[data-close-welcome="true"]');
            if (closer) {
                e.preventDefault();
                close({ markSeen: true });
            }
        });
        // ①+ 关键！点卡片内部区域必须阻止冒泡到 mask，否则点哪里都关（最常见 bug，Constraint 6）
        $card.addEventListener('click', function (e) {
            e.stopPropagation();
        });

        // ② 键盘 Esc 关（仅在卡片显示时有效，不影响输入框；T-017 14.13：Esc 不关 input 内容 —— 这里只关卡，不动 input.value，所以天然满足）
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && isOpen()) {
                close({ markSeen: true });
            }
        });

        // ③ ？帮助按钮唤回（强制 ignoreLocalStorage，不写任何 LS 标记）
        $helpBtn.addEventListener('click', function () {
            show({ ignoreLocalStorage: true });
        });

        // ④ shortcut-card 点击数据流分发（事件委托挂在 .welcome-shortcuts 上，后续加卡片只要加 HTML 不用改 JS）
        $shortcuts.addEventListener('click', handleShortcutClick);
    }

    // =============== 自动弹出判断（DOMContentLoaded 后调用，G-03 老用户不打扰） ===============
    function shouldAutoShow() {
        // 优先级：forever（用户明确不要）→ ls_seen（看过一次）→ session_seen（内存兜底）→ 默认弹
        if (safeLsGet(LS_KEY_FOREVER) === 'true') return false;
        if (safeLsGet(LS_KEY_SEEN) === 'true') return false;
        if (sessionWelcomeShown) return false;
        return true;
    }

    // =============== 初始化入口（fail-soft 第一关：三大件 DOM 找不到就静默 return，不影响原功能） ===============
    function init() {
        if (!initDomRefs()) {
            // DOM 没加（Task 2 没合并 / 或用户没加欢迎卡片 DOM）→ 直接 return，静默降级不报错
            return;
        }
        bindEvents();

        // 按判断结果决定是否自动弹卡
        if (shouldAutoShow()) {
            // 延迟 AUTO_SHOW_DELAY_MS 再弹：避免卡首屏高德地图的加载（AUTO_SHOW_DELAY_MS = 150ms）
            setTimeout(function () { show({ ignoreLocalStorage: false }); }, AUTO_SHOW_DELAY_MS);
        }

        // 公开命名空间到 window（供 ?按钮调试 / 测试脚本用 / 后续多轮上下文用）
        window.WelcomeColdStart = {
            show: show,
            close: close,
            isOpen: isOpen,
            shouldAutoShow: shouldAutoShow,
            // 方便测试/调试的工具方法（清 localStorage，测试自动弹卡）
            _debugClearLs: function () {
                try {
                    window.localStorage.removeItem(LS_KEY_SEEN);
                    window.localStorage.removeItem(LS_KEY_FOREVER);
                } catch (e) { /* 忽略 */ }
                sessionWelcomeShown = false;
            }
        };
    }

    // =============== 挂载到 DOMContentLoaded 或立即执行（DOM 已就绪的情况） ===============
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        // DOM 已经解析完了（比如 app.js 被异步 defer/async 加载，或者动态插入）→ 等一帧再 init 稳一点
        if (window.requestAnimationFrame) {
            window.requestAnimationFrame(init);
        } else {
            setTimeout(init, 16);
        }
    }
})();
```

- [ ] **Step 4.2: 运行以下 JS token 12 项自检脚本（必跑，验证核心逻辑存在性）**

  Run:
  ```powershell
  cd "c:\Users\HUAWEI\Desktop\项目\campus-spatial-intelligence-agent"
  python -c "
  import re
  js = open('static/js/app.js', encoding='utf-8').read()
  required = [
      ('shouldAutoShow.*DOMContentLoaded|DOMContentLoaded.*shouldAutoShow', '自动弹卡逻辑（shouldAutoShow + DOMContentLoaded/readyState 判断）'),
      (r'LS_KEY_SEEN.*whu_welcome_seen.*setItem', '写入 whu_welcome_seen 标记（T-017 14.2）'),
      (r'LS_KEY_FOREVER.*whu_welcome_dont_show_forever.*checked', '写入 forever 标记 + 复选框 checked 判断（T-017 14.3）'),
      (r'data-close-welcome.*addEventListener.*click|addEventListener.*data-close-welcome.*click', '5 关闭方式之一：data-close-welcome 统一事件委托（14.4）'),
      (r"key === 'Escape'.*isOpen.*close|key\s*===\s*'Escape'.*close", '5 关闭方式之二：Esc 关卡（14.4）'),
      (r'input\.value.*displayText.*submitNaturalLanguageQuery|displayText.*input\.value.*submitNaturalLanguageQuery', '点 shortcut 填 value + 调 submitNaturalLanguageQuery（T-017 14.5）'),
      (r"case 'poi_query'.*submitNaturalLanguageQuery|poi_query[\s\S]{0,400}submitNaturalLanguageQuery", 'poi_query 分支（T-017 14.7）'),
      (r"case 'recommend_poi'.*showPoiSidebar|recommend_poi[\s\S]{0,600}showPoiSidebar", 'recommend_poi 分支展开侧边栏（T-017 14.8，不调 API）'),
      (r'helpBtn.*addEventListener.*click.*ignoreLocalStorage.*true|help-btn.*ignoreLocalStorage.*true', '？按钮强制 ignoreLocalStorage 唤回（T-017 14.9）'),
      (r'try.*localStorage.*catch|sessionWelcomeShown|QuotaExceededError', 'localStorage 封装 + 内存兜底 sessionWelcomeShown（T-017 14.10）'),
      (r'DEBOUNCE_MS.*1500|lastShortcutAt.*Date\.now', '1500ms 防抖 DEBOUNCE_MS + lastShortcutAt 时间戳判断（T-017 14.11）'),
      (r"Escape.*close.*input\.value|close.*key === 'Escape'.*value", 'Esc 关卡不影响 input.value（T-017 14.13 — 代码里不碰 input.value 就天然满足）'),
  ]
  ok_total = 0
  fail_total = 0
  for regex, name in required:
      found = bool(re.search(regex, js, flags=re.IGNORECASE | re.DOTALL))
      if found:
          ok_total += 1
          print(f'  ✅ {name}')
      else:
          fail_total += 1
          print(f'  ❌ {name}')
  print()
  # 额外检查：有没有污染全局（除了 window.WelcomeColdStart 之外，IIFE 闭包里 var 的没暴露）
  expose_ok = 'window.WelcomeColdStart' in js and re.search(r'\b(function|var)\s+(submitNaturalLanguageQuery|showPoiSidebar|close|show|isOpen)\s*[=(]', js) is None
  print(f'  ✅ 零污染全局（仅暴露 window.WelcomeColdStart）' if expose_ok else f'  ⚠️  可能污染全局（需人工看，但通常 OK）')
  print()
  print(f'✅ JS TOKEN CHECK PASSED ({ok_total}/{len(required)})' if fail_total == 0 else f'❌ JS TOKEN CHECK FAILED ({fail_total} FAIL)')
  assert fail_total == 0, 'JS tokens missing!'
  "
  ```
  Expected: 12/12 ✅，最终 `✅ JS TOKEN CHECK PASSED (12/12)`。

- [ ] **Step 4.3: 可选语法自检（有 Node 就跑，没有用 Python 括号平衡兜底）**

  Run (任选其一):
  ```powershell
  # A) 有 Node
  node --check static/js/app.js
  # 预期：无输出 → 语法 OK
  ```
  ```powershell
  # B) Python 兜底（无 Node 时用，简单括号平衡 + 关键字检查）
  cd <repo_root>
  python -c "
  content = open('static/js/app.js', encoding='utf-8').read()
  c1=content.count('{'); c2=content.count('}'); p1=content.count('('); p2=content.count(')'); b1=content.count('['); b2=content.count(']')
  ok = (c1==c2) and (p1==p2) and (b1==b2)
  print(f'  Balance: {{ {c1}/{c2}  (✅) if c1==c2 else \"❌\"}}   ( {p1}/{p2}  (✅) if p1==p2 else \"❌\"}}   [ {b1}/{b2}  (✅) if b1==b2 else \"❌\"]}')
  print('  语法：✅ BALANCED' if ok else '❌ UNBALANCED')
  assert ok, 'Bracket unbalance!'
  "
  ```

- [ ] **Step 4.4: Self-review 5 条（Implementer 自审）**
  1. **Append-only 检查**：新增 IIFE 确实在 app.js 文件末尾吗？有没有不小心覆盖掉原 app.js 最后几行代码？（Read 最后 20 行核对）
  2. **Fail-soft 全链路检查**：所有 DOM query 找不到 → return；所有 localStorage → try/catch；所有公开函数调用 → typeof 检查 + 兜底；对吗？
  3. **StopPropagation 检查**：`$card.addEventListener('click', e => e.stopPropagation())` 有没有写？（没写的话，点卡片内任意位置都会关，这是 overlay 第一大 bug）
  4. **污染全局检查**：除了 `window.WelcomeColdStart = {...}`，有没有其他地方直接赋值全局变量（比如 `var xxx = 1` 在 IIFE 外面？没有，因为整个逻辑都在 IIFE 里，var 的都是私有的）。
  5. **硬编码数字检查**：150ms / 1500ms / 200ms / 280ms / 650ms / 2050ms 这些数字和 spec/注释对应吗？（ANIM_OUT_DURATION=200 对应 CSS 180ms + buffer，OK；DEBOUNCE=1500 对应 T-017 14.11 的 1.5s，OK；AUTO_DELAY=150 对应首屏等 150ms，OK）。

---

## Implementer Report 要求

完成后写 `.superpowers/sdd/task-4-report.md`，模板：

```markdown
# Task 4 Report

| 项 | 值 |
|----|----|
| Status | DONE / BLOCKED / NEEDS_CONTEXT |
| 改动文件（1 个） | static/js/app.js（末尾追加 IIFE 模块，append-only） |
| Step 4.2 JS token 12 项自检结果 | ✅ 12/12 PASS / ❌ N 项失败 |
| Step 4.3 语法自检结果 | ✅ Node --check 语法 OK / ✅ Python 括号平衡 OK / ❌ 语法错误 |
| 暴露的全局对象 | window.WelcomeColdStart（4 个公开方法：show/close/isOpen/shouldAutoShow + 1 个 debug），是否仅此 1 个？是/否 |
| Self-review 5 条结果 | 1. Append-only 末尾: YES/NO；2. Fail-soft 全覆盖: YES/NO；3. StopPropagation 写了: YES/NO；4. 只暴露 WelcomeColdStart: YES/NO；5. 硬编码数字匹配 spec: YES/NO |
| Concerns | 有写，没有 N/A |
```

返回时只给 Status + Step 4.2 结果 + Concerns。
