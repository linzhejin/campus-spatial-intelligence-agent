import json
import logging
import re
from pathlib import Path
from typing import Literal, Optional

import httpx
from pydantic import BaseModel, ValidationError

from config import DEEPSEEK_API_KEY, OPENAI_BASE_URL, LLM_MODEL, WHU_POIS

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

FEW_SHOT_EXAMPLES = [
    (
        "从牌坊到樱顶",
        '{"task_type":"path_planning","start":{"name":"牌坊","type":"poi"},"end":{"name":"樱顶","type":"poi"},"constraints":{"distance":"medium","slope":"normal","scenery":"normal"},"weights":null,"input_method":"nl","ambiguity":null}',
    ),
    (
        "从牌坊到樱顶，避开陡坡",
        '{"task_type":"path_planning","start":{"name":"牌坊","type":"poi"},"end":{"name":"樱顶","type":"poi"},"constraints":{"distance":"medium","slope":"avoid","scenery":"normal"},"weights":{"distance":0.2,"slope":0.6,"scenery":0.2},"input_method":"nl","ambiguity":null}',
    ),
    (
        "我第一次来武大，想看樱花和老图书馆，但膝盖不好",
        '{"task_type":"path_planning","start":null,"end":null,"constraints":{"distance":"medium","slope":"avoid","scenery":"high"},"weights":{"distance":0.1,"slope":0.5,"scenery":0.4},"input_method":"nl","ambiguity":"请指定起点"}',
    ),
    (
        "从梅园到桂园，最短路径",
        '{"task_type":"path_planning","start":{"name":"梅园","type":"poi"},"end":{"name":"桂园","type":"poi"},"constraints":{"distance":"short","slope":"normal","scenery":"normal"},"weights":{"distance":0.8,"slope":0.1,"scenery":0.1},"input_method":"nl","ambiguity":null}',
    ),
    (
        "带朋友逛，从教五去图书馆，走风景好的路",
        '{"task_type":"path_planning","start":{"name":"教五","type":"poi"},"end":{"name":"总图书馆","type":"poi"},"constraints":{"distance":"medium","slope":"normal","scenery":"high"},"weights":{"distance":0.2,"slope":0.1,"scenery":0.7},"input_method":"nl","ambiguity":null}',
    ),
    (
        "樱顶在哪里",
        '{"task_type":"poi_query","start":{"name":"樱顶","type":"poi"},"end":null,"constraints":{"distance":"medium","slope":"normal","scenery":"normal"},"weights":null,"input_method":"nl","ambiguity":null}',
    ),
    (
        "你能做什么",
        '{"task_type":"help","start":null,"end":null,"constraints":{"distance":"medium","slope":"normal","scenery":"normal"},"weights":null,"input_method":"nl","ambiguity":null}',
    ),
    (
        "推荐几个景点",
        '{"task_type":"help","start":null,"end":null,"constraints":{"distance":"medium","slope":"normal","scenery":"normal"},"weights":null,"input_method":"nl","ambiguity":null}',
    ),
    (
        "今天天气怎么样",
        '{"task_type":"chat","start":null,"end":null,"constraints":{"distance":"medium","slope":"normal","scenery":"normal"},"weights":null,"input_method":"nl","ambiguity":null}',
    ),
]


class PoiRef(BaseModel):
    name: str
    type: Literal["poi", "coord"]
    coordinates: Optional[dict] = None


class Constraints(BaseModel):
    distance: Literal["short", "medium", "relaxed"]
    slope: Literal["avoid", "normal", "any"]
    scenery: Literal["high", "normal", "any"]


class TaskIntent(BaseModel):
    task_type: Literal["path_planning", "poi_query", "help", "unknown", "chat"]
    start: Optional[PoiRef] = None
    end: Optional[PoiRef] = None
    constraints: Constraints
    weights: Optional[dict] = None
    input_method: Literal["nl", "shortcut", "map_click"] = "nl"
    ambiguity: Optional[str] = None
    weight_source: Optional[Literal["explicit_nl", "shortcut", "default"]] = None


_system_prompt_cache = None


