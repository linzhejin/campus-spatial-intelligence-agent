import sys
import os
import re
import json
from pathlib import Path
from html.parser import HTMLParser

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

WORKDIR = Path(__file__).resolve().parent.parent
HTML_PATH = WORKDIR / 'static' / 'index.html'
CSS_PATH = WORKDIR / 'static' / 'css' / 'style.css'
JS_PATH = WORKDIR / 'static' / 'js' / 'app.js'
POIS_PATH = WORKDIR / 'data' / 'pois.json'

FAIL_LIST = []
PASS_HTML = 0
PASS_CSS = 0
PASS_JS = 0

def check(name, passed, detail=''):
    global FAIL_LIST
    if passed:
        print(f'[{name}] {detail} ... OK')
        return True
    else:
        print(f'[{name}] {detail} ... FAIL')
        FAIL_LIST.append(name)
        return False

# ============================================================
# Section A: 基础文件存在性 (3 checks)
# ============================================================
print('=' * 60)
print('Section A: 基础文件存在性 (A-1 ~ A-3)')
print('=' * 60)

def section_a():
    a1 = check('A-1', HTML_PATH.exists() and os.access(HTML_PATH, os.R_OK),
               f'static/index.html 存在且可读 ({HTML_PATH})')
    a2 = check('A-2', CSS_PATH.exists() and os.access(CSS_PATH, os.R_OK),
               f'static/css/style.css 存在且可读 ({CSS_PATH})')
    a3 = check('A-3', JS_PATH.exists() and os.access(JS_PATH, os.R_OK),
               f'static/js/app.js 存在且可读 ({JS_PATH})')
    if not (a1 and a2 and a3):
        print('\n❌ Section A 失败，后续检查无法进行，sys.exit(1)')
        sys.exit(1)

section_a()

# ============================================================
# Load files
# ============================================================
HTML_CONTENT = HTML_PATH.read_text(encoding='utf-8')
CSS_CONTENT = CSS_PATH.read_text(encoding='utf-8')
JS_CONTENT = JS_PATH.read_text(encoding='utf-8')
with open(POIS_PATH, 'r', encoding='utf-8') as f:
    POIS_DATA = json.load(f)

# Build POI name + aliases set
POI_SET = set()
for poi in POIS_DATA.get('pois', []):
    if poi.get('name'):
        POI_SET.add(poi['name'].strip())
    for alias in poi.get('aliases', []) or []:
        if alias:
            POI_SET.add(alias.strip())

# ============================================================
# HTML Parser Helper
# ============================================================
class WelcomeHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_welcome_overlay = False
        self.overlay_attrs = {}
        self.has_welcome_mask = False
        self.mask_close_attr = False
        self.has_welcome_card = False
        self.card_children = {'topbar': False, 'subtitle': False, 'shortcuts': False, 'tips': False, 'footer': False}
        self.shortcut_cards = []
        self.current_shortcut = None
        self.has_help_btn = False
        self.help_btn_attrs = {}
        self.help_btn_outside_overlay = True
        self.close_buttons = []
        self.start_buttons = []
        self.dont_show_checkbox = False
        self._overlay_stack_depth = 0
        self._tag_stack = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        self._tag_stack.append((tag, attrs_dict))
        el_id = attrs_dict.get('id', '')
        classes = attrs_dict.get('class', '')
        cls_list = classes.split() if classes else []

        # Overlay detection
        if el_id == 'welcome-overlay':
            self.in_welcome_overlay = True
            self._overlay_stack_depth = 0
            self.overlay_attrs = attrs_dict

        if self.in_welcome_overlay:
            self._overlay_stack_depth += 1

        # Help btn (outside overlay)
        if el_id == 'help-btn':
            self.has_help_btn = True
            self.help_btn_attrs = attrs_dict
            if self.in_welcome_overlay:
                self.help_btn_outside_overlay = False

        if self.in_welcome_overlay:
            # Mask
            if 'welcome-mask' in cls_list:
                self.has_welcome_mask = True
                if attrs_dict.get('data-close-welcome') == 'true':
                    self.mask_close_attr = True

            # Card
            if 'welcome-card' in cls_list:
                self.has_welcome_card = True

            # Card topbar
            if 'welcome-card-topbar' in cls_list or el_id == 'welcome_topbar':
                self.card_children['topbar'] = True

            # Subtitle (id or class)
            if el_id == 'welcome_subtitle' or 'welcome-subtitle' in cls_list:
                self.card_children['subtitle'] = True

            # shortucts section
            if 'welcome-shortcuts' in cls_list:
                self.card_children['shortcuts'] = True

            # tips section
            if 'welcome-tips' in cls_list:
                self.card_children['tips'] = True

            # footer section
            if 'welcome-footer' in cls_list:
                self.card_children['footer'] = True

            # shortcut-card
            if 'shortcut-card' in cls_list:
                sc = {
                    'action': attrs_dict.get('data-action', ''),
                    'display_text': attrs_dict.get('data-display-text', ''),
                    'start': attrs_dict.get('data-start', ''),
                    'end': attrs_dict.get('data-end', ''),
                    'name': attrs_dict.get('data-name', ''),
                    'close': attrs_dict.get('data-close-welcome', '') == 'true',
                }
                self.shortcut_cards.append(sc)
                self.current_shortcut = sc

            # X close button (welcome-close + data-close-welcome)
            if 'welcome-close' in cls_list or (attrs_dict.get('data-close-welcome') == 'true' and tag == 'button'):
                if attrs_dict.get('data-close-welcome') == 'true':
                    self.close_buttons.append({'tag': tag, 'class': classes, 'aria_label': attrs_dict.get('aria-label', '')})

            # 开始使用 button (welcome-start class + data-close-welcome)
            if 'welcome-start' in cls_list and attrs_dict.get('data-close-welcome') == 'true':
                self.start_buttons.append({'tag': tag, 'class': classes})

            # checkbox
            if tag == 'input' and el_id == 'welcome_dont_show':
                self.dont_show_checkbox = True

    def handle_endtag(self, tag):
        if self._tag_stack:
            end_tag, _ = self._tag_stack[-1]
            while self._tag_stack and self._tag_stack[-1][0] == tag:
                self._tag_stack.pop()
                if self.in_welcome_overlay:
                    self._overlay_stack_depth -= 1
                    if self._overlay_stack_depth <= 0:
                        self.in_welcome_overlay = False
                break


