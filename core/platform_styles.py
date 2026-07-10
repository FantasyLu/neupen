"""
平台风格配置模块
平台风格档案存储在数据库 platform_styles 表中，与 Novel.style_profile 格式完全一致。
对外提供 CRUD 函数，供 UI 管理页和风格设置页调用。
"""

from __future__ import annotations

# 量化维度字段列表（顺序固定，供 UI 和格式化使用）
SLIDER_FIELDS: list[tuple[str, str]] = [
    ("sentence_patterns", "句式长短"),
    ("vocabulary",        "词汇雅俗"),
    ("narrative_voice",   "叙述距离"),
    ("dialogue_style",    "对话密度"),
    ("description_style", "描写密度"),
    ("rhythm_pacing",     "叙事节奏"),
    ("emotion_expression","情感表达方式"),
]

_SLIDER_FIELD_NAMES = [f for f, _ in SLIDER_FIELDS]

# ======================================
# 内置默认风格档案（仅用于首次 seed 数据库）
# 结构：{平台名: {标签名: style_profile_dict}}
# ======================================
DEFAULT_PLATFORM_STYLES: dict[str, dict[str, dict]] = {
    "起点中文网": {
        "玄幻": {
            "overall_style": "男频玄幻：热血升级爽文，金手指驱动，境界体系明确",
            "sentence_patterns": 1, "vocabulary": 3, "narrative_voice": 3,
            "dialogue_style": 3, "description_style": 3, "rhythm_pacing": 5, "emotion_expression": 3,
            "signature_techniques": "主角持续突破境界，战斗密集爽点，敌我对比鲜明",
            "polish_instructions": "①多用短句和感叹词烘托战斗氛围 ②情节推进快，矛盾直接不拖沓 ③境界/功法细节要清晰",
            "custom_notes": "",
        },
        "都市": {
            "overall_style": "男频都市：逆袭打脸，从弱到强，财富权力身份反转",
            "sentence_patterns": 2, "vocabulary": 2, "narrative_voice": 3,
            "dialogue_style": 4, "description_style": 2, "rhythm_pacing": 5, "emotion_expression": 4,
            "signature_techniques": "遭受轻视→亮实力→打脸，强弱对比的对话推进",
            "polish_instructions": "①对话展现主角霸气和自信 ②描写聚焦财富地位权力 ③节奏明快，高潮迭出",
            "custom_notes": "",
        },
        "仙侠": {
            "overall_style": "男频仙侠：修仙问道，古雅典正，道法自然",
            "sentence_patterns": 4, "vocabulary": 5, "narrative_voice": 2,
            "dialogue_style": 2, "description_style": 4, "rhythm_pacing": 3, "emotion_expression": 2,
            "signature_techniques": "因果轮回哲学意味，仙术描写恢弘绚烂，诗词意境点缀",
            "polish_instructions": "①语言古朴典雅，适当引用诗词 ②避免现代口语 ③战斗描写大气，仙术绚烂",
            "custom_notes": "",
        },
        "末世": {
            "overall_style": "末世生存：文明崩塌，硬核设定，人性明暗交织",
            "sentence_patterns": 1, "vocabulary": 2, "narrative_voice": 3,
            "dialogue_style": 3, "description_style": 2, "rhythm_pacing": 5, "emotion_expression": 3,
            "signature_techniques": "生存技能/异能优势，资源短缺与团体博弈，人性阴暗面刻画",
            "polish_instructions": "①语言简练直接，少抒情多动作 ②紧张氛围是核心 ③丧尸/变异兽细节要有真实感",
            "custom_notes": "",
        },
        "科幻": {
            "overall_style": "男频科幻：星际战争机甲对决，宏大宇宙观，个人英雄主义",
            "sentence_patterns": 2, "vocabulary": 3, "narrative_voice": 3,
            "dialogue_style": 3, "description_style": 4, "rhythm_pacing": 4, "emotion_expression": 3,
            "signature_techniques": "硬核科技细节前后一致，战略级对决有层次感",
            "polish_instructions": "①科技设定要有说服力 ②战斗描写震撼宏大 ③宇宙观宏大，彰显个人英雄主义",
            "custom_notes": "",
        },
    },
    "晋江文学城": {
        "古代言情": {
            "overall_style": "晋江古言：情感细腻，宫廷权谋，语言古雅",
            "sentence_patterns": 4, "vocabulary": 5, "narrative_voice": 4,
            "dialogue_style": 3, "description_style": 4, "rhythm_pacing": 3, "emotion_expression": 4,
            "signature_techniques": "宫斗/家族线与感情线交织，甜宠与虐心交替",
            "polish_instructions": "①多用四字词、诗句渲染意境 ②避免现代白话 ③女主内心独白丰富，情绪层次分明",
            "custom_notes": "",
        },
        "现代言情": {
            "overall_style": "晋江现言：都市情感，真实细腻，烟火气与共情力",
            "sentence_patterns": 3, "vocabulary": 3, "narrative_voice": 4,
            "dialogue_style": 4, "description_style": 3, "rhythm_pacing": 3, "emotion_expression": 4,
            "signature_techniques": "生活细节和氛围营造，女性视角温柔感",
            "polish_instructions": "①对话自然有张力 ②内心活动描写占比高 ③有真实的烟火气和共情力",
            "custom_notes": "",
        },
        "玄幻仙侠": {
            "overall_style": "晋江女频玄幻：双强CP，世界观华美，情感虐点甜点交织",
            "sentence_patterns": 3, "vocabulary": 4, "narrative_voice": 4,
            "dialogue_style": 3, "description_style": 4, "rhythm_pacing": 3, "emotion_expression": 4,
            "signature_techniques": "疯批/深情男主，女主独立强大，仙法神通唯美绚烂",
            "polish_instructions": "①人物心理刻画深入 ②情感逻辑合理细腻 ③世界观描写华美瑰丽",
            "custom_notes": "",
        },
        "悬疑灵异": {
            "overall_style": "晋江悬疑：灵异探案，氛围克制，层层揭开真相",
            "sentence_patterns": 2, "vocabulary": 3, "narrative_voice": 2,
            "dialogue_style": 3, "description_style": 4, "rhythm_pacing": 3, "emotion_expression": 2,
            "signature_techniques": "细节埋伏笔，结尾反转有说服力，恐惧与好奇并重",
            "polish_instructions": "①氛围营造是关键，节奏先慢后快 ②避免单纯猎奇，要有情感内核 ③人物有明显缺陷和成长弧",
            "custom_notes": "",
        },
    },
    "番茄小说": {
        "轻松甜宠": {
            "overall_style": "番茄甜宠：轻松明快，爽感强，男女主互动甜蜜有笑点",
            "sentence_patterns": 1, "vocabulary": 1, "narrative_voice": 4,
            "dialogue_style": 5, "description_style": 2, "rhythm_pacing": 5, "emotion_expression": 5,
            "signature_techniques": "每章有明显情感钩子或甜点，冲突化解迅速",
            "polish_instructions": "①语言通俗易懂，短句为主，接地气 ②人物可爱讨喜 ③不拖泥带水",
            "custom_notes": "",
        },
        "无限流": {
            "overall_style": "番茄无限流：副本任务结构，生死危机感，绝地逆袭爽感",
            "sentence_patterns": 2, "vocabulary": 2, "narrative_voice": 3,
            "dialogue_style": 3, "description_style": 3, "rhythm_pacing": 5, "emotion_expression": 3,
            "signature_techniques": "规则设定清晰，每副本独特玩法，破解难题逆袭翻盘",
            "polish_instructions": "①生死危机感强烈 ②队友与反派刻画立体，避免工具人 ③节奏紧张，爽点密集",
            "custom_notes": "",
        },
        "都市异能": {
            "overall_style": "番茄都市异能：扮猪吃虎，快速升级，大众爽文逻辑",
            "sentence_patterns": 1, "vocabulary": 1, "narrative_voice": 3,
            "dialogue_style": 4, "description_style": 2, "rhythm_pacing": 5, "emotion_expression": 4,
            "signature_techniques": "被人看轻→亮实力→打脸→再上一层楼",
            "polish_instructions": "①语言平实，节奏快，面向大众 ②减少复杂政治权谋 ③每章保证爽感，强情节钩子",
            "custom_notes": "",
        },
        "丧尸": {
            "overall_style": "番茄丧尸：末日爆发，系统金手指，极限生存无情杀伐",
            "sentence_patterns": 1, "vocabulary": 1, "narrative_voice": 3,
            "dialogue_style": 3, "description_style": 2, "rhythm_pacing": 5, "emotion_expression": 3,
            "signature_techniques": "空间/异能囤物资，前世仇人开局清理，晶核升级体系",
            "polish_instructions": "①节奏极快，开篇即爆发 ②语言通俗大白话，多用短句 ③杜绝圣母行为，强调爽感对比",
            "custom_notes": "",
        },
        "异能": {
            "overall_style": "番茄异能：觉醒金手指，扮猪吃虎，幽默热血",
            "sentence_patterns": 1, "vocabulary": 1, "narrative_voice": 4,
            "dialogue_style": 4, "description_style": 2, "rhythm_pacing": 5, "emotion_expression": 4,
            "signature_techniques": "垃圾/搞笑异能+隐藏神级，反差爽感，频繁震惊路人",
            "polish_instructions": "①语言极度口语化，夹杂网络热梗 ②整体幽默风趣 ③主角皮与贱，越级反杀密集",
            "custom_notes": "",
        },
    },
    "掌阅": {
        "武侠": {
            "overall_style": "武侠：江湖恩怨快意恩仇，侠义精神，文白相间",
            "sentence_patterns": 3, "vocabulary": 4, "narrative_voice": 2,
            "dialogue_style": 3, "description_style": 3, "rhythm_pacing": 4, "emotion_expression": 2,
            "signature_techniques": "武功招式古雅有力，江湖势力盘根错节，侠义底线贯穿",
            "polish_instructions": "①语言文白相间，有传统武侠韵味 ②武功描写具体生动 ③爱情线为辅，武道追求为主",
            "custom_notes": "",
        },
        "历史架空": {
            "overall_style": "历史架空：朝堂权谋，帝王将相，历史厚重感",
            "sentence_patterns": 4, "vocabulary": 5, "narrative_voice": 2,
            "dialogue_style": 2, "description_style": 3, "rhythm_pacing": 3, "emotion_expression": 2,
            "signature_techniques": "官制礼仪器物符合时代，各方势力制衡，典故增添厚重感",
            "polish_instructions": "①语言正式，间有典故 ②历史氛围浓厚，细节考究 ③主角有俯瞰全局的智识优势",
            "custom_notes": "",
        },
    },
}