def _load_system_prompt() -> str:
    global _system_prompt_cache
    if _system_prompt_cache is not None:
        return _system_prompt_cache
    prompt_path = PROMPTS_DIR / "parse_system.txt"
    with open(prompt_path, "r", encoding="utf-8") as f:
        template = f.read()
    poi_list = json.dumps(
        [{"name": k, "type": v["type"], "desc": v.get("desc", "")} for k, v in WHU_POIS.items()],
        ensure_ascii=False,
    )
    _system_prompt_cache = template.replace("{poi_list_json}", poi_list).replace("{poi_count}", str(len(poi_list)))
    return _system_prompt_cache


def _build_messages(query: str, context: Optional[dict] = None) -> list[dict]:
    system_prompt = _load_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]

    for shot_query, shot_output in FEW_SHOT_EXAMPLES:
        messages.append({"role": "user", "content": shot_query})
        messages.append({"role": "assistant", "content": shot_output})

    if context:
        messages.append({"role": "user", "content": f"上下文: {json.dumps(context, ensure_ascii=False)}\n当前查询: {query}"})
    else:
        messages.append({"role": "user", "content": query})

    return messages


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("无法从响应中提取 JSON", text, 0)


def _fallback_task_intent(query: str) -> TaskIntent:
    return TaskIntent(
        task_type="path_planning",
        start=None,
        end=None,
        constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
        weights=None,
        input_method="nl",
        ambiguity="解析失败，请尝试更明确的描述",
    )


def parse_query(query: str, context: Optional[dict] = None) -> TaskIntent:
    if not DEEPSEEK_API_KEY:
        logger.error("DEEPSEEK_API_KEY 未配置，使用兜底解析")
        return _fallback_task_intent(query)

    try:
        from openai import OpenAI
    except ImportError:
        logger.error("openai SDK 未安装")
        return _fallback_task_intent(query)

    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=OPENAI_BASE_URL,
        timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
    )

    messages = _build_messages(query, context)

    last_error = None
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                temperature=0.1,
                max_tokens=512,
            )
            raw_text = response.choices[0].message.content
            data = _extract_json(raw_text)
            return _t011_post_process(_annotate_weight_source(TaskIntent(**data)), query, context)
        except ValidationError as e:
            last_error = e
            logger.warning(f"Pydantic 校验失败 (attempt {attempt + 1}): {e}")
        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(f"JSON 解析失败 (attempt {attempt + 1}): {e}")
        except Exception as e:
            last_error = e
            logger.warning(f"LLM 调用失败 (attempt {attempt + 1}): {type(e).__name__}: {e}")

        if attempt < 2:
            messages.append({
                "role": "user",
                "content": "上一次输出格式有误，请重新输出正确的 JSON 格式。",
            })

    logger.error(f"解析全部失败，使用兜底方案。最后错误: {last_error}")
    return _t011_post_process(_annotate_weight_source(_fallback_task_intent(query)), query, context)


def _annotate_weight_source(intent: TaskIntent) -> TaskIntent:
    """根据输入来源和 weights 状态，打标 weight_source 字段（DEC-013）。

    优先级规则（架构审核 N6）：
      - input_method=shortcut                  → shortcut     （快捷按钮预设）
      - input_method=nl 且 weights != null     → explicit_nl  （NL 明确表达偏好）
      - input_method=nl 且 weights == null     → default      （无偏好，用默认权重）
    """
    if intent.input_method == "shortcut":
        intent.weight_source = "shortcut"
    elif intent.input_method == "nl":
        if intent.weights is not None:
            intent.weight_source = "explicit_nl"
        else:
            intent.weight_source = "default"
    else:
        intent.weight_source = "default"
    return intent


# ====== T-011 append-only 扩展：验收 5~11 辅助函数 ======
# 不动原 parse_query 函数体的 LLM 调用逻辑，新增规则兜底 + 后处理

DEFAULT_CONSTRAINTS_DICT = {
    "distance": "medium",
    "slope": "normal",
    "scenery": "normal",
}

UNKNOWN_GUIDE_TEXT = (
    "抱歉，我只能回答武大校园内的路径规划和景点信息查询问题哦。"
    "可以告诉我你想从哪走到哪，或者问「樱顶在哪」查询景点介绍~"
)

HELP_GUIDE_AMBIGUITY = None

