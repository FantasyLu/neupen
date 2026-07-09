"""
平台风格配置模块
全局存储各平台/标签的写作风格描述，供写手和润色师注入提示词
"""

import json
from pathlib import Path
from core.config import DATA_DIR

PLATFORM_STYLES_FILE = DATA_DIR / "platform_styles.json"

# ======================================
# 内置默认风格（首次使用时写入）
# ======================================
DEFAULT_STYLES: dict[str, dict[str, str]] = {
    "起点中文网": {
        "玄幻": (
            "男频玄幻风格：主角拥有逆天资质或金手指，持续升级突破境界；"
            "战斗场景节奏紧凑、爽点密集，要有热血感和压迫感；"
            "世界观宏大，修炼体系明确（斗气、灵气、法力等级分明）；"
            "情节推进快，矛盾冲突直接，避免铺垫过长；"
            "多用短句和感叹词烘托战斗紧张氛围，敌我关系清晰。"
        ),
        "都市": (
            "男频都市风格：主角从弱到强的逆袭成长线，遭受轻视后强力打脸；"
            "节奏明快，矛盾和高潮迭出；涉及商战、医术、武功等能力加持；"
            "人物对话多为强弱对比，彰显主角霸气和自信；"
            "细节描写以财富、地位、权力为核心，体现身份反转的爽感。"
        ),
        "仙侠": (
            "男频仙侠风格：修仙问道，长寿永生为终极目标；"
            "情节围绕机缘、资源、功法、仙器展开；世界观宏大，仙界/魔界/人界层次分明；"
            "充满因果轮回、道法自然的哲学意味；战斗描写恢弘大气，仙术绚烂；"
            "语言古朴典雅，适当引用诗词意境，避免现代口语。"
        ),
        "末世": (
            "末世风格：描写人类文明崩塌后的生存挣扎，硬核设定；"
            "丧尸/变异兽/异能人等要素真实感强；"
            "主角需有生存技能或异能优势，面对资源短缺和团体博弈；"
            "人性阴暗面的刻画（背叛、掠夺）与温情并存；"
            "紧张氛围营造是核心，语言简练直接，少抒情多动作描写。"
        ),
        "科幻": (
            "男频科幻风格：星际战争、机甲对决、未来科技为主要背景；"
            "硬核设定要有说服力，科技细节需前后一致；"
            "主角依靠智慧、能力或特殊机缘不断突破；"
            "宏大宇宙观下个人英雄主义的彰显；"
            "战斗描写震撼宏大，战略级别的对决要有层次感。"
        ),
    },
    "晋江文学城": {
        "古代言情": (
            "晋江古言风格：情感细腻，女主内心独白丰富，人物情绪层次分明；"
            "宫廷或世家背景，充满权谋博弈与情感纠葛；"
            "语言古雅，多用四字词、诗句渲染意境，避免现代白话；"
            "主要冲突为感情线与宫斗/家族线的交织；"
            "适度甜宠与虐心交替，节奏张弛有度，情感逻辑细腻合理。"
        ),
        "现代言情": (
            "晋江现言风格：都市背景，情感真实细腻；"
            "常见类型：先婚后爱、霸道总裁、青梅竹马、商战恋情；"
            "对话自然有张力，内心活动描写占比高；"
            "注重生活细节和氛围营造，有真实的烟火气；"
            "爱情线为核心，辅以事业/成长/家庭支线；"
            "文笔流畅优美，带有女性视角的温柔感和共情力。"
        ),
        "玄幻仙侠": (
            "晋江女频玄幻风格：双强CP，旗鼓相当的爱情博弈；"
            "女主有独立人格和强大实力，不依赖男主；"
            "情感虐点与甜点交织，读者情绪起伏大；"
            "世界观华美瑰丽，仙法神通描写唯美绚烂；"
            "疯批/深情/执念男主是常见CP设计；"
            "人物心理刻画深入，情感逻辑合理细腻，不能只有降智行为。"
        ),
        "悬疑灵异": (
            "晋江悬疑风格：灵异探案、民俗志怪或惊悚推理为核心；"
            "氛围营造是关键，节奏先慢后快，层层剥开真相；"
            "细节埋设伏笔，结尾反转要有说服力；"
            "人物有明显缺陷和成长弧，不是工具人；"
            "恐惧和好奇心并重，避免单纯猎奇，要有情感内核。"
        ),
    },
    "番茄小说": {
        "轻松甜宠": (
            "番茄甜宠风格：轻松明快，节奏快，爽感强；"
            "日常生活化情节，减少过度煽情；"
            "人物可爱讨喜，男女主互动甜蜜自然、有笑点；"
            "语言通俗易懂，短句为主，接地气；"
            "冲突化解迅速，不拖泥带水；"
            "每章必须有明显的情感钩子或甜点，保证读者追更动力。"
        ),
        "无限流": (
            "番茄无限流风格：副本或位面任务为核心结构，规则设定清晰；"
            "生死危机感强烈，每个副本都有新鲜感和独特玩法；"
            "主角靠智慧/能力/系统逐步成长，有明显的能力扩张线；"
            "队友与反派刻画立体，避免工具人；"
            "节奏紧张，爽点在于破解难题和绝地逆袭翻盘。"
        ),
        "都市异能": (
            "番茄都市风格：语言平实、节奏快，面向大众读者；"
            "主角拥有异能或系统，快速成长为顶尖存在；"
            "爽文逻辑：被人看轻→亮实力→打脸→再上一层楼；"
            "减少过于复杂的政治权谋，以打脸和升级为主线；"
            "每章保证足够爽感，不铺垫过长，读者粘性靠强情节钩子维持。"
        ),
    },
    "掌阅": {
        "武侠": (
            "武侠风格：江湖恩怨、快意恩仇为主题；"
            "武功描写具体生动，招式名称古雅有力；"
            "侠义精神贯穿全文，主角行事有原则底线；"
            "江湖势力林立，人物关系盘根错节；"
            "语言文白相间，有传统武侠小说的韵味；"
            "爱情线为辅，家国情怀或个人武道追求为主线。"
        ),
        "历史架空": (
            "历史架空风格：以真实历史为底本，架空改编；"
            "朝堂权谋、帝王将相为核心场景；"
            "细节考究，历史氛围浓厚（官制、礼仪、器物符合时代）；"
            "主角有俯瞰全局的智识优势；"
            "情节跌宕，各方势力制衡的政治博弈是看点；"
            "语言正式，间有典故，增添历史厚重感。"
        ),
    },
}