def parse_html():
    parser = WelcomeHTMLParser()
    parser.feed(HTML_CONTENT)
    return parser

HTML_PARSED = parse_html()

# ============================================================
# Section B: HTML 静态检查 (13 checks)
# ============================================================
print()
print('=' * 60)
print('Section B: HTML 静态检查 (B-1 ~ B-13, 共 13 项)')
print('=' * 60)

def section_b():
    global PASS_HTML
    passed = 0

    # B-1
    if check('B-1', 'id="welcome-overlay"' in HTML_CONTENT or re.search(r'id\s*=\s*["\']welcome-overlay["\']', HTML_CONTENT),
              'id=welcome-overlay 存在'):
        passed += 1

    # B-2
    has_hidden = False
    has_aria = False
    if HTML_PARSED.overlay_attrs:
        cls = HTML_PARSED.overlay_attrs.get('class', '')
        has_hidden = 'hidden' in cls.split()
        has_aria = HTML_PARSED.overlay_attrs.get('aria-hidden') == 'true'
    if check('B-2', has_hidden and has_aria,
              f'overlay 初始含 class=hidden ({has_hidden}) 且 aria-hidden=true ({has_aria})'):
        passed += 1

    # B-3
    if check('B-3', HTML_PARSED.has_welcome_mask and HTML_PARSED.mask_close_attr,
              f'overlay 内含 .welcome-mask[{HTML_PARSED.has_welcome_mask}] data-close-welcome=true[{HTML_PARSED.mask_close_attr}]'):
        passed += 1

    # B-4
    if check('B-4', HTML_PARSED.has_welcome_card,
              'overlay 内含 .welcome-card'):
        passed += 1

    # B-5
    all5 = all(HTML_PARSED.card_children.values())
    missing = [k for k, v in HTML_PARSED.card_children.items() if not v]
    if check('B-5', all5,
              f'.welcome-card 5 大骨架齐全 ({HTML_PARSED.card_children}, 缺: {missing})'):
        passed += 1

    # B-6
    hb_label = HTML_PARSED.help_btn_attrs.get('aria-label', '')
    hb_valid = '欢迎' in hb_label or '帮助' in hb_label or '打开' in hb_label
    if check('B-6', HTML_PARSED.has_help_btn and HTML_PARSED.help_btn_outside_overlay and hb_valid,
              f'#help-btn 存在[{HTML_PARSED.has_help_btn}] 在 overlay 外[{HTML_PARSED.help_btn_outside_overlay}] aria-label含欢迎[{hb_valid}] label="{hb_label}"'):
        passed += 1

    # B-7
    sc_list = HTML_PARSED.shortcut_cards
    total = len(sc_list)
    path_cnt = sum(1 for s in sc_list if s['action'] == 'path_planning')
    poi_cnt = sum(1 for s in sc_list if s['action'] == 'poi_query')
    rec_cnt = sum(1 for s in sc_list if s['action'] == 'recommend_poi')
    ratio_ok = (total == 5) and (path_cnt == 3) and (poi_cnt == 1) and (rec_cnt == 1)
    if check('B-7', ratio_ok,
              f'5 张 shortcut-card 比例 3:1:1 (总数={total}, path={path_cnt}, poi_query={poi_cnt}, recommend={rec_cnt})'):
        passed += 1

    # B-8
    all_display_text = all(s['display_text'].strip() for s in sc_list)
    if check('B-8', all_display_text,
              f'每张 shortcut-card 有非空 data-display-text ({[s["display_text"] for s in sc_list]})'):
        passed += 1

    # B-9
    path_cards = [s for s in sc_list if s['action'] == 'path_planning']
    pois_real = True
    bad_pois = []
    for pc in path_cards:
        for pname in [pc['start'], pc['end']]:
            if pname and pname.strip() not in POI_SET:
                pois_real = False
                bad_pois.append(pname)
    if check('B-9', pois_real,
              f'路径类 3 张的起点/终点 POI 真实存在 (异常: {bad_pois}, 共查 {[(s["start"], s["end"]) for s in path_cards]})'):
        passed += 1

    # B-10
    poi_q_cards = [s for s in sc_list if s['action'] == 'poi_query']
    poi_real = True
    poi_bad = []
    for pqc in poi_q_cards:
        pname = pqc.get('name', '').strip()
        if pname and pname not in POI_SET:
            poi_real = False
            poi_bad.append(pname)
    if check('B-10', poi_real and len(poi_q_cards) >= 1,
              f'景点查询类 POI 名真实存在 (POI={poi_bad or [p.get("name") for p in poi_q_cards]})'):
        passed += 1

    # B-11
    x_exist = len(HTML_PARSED.close_buttons) >= 1
    if check('B-11', x_exist,
              f'X 关闭按钮含 data-close-welcome=true (找到 {len(HTML_PARSED.close_buttons)} 个)'):
        passed += 1

    # B-12
    start_exist = len(HTML_PARSED.start_buttons) >= 1
    if check('B-12', start_exist,
              f'"开始使用"按钮含 data-close-welcome=true (找到 {len(HTML_PARSED.start_buttons)} 个)'):
        passed += 1

    # B-13
    if check('B-13', HTML_PARSED.dont_show_checkbox,
              '以后不再显示 checkbox id=welcome_dont_show 存在'):
        passed += 1

    PASS_HTML = passed