# 从 WHU_POIS 提取 POI 名称 + 别名（小写），用于规则匹配
_POI_NAMES_SET = set(WHU_POIS.keys())
_POI_NAMES_LOWER = {name.lower(): name for name in WHU_POIS.keys()}

# POI 查询关键词模式（T-011 验收 7）
_POI_QUERY_PATTERNS = [
    r"^(.+?)在(哪|哪里|哪儿|什么地方|哪块)",
    r"^(.+?)介?绍(一下)?$",
    r"^(.+?)是(什么|啥|咋样|怎么样)",
    r"^(.+?)的?(位置|地址|坐标|在哪)",
    r"^找(.+?)$",
    r"^查(一?下)?(.+?)$",
    r"^(.+?)怎么走$",   # "X怎么走" 无起点 → poi_query
    r"^(.+?)怎么去$",   # "X怎么去" 无起点 → poi_query
]

# Help 查询关键词（T-011 验收 8）
_HELP_PATTERNS = [
    r"你能做什么|你会什么|有什么功能|功能介绍|介?绍(一下)?你|怎么(用|玩|操作|使用)",
    r"推?荐(几个|一些|一下)?景点|有哪些(景点|好玩的|好看的|地方)|有什么(景点|好玩的|好看的|地方)",
    r"推荐|help|帮助|功能|怎么用|说明|guide",
    r"武大(有什么|有啥)(景点|好玩的|地方)",
    r"想去赏樱|想去赏花|想去赏景",   # 想赏樱/花/景 → help（推荐赏花地点）
    r"想看.*(?:樱花|最美的景|最美的花|最美的校园)(?!.*路线)",   # 想看风景 → help（除非含路线）
]

# 明显无关的闲聊关键词 —— 命中即 chat（交给 DeepSeek 对话模型闲聊，而非 unknown 报错）
# 注意: 不含 "你好" "谢谢" 等礼貌用语，它们常作为路径 query 的前后缀
_UNRELATED_KEYWORDS = [
    "天气", "下雨", "温度", "湿度", "刮风",
    "吃饭", "餐厅", "外卖", "食堂推荐", "吃什么", "好吃", "美食",
    "酒店", "住宿", "订房", "订酒店",
    "电影", "唱歌", "逛街", "购物",
    "笑话", "故事", "你是谁", "再见",
    "讲个", "写个", "翻译", "代码", "编程",
    "股票", "基金", "理财",
    "高考", "考研", "分数", "分数线", "招生",
]

# 路径语义关键词 — 当 query 同时命中 _UNRELATED_KEYWORDS 和这些词时，
# 说明用户可能在礼貌语或补充说明中提到了无关词，但主体仍是路径请求
_PATH_SEMANTIC_KEYWORDS = [
    "从", "到", "去", "走", "路线", "路径", "怎么走", "避开", "风景", "平坦",
    "最短", "陡坡", "逛", "出发", "规划",
]

# 路径提取时应忽略的非地名前缀（语气词/否定词/修饰词），
# 避免 "想去X" 被误判为 start="想"、"不对去X" 被误判为 start="不对"
_NON_PLACE_PREFIXES = {
    "想", "要", "不", "别", "就", "先", "再", "改", "换",
    "不对", "不是", "不想", "不要", "想要", "打算", "准备",
    "可以", "还能", "还是", "改成", "换成", "然后", "顺便",
}

# 校园闲聊关键词 — 命中则 task_type=chat（不是 unknown）
_CAMPUS_CHAT_KEYWORDS = [
    "樱花", "🌸", "开了吗", "花期", "枫叶", "银杏", "桂花", "梅花",
    "食堂", "好吃", "美食", "餐厅",
    "教学楼", "教室", "自习", "座位",
    "操场", "体育馆", "游泳", "篮球", "足球",
    "校车", "公交", "怎么坐车", "交通",
    "宿舍", "寝室", "住宿",
    "学长", "学姐", "新生", "入学", "报到",
    "社团", "活动", "讲座", "演出",
    "武大", "珞珈山", "校园", "学校",
    "上课", "下课", "课程", "选修",
    "附近", "周边", "购物", "超市",
    "几点", "时间", "开门", "关门", "开放",
    "人多吗", "拥挤", "排队",
    "拍照", "打卡",
    "天气", "热", "冷", "下雨",
    # 礼貌用语/寒暄 —— 走闲聊而非 unknown
    "你好", "您好", "嗨", "哈喽", "hello", "hi", "在吗",
    "谢谢", "感谢", "谢啦", "辛苦了", "早上好", "下午好", "晚上好",
]