# ======================================
# 平台标签的7维度风格建议值（1-5）
# 与 PolisherAgent._STYLE_SLIDER_MAP 维度一致：
#   sentence_patterns / vocabulary / narrative_voice / dialogue_style /
#   description_style / rhythm_pacing / emotion_expression
# ======================================
PLATFORM_SLIDER_DEFAULTS: dict[str, dict[str, dict[str, int]]] = {
    "起点中文网": {
        "玄幻": {
            "sentence_patterns": 5,   # 短句为主，热血紧凑
            "vocabulary":        3,   # 雅俗均衡
            "narrative_voice":   3,   # 内外兼顾
            "dialogue_style":    3,   # 对话适中
            "description_style": 3,   # 描写适度
            "rhythm_pacing":     5,   # 节奏极快
            "emotion_expression":3,   # 情感适度外显
        },
        "都市": {
            "sentence_patterns": 4,
            "vocabulary":        2,   # 偏口语
            "narrative_voice":   3,
            "dialogue_style":    4,   # 对话较多，打脸靠对话
            "description_style": 2,   # 描写克制
            "rhythm_pacing":     5,
            "emotion_expression":4,
        },
        "仙侠": {
            "sentence_patterns": 2,   # 长句偏多，古雅绵密
            "vocabulary":        5,   # 高度书面，文白杂糅
            "narrative_voice":   2,   # 保持距离，道法自然
            "dialogue_style":    2,   # 对话较少
            "description_style": 4,   # 描写丰富，仙术绚烂
            "rhythm_pacing":     3,
            "emotion_expression":2,   # 情感含蓄
        },
        "末世": {
            "sentence_patterns": 5,
            "vocabulary":        2,
            "narrative_voice":   3,
            "dialogue_style":    3,
            "description_style": 2,   # 简练直接，少抒情
            "rhythm_pacing":     5,
            "emotion_expression":3,
        },
        "科幻": {
            "sentence_patterns": 4,
            "vocabulary":        3,
            "narrative_voice":   3,
            "dialogue_style":    3,
            "description_style": 4,   # 科技细节丰富
            "rhythm_pacing":     4,
            "emotion_expression":3,
        },
    },
    "晋江文学城": {
        "古代言情": {
            "sentence_patterns": 2,   # 长句偏多，古雅绵密
            "vocabulary":        5,   # 高度书面
            "narrative_voice":   4,   # 贴近女主视角，内心活动多
            "dialogue_style":    3,
            "description_style": 4,   # 氛围渲染充分
            "rhythm_pacing":     3,
            "emotion_expression":4,   # 情感较为直白
        },
        "现代言情": {
            "sentence_patterns": 3,
            "vocabulary":        3,
            "narrative_voice":   4,
            "dialogue_style":    4,   # 对话较多
            "description_style": 3,
            "rhythm_pacing":     3,
            "emotion_expression":4,
        },
        "玄幻仙侠": {
            "sentence_patterns": 3,
            "vocabulary":        4,
            "narrative_voice":   4,
            "dialogue_style":    3,
            "description_style": 4,   # 世界观华美，描写丰富
            "rhythm_pacing":     3,
            "emotion_expression":4,
        },
        "悬疑灵异": {
            "sentence_patterns": 4,
            "vocabulary":        3,
            "narrative_voice":   2,   # 叙述距离较远，氛围克制
            "dialogue_style":    3,
            "description_style": 4,   # 氛围营造靠描写
            "rhythm_pacing":     3,   # 先慢后快
            "emotion_expression":2,   # 克制，靠氛围带情绪
        },
    },
    "番茄小说": {
        "轻松甜宠": {
            "sentence_patterns": 5,   # 短句为主，接地气
            "vocabulary":        1,   # 口语化
            "narrative_voice":   4,
            "dialogue_style":    5,   # 大量对话，甜蜜互动
            "description_style": 2,   # 描写克制
            "rhythm_pacing":     5,
            "emotion_expression":5,   # 情感外露，甜
        },
        "无限流": {
            "sentence_patterns": 4,
            "vocabulary":        2,
            "narrative_voice":   3,
            "dialogue_style":    3,
            "description_style": 3,
            "rhythm_pacing":     5,
            "emotion_expression":3,
        },
        "都市异能": {
            "sentence_patterns": 5,
            "vocabulary":        1,
            "narrative_voice":   3,
            "dialogue_style":    4,
            "description_style": 2,
            "rhythm_pacing":     5,
            "emotion_expression":4,
        },
        "丧尸": {
            "sentence_patterns": 5,
            "vocabulary":        1,
            "narrative_voice":   3,
            "dialogue_style":    3,
            "description_style": 2,
            "rhythm_pacing":     5,
            "emotion_expression":3,
        },
        "异能": {
            "sentence_patterns": 5,
            "vocabulary":        1,
            "narrative_voice":   4,
            "dialogue_style":    4,
            "description_style": 2,
            "rhythm_pacing":     5,
            "emotion_expression":4,
        },
    },
    "掌阅": {
        "武侠": {
            "sentence_patterns": 3,
            "vocabulary":        4,   # 文白相间
            "narrative_voice":   2,
            "dialogue_style":    3,
            "description_style": 3,
            "rhythm_pacing":     4,
            "emotion_expression":2,
        },
        "历史架空": {
            "sentence_patterns": 2,   # 长句，正式
            "vocabulary":        5,
            "narrative_voice":   2,
            "dialogue_style":    2,
            "description_style": 3,
            "rhythm_pacing":     3,
            "emotion_expression":2,
        },
    },
}


