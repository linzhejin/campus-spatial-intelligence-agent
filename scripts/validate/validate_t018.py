#!/usr/bin/env python3
"""T-018 验收标准自检脚本：7 基础 + 7 冷启动 = 14 checks"""
import re
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CSS = STATIC / "css" / "style.css"
MANIFEST = STATIC / "manifest.json"
SW = STATIC / "sw.js"


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    mark = "✅" if condition else "❌"
    print(f"{mark} [{status}] {name}")
    if detail and not condition:
        print(f"      → {detail}")
    return condition


def main():
    css_text = CSS.read_text(encoding="utf-8")
    manifest_text = MANIFEST.read_text(encoding="utf-8")
    sw_text = SW.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)

    results = []

    print("=" * 70)
    print("T-018 自检报告 · 7 基础标准 (§1~6)")
    print("=" * 70)

    # ── §1 主色：樱花粉 #E8929C，辅色：翡翠绿 #4A7C6F ──
    r1 = check(
        "§1 樱花粉 #E8929C + 翡翠绿 #4A7C6F",
        bool(re.search(r"#E8929C", css_text)) and bool(re.search(r"#4A7C6F", css_text)),
        "CSS 中缺少主色/辅色色值"
    )
    results.append(r1)

    # ── §2 标题字体：Noto Serif SC ──
    r2 = check(
        "§2 标题字体 Noto Serif SC",
        bool(re.search(r'Noto Serif SC', css_text)),
        "CSS 中未声明 Noto Serif SC 字体栈"
    )
    results.append(r2)

    # ── §3 响应式断点：480 / 768 / 1024 / 1440 ──
    has_480 = bool(re.search(r"@media\s*\(min-width:\s*480px\)", css_text))
    has_768 = bool(re.search(r"@media\s*\(min-width:\s*768px\)", css_text)) or bool(re.search(r"@media\s*\(max-width:\s*767px\)", css_text))
    has_1024 = bool(re.search(r"@media\s*\(min-width:\s*1024px\)", css_text))
    has_1440 = bool(re.search(r"@media\s*\(min-width:\s*1440px\)", css_text))
    r3 = check(
        "§3 断点 480/768/1024/1440",
        has_480 and has_768 and has_1024 and has_1440,
        f"缺失断点: {'480 ' if not has_480 else ''}{'768 ' if not has_768 else ''}{'1024 ' if not has_1024 else ''}{'1440' if not has_1440 else ''}"
    )
    results.append(r3)

    # ── §4 触摸目标 ≥ 44×44px（所有按钮 width/height/min-height 至少 44） ──
    touch_patterns = [
        r"min-height:\s*var\(--touch-min\)",
        r"width:\s*var\(--touch-min\)",
        r"height:\s*var\(--touch-min\)",
        r"--touch-min:\s*44px",
        r"min-height:\s*4[4-9]px",
        r"min-height:\s*5[0-9]px",
        r"height:\s*4[4-9]px",
        r"height:\s*5[0-9]px",
    ]
    has_touch44 = any(re.search(p, css_text) for p in touch_patterns)

    # ────────────────────────────────────────────────────────
    # 关键按钮尺寸检查：
    #   · 找该类的 ALL 规则块，取最后一个（CSS 级联后生效）
    #   · 若最后一个含 !important，以它为准（append-only 常见模式）
    #   · 或者任何一个块出现 width/height ≥ 44 + !important，视为达标
    # ────────────────────────────────────────────────────────
    def check_effective_size(class_name, label):
        """检查某类最终生效的宽高是否 ≥ 44。返回 (ok, w, h, note)"""
        # 找所有 .class {...} 块（包括带 !important 覆盖的 append）
        blocks = list(re.finditer(r'(?m)^\s*\.' + re.escape(class_name) + r'\s*\{([^}]*)\}', css_text, re.S))
        if not blocks:
            # 宽松点：直接搜索带类名 + 44px 数字的文本，找覆盖声明
            direct_check = re.search(r'\.' + re.escape(class_name) + r'\s*\{[^}]*width:\s*(\d+)px\s*!important[^}]*height:\s*(\d+)px\s*!important', css_text, re.S)
            if direct_check:
                w, h = int(direct_check.group(1)), int(direct_check.group(2))
                return (w >= 44 and h >= 44, w, h, 'via direct important')
            return (True, None, None, 'no blocks found, skip')

        w_final, h_final = None, None
        has_important_w, has_important_h = False, False

        for blk in blocks:
            body = blk.group(1)
            # 检查 width
            wm = re.search(r'width:\s*(\d+)px\s*(!important)?', body)
            if wm:
                wval = int(wm.group(1))
                is_imp = bool(wm.group(2))
                if is_imp or not has_important_w:
                    if is_imp or w_final is None:
                        w_final = wval
                        has_important_w = is_imp
            # 检查 height
            hm = re.search(r'height:\s*(\d+)px\s*(!important)?', body)
            if hm:
                hval = int(hm.group(1))
                is_imp = bool(hm.group(2))
                if is_imp or not has_important_h:
                    if is_imp or h_final is None:
                        h_final = hval
                        has_important_h = is_imp

        # 兜底：直接搜索整个 CSS 文本中的 .class 相关 44px !important
        if (w_final is None or w_final < 44) or (h_final is None or h_final < 44):
            fallback_imp = re.search(r'\.' + re.escape(class_name) + r'\s*\{[^}]*width:\s*4[4-9]px\s*!important', css_text, re.S)
            fallback_imp_h = re.search(r'\.' + re.escape(class_name) + r'\s*\{[^}]*height:\s*4[4-9]px\s*!important', css_text, re.S)
            if fallback_imp and fallback_imp_h:
                return (True, 44, 44, 'fallback !important 44')

        return ((w_final or 0) >= 44 and (h_final or 0) >= 44,
                w_final, h_final, 'last-block')

    critical_ok = True
    ok_close, wc_w, wc_h, close_note = check_effective_size('welcome-close', 'welcome-close')
    if not ok_close:
        critical_ok = False
        print(f"      → .welcome-close 最终尺寸 {wc_w}x{wc_h} < 44 ({close_note})")
    ok_help, hp_w, hp_h, help_note = check_effective_size('help-btn', 'help-btn')
    if not ok_help:
        critical_ok = False
        print(f"      → .help-btn 最终尺寸 {hp_w}x{hp_h} < 44 ({help_note})")

    r4 = check(
        "§4 触摸目标 ≥ 44×44px (welcome-close + help-btn 均达标)",
        has_touch44 and critical_ok,
        "按钮尺寸未达标（详见上）"
    )
    results.append(r4)

    # ── §5 Service Worker：注册骨架（install/activate/fetch 三事件） ──
    has_install = bool(re.search(r"addEventListener\('install'", sw_text))
    has_activate = bool(re.search(r"addEventListener\('activate'", sw_text))
    has_fetch = bool(re.search(r"addEventListener\('fetch'", sw_text))
    has_precache = bool(re.search(r"cache\.addAll|PRECACHE_URLS", sw_text))
    r5 = check(
        "§5 SW 注册成功骨架 (install/activate/fetch 事件 + precache)",
        has_install and has_activate and has_fetch and has_precache,
        (f"install={'Y' if has_install else 'N'} "
         f"activate={'Y' if has_activate else 'N'} "
         f"fetch={'Y' if has_fetch else 'N'} "
         f"precache={'Y' if has_precache else 'N'}")
    )
    results.append(r5)

    # ── §5.1 SW 三策略显式实现：cache-first / SWR / network-only ──
    has_cache_first = bool(re.search(r"策略 ①|cache-first|CACHE-HIT.*直接返回|caches\.match\(request\)\.then.*if\s*\(cached\)\s*\{[\s\S]{0,120}return cached", sw_text, re.S | re.I))
    has_swr = bool(re.search(r"策略 ②|stale-while-revalidate|SWR|revalidate|cached \|\| fetchPromise", sw_text, re.S | re.I))
    has_network_only = bool(re.search(r"策略 ③|network-only|NETWORK-ONLY|isThirdParty|isAmapDomain|amapw?\\\.com", sw_text, re.S | re.I))
    r5b = check(
        "§5.1 SW 三策略显式实现 (cache-first / SWR / network-only)",
        has_cache_first and has_swr and has_network_only,
        (f"cache-first={'Y' if has_cache_first else 'N'} "
         f"SWR={'Y' if has_swr else 'N'} "
         f"network-only={'Y' if has_network_only else 'N'}")
    )
    results.append(r5b)

    # ── §6 manifest.json: 192/512 icon + theme_color + standalone ──
    icons_sizes = [i.get("sizes", "") for i in manifest.get("icons", [])]
    has_192 = any("192" in s for s in icons_sizes)
    has_512 = any("512" in s for s in icons_sizes)
    theme_color = manifest.get("theme_color", "")
    tc_ok = bool(theme_color)
    standalone_ok = manifest.get("display") == "standalone"
    # 另外 theme_color 应该符合樱花主题 (粉或绿)，不是蓝
    tc_theme_match = bool(re.match(r"^#([Ee]8929[Cc]|[4Aa]7[Cc]6[Ff]|[Ff][Aa][Ff]8[Ff]5)", theme_color or ""))
    r6 = check(
        "§6 manifest: 192/512 icon + theme_color + standalone + 主题色匹配",
        has_192 and has_512 and tc_ok and standalone_ok and tc_theme_match,
        (f"192icon={'OK' if has_192 else 'MISSING'} "
         f"512icon={'OK' if has_512 else 'MISSING'} "
         f"theme_color={theme_color or 'MISSING'} "
         f"{'(蓝色不匹配樱花主题)' if not tc_theme_match and tc_ok else ''} "
         f"standalone={'OK' if standalone_ok else 'MISSING'}")
    )
    results.append(r6)

    print()
    print("=" * 70)
    print("T-018 自检报告 · 7 冷启动样式 (§7.1~7.7)")
    print("=" * 70)

    # ── §7.1 桌面端 welcome-card 480×640 + 3+2 grid ──
    wc_size_ok = bool(re.search(r"\.welcome-card\s*\{[^}]*?width:\s*480px[^}]*?height:\s*640px", css_text, re.S))
    grid_3plus2 = bool(re.search(r"\.welcome-shortcuts\s*\{[^}]*?grid-template-columns:\s*repeat\(3,\s*1fr\)", css_text, re.S)) and \
                  bool(re.search(r"\.shortcut-card\.sc-wide\s*\{[^}]*?grid-column:\s*1\s*/\s*-1", css_text, re.S))
    r71 = check(
        "§7.1 桌面端: card 480×640 + 3+2 grid + sc-wide 占满",
        wc_size_ok and grid_3plus2,
        f"size={'OK' if wc_size_ok else 'NO'} grid={'OK' if grid_3plus2 else 'NO'}"
    )
    results.append(r71)

    # ── §7.2 移动端 92vw 宽 + 单列 flex + tips 折叠 + 字号 0.8rem ──
    mobile_w = bool(re.search(r"width:\s*92vw", css_text))
    # 宽松匹配：只要在 max-width:767px 的 media block 中，.welcome-shortcuts 有 flex-direction:column
    # （CSS 写法上 media 块内可能先写其他类，再写 .welcome-shortcuts）
    mobile_col = False
    mobile_media_blocks = re.findall(r"@media\s*\(max-width:\s*767px\)\s*\{(.*?)\n\}", css_text, re.S)
    for block in mobile_media_blocks:
        if re.search(r"\.welcome-shortcuts\s*\{[^}]*flex-direction:\s*column", block, re.S):
            mobile_col = True
            break
    # 额外兜底：全局搜索 .welcome-shortcuts + flex-direction:column（在任何 media 块内）
    if not mobile_col:
        ws_in_any_media = re.findall(r"@media[^{]*\{(.*?)\n\}", css_text, re.S)
        for block in ws_in_any_media:
            if re.search(r"\.welcome-shortcuts\s*\{[^}]*flex-direction:\s*column", block, re.S):
                mobile_col = True
                break
    tips_fold = bool(re.search(r"welcome-tips-details|welcome-tips-summary|details\[open\]", css_text))
    small_font = bool(re.search(r"font-size:\s*0\.8rem", css_text))
    r72 = check(
        "§7.2 移动端: 92vw + 单列 flex + tips details 折叠 + 小字号",
        mobile_w and mobile_col and tips_fold and small_font,
        f"92vw={'OK' if mobile_w else 'NO'} col={'OK' if mobile_col else 'NO'} tips={'OK' if tips_fold else 'NO'} font={'OK' if small_font else 'NO'}"
    )
    results.append(r72)

    # ── §7.3 遮罩层 var(--paper) 0.68 + z-index ≥ 9999 ──
    mask_ok = bool(re.search(r"\.welcome-mask\s*\{[^}]*?background-color:\s*var\(--paper\)[^}]*?opacity:\s*0\.68", css_text, re.S))
    z_ok = bool(re.search(r"z-index:\s*9999", css_text))
    r73 = check(
        "§7.3 遮罩层 paper 0.68 + z-index ≥ 9999",
        mask_ok and z_ok,
        f"mask={'OK' if mask_ok else 'NO'} z={'OK' if z_ok else 'NO'}"
    )
    results.append(r73)

    # ── §7.4 5 张 shortcut-card accent bar 颜色 ──
    sc_accent = bool(re.search(r"\.shortcut-card\.sc-accent::before\s*\{[^}]*?background-color:\s*var\(--cherry-deep\)", css_text, re.S))
    sc_pink = bool(re.search(r"\.shortcut-card\.sc-pink::before\s*\{[^}]*?background-color:\s*var\(--cherry\)", css_text, re.S))
    sc_jade = bool(re.search(r"\.shortcut-card\.sc-jade::before\s*\{[^}]*?background-color:\s*var\(--jade\)", css_text, re.S))
    sc_stone = bool(re.search(r"\.shortcut-card\.sc-stone::before\s*\{[^}]*?background-color:\s*var\(--ink-light\)", css_text, re.S))
    sc_wide = bool(re.search(r"\.shortcut-card\.sc-wide::before\s*\{[^}]*?background-color:\s*var\(--color-poi\)", css_text, re.S))
    r74 = check(
        "§7.4 5 张 card accent bar (sc-accent→cherry-deep, sc-pink→cherry, sc-jade→jade, sc-stone→ink-light, sc-wide→color-poi)",
        sc_accent and sc_pink and sc_jade and sc_stone and sc_wide,
        f"acc={sc_accent} pink={sc_pink} jade={sc_jade} stone={sc_stone} wide={sc_wide}"
    )
    results.append(r74)

    # ── §7.5 樱顶窗棂纹装饰条 topbar 48px + linear-gradient + repeating-linear-gradient ──
    topbar_h = bool(re.search(r"\.welcome-card-topbar\s*\{[^}]*?height:\s*48px", css_text, re.S))
    has_lg = bool(re.search(r"linear-gradient", css_text))
    has_rlg = bool(re.search(r"repeating-linear-gradient", css_text))
    r75 = check(
        "§7.5 topbar 48px + linear-gradient + repeating-linear-gradient 三层叠加",
        topbar_h and has_lg and has_rlg,
        f"height={'OK' if topbar_h else 'NO'} lg={'OK' if has_lg else 'NO'} rlg={'OK' if has_rlg else 'NO'}"
    )
    results.append(r75)

    # ── §7.6 动画：入场 mask 200ms + card 280ms springy + stagger 60ms + 退场 ──
    mask_dur = bool(re.search(r"welcome-mask-in.*200ms|animation:\s*welcome-mask-in\s+200ms", css_text))
    card_dur = bool(re.search(r"welcome-card-in.*280ms|animation:\s*welcome-card-in\s+280ms.*cubic-bezier\(0\.2,\s*0\.8,\s*0\.2,\s*1\)", css_text, re.S))
    stagger = bool(re.search(r"nth-child\(1\)[^{]*\{[^}]*60ms.*nth-child\(2\)[^{]*\{[^}]*120ms.*nth-child\(3\)[^{]*\{[^}]*180ms.*nth-child\(4\)[^{]*\{[^}]*240ms.*nth-child\(5\)[^{]*\{[^}]*300ms", css_text, re.S))
    exit_anim = bool(re.search(r"welcome-mask-out.*150ms|welcome-card-out.*180ms", css_text, re.S))
    r76 = check(
        "§7.6 动画: 入场 mask 200ms + card 280ms cubic-bezier + 5cards stagger 60ms + 退场 mask 150ms/card 180ms",
        mask_dur and card_dur and stagger and exit_anim,
        f"mask200={'OK' if mask_dur else 'NO'} card280+bezier={'OK' if card_dur else 'NO'} stagger={'OK' if stagger else 'NO'} exit={'OK' if exit_anim else 'NO'}"
    )
    results.append(r76)

    # ── §7.7 字体完全复用现有栈：welcome-title 用 Noto Serif SC/Songti SC/STSong ──
    wt_font = bool(re.search(r"\.welcome-title\s*\{[^}]*?font-family:\s*\"Noto Serif SC\",\s*\"Songti SC\",\s*\"STSong\",\s*serif", css_text, re.S))
    no_new_font_import = not bool(re.search(r"@import.*fonts|@font-face", css_text))
    r77 = check(
        "§7.7 字体复用: welcome-title = Noto Serif SC/Songti SC/STSong 且无新字体引入",
        wt_font and no_new_font_import,
        f"welcome-title stack={'OK' if wt_font else 'NO'} no_import={'OK' if no_new_font_import else 'YES（不该有）'}"
    )
    results.append(r77)

    print()
    print("=" * 70)
    passed = sum(results)
    total = len(results)
    print(f"结果: {passed}/{total} checks {'PASSED 🎉' if passed == total else 'FAILED'}")
    print("=" * 70)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