def _is_explicit_poi_query(q: str) -> bool:
    """判断 query 是否是明确的 POI 查询模式（"X在哪/是什么/怎么走"等）。

    这些模式优先级高于闲聊检测：即使 X 含"樱花/珞珈山"等闲聊词，
    "樱花大道在哪里"、"珞珈山是什么" 也应判为 poi_query 而非 chat。
    """
    q = q.strip()
    if re.match(r"^(.+?)(?:怎么走|怎么去|去哪|在哪里|在哪儿|在哪)$", q):
        return True
    for pattern in _POI_QUERY_PATTERNS:
        if re.match(pattern, q):
            return True
    return False


def _rule_based_classify(query: str) -> dict:
    """规则优先兜底分类（T-011 验收 7/8/10）。

    当 LLM 未配置 / 调用失败 / 输出不稳定时，
    用关键词规则识别 task_type。返回 dict：
      {
        "task_type": "path_planning" | "poi_query" | "help" | "unknown",
        "start_name": str | None,
        "end_name": str | None,
      }
    """
    q = query.strip()
    q_lower = q.lower()

    # Step 0.5: 校园闲聊检测 — 含校园关键词但不含路径语义，且不是明确的 POI 查询
    has_path_semantics_0 = any(kw in q for kw in _PATH_SEMANTIC_KEYWORDS)
    if not has_path_semantics_0 and not _is_explicit_poi_query(q):
        for kw in _CAMPUS_CHAT_KEYWORDS:
            if kw in q:
                return {
                    "task_type": "chat",
                    "start_name": None,
                    "end_name": None,
                }

    # Step 1: 先匹配明确无关词（天气/股票/笑话等）
    # 命中走 chat（交给 DeepSeek 对话模型闲聊），而非 unknown 报错
    # 但如果 query 同时含路径语义关键词，跳过（礼貌用语 + 路径请求不误判）
    has_path_semantics = any(kw in q for kw in _PATH_SEMANTIC_KEYWORDS)
    if not has_path_semantics:
        for kw in _UNRELATED_KEYWORDS:
            if kw in q:
                return {
                    "task_type": "chat",
                    "start_name": None,
                    "end_name": None,
                }

    # Step 1.5: "X附近有什么" → poi_query（在 help 之前，因为更具体）
    m_nearby = re.match(r"^(.+?)附近有(?:什么|哪些)", q)
    if m_nearby:
        candidate = m_nearby.group(1).strip(" 的地得了吗啊呀你我他她它，。！？、")
        matched_poi = _fuzzy_match_poi_name(candidate)
        if matched_poi:
            return {
                "task_type": "poi_query",
                "start_name": matched_poi,
                "end_name": None,
            }

    # Step 2: 匹配 Help / 功能说明（T-011 验收 8）
    for pattern in _HELP_PATTERNS:
        if re.search(pattern, q, flags=re.IGNORECASE):
            return {
                "task_type": "help",
                "start_name": None,
                "end_name": None,
            }

    # Step 3: 匹配 "我在X" → 声明起点，path_planning
    at_pattern = re.match(r"我?在(.+?)(?:附近|周围)?$", q)
    if at_pattern:
        candidate = at_pattern.group(1).strip(" 的地得了吗啊呀你我他她它，。！？、")
        matched = _fuzzy_match_poi_name(candidate)
        if matched and len(candidate) <= 6:
            return {
                "task_type": "path_planning",
                "start_name": matched,
                "end_name": None,
            }

    # Step 4: 匹配 POI 纯查询（T-011 验收 7）—— 不包含 A→B 路径语义
    # "X怎么走/去哪" 无起点 → poi_query（只有孤立的目的地问路）
    m_x_how = re.match(r"^(.+?)(?:怎么走|怎么去|去哪|在哪里|在哪儿|在哪)$", q)
    if m_x_how:
        candidate = m_x_how.group(1).strip(" 的地得了吗啊呀你我他她它，。！？、")
        # 只有当候选不含路径结构（从/到）且能匹配到单个 POI 时才判为 poi_query
        has_path_structure = any(w in candidate for w in _PATH_SEMANTIC_KEYWORDS)
        if not has_path_structure:
            matched_poi = _fuzzy_match_poi_name(candidate)
            if matched_poi:
                return {
                    "task_type": "poi_query",
                    "start_name": matched_poi,
                    "end_name": None,
                }

    has_path_word = any(w in q for w in ["到", "去", "往", "走", "出发", "从", "路线", "路径"])
    if not has_path_word:
        for pattern in _POI_QUERY_PATTERNS:
            m = re.match(pattern, q)
            if m:
                candidate = m.group(1).strip(" 的地得了吗啊呀你我他她它")
                matched_poi = _fuzzy_match_poi_name(candidate)
                if matched_poi:
                    return {
                        "task_type": "poi_query",
                        "start_name": matched_poi,
                        "end_name": None,
                    }
        # 纯 POI 名（无动词）也视为 poi_query
        pure_name = _fuzzy_match_poi_name(q)
        if pure_name and len(q) <= 8:
            return {
                "task_type": "poi_query",
                "start_name": pure_name,
                "end_name": None,
            }

    # Step 5: 路径提取兜底 — "从X到Y"、"X去Y" 等模式
    path_patterns = [
        re.compile(r"从(.+?)(?:出发|走|去|到)(.+)(?:怎么走|路线|怎么去|的路线)?$"),
        re.compile(r"(.+?)去(.+)(?:怎么走|路线|怎么去)?$"),
        re.compile(r"(.+?)到(.+)(?:怎么走|路线|怎么去)?$"),
    ]
    for pat in path_patterns:
        m = pat.search(q)
        if m:
            start_candidate = m.group(1).strip(" 的地得了吗啊呀你我他她它，。！？、")
            end_candidate = m.group(2).strip(" 的地得了吗啊呀你我他她它，。！？、")
            start_match = _fuzzy_match_poi_name(start_candidate)
            end_match = _fuzzy_match_poi_name(end_candidate)
            if start_match and end_match and start_match != end_match:
                return {
                    "task_type": "path_planning",
                    "start_name": start_match,
                    "end_name": end_match,
                }
            elif start_match and not end_match:
                # 终点未识别但用户明确说了，保留原名（后续 get_poi 找不到时引导）；
                # 但语气词/否定词（如"想去X"的"想"）不保留为地名
                end_name = end_candidate if (
                    end_candidate and len(end_candidate) <= 12
                    and end_candidate not in _NON_PLACE_PREFIXES
                ) else None
                return {
                    "task_type": "path_planning",
                    "start_name": start_match,
                    "end_name": end_name,
                }
            elif end_match and not start_match:
                # 起点未识别但用户明确说了，保留原名（后续 get_poi 找不到时引导）；
                # 但语气词/否定词（如"想去X"的"想"）不保留为地名
                start_name = start_candidate if (
                    start_candidate and len(start_candidate) <= 12
                    and start_candidate not in _NON_PLACE_PREFIXES
                ) else None
                return {
                    "task_type": "path_planning",
                    "start_name": start_name,
                    "end_name": end_match,
                }
            break  # first matching pattern wins

    # Step 5: 其他 — 保留 path_planning 默认（让原 parse_query / LLM 继续处理）
    return {
        "task_type": None,  # None 表示不覆盖，走原逻辑
        "start_name": None,
        "end_name": None,
    }