def get_platform_slider_defaults(platform: str, tags: list[str]) -> dict[str, int] | None:
    """
    返回指定平台+标签组合的7维度建议值（取多个标签时平均后四舍五入）。
    找不到匹配时返回 None。
    """
    if not platform or not tags:
        return None
    platform_map = PLATFORM_SLIDER_DEFAULTS.get(platform)
    if not platform_map:
        return None

    matched = [platform_map[tag] for tag in tags if tag in platform_map]
    if not matched:
        return None

    # 多标签取平均，四舍五入到最近整数，钳制到[1,5]
    fields = ["sentence_patterns", "vocabulary", "narrative_voice",
              "dialogue_style", "description_style", "rhythm_pacing", "emotion_expression"]
    result = {}
    for f in fields:
        avg = sum(m[f] for m in matched) / len(matched)
        result[f] = max(1, min(5, round(avg)))
    return result


def load_platform_styles() -> dict[str, dict[str, str]]:
    """
    读取平台风格配置。
    首次调用若文件不存在，自动写入默认值。
    """
    if PLATFORM_STYLES_FILE.exists():
        try:
            return json.loads(PLATFORM_STYLES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    # 文件不存在或损坏，写入默认值
    save_platform_styles(DEFAULT_STYLES)
    return dict(DEFAULT_STYLES)


def save_platform_styles(styles: dict[str, dict[str, str]]):
    """将平台风格配置保存到 JSON 文件"""
    PLATFORM_STYLES_FILE.write_text(
        json.dumps(styles, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_style_description(platform: str, tags: list[str]) -> str:
    """
    根据平台名和标签列表，拼合出完整的风格描述字符串。
    返回空字符串表示没有匹配的描述。
    """
    if not platform or not tags:
        return ""
    styles = load_platform_styles()
    platform_styles = styles.get(platform, {})
    parts = []
    for tag in tags:
        desc = platform_styles.get(tag, "")
        if desc:
            parts.append(f"【{tag}】{desc}")
    if not parts:
        return ""
    header = f"目标平台：{platform}，创作标签：{'、'.join(tags)}"
    return header + "\n" + "\n".join(parts)