# ======================================
# 数据库 CRUD
# ======================================

def get_all_platform_styles() -> dict[str, dict[str, dict]]:
    """
    从数据库读取全部平台风格档案。
    返回 {平台名: {标签名: profile_dict}}
    """
    from core.models import get_db, PlatformStyle
    db = get_db()
    try:
        rows = db.query(PlatformStyle).order_by(PlatformStyle.platform, PlatformStyle.tag).all()
        result: dict[str, dict[str, dict]] = {}
        for row in rows:
            result.setdefault(row.platform, {})[row.tag] = row.to_profile_dict()
        return result
    finally:
        db.close()


def get_platform_slider_defaults(platform: str, tags: list[str]) -> dict | None:
    """
    返回指定平台+标签组合的风格档案。
    多标签时：量化维度取均值四舍五入，文本维度取第一个匹配标签的值。
    找不到匹配时返回 None。
    """
    if not platform or not tags:
        return None
    from core.models import get_db, PlatformStyle
    db = get_db()
    try:
        rows = (db.query(PlatformStyle)
                .filter(PlatformStyle.platform == platform,
                        PlatformStyle.tag.in_(tags))
                .all())
        if not rows:
            return None
        if len(rows) == 1:
            return rows[0].to_profile_dict()
        # 多标签：量化维度取均值，文本维度取第一个
        result = rows[0].to_profile_dict()
        for field in _SLIDER_FIELD_NAMES:
            vals = [getattr(r, field) for r in rows if getattr(r, field) is not None]
            if vals:
                result[field] = max(1, min(5, round(sum(vals) / len(vals))))
        return result
    finally:
        db.close()