def _fuzzy_match_poi_name(candidate: str) -> str | None:
    """按字符串包含关系 + 大小写近似匹配 POI 名（规则兜底用，确定性匹配）。

    匹配优先级 (降序):
      1. 精确名称匹配
      2. 精确别名匹配（通过 WHU_POIS 中的 name 字段）
      3. 候选是 name 的前缀 或 name 是候选的前缀
      4. 子串包含
    同优先级内按 name 字母序解耦，保证确定性。
    """
    if not candidate:
        return None
    cand = candidate.strip()
    # 精确匹配名称
    if cand in _POI_NAMES_SET:
        return cand
    # 精确匹配小写名称
    cand_low = cand.lower()
    if cand_low in _POI_NAMES_LOWER:
        return _POI_NAMES_LOWER[cand_low]
    # 分级模糊匹配（确定性排序）
    prefix_matches = []
    substring_matches = []
    for name in sorted(_POI_NAMES_SET):  # 按字母序遍历保证确定性
        if name.startswith(cand) or cand.startswith(name):
            prefix_matches.append(name)
        elif cand in name or name in cand:
            substring_matches.append(name)
    # 前缀匹配优先，然后子串匹配，各内部按字母序
    if prefix_matches:
        return prefix_matches[0]
    if substring_matches:
        return substring_matches[0]
    return None


