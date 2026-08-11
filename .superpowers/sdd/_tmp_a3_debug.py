import re
from pathlib import Path
import sys
try:
    sys.stdout.reconfigure(encoding='utf-8')
except:
    pass

WORKDIR = Path(__file__).resolve().parent.parent.parent
JS_PATH = WORKDIR / 'static' / 'js' / 'app.js'
JS_CONTENT = JS_PATH.read_text(encoding='utf-8')

iife_start = JS_CONTENT.find('冷启动欢迎卡片（方案 1）交互逻辑')
iife_sub = JS_CONTENT[iife_start:]
fn_open = re.search(r'\(function\s*\(\s*\)\s*\{', iife_sub)
iife_body = iife_sub[fn_open.start():] if fn_open else ''
js_lines = iife_body.split('\n')

print('=== 搜索 IIFE 内部所有 window.*= 匹配，并输出上下文行 ===')
pattern = r'(window\.[A-Za-z0-9_$]+\s*=)'
matches = list(re.finditer(pattern, iife_body))
for idx, m in enumerate(matches, 1):
    pos = m.start()
    lineno = iife_body[:pos].count('\n') + 1
    start_line = max(0, lineno-2)
    end_line = min(len(js_lines), lineno+1)
    print(f'--- Match #{idx} at IIFE internal line {lineno} ---')
    for i in range(start_line, end_line):
        marker = ' >>> ' if i == lineno-1 else '     '
        print(f'{marker}{i+1}: {js_lines[i].rstrip()}')
    print()

print('=== 严格匹配：window.大写字母开头的自定义全局 = 赋值 ===')
strict = []
for m in re.finditer(r'window\.([A-Z][A-Za-z0-9_]*)\s*=', iife_body):
    name = m.group(1)
    lineno = iife_body[:m.start()].count('\n') + 1
    strict.append((name, lineno))
    line = js_lines[lineno-1].strip() if lineno-1 < len(js_lines) else '???'
    print(f'  {name} at IIFE line {lineno}: {line[:120]}')
print()
print(f'严格的「自定义全局赋值」列表 (仅首字母大写): {[s[0] for s in strict]}')
print(f'A3 通过条件：这个列表 == ["WelcomeColdStart"]')
A3 = [s[0] for s in strict] == ['WelcomeColdStart']
print(f'A3: {A3}')
