"""
Stage 0 · 05_TASKS.md 32 Task 快速状态扫描（token 级）
目标：1 分钟内给每个 Task 打 PASS/FAIL/UNKNOWN 状态，输出剩余 Task 清单 + 依赖拓扑 Batch
注意：不验证功能，只验证「核心验收标准 token 是否存在于对应文件」。结果仅供排期参考，真正 Stage 7 要按 05_TASKS 验收标准严格执行。
"""
import sys, re, json, os
from pathlib import Path

try: sys.stdout.reconfigure(encoding='utf-8')
except: pass

WORKDIR = Path(__file__).resolve().parent
PROJECT_DOCS = WORKDIR / 'project-docs'
APP_ROOT = WORKDIR

FILES = {}
def read(path):
    p = WORKDIR / path
    if not p.exists(): return ''
    try: return p.read_text(encoding='utf-8')
    except: return ''

FILES['app.py'] = read('app.py')
FILES['config.py'] = read('config.py')
FILES['requirements'] = read('requirements.txt')
FILES['routes.py'] = read('api/routes.py')
FILES['parser.py'] = read('agents/parser.py')
FILES['explainer.py'] = read('agents/explainer.py')
FILES['parse_prompt'] = read('agents/prompts/parse_system.txt')
FILES['explain_prompt'] = read('agents/prompts/explain_system.txt')
FILES['coord.py'] = read('spatial/coord_transform.py')
FILES['poi.py'] = read('spatial/poi.py')
FILES['network.py'] = read('spatial/network.py')
FILES['routing.py'] = read('spatial/routing.py')
FILES['pois.json'] = read('data/pois.json')
FILES['annotations.json'] = read('data/road_annotations.json')
FILES['html'] = read('static/index.html')
FILES['css'] = read('static/css/style.css')
FILES['appjs'] = read('static/js/app.js')
FILES['configjs'] = read('static/js/config.js')
FILES['manifest'] = read('static/manifest.json')
FILES['sw.js'] = read('static/sw.js')
FILES['validate_osm'] = read('scripts/validate_osm_network.py')
FILES['export_edges'] = read('scripts/export_edges_for_annotation.py')
FILES['csv2json'] = read('scripts/csv_to_json.py')
FILES['decisions'] = read('project-docs/06_DECISIONS.md')
FILES['readme'] = read('README.md')
FILES['qa_report'] = read('project-docs/08_QA_REPORT.md')
FILES['release'] = read('project-docs/09_RELEASE.md')
FILES['test_e2e'] = read('tests/test_end_to_end.py')
FILES['test_multiturn'] = read('tests/test_multiturn.py')
TESTS_EXIST = (WORKDIR / 'tests').exists()

def has(filekey, *patterns):
    txt = FILES.get(filekey, '')
    return all(bool(re.search(p, txt, flags=re.IGNORECASE | re.DOTALL)) for p in patterns)

checks = {}

# ============ Phase 0 ============
checks['T-001'] = ('scripts/validate_osm_network.py', os.path.exists(WORKDIR/'scripts'/'validate_osm_network.py') and has('validate_osm', 'osmnx|coverage_rate|osm_validation_report'))
checks['T-002'] = ('requirements.txt', bool(re.search(r'flask|osmnx|networkx|pydantic|openai', FILES['requirements'], flags=re.I)))

# ============ Phase 1 ============
checks['T-003'] = ('app.py+config.py', has('app.py', r'Flask\(|CORS|app\.run') and has('config.py', r'DEFAULT_WEIGHTS|WHU_BBOX|WEIGHT_BOUNDS'))
checks['T-004'] = ('data/pois.json+config.py', (WORKDIR/'data'/'pois.json').exists() and len(json.loads(FILES['pois.json'] or '{}').get('pois',[]))>=15 and has('config.py', '_load_whu_pois_for_prompt'))
checks['T-005'] = ('prompts 2 files', os.path.exists(WORKDIR/'agents'/'prompts'/'parse_system.txt') and os.path.exists(WORKDIR/'agents'/'prompts'/'explain_system.txt') and has('parse_prompt', 'task_type|ambiguity|weights') and len(re.findall(r'输入：', FILES['parse_prompt']))>=5 and len(re.findall(r'Few-shot', FILES['explain_prompt']+FILES['parse_prompt']))>=2)