def _apply_priority_logic(intent: TaskIntent, weight_source_hint: str | None = None) -> TaskIntent:
    """显式打标优先级来源（T-011 验收 6 · 架构审核 N6）。

    优先级顺序：explicit_nl > shortcut > default
    - NL 明确写了偏好（weights != null） → explicit_nl
    - 快捷按钮 mode                     → shortcut
    - 其他 fallback / 无偏好              → default
    """
    if weight_source_hint:
        intent.weight_source = weight_source_hint
        return intent
    # 默认已经由 _annotate_weight_source 打标
    if intent.weight_source is None:
        intent = _annotate_weight_source(intent)
    return intent


def _merge_context_with_intent(intent: TaskIntent, context: dict | None, query: str) -> TaskIntent:
    """多轮上下文承接：本轮缺 start/end 时复用 context 中的起终点（T-011 验收 9）。

    context 结构示例（前端从 localStorage 的 whu_walker:context:{sid} 透传）：
      {
        "start": {"name": "牌坊", "type": "poi"},
        "end":   {"name": "樱顶",  "type": "poi"},
        "constraints": {...},
        "weights": {...},
        "previous_intent": {...TaskIntent snapshot...}
      }
    """
    if not context:
        return intent
    if intent.task_type not in ("path_planning", "poi_query"):
        return intent

    # 缺 start → 补 context.start
    if intent.start is None and isinstance(context.get("start"), dict):
        ctx_start = context["start"]
        if ctx_start.get("name"):
            intent.start = PoiRef(
                name=ctx_start["name"],
                type=ctx_start.get("type", "poi"),
                coordinates=ctx_start.get("coordinates"),
            )
    # 缺 end → 补 context.end（poi_query 不需要 end）
    if intent.task_type == "path_planning" and intent.end is None and isinstance(context.get("end"), dict):
        ctx_end = context["end"]
        if ctx_end.get("name"):
            intent.end = PoiRef(
                name=ctx_end["name"],
                type=ctx_end.get("type", "poi"),
                coordinates=ctx_end.get("coordinates"),
            )
    # 清除已补全字段的 ambiguity 提示
    if intent.start is not None and intent.end is not None and intent.ambiguity in (
        "请指定起点", "请指定终点", "请指定起点和终点"
    ):
        intent.ambiguity = None
    return intent


def _resolve_ambiguity_completion(intent: TaskIntent, query: str, context: dict | None) -> TaskIntent:
    """ambiguity 补全：上轮返回 ambiguity 提示，本轮用户单 POI 名视为补字段（T-011 验收 11）。

    场景：
      - 上轮 ambiguity="请指定起点"，本轮用户回复"牌坊" → start=牌坊，保留 context 中的 end/constraints/weights
      - 上轮 ambiguity="请指定终点"，本轮用户回复"樱顶" → end=樱顶，保留 context 中的 start/constraints/weights
    """
    if not context:
        return intent

    prev = context.get("previous_intent") or {}
    prev_ambi = prev.get("ambiguity") or context.get("last_ambiguity") or ""
    if not prev_ambi:
        return intent

    # 本轮 query 解析出的纯 POI 名
    matched_poi = _fuzzy_match_poi_name(query.strip())
    if not matched_poi:
        return intent

    prev_start = prev.get("start")
    prev_end = prev.get("end")
    prev_constraints = prev.get("constraints") or DEFAULT_CONSTRAINTS_DICT
    prev_weights = prev.get("weights")

    merged = False
    if "请指定起点" in prev_ambi and prev_end is not None:
        # 上轮缺起点，本轮补的是起点
        intent.start = PoiRef(name=matched_poi, type="poi")
        intent.end = PoiRef(
            name=prev_end["name"],
            type=prev_end.get("type", "poi"),
            coordinates=prev_end.get("coordinates"),
        )
        merged = True
    elif "请指定终点" in prev_ambi and prev_start is not None:
        # 上轮缺终点，本轮补的是终点
        intent.start = PoiRef(
            name=prev_start["name"],
            type=prev_start.get("type", "poi"),
            coordinates=prev_start.get("coordinates"),
        )
        intent.end = PoiRef(name=matched_poi, type="poi")
        merged = True

    if merged:
        # 保留上轮 constraints / weights，防止 LLM 重新识别为空
        try:
            intent.constraints = Constraints(**prev_constraints)
        except (ValidationError, TypeError):
            pass
        if prev_weights is not None:
            intent.weights = prev_weights
        intent.ambiguity = None
        intent.task_type = "path_planning"

    return intent