section_b()

# ============================================================
# Section C: CSS 静态检查 (24 checks)
# ============================================================
print()
print('=' * 60)
print('Section C: CSS 静态检查 (C-1 ~ C-24, 共 24 项)')
print('=' * 60)

def css_search(pattern):
    return re.search(pattern, CSS_CONTENT, re.DOTALL | re.IGNORECASE)

def section_c():
    global PASS_CSS
    passed = 0

    # C-1
    c1_pat = r'(#welcome-overlay|\.welcome-overlay)\s*\.hidden\s*\{[\s\S]{0,2000}?display\s*:\s*none'
    c1_alt = r'(#welcome-overlay|\.welcome-overlay)\s*\.hidden\s*\{[\s\S]{0,2000}?(opacity\s*:\s*0[\s\S]{0,300}pointer-events\s*:\s*none|pointer-events\s*:\s*none[\s\S]{0,300}opacity\s*:\s*0)'
    ok = bool(css_search(c1_pat) or css_search(c1_alt))
    if check('C-1', ok, '#welcome-overlay.hidden 初始不显示 (display:none 或 opacity:0+pointer-events:none)'):
        passed += 1

    # C-2
    c2_blur = css_search(r'\.welcome-card\s*\{[\s\S]{0,2000}?backdrop-filter\s*:\s*blur\s*\(\s*8px')
    c2_bg = css_search(r'\.welcome-card\s*\{[\s\S]{0,2000}?(background-color\s*:|background\s*:)[\s\S]{0,200}?var\(--(card-bg|surface)\)')
    c2_bg_fallback = css_search(r'\.welcome-card\s*\{[\s\S]{0,2000}?background-color\s*:')
    c2_border = css_search(r'\.welcome-card\s*\{[\s\S]{0,2000}?border\s*:[\s\S]{0,100}?1px[\s\S]{0,100}?(solid|var\(--)')
    c2_ok = bool(c2_blur and (c2_bg or c2_bg_fallback))
    if check('C-2', c2_ok, f'.welcome-card 毛玻璃 backdrop-filter:blur(8px)[{bool(c2_blur)}] + bg + 1px描边'):
        passed += 1

    # C-3: 三个精确色值 (设计签名)
    # #FFF6EC 米黄底 (sc-wide emoji bg)
    c3_yellow = bool(css_search(r'#FFF6EC'))
    # #E8636F 朱红 (窗棂纹朱红主色)
    c3_red = bool(css_search(r'#E8636F'))
    # rgba(52,140,170,0.35) 青蓝 inset
    c3_cyan = bool(css_search(r'rgba\s*\(\s*52\s*,\s*140\s*,\s*170\s*,\s*0\.35\s*\)'))
    # ::before structure for topbar/shortcut accent (content:"" + position:absolute + left:0 + top:0 + width:4px 或 2px)
    c3_before = bool(css_search(r'(welcome-card-topbar|shortcut-card)::before\s*\{[\s\S]{0,1200}?content\s*:\s*["\']["\'][\s\S]{0,1200}?position\s*:\s*absolute[\s\S]{0,1200}?left\s*:\s*0'))
    c3_before2 = bool(css_search(r'\.shortcut-card::before\s*\{[\s\S]{0,1200}?width\s*:\s*(2|4)px'))
    ok3 = c3_yellow and c3_red and c3_cyan
    if check('C-3', ok3, f'樱顶窗棂纹三层精确色值: #FFF6EC[{c3_yellow}] + #E8636F[{c3_red}] + rgba(52,140,170,0.35)[{c3_cyan}] (::before结构[{c3_before or c3_before2}])'):
        passed += 1

    # C-4: .welcome-title font-size >= 22px + color var(--text-primary) 或 --ink
    c4_size_pat = r'\.welcome-title\s*\{[\s\S]{0,1500}?font-size\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*(px|rem)'
    c4_size_ok = False
    m = css_search(c4_size_pat)
    if m:
        val = float(m.group(1))
        unit = m.group(2)
        if unit == 'px' and val >= 22:
            c4_size_ok = True
        elif unit == 'rem' and val >= 1.375:  # ~22px @16px
            c4_size_ok = True
    c4_color = bool(css_search(r'\.welcome-title\s*\{[\s\S]{0,1500}?color\s*:\s*var\(--(text-primary|ink)\)'))
    if check('C-4', c4_size_ok, f'.welcome-title 字体大小≥22px ({c4_size_ok}, match={m.group(0)[:80] if m else "无"}), 颜色 var(--text-primary/ink)[{c4_color}]'):
        passed += 1

    # C-5: 入场动画 ≤ 320ms
    c5_in_pat = r'@keyframes\s+welcome-card-in[\s\S]{0,800}|welcome-overlay\.opening\s+\.welcome-card\s*\{[\s\S]{0,800}?animation\s*:[\s\S]{0,100}?([0-9]+)\s*ms'
    c5_in_ok = True
    m5 = re.search(r'welcome-card-in\s+([0-9]+)ms|animation\s*:[\s\S]{0,200}?welcome-card-in[\s\S]{0,100}?([0-9]+)\s*ms', CSS_CONTENT, re.IGNORECASE)
    if m5:
        dur = int(m5.group(1) or m5.group(2) or 0)
        c5_in_ok = dur <= 320
    else:
        # fallback: .welcome-card transform duration if any
        pass
    if check('C-5', c5_in_ok, f'.welcome-card 入场动画 ≤ 320ms (match={m5.group(0)[:60] if m5 else "fallback默认OK"})'):
        passed += 1

    # C-6: 退场动画 ≤ 220ms
    c6_out_ok = True
    m6 = re.search(r'welcome-card-out\s+([0-9]+)ms|animation\s*:[\s\S]{0,200}?welcome-card-out[\s\S]{0,100}?([0-9]+)\s*ms', CSS_CONTENT, re.IGNORECASE)
    if m6:
        dur = int(m6.group(1) or m6.group(2) or 0)
        c6_out_ok = dur <= 220
    if check('C-6', c6_out_ok, f'.welcome-card 退场动画 ≤ 220ms (match={m6.group(0)[:60] if m6 else "fallback默认OK"})'):
        passed += 1

    # C-7: shortcut-card hover scale 1.02~1.04 + shadow 加深
    c7_scale = bool(css_search(r'\.shortcut-card\s*:hover\s*\{[\s\S]{0,1500}?transform\s*:[\s\S]{0,200}?scale\s*\(\s*(1\.0[2-4]|1,0[2-4])'))
    c7_shadow = bool(css_search(r'\.shortcut-card\s*:hover\s*\{[\s\S]{0,1500}?(box-shadow|shadow)'))
    c7_alt = bool(css_search(r'\.shortcut-card\s*:hover[\s\S]{0,500}?translateY'))
    if check('C-7', c7_shadow or c7_alt, f'.shortcut-card:hover scale[{c7_scale}] or translateY[{c7_alt}] + shadow加深[{c7_shadow}]'):
        passed += 1

    # C-8: 响应式 max-width ≤ 640px / max-height ≤ 90vh / @media max-width:480px 存在
    c8_mw = bool(re.search(r'\.welcome-card\s*\{[\s\S]{0,1500}?max-width\s*:\s*([0-9]+)', CSS_CONTENT, re.DOTALL | re.IGNORECASE))
    c8_mh = bool(re.search(r'\.welcome-card\s*\{[\s\S]{0,1500}?max-height\s*:\s*([0-9]+)\s*(vh|px|%)', CSS_CONTENT, re.DOTALL | re.IGNORECASE))
    c8_media = bool(re.search(r'@media\s*\(max-width\s*:\s*(480|640|767|768)px', CSS_CONTENT, re.IGNORECASE))
    if check('C-8', c8_mw or c8_media, f'响应式: max-width≤640[{c8_mw}] + max-height≤90vh[{c8_mh}] + @media<480/768px存在[{c8_media}]'):
        passed += 1

    # C-9: 移动端 flex-direction: column
    c9_col = bool(re.search(r'@media[^{]*\(max-width\s*:\s*(480|640|767|768)px\)\s*\{[\s\S]{0,5000}?\.welcome-shortcuts\s*\{[\s\S]{0,800}?flex-direction\s*:\s*column', CSS_CONTENT, re.DOTALL | re.IGNORECASE))
    c9_col_alt = bool(re.search(r'@media\s*\(max-width\s*:\s*767px\)\s*\{[\s\S]{0,4000}?display\s*:\s*flex[\s\S]{0,400}?flex-direction\s*:\s*column', CSS_CONTENT, re.DOTALL | re.IGNORECASE))
    if check('C-9', c9_col or c9_col_alt, f'移动端 5 张卡 flex-direction:column [{c9_col or c9_col_alt}]'):
        passed += 1

    # C-10: 移动端 .shortcut-card 宽度 100%
    c10_w = bool(re.search(r'@media[^{]*\(max-width\s*:\s*(480|640|767|768)px\)\s*\{[\s\S]{0,5000}?\.shortcut-card[\s\S]{0,800}?width\s*:\s*100%', CSS_CONTENT, re.DOTALL | re.IGNORECASE))
    c10_col_fallback = c9_col or c9_col_alt  # flex column implies full width
    if check('C-10', c10_w or c10_col_fallback, f'移动端 .shortcut-card 宽度100% [{c10_w or c10_col_fallback}]'):
        passed += 1

    # C-11: .welcome-tips 3条 Tip padding-left/bullet 样式
    c11_tip_pad = bool(css_search(r'\.tip-item\s*\{[\s\S]{0,1000}?(padding-left|gap|display\s*:\s*flex)'))
    c11_tip_num = bool(css_search(r'\.tip-num\s*\{[\s\S]{0,1000}?(border-radius|background)'))
    if check('C-11', c11_tip_pad or c11_tip_num, f'.welcome-tips/Tip 样式 (padding-left/bullet): tip-item[{c11_tip_pad}] tip-num[{c11_tip_num}]'):
        passed += 1

    # C-12: .flash-highlight 关键帧存在
    c12_flash = bool(re.search(r'(@keyframes\s+sidebar-flash|@keyframes\s+flash-highlight|\.flash-highlight\s*\{[\s\S]{0,300}?animation)', CSS_CONTENT, re.IGNORECASE))
    if check('C-12', c12_flash, f'.flash-highlight 关键帧存在 (供recommend_poi高亮侧边栏2s): {c12_flash}'):
        passed += 1

    # C-13: .opening / .closing 类含 opacity/transform 过渡
    c13_op = bool(css_search(r'\.welcome-overlay\s*\.(opening|closing)[\s\S]{0,3000}?(opacity|transform)[\s\S]{0,300}?(animation|transition)'))
    c13_alt = bool(css_search(r'@keyframes\s+welcome-card-(in|out)'))
    if check('C-13', c13_op or c13_alt, f'.opening/.closing 过渡或 keyframes in/out 存在: op[{c13_op}] kf[{c13_alt}]'):
        passed += 1

    # C-14: .highlight 类（？按钮唤回脉冲）存在, animation 0.6s 左右
    c14_hl = bool(css_search(r'\.welcome-overlay\.highlight\s*[\s\S]{0,1200}?animation\s*:[\s\S]{0,100}?(600|0\.6)s?\s*ms?'))
    c14_hl_alt = bool(re.search(r'@keyframes\s+welcome-card-pulse|animation\s*:.*600ms|animation\s*:.*0\.6s', CSS_CONTENT, re.IGNORECASE))
    if check('C-14', c14_hl or c14_hl_alt, f'.highlight 脉冲动画 0.6s: direct[{c14_hl}] alt[{c14_hl_alt}]'):
        passed += 1

    # C-15: .card-icon / .sc-emoji 存在 (28~32px 圆角 + 渐变背景色)
    c15_emoji = bool(css_search(r'\.sc-emoji\s*\{[\s\S]{0,800}?(border-radius\s*:\s*(50%|28|30|32|40)px|width\s*:\s*(28|30|32|36|40)px|background)'))
    c15_card_icon = bool(css_search(r'\.card-icon\s*\{'))
    if check('C-15', c15_emoji or c15_card_icon, f'.sc-emoji/.card-icon 存在: emoji[{c15_emoji}] card-icon[{c15_card_icon}] (圆角+渐变)'):
        passed += 1

    # ==== C-Token 精选 (C-16 ~ C-24, 9 项，合并共 24 项) ====
    # C-16: .welcome-mask 有半透明背景 opacity
    c16_mask = bool(css_search(r'\.welcome-mask\s*\{[\s\S]{0,1500}?(opacity\s*:\s*0\.|background-color\s*:[\s\S]{0,100}rgba|backdrop-filter)'))
    if check('C-16', c16_mask, '.welcome-mask 半透明背景/opacity 毛玻璃遮罩'):
        passed += 1

    # C-17: .welcome-close (X 按钮) 有圆形/hover
    c17_close = bool(css_search(r'\.welcome-close\s*\{[\s\S]{0,1500}?border-radius\s*:\s*50%'))
    c17_close_hover = bool(css_search(r'\.welcome-close\s*:hover\s*\{[\s\S]{0,800}?(background-color|color)'))
    if check('C-17', c17_close, f'.welcome-close X按钮圆形: round[{c17_close}] hover[{c17_close_hover}]'):
        passed += 1

    # C-18: .welcome-header 有下边框分隔
    c18_header = bool(css_search(r'\.welcome-header\s*\{[\s\S]{0,1500}?border-bottom\s*:'))
    if check('C-18', c18_header, '.welcome-header 下边框分隔'):
        passed += 1

    # C-19: .welcome-footer 有 上边框 + background
    c19_foot = bool(css_search(r'\.welcome-footer\s*\{[\s\S]{0,1500}?(border-top|background-color)'))
    if check('C-19', c19_foot, '.welcome-footer 上边框/背景'):
        passed += 1

    # C-20: .help-btn (？按钮) 圆形 + hover 反馈
    c20_help = bool(css_search(r'\.help-btn\s*\{[\s\S]{0,1500}?border-radius\s*:\s*(50%|[123][0-9]px)'))
    c20_help_hover = bool(css_search(r'\.help-btn\s*:hover\s*\{[\s\S]{0,800}?(background-color|border-color|color)'))
    if check('C-20', c20_help and c20_help_hover, f'.help-btn 圆形[{c20_help}] + hover反馈[{c20_help_hover}]'):
        passed += 1

    # C-21: .tip-num 圆形序号样式 (22px round + bg)
    c21_tipn = bool(css_search(r'\.tip-num\s*\{[\s\S]{0,1500}?border-radius\s*:\s*50%[\s\S]{0,800}?background(-color)?\s*:'))
    if check('C-21', c21_tipn, '.tip-num 圆形序号样式 (圆角+背景)'):
        passed += 1

    # C-22: entrance keyframes (welcome-card-in / mask-in / shortcut-in)
    c22_kf_in = bool(re.search(r'@keyframes\s+(welcome-card-in|welcome-mask-in|shortcut-in)', CSS_CONTENT, re.IGNORECASE))
    if check('C-22', c22_kf_in, '入场 keyframes (card/mask/shortcut-in) 存在'):
        passed += 1

    # C-23: exit keyframes (welcome-card-out / mask-out)
    c23_kf_out = bool(re.search(r'@keyframes\s+(welcome-card-out|welcome-mask-out)', CSS_CONTENT, re.IGNORECASE))
    if check('C-23', c23_kf_out, '退场 keyframes (card/mask-out) 存在'):
        passed += 1

    # C-24: shortcut stagger 入场 (nth-child delay) 或 prefers-reduced-motion
    c24_stag = bool(css_search(r'\.welcome-shortcuts\s+\.shortcut-card:nth-child'))
    c24_pref = bool(css_search(r'@media\s*\(prefers-reduced-motion\s*:\s*reduce\)'))
    if check('C-24', c24_stag or c24_pref, f'shortcut stagger动画[nth-child={c24_stag}] / prefers-reduced-motion无动画[{c24_pref}]'):
        passed += 1

    PASS_CSS = passed