# ============ Phase 2 ============
checks['T-006'] = ('coord_transform.py', has('coord.py', r'gcj02_to_wgs84|wgs84_to_gcj02') and has('coord.py', r'def\s+gcj02_to_wgs84|def\s+wgs84_to_gcj02'))
checks['T-007'] = ('network.py', has('network.py', r'load_or_download_network|read_graphml|write_graphml|graph_from_bbox'))
checks['T-008'] = ('poi.py', has('poi.py', r'find_poi|search_pois|list_all_pois|load_pois|aliases|fuzzy'))
checks['T-009'] = ('routing.py', has('routing.py', r'compute_route|resolve_weights|filter_by_constraints|overlap_rate|slope_level.*5|filter_status'))
checks['T-010'] = ('export+csv2json scripts', os.path.exists(WORKDIR/'scripts'/'export_edges_for_annotation.py') and os.path.exists(WORKDIR/'scripts'/'csv_to_json.py') and has('export_edges', r'Folium|folium|csv') and has('csv2json', r'road_annotations\.json|slope_level'))

# ============ Phase 3 ============
checks['T-011'] = ('parser.py', has('parser.py', r'TaskIntent|Pydantic|parse_query|poi_query|help|unknown|Ambiguity|ambiguity') and has('parser.py', r'Literal\[.*path_planning.*poi_query') and bool(re.search(r'task_type.*help|task_type.*unknown', FILES['parser.py'], flags=re.I|re.S)))
checks['T-012'] = ('explainer.py', has('explainer.py', r'generate_explanation|generate_poi_description|LLM|template.*fallback|timeout') and has('explainer.py', r'DEEPSEEK_API_KEY|MAX_EXPLANATION_LENGTH'))
checks['T-013'] = ('prompt cache', has('parser.py', r'_system_prompt_cache|system_prompt_cache') and has('explainer.py', r'_system_prompt_cache|system_prompt_cache'))

# ============ Phase 4 ============
checks['T-014'] = ('routes.py 5 endpoints + poi_query/help/unknown + context merge', has('routes.py', r'/api/parse|/api/route|/api/chat|/api/pois|/api/pois/<name>') and has('routes.py', r'status.*poi_detail|task_type.*poi_query|recommended_pois|features.*列表|status.*help|status.*unknown') and has('routes.py', r"context|ambiguity.*合并|ambiguity.*补全|context.*merge"))
checks['T-015'] = ('network init endpoints', has('routes.py', r'/api/network/init|/api/network/status|_network_initialized|cached|连通性|edge.*数'))

# ============ Phase 5 ============
checks['T-016'] = ('html+manifest+POI sidebar/InfoWindow/help DOM', has('html', r'id="welcome-overlay"|id="help-btn"|welcome-shortcuts|welcome-tips|welcome-footer|AMap\.load|manifest\.json|header|main|footer|poi-sidebar|InfoWindow|从这里出发|到这里去|推荐景点卡片|功能说明|快捷 Chip|example_queries') and os.path.exists(WORKDIR/'static'/'manifest.json') and has('manifest', r'192|512|theme_color|display.*standalone',))
checks['T-017'] = ('app.js 交互 + 冷启动 14.x + POI Marker + InfoWindow按钮 + 3 status UI + ambiguity UI', has('appjs', r'submitNaturalLanguageQuery|/api/chat|/api/parse|polyline|POI.*Marker|showPoiSidebar|从这里出发|到这里去|panTo|候选 POI|多轮.*context|localStorage|场景.*3|poi_detail.*panTo|status.*help.*展开侧边栏|status.*unknown.*引导|ambiguity.*气泡|DEBOUNCE_MS.*1500|Esc.*关卡|localStorage.*QuotaExceededError|sessionWelcomeShown|欢迎卡片.*自动弹出|shortcut.*ignoreLocalStorage'))
checks['T-018'] = ('css 视觉 + sw.js + icons + 冷启动 7.x', has('css', r'style\.css|樱花粉|#E8929C|翡翠绿|#4A7C6F|Noto Serif SC|480px|768px|1024px|1440px|44px|welcome-card.*480×640|welcome-shortcuts.*grid|welcome-mask.*0\.68|樱顶.*窗棂|sc-accent.*--cherry-deep|stagger|prefers-reduced-motion|flash-highlight') and os.path.exists(WORKDIR/'static'/'sw.js') and has('sw.js', r'cache-first|stale-while-revalidate|network-only|Service Worker') and (WORKDIR/'static'/'icons').exists())
checks['T-019'] = ('config.js + 浏览器', os.path.exists(WORKDIR/'static'/'js'/'config.js') and has('configjs', r'AMAP_KEY|API_BASE_URL|DEFAULT_CENTER') and has('html', r'config\.js'))