def _t011_post_process(
    intent: TaskIntent,
    query: str,
    context: dict | None,
    shortcut_mode_hint: bool = False,
) -> TaskIntent:
    """T-011 统一后处理入口（append-only 在 parse_query 返回前调用，不修改原 LLM 流程）。

    处理顺序（符合优先级规则）：
      1. 规则兜底分类（验收 7/8/10）—— LLM 误判时强覆盖 task_type
      2. ambiguity 补全（验收 11）—— 用户单 POI 名补缺字段
      3. 多轮上下文承接（验收 9）—— 缺 start/end 从 context 复用
      4. 优先级打标（验收 6）—— weight_source 字段
      5. unknown 兜底文案（验收 10）—— task_type=unknown 时填充引导语
    """
    # 1. 规则分类优先覆盖（LLM 没判对 / fallback 默认 intent 时用规则纠正）
    classified = _rule_based_classify(query)
    if classified["task_type"] == "poi_query":
        intent.task_type = "poi_query"
        intent.start = PoiRef(name=classified["start_name"], type="poi")
        intent.end = None
        intent.ambiguity = None
    elif classified["task_type"] == "help":
        intent.task_type = "help"
        intent.start = None
        intent.end = None
        intent.ambiguity = None
    elif classified["task_type"] == "unknown":
        intent.task_type = "unknown"
        intent.start = None
        intent.end = None
        intent.ambiguity = UNKNOWN_GUIDE_TEXT
    elif classified["task_type"] == "chat":
        intent.task_type = "chat"
        intent.start = None
        intent.end = None
        intent.ambiguity = None
    elif classified["task_type"] == "path_planning":
        # 正则兜底：从 query 中提取到起终点，覆盖 LLM fallback 的空值；
        # 同时纠正 LLM 误判的 task_type（如 "我在牌坊" 被 LLM 判成 poi_query）
        intent.task_type = "path_planning"
        if classified["start_name"]:
            intent.start = PoiRef(name=classified["start_name"], type="poi")
        if classified["end_name"]:
            intent.end = PoiRef(name=classified["end_name"], type="poi")
        # 两个都补全了 → 清除 ambiguity
        if intent.start is not None and intent.end is not None and intent.ambiguity:
            intent.ambiguity = None

    # 2. ambiguity 补全（T-011 验收 11）—— 在 context 合并之前判断是否命中补全语义
    intent = _resolve_ambiguity_completion(intent, query, context)

    # 3. 多轮 context 承接（T-011 验收 9）—— 缺 start/end 时用 context 补齐
    intent = _merge_context_with_intent(intent, context, query)

    # 4. 优先级打标（T-011 验收 6）
    if shortcut_mode_hint:
        intent = _apply_priority_logic(intent, weight_source_hint="shortcut")
    else:
        intent = _apply_priority_logic(intent)

    # 5. unknown 兜底：如果 task_type=unknown 且 ambiguity 空，补引导语
    if intent.task_type == "unknown" and not intent.ambiguity:
        intent.ambiguity = UNKNOWN_GUIDE_TEXT

    # 6. help / unknown / chat 类型的 constraints 保证合法（T-011 验收 12）
    if intent.task_type in ("help", "unknown", "chat"):
        try:
            intent.constraints = Constraints(**DEFAULT_CONSTRAINTS_DICT)
        except (ValidationError, TypeError):
            pass

    return intent