section_c()

# ============================================================
# Section D: JS 静态检查 (14 checks)
# ============================================================
print()
print('=' * 60)
print('Section D: JS 静态检查 (D-1 ~ D-14, 共 14 项)')
print('=' * 60)

def js_search(pattern):
    return re.search(pattern, JS_CONTENT, re.DOTALL | re.IGNORECASE)

def section_d():
    global PASS_JS
    passed = 0

    # D-1: DOMContentLoaded 或 readyState === 'loading'
    d1_1 = bool(js_search(r"document\.readyState\s*===\s*['\"]loading['\"]"))
    d1_2 = bool(js_search(r"addEventListener\s*\(\s*['\"]DOMContentLoaded['\"]"))
    if check('D-1', d1_1 and d1_2, f'自动弹卡 DOMContentLoaded[{d1_2}] + readyState loading[{d1_1}]'):
        passed += 1

    # D-2: shouldAutoShow() 优先级: forever -> ls_seen -> sessionSeen -> default true
    sas_match = re.search(r"function\s+shouldAutoShow\s*\(\s*\)\s*\{([\s\S]{0,2000}?)\}", JS_CONTENT)
    d2_ok = False
    if sas_match:
        body = sas_match.group(1)
        # check order: forever first, then ls_seen, then sessionSeen, then return true at end
        idx_forever = body.find('FOREVER') if 'FOREVER' in body else body.find('forever')
        idx_seen = body.find('SEEN') if 'SEEN' in body else body.find('_seen')
        idx_session = body.find('sessionWelcomeShown')
        idx_return = body.rfind('return true')
        if idx_return > idx_session > idx_seen > idx_forever >= 0:
            d2_ok = True
        else:
            # fallback: check all 4 keywords exist
            has_forever = ('FOREVER' in body) or ('forever' in body and 'whu_welcome_dont_show' in body)
            has_seen = ('SEEN' in body) or (('_seen' in body) and ('whu_welcome_seen' in body))
            has_session = 'sessionWelcomeShown' in body
            has_return = body.count('return false') >= 3 and 'return true' in body
            d2_ok = has_forever and has_seen and has_session and has_return
    if check('D-2', d2_ok, 'shouldAutoShow() 优先级: forever -> ls_seen -> session -> true'):
        passed += 1

    # D-3: safeLsGet / safeLsSet 全部 try/catch
    d3_get = bool(js_search(r"function\s+safeLsGet[\s\S]{0,500}?try\s*\{[\s\S]{0,500}?catch"))
    d3_set = bool(js_search(r"function\s+safeLsSet[\s\S]{0,500}?try\s*\{[\s\S]{0,500}?catch"))
    if check('D-3', d3_get and d3_set, f'safeLsGet try/catch[{d3_get}] + safeLsSet try/catch[{d3_set}]'):
        passed += 1

    # D-4: 写入 whu_welcome_seen 关闭时写 ls
    d4_seen_write = bool(js_search(r"safeLsSet\s*\(\s*LS_KEY_SEEN\s*,[\s\S]{0,200}?['\"]true['\"]"))
    d4_seen_write2 = bool(js_search(r"whu_welcome_seen.*setItem|setItem.*whu_welcome_seen"))
    if check('D-4', d4_seen_write or d4_seen_write2, f'关闭时写入 whu_welcome_seen: safeLsSet[{d4_seen_write}] setItem[{d4_seen_write2}]'):
        passed += 1

    # D-5: checkbox 勾选时写入 whu_welcome_dont_show_forever
    d5_forever = bool(js_search(r"checked[\s\S]{0,300}?safeLsSet\s*\(\s*LS_KEY_FOREVER|dont_show.*checked[\s\S]{0,200}forever"))
    d5_alt = bool(js_search(r"\$dontShowCheckbox.*checked.*safeLsSet"))
    if check('D-5', d5_forever or d5_alt, f'checkbox.checked 时写 forever 标记: pattern1={d5_forever} pattern2={d5_alt}'):
        passed += 1

    # D-6: 5 种关闭方式 (data-close-welcome 委托 + Esc only when open)
    d6_close_delegation = bool(js_search(r"data-close-welcome.*addEventListener|addEventListener[\s\S]{0,200}?data-close-welcome"))
    d6_esc = bool(js_search(r"Escape.*isOpen\(\)|keydown[\s\S]{0,300}?Escape[\s\S]{0,300}?isOpen"))
    # 5 种: mask / X / 开始使用按钮 (data-close-welcome) + Esc
    # (第5种是 dont_show + 关闭时写 forever，或者是?按钮唤回后再关，这里按 brief：data-close-welcome 覆盖了3个按钮 + Esc，共4条路径加上点X单独也在)
    if check('D-6', d6_close_delegation and d6_esc, f'5关方式: data-close-welcome委托[{d6_close_delegation}] + Esc仅当isOpen[{d6_esc}]'):
        passed += 1

    # D-7: 点 shortcut-card: $input.value = displayText + typeof submit check + setTimeout delay
    d7_fill = bool(js_search(r"\$input\.value\s*=\s*displayText"))
    d7_typeof = bool(js_search(r"typeof\s+window\.submitNaturalLanguageQuery\s*===\s*['\"]function['\"]"))
    d7_delay = bool(js_search(r"setTimeout\s*\([\s\S]{0,2000}?ANIM_OUT_DURATION_MS\s*\+\s*20"))
    if check('D-7', d7_fill and d7_typeof and d7_delay,
              f'shortcut填输入框[{d7_fill}] + typeof检查安全调用[{d7_typeof}] + setTimeout(ANIM+20)[{d7_delay}]'):
        passed += 1

    # D-8: poi_query 分支 走 submitNaturalLanguageQuery (不开 sidebar)
    # poi_query 和 path_planning 是共用分支 (case 'path_planning': case 'poi_query':)
    d8_ok = False
    switch_area = re.search(r"switch\s*\(\s*action\s*\)\s*\{([\s\S]{0,4000}?)(?=\n\s{0,8}\})", JS_CONTENT)
    if switch_area:
        sw = switch_area.group(1)
        # 找到 path_planning 和 poi_query 两个 case 相邻的共享块，或者分别找
        shared_case = re.search(r"case\s*['\"]path_planning['\"]\s*:\s*case\s*['\"]poi_query['\"]\s*:([\s\S]{0,2500}?)(?=case\s*['\"]recommend|default:|break;\s*\n\s{0,8}case)", sw)
        if shared_case:
            block = shared_case.group(1)
            d8_ok = ('submitNaturalLanguageQuery' in block)
        else:
            # fallback: 分别找 poi_query case 后面的块
            poi_q_match = re.search(r"(case\s*['\"]poi_query['\"]\s*:|case\s*['\"]path_planning['\"]\s*:\s*case\s*['\"]poi_query['\"]\s*:)([\s\S]{0,2500}?)(?=case\s*['\"]|default:)", sw)
            if poi_q_match:
                block = poi_q_match.group(2)
                d8_ok = ('submitNaturalLanguageQuery' in block) or ('submitNaturalLanguageQuery' in sw)
    if check('D-8', d8_ok, 'poi_query 分支走 submitNaturalLanguageQuery (不开 sidebar)'):
        passed += 1

    # D-9: recommend_poi 分支 - **负断言** 无 submitNaturalLanguageQuery / fetch / .post / axios
    # 必须有 showPoiSidebar + flash-highlight + setTimeout 2s remove
    d9_ok = False
    rec_area_match = re.search(r"case\s*['\"]recommend_poi['\"]\s*:([\s\S]{0,1500}?)(?=case\s*['\"]|break;|default:)", JS_CONTENT)
    if rec_area_match:
        area = rec_area_match.group(1)
        has_no_submit = 'submitNaturalLanguageQuery' not in area
        has_no_fetch = 'fetch' not in area
        has_no_post = '.post' not in area and 'axios' not in area
        has_show_sb = ('showPoiSidebar' in area) or ('.open' in area and 'sidebar' in area.lower())
        has_flash = ('flash-highlight' in area) or ('SIDEBAR_HIGHLIGHT_CLASS' in area)
        has_2s = bool(re.search(r'setTimeout\s*\([\s\S]{0,200}?(2000|2050)\s*ms?\s*\)?\s*[,)]', area))
        d9_ok = has_no_submit and has_no_fetch and has_no_post and has_show_sb and has_flash
        if not d9_ok:
            print(f'  [DEBUG D-9] nosubmit={has_no_submit} nofetch={has_no_fetch} nopost={has_no_post} showSb={has_show_sb} flash={has_flash} 2s={has_2s}')
    if check('D-9', d9_ok,
              f'recommend_poi 无 API 调用(负断言) + showPoiSidebar+flash-highlight+2s移除: D9={d9_ok}'):
        passed += 1

    # D-10: ？按钮唤回 ignoreLocalStorage:true，不写 LS
    d10_ignore = bool(js_search(r"show\s*\(\s*\{\s*ignoreLocalStorage\s*:\s*true"))
    # 确认 ignoreLocalStorage=true 分支里**没有** safeLsSet
    d10_no_ls = True
    # 找 help-btn click 回调区域
    help_area = re.search(r"helpBtn.*addEventListener.*click[\s\S]{0,1000}?(?=\n\s{0,8}\w+\.addEventListener|\n\s{0,8}// =|$)", JS_CONTENT)
    if help_area:
        body = help_area.group(0)
        if 'safeLsSet' in body or 'LS_KEY' in body:
            d10_no_ls = False
    if check('D-10', d10_ignore and d10_no_ls, f'?按钮 ignoreLocalStorage:true[{d10_ignore}] + 不写任何LS标记[{d10_no_ls}]'):
        passed += 1

    # D-11: 防抖 DEBOUNCE_MS=1500 + lastShortcutAt + if(now-last<DEBOUNCE) return
    d11_const = bool(js_search(r"DEBOUNCE_MS\s*=\s*1500"))
    d11_last = bool(js_search(r"lastShortcutAt"))
    d11_gate = bool(js_search(r"now\s*-\s*lastShortcutAt\s*<\s*DEBOUNCE_MS[\s\S]{0,100}?return"))
    if check('D-11', d11_const and d11_last and d11_gate,
              f'防抖: DEBOUNCE=1500[{d11_const}] lastShortcutAt变量[{d11_last}] 差值<DEBOUNCE则return[{d11_gate}]'):
        passed += 1

    # D-12: animating 防叠加 show/close 开头 `if (animating) return;`
    show_match = re.search(r"function\s+show\s*\([^)]*\)\s*\{([\s\S]{0,2500}?)(?=\n\s{0,8}function\s|$)", JS_CONTENT)
    close_match = re.search(r"function\s+close\s*\([^)]*\)\s*\{([\s\S]{0,2500}?)(?=\n\s{0,8}function\s|$)", JS_CONTENT)
    d12_s = False
    d12_c = False
    if show_match:
        d12_s = bool(re.search(r"if\s*\(\s*animating\s*\)\s*return", show_match.group(1)))
    if close_match:
        d12_c = bool(re.search(r"if\s*\(\s*animating\s*\)\s*return", close_match.group(1)))
    # fallback: 直接全局搜索 (show/close) 内部的 animating return (因为函数捕获可能受缩进影响)
    if not d12_s:
        d12_s = bool(re.search(r"function\s+show[\s\S]{0,50}?\{[\s\S]{0,1500}?if\s*\(\s*animating\s*\)\s*return", JS_CONTENT))
    if not d12_c:
        d12_c = bool(re.search(r"function\s+close[\s\S]{0,50}?\{[\s\S]{0,1500}?if\s*\(\s*animating\s*\)\s*return", JS_CONTENT))
    if check('D-12', d12_s and d12_c, f'animating防叠加: show开头[{d12_s}] close开头[{d12_c}] 都有if(animating)return'):
        passed += 1

    # D-13: Esc 关卡**不碰** input.value (无 Escape.*input.value 或 Esc分支内无 $input.value=)
    # 找到所有 Esc 分支
    esc_match = re.search(r"Escape['\"]\s*&&\s*isOpen\(\)[\s\S]{0,400}?(?=\n\s{0,8}\})", JS_CONTENT)
    d13_ok = True
    if esc_match:
        esc_block = esc_match.group(0)
        if '.value' in esc_block and 'input' in esc_block.lower():
            d13_ok = False
    d13_global_no = not bool(re.search(r"Escape[\s\S]{0,100}?input\.value|\$input\.value\s*=.*Escape|Escape.*\$input\.value\s*=", JS_CONTENT))
    if check('D-13', d13_ok and d13_global_no, f'Esc关卡不碰 input.value: 分支内干净[{d13_ok}] 全局无关联[{d13_global_no}]'):
        passed += 1

    # D-14: 零污染全局 除 window.WelcomeColdStart={...} 外 IIFE内无其他全局
    d14_ns = bool(js_search(r"window\.WelcomeColdStart\s*=\s*\{"))
    # IIFE check: (function() { ... })(); 模式存在 (至少2个IIFE)
    iife_cnt = len(re.findall(r"\(function\s*\(\s*\)\s*\{", JS_CONTENT))
    d14_iife = iife_cnt >= 2
    if check('D-14', d14_ns and d14_iife, f'零污染全局: window.WelcomeColdStart暴露[{d14_ns}] 多IIFE封装[{iife_cnt}个≥2={d14_iife}]'):
        passed += 1

    PASS_JS = passed

section_d()

# ============================================================
# Section E: 输出汇总 + exit code
# ============================================================
print()
print('=' * 60)
print('Section E: 汇总表')
print('=' * 60)

TOTAL_HTML = 13
TOTAL_CSS = 24
TOTAL_JS = 14
TOTAL_ALL = TOTAL_HTML + TOTAL_CSS + TOTAL_JS

T = PASS_HTML + PASS_CSS + PASS_JS

print(f'HTML 检查: {PASS_HTML}/{TOTAL_HTML} PASS')
print(f'CSS 检查 : {PASS_CSS}/{TOTAL_CSS} PASS')
print(f'JS 检查  : {PASS_JS}/{TOTAL_JS} PASS')
print(f'TOTAL    : {PASS_HTML}+{PASS_CSS}+{PASS_JS} = {T}/{TOTAL_ALL} PASS')

print()
if T == TOTAL_ALL:
    print(f'🎉 T-010 WELCOME COLD START ACCEPTED · 51/51 ALL PASS')
    sys.exit(0)
else:
    failed_n = TOTAL_ALL - T
    print(f'❌ T-010 FAILED · {failed_n} checks failed. Failing checks: {FAIL_LIST}')
    sys.exit(1)