# ============ Phase 6 ============
checks['T-020'] = ('routing length cap', has('routing.py', r'路径长度上限|length.*上限|最短路径.*×3|2000m|degraded_slope.*上限|filter_status.*degraded_length|max_len.*3'))
checks['T-021'] = ('LLM timeout 5s/10s + APITimeout', (has('parser.py', r'timeout|connect.*5|read.*10|APITimeout|httpx|OpenAI.*timeout') and has('explainer.py', r'timeout|connect.*5|read.*10|APITimeout|超时.*兜底')))
checks['T-022'] = ('candidates 闭环', has('routes.py', r'candidates|候选.*POI|scenery_score|路网距离') and has('appjs', r'candidates.*卡片|候选.*点击.*重新规划|无候选.*提示'))
checks['T-023'] = ('避人流映射', has('parser.py', r'避人流|避开人流|人少.*路|distance.*relaxed|scenery.*any|清静.*路线'))
checks['T-024'] = ('权重优先级 NL>shortcut>default', has('parser.py', r'weight_source|_annotate_weight|优先级') and has('routes.py', r'weight_source') and has('appjs', r'clearShortcutHighlight|addShortcutHighlight'))

# ============ Phase 7 ============
checks['T-025'] = ('road_annotations.json 标注 80%', (WORKDIR/'data'/'road_annotations.json').exists() and len(json.loads(FILES['annotations.json'] or '{}').get('edges',[]))>=80 and json.loads(FILES['annotations.json'] or '{}').get('coverage_rate',0)>=0.8)
checks['T-026'] = ('network.py merge annotations', has('network.py', r'road_annotations\.json|merge.*annotation|slope_level.*json|scenery_level.*json|覆盖率.*80%|degraded.*缺标注|部分路段缺标注数据'))

# ============ Phase 8 ============
checks['T-027'] = ('tests/ 单元测试 3 files', TESTS_EXIST and (WORKDIR/'tests'/'test_routing.py').exists() and (WORKDIR/'tests'/'test_coord_transform.py').exists() and (WORKDIR/'tests'/'test_poi.py').exists())
checks['T-028'] = ('end-to-end 15 query', TESTS_EXIST and (WORKDIR/'tests'/'test_end_to_end.py').exists() and has('test_e2e', 'STANDARD_QUERIES', 'test_task_type_accuracy', 'test_path_connectivity'))
checks['T-029'] = ('multiturn 10 tests', TESTS_EXIST and (WORKDIR/'tests'/'test_multiturn.py').exists() and has('test_multiturn', 'MULTITURN_TEST_CASES', 'test_all_10_cases_defined', 'poi_query'))
checks['T-030'] = ('QA report 8异常+5浏览器', (WORKDIR/'project-docs'/'08_QA_REPORT.md').exists() and has('qa_report', r'8.*异常|5.*浏览器|PWA|附录.*E|不相关问题.*兜底|help.*POI.*闭环'))
checks['T-031'] = ('SLA performance', has('qa_report', r'热启动.*P50.*15|冷启动.*P50.*30|SLA|性能.*测试'))

# ============ Phase 9 ============
checks['T-032'] = ('Render deploy + README DX', (WORKDIR/'render.yaml').exists() or has('readme', r'gunicorn.*app:app|render|Procfile') and has('readme', r'15.*分钟|Hello World|环境变量配置|部署步骤') and has('decisions', r'高德.*Referer|Referer.*白名单'))

# ============ 输出总表 ============
PASS=0; FAIL=0; UNKNOWN=0
remaining=[]
print(f"{'ID':<7} {'CHECK_FILE':<30} {'STATUS':<7}")
print('-'*50)
for tid, (filekey, ok) in checks.items():
    status = '✅ PASS' if ok else '❌ FAIL'
    if ok: PASS+=1
    else:
        FAIL+=1
        remaining.append(tid)
    print(f"{tid:<7} {filekey:<30} {status}")

print()
print(f"扫描汇总: ✅ {PASS} / ❌ {FAIL} / 总 {len(checks)} = {PASS/(len(checks) or 1)*100:.1f}%")
print(f"剩余待办 Task 清单 ({len(remaining)}): {remaining}")

# 按 05_TASKS.md Mermaid 依赖图做拓扑批次（仅 FAIL Task）
BATCHES = [
    ("Batch 1 · 无依赖独立（并行派发）", [t for t in ["T-020","T-021","T-024","T-026"] if t in remaining]),
    ("Batch 2 · 需 routes.js + app.js", [t for t in ["T-022"] if t in remaining]),
    ("Batch 3 · 无依赖测试（并行）", [t for t in ["T-027"] if t in remaining]),
    ("Batch 4 · Batch 3 后测试脚本", [t for t in ["T-028","T-029"] if t in remaining]),
    ("Batch 5 · 报告+部署", [t for t in ["T-030","T-031","T-032"] if t in remaining]),
]
print()
print("剩余 Task 批次（依赖拓扑排序）:")
for bname, tasks in BATCHES:
    if tasks:
        print(f"  {bname}: {tasks}")

with open(WORKDIR/'_tmp_tasks_status.json','w',encoding='utf-8') as f:
    json.dump({"pass_count":PASS,"fail_count":FAIL,"remaining":remaining,"BATCHES":BATCHES,"checks":{k:v[1] for k,v in checks.items()}},f,ensure_ascii=False,indent=2)