def upsert_platform_style(platform: str, tag: str, profile: dict) -> None:
    """新增或更新一个平台标签的风格档案"""
    from core.models import get_db, PlatformStyle
    db = get_db()
    try:
        row = (db.query(PlatformStyle)
               .filter(PlatformStyle.platform == platform, PlatformStyle.tag == tag)
               .first())
        if not row:
            row = PlatformStyle(platform=platform, tag=tag)
            db.add(row)
        for field in ("overall_style", "signature_techniques", "polish_instructions", "custom_notes"):
            setattr(row, field, profile.get(field) or None)
        for field in _SLIDER_FIELD_NAMES:
            v = profile.get(field)
            setattr(row, field, int(v) if isinstance(v, int) else None)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def delete_platform_style(platform: str, tag: str) -> None:
    """删除指定平台标签"""
    from core.models import get_db, PlatformStyle
    db = get_db()
    try:
        db.query(PlatformStyle).filter(
            PlatformStyle.platform == platform, PlatformStyle.tag == tag
        ).delete()
        db.commit()
    finally:
        db.close()


def delete_platform(platform: str) -> None:
    """删除整个平台（含所有标签）"""
    from core.models import get_db, PlatformStyle
    db = get_db()
    try:
        db.query(PlatformStyle).filter(PlatformStyle.platform == platform).delete()
        db.commit()
    finally:
        db.close()


def get_platform_names() -> list[str]:
    """返回所有平台名称列表（有序）"""
    from core.models import get_db, PlatformStyle
    db = get_db()
    try:
        rows = db.query(PlatformStyle.platform).distinct().order_by(PlatformStyle.platform).all()
        return [r[0] for r in rows]
    finally:
        db.close()


# ======================================
# 已废弃接口（保留避免旧调用报错）
# ======================================

def load_platform_styles() -> dict[str, dict[str, dict]]:
    """已废弃：请使用 get_all_platform_styles()"""
    return get_all_platform_styles()


def save_platform_styles(styles: dict) -> None:
    """已废弃：请使用 upsert_platform_style()"""
    pass


def get_style_description(platform: str, tags: list[str]) -> str:
    """已废弃：平台风格不再单独注入 LLM"""
    return ""
