"""
五大专业 Agent 实现
基于 CrewAI 框架，每个 Agent 有明确的职责、工具和系统提示词

1. 大纲师 Agent  - 从灵感生成完整大纲，拆分卷纲和章纲
2. 人设师 Agent  - 生成结构化人物档案
3. 写手部 Agent  - 逐章生成小说正文
4. 审核师 Agent  - 全面检测内容冲突（核心中的核心）
5. 润色师 Agent  - 文笔润色，降低AI痕迹
"""

import json
import re
from typing import Optional

from core.llm import NovelLLM
from core.memory import MemoryManager
from core.detector import ConflictDetector, ReviewReport


def _safe_json_loads(text: str) -> dict | list:
    """json.loads with json-repair fallback for LLM output."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            return json.loads(repair_json(text))
        except Exception:
            raise


# ======================================
# Agent 1: 大纲师
# ======================================

class OutlineAgent:
    """
    大纲师 Agent
    职责：从一句话灵感生成完整的多层大纲结构
    """

    SYSTEM_PROMPT = """你是一位世界级的小说大纲策划师，专注于中长篇小说的结构设计。

你的核心能力：
- 从一个灵感种子，生长出完整的故事世界
- 设计精妙的三幕结构和多层次冲突
- 布局伏笔和悬念，确保读者持续投入
- 精确控制每章的节奏和信息量

你输出的大纲必须包含：
1. **总大纲**：主题、核心矛盾、故事结构、主角弧光、结局方向
2. **卷大纲**：每卷的主题、核心冲突、人物关系变化
3. **章纲**：每章必须包含以下6个要素：
   - 核心事件（这章发生了什么决定性的事）
   - 主要冲突（内部冲突/外部冲突）
   - 出场人物（本章出场的主要角色）
   - 场景（时间、地点、氛围）
   - 埋下的伏笔（为后文准备的种子）
   - 回收的伏笔（收束之前布局的线索）
   - 情感基调（读者应有的情绪体验）

输出格式：必须是合法的JSON格式。"""

    def __init__(self, novel_id: int, model_id: str = None):
        self.novel_id = novel_id
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.llm = NovelLLM(model_id)

    @staticmethod
    def _build_foreshadowing_schedule_prompt(active_fs: list) -> str:
        """将活跃伏笔列表转为调度提示文本，注入章纲生成 prompt"""
        if not active_fs:
            return ""
        importance_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}
        lines = ["【待回收伏笔调度表（生成章纲时必须安排以下伏笔的回收，不得遗漏）】"]
        for f in active_fs:
            icon = importance_icon.get(f.importance, "🟡")
            deadline = f"最晚第{f.collect_by_chapter}章回收。" if f.collect_by_chapter else "无截止时间要求。"
            desc = f.description or "（无描述）"
            lines.append(
                f"- {icon} [{f.importance}重要] 《{f.name}》（第{f.set_chapter}章埋下）：{desc} {deadline}"
            )
        lines.append(
            "\n要求：在生成的章纲 outline_foreshadowing_collect 字段中，"
            "确保每个有截止时间的伏笔都在其截止章节前被安排回收。"
            "优先安排高重要度伏笔。"
        )
        return "\n".join(lines)

    def generate_full_outline(self, logline: str, genre: str = "",
                               world_setting: str = "",
                               total_chapters: int = 100) -> dict:
        """
        从一句话灵感生成完整大纲
        返回结构化的大纲数据
        """
        # 注入现有活跃伏笔调度信息（若有）
        active_fs = self.memory.global_mem.get_active_foreshadowings()
        fs_schedule_text = self._build_foreshadowing_schedule_prompt(active_fs)
        fs_prefix = f"{fs_schedule_text}\n\n" if fs_schedule_text else ""

        user_prompt = f"""{fs_prefix}请根据以下灵感，为一部{total_chapters}章的{genre}小说生成完整大纲：

**核心灵感：** {logline}

**世界观提示：** {world_setting or '请自由发挥'}

请生成以下结构的JSON数据：
{{
  "total_outline": {{
    "premise": "前提设定",
    "theme": "核心主题",
    "main_conflict": "全书主要矛盾",
    "story_structure": {{
      "act1": "第一幕：开端（约前20%）",
      "act2": "第二幕：发展（约60%）",
      "act3": "第三幕：高潮+结局（约20%）"
    }},
    "protagonist_arc": "主角成长弧光",
    "ending_summary": "结局概要",
    "world_setting": {{
      "基本规则": "世界运转的核心规则",
      "特殊体系": "魔法/科技/武功等特殊体系",
      "社会结构": "社会制度和权力结构",
      "地理环境": "主要地点和地理特征"
    }}
  }},
  "volumes": [
    {{
      "volume_number": 1,
      "title": "卷名",
      "summary": "卷简介",
      "main_conflict": "本卷核心矛盾",
      "arc_goal": "本卷目标主题",
      "start_chapter": 1,
      "end_chapter": 30
    }}
  ],
  "chapters": [
    {{
      "chapter_number": 1,
      "volume_number": 1,
      "title": "章节标题",
      "outline_core_event": "本章核心事件",
      "outline_conflict": "主要冲突（内部/外部）",
      "outline_characters": ["人物A", "人物B"],
      "outline_scene": "场景描述（时间、地点、氛围）",
      "outline_foreshadowing_set": ["埋下的伏笔1", "埋下的伏笔2"],
      "outline_foreshadowing_collect": ["回收的伏笔（如有）"],
      "outline_emotion": "情感基调",
      "outline_ending": "本章结尾方式（悬念/温馨/震撼等）"
    }}
  ]
}}

注意：
- 前10章要重点展示，每章都要有吸引人的钩子
- 伏笔要在合理时机回收，不要留太多没有回收的线索
- 确保每章字数在2000-4000字能写完的量
- 生成至少前20章的详细章纲，其余章节可以简略"""

        response = self.llm.generate(self.SYSTEM_PROMPT, user_prompt, max_tokens=32000)

        # 提取 JSON
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0:
            outline_data = _safe_json_loads(response[json_start:json_end])
            return outline_data
        else:
            raise ValueError(f"大纲生成失败，无法解析JSON：{response[:500]}")

    def refine_chapter_outline(self, chapter_number: int,
                                user_feedback: str) -> dict:
        """
        根据用户反馈调整特定章节的章纲
        """
        chapter = self.memory.global_mem.get_chapter_outline(chapter_number)
        if not chapter:
            raise ValueError(f"章节 {chapter_number} 的章纲不存在")

        global_ctx = self.memory.global_mem.build_global_context()
        active_fs = self.memory.global_mem.get_active_foreshadowings()
        fs_schedule_text = self._build_foreshadowing_schedule_prompt(active_fs)
        fs_block = f"\n{fs_schedule_text}\n" if fs_schedule_text else ""

        user_prompt = f"""当前小说上下文：
{global_ctx}
{fs_block}

当前第{chapter_number}章章纲：
{chapter.to_outline_text()}

用户修改要求：{user_feedback}

请根据用户要求调整章纲，保持与整体大纲的一致性。
输出JSON格式，只包含需要修改的字段：
{{
  "chapter_number": {chapter_number},
  "title": "修改后的标题（如有）",
  "outline_core_event": "...",
  ...其他需要修改的字段
}}"""

        response = self.llm.generate(self.SYSTEM_PROMPT, user_prompt)
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0:
            return _safe_json_loads(response[json_start:json_end])
        return {}

    PARSE_DOCUMENT_PROMPT = """你是一位专业的小说编辑，擅长从自由格式的大纲或设定文档中识别并提取结构化信息。

你的任务：阅读用户提供的文档，识别其中包含的内容（整体大纲、世界观设定、人物档案、章节大纲），并将其结构化输出为 JSON。

输出规则：
- 只提取文档中实际存在的信息，不要凭空编造或补全
- 文档里没有提到的字段留为空字符串或空数组，不要填入
- 输出合法 JSON，不含其他文字

输出格式：
{
  "total_outline": {
    "premise": "前提设定",
    "theme": "核心主题",
    "main_conflict": "全书主要矛盾",
    "protagonist_arc": "主角成长弧光",
    "ending_summary": "结局概要",
    "story_structure": {"act1": "第一幕", "act2": "第二幕", "act3": "第三幕"}
  },
  "world_setting": {
    "键名": "对应的设定内容"
  },
  "characters": [
    {
      "name": "姓名",
      "role": "主角/配角/反派等",
      "age": "",
      "gender": "",
      "personality": "",
      "background": "",
      "appearance": "",
      "motivations": "",
      "relationships": "",
      "is_main": true
    }
  ],
  "chapters": [
    {
      "chapter_number": 1,
      "title": "",
      "outline_core_event": "",
      "outline_conflict": "",
      "outline_scene": "",
      "outline_emotion": ""
    }
  ]
}

如果某一大类（如 characters）文档中完全没有涉及，输出空数组 []。
total_outline 和 world_setting 的字段若文档未提及则留空字符串。"""

    def parse_document(self, document_text: str) -> dict:
        """
        解析用户提供的自由格式大纲/设定文档，识别并提取各类结构化信息

        Returns:
            dict with keys: total_outline, world_setting, characters, chapters
        """
        user_prompt = f"""请解析以下文档，提取其中的结构化信息：

{document_text[:12000]}{"...(内容过长已截断)" if len(document_text) > 12000 else ""}"""

        response = self.llm.generate(
            self.PARSE_DOCUMENT_PROMPT, user_prompt,
            max_tokens=8192, cache_system=False
        )
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            return _safe_json_loads(response[json_start:json_end])
        raise ValueError(f"文档解析返回格式错误：{response[:300]}")

    def generate_chapter_range_outlines(self, start: int, end: int, description: str) -> list[dict]:
        """
        为指定章节范围批量生成章纲。

        Args:
            start: 起始章节号（含）
            end:   结束章节号（含）
            description: 用户对这些章节应完成哪些内容/进展的描述

        Returns:
            list[dict]，每项含 chapter_number / title / outline_core_event 等字段
        """
        global_ctx = self.memory.global_mem.build_global_context()
        active_fs = self.memory.global_mem.get_active_foreshadowings()
        fs_text = self._build_foreshadowing_schedule_prompt(active_fs)
        fs_block = f"\n{fs_text}\n" if fs_text else ""

        ch_count = end - start + 1

        user_prompt = f"""请为第{start}章到第{end}章（共{ch_count}章）设计详细的章节大纲。

【用户对这段剧情的要求】
{description}
{fs_block}
【当前小说背景】
{global_ctx}

请返回一个 JSON 数组，严格包含 {ch_count} 个元素（章节号 {start} 到 {end}），格式如下：
[
  {{
    "chapter_number": {start},
    "title": "章节标题",
    "outline_core_event": "本章最重要的事件（必填）",
    "outline_conflict": "主要冲突或张力",
    "outline_scene": "场景：时间、地点、氛围",
    "outline_emotion": "情感基调",
    "outline_ending": "结尾方式，给下一章留下悬念或钩子"
  }},
  ...
]

设计要求：
- {ch_count} 章之间要有清晰的节奏变化（起伏感），不要每章都是同一种模式
- 章节之间应有因果逻辑，前章的事件引发后章的反应
- 内容方向必须符合用户描述，整体弧线要在这 {ch_count} 章内完整交代
- 只返回 JSON 数组，不要任何其他文字"""

        response = self.llm.generate(
            self.SYSTEM_PROMPT, user_prompt,
            max_tokens=min(3000 + ch_count * 400, 16000)
        )

        arr_start = response.find("[")
        arr_end = response.rfind("]") + 1
        if arr_start >= 0 and arr_end > arr_start:
            result = _safe_json_loads(response[arr_start:arr_end])
            if isinstance(result, list):
                return result
        raise ValueError(f"章纲生成返回格式错误：{response[:300]}")

    def analyze_chapter_consistency(self, chapter_number: int, content: str) -> dict:
        """
        分析章节内容，检测是否需要同步更新大纲或设定。

        Returns:
            {
              "new_characters": [{"name", "role", "personality", "background", "reason"}],
              "character_updates": [{"name", "field", "new_value", "reason"}],
              "outline_updates": [{"field", "current_value", "suggestion", "reason"}],
              "world_setting_updates": [{"key", "value", "reason"}]
            }
        """
        global_ctx = self.memory.global_mem.build_global_context()
        all_chars = self.memory.global_mem.get_all_characters()
        existing_chars = [c.name for c in all_chars]

        # 构建现有人物状态摘要，供 AI 对比
        char_state_lines = []
        for c in all_chars:
            parts = [f"【{c.name}】"]
            if c.current_state:
                parts.append(f"当前状态：{c.current_state[:120]}")
            if c.growth_arc:
                parts.append(f"成长弧光：{c.growth_arc[:80]}")
            if c.abilities:
                parts.append(f"能力：{c.abilities[:80]}")
            char_state_lines.append("  ".join(parts))
        char_state_summary = "\n".join(char_state_lines) or "（无）"

        user_prompt = f"""请仔细阅读第{chapter_number}章正文，与现有大纲、设定和人物档案对照，找出需要新增或更新的内容。

【第{chapter_number}章正文】
{content[:8000]}{"…（已截断）" if len(content) > 8000 else ""}

【现有大纲和设定摘要】
{global_ctx}

【已有人物及当前状态】
{char_state_summary}

请按以下 JSON 格式输出检测结果，只列出章节中实际发生且需要记录的变化，不要虚构：

{{
  "new_characters": [
    {{
      "name": "角色名",
      "role": "主角/配角/反派等",
      "personality": "性格特点",
      "background": "背景信息（从章节推断）",
      "reason": "为什么需要新增"
    }}
  ],
  "character_updates": [
    {{
      "name": "已有人物的姓名（必须在已有人物列表中）",
      "field": "current_state 或 growth_arc 或 abilities 或 relationships 之一",
      "new_value": "更新后的完整内容",
      "reason": "本章中发生了什么导致此变化"
    }}
  ],
  "outline_updates": [
    {{
      "field": "main_conflict 或 protagonist_arc 或 ending_summary 等字段名",
      "current_value": "当前字段值简述",
      "suggestion": "建议添加或修改的内容",
      "reason": "为什么需要更新"
    }}
  ],
  "world_setting_updates": [
    {{
      "key": "设定条目名称",
      "value": "具体设定内容",
      "reason": "章节中揭示了这个新设定"
    }}
  ]
}}

只返回 JSON，不包含其他文字。某类没有需要更新时对应数组留空 []。"""

        response = self.llm.generate(self.SYSTEM_PROMPT, user_prompt, max_tokens=4096)
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            result = _safe_json_loads(response[json_start:json_end])
            # 过滤掉 character_updates 中不在已有人物列表里的条目（防止 AI 乱填）
            if isinstance(result, dict) and "character_updates" in result:
                result["character_updates"] = [
                    u for u in result.get("character_updates", [])
                    if u.get("name") in existing_chars
                ]
            return result
        return {
            "new_characters": [], "character_updates": [],
            "outline_updates": [], "world_setting_updates": []
        }

    def close(self):
        self.memory.close()


# ======================================
# Agent 2: 人设师
# ======================================

class CharacterAgent:
    """
    人设师 Agent
    职责：根据大纲生成结构化的人物档案，检测人设矛盾
    """

    SYSTEM_PROMPT = """你是一位专业的小说人物塑造专家，擅长创造立体、真实、有深度的人物。

你设计人物的原则：
- 每个人物都有独特的内核矛盾（外表与内心的对立）
- 人物的行为必须源于其成长背景和心理创伤
- 主角的弧光必须与故事主题深度契合
- 配角要有自己的动机，不只是主角的工具
- 反派必须有合理的逻辑，让读者能够理解（但不认同）其行为

人设档案必须包含：
- 基本信息（姓名、年龄、外貌）
- 性格特征（表层性格、深层性格、性格矛盾点）
- 背景故事（成长经历、关键事件、心理创伤）
- 能力设定（技能、特长、局限）
- 人际关系（与其他人物的关系网络）
- 成长弧光（全书的性格变化轨迹）
- 说话风格（口头禅、语气、思维方式）

输出格式：合法的JSON格式。"""

    def __init__(self, novel_id: int, model_id: str = None):
        self.novel_id = novel_id
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.llm = NovelLLM(model_id)

    def generate_characters(self, outline_text: str) -> list[dict]:
        """
        根据大纲生成所有主要人物的档案
        """
        user_prompt = f"""根据以下小说大纲，生成所有主要人物的详细档案：

{outline_text}

请生成JSON格式的人物列表：
{{
  "characters": [
    {{
      "name": "人物姓名",
      "aliases": ["别名1", "别名2"],
      "role": "主角/女主/反派/配角",
      "is_main": true,
      "age": "年龄或范围",
      "gender": "性别",
      "appearance": "外貌描述（特征性的，而非完美）",
      "personality": "性格特征（包括优点、缺点、矛盾点）",
      "background": "详细背景故事",
      "abilities": ["能力1", "能力2"],
      "relationships": {{
        "人物B": "关系描述",
        "人物C": "关系描述"
      }},
      "growth_arc": "全书成长轨迹",
      "current_state": "故事开始时的状态",
      "motivations": "核心动机（表层目标+深层渴望）",
      "secrets": "隐藏信息或秘密",
      "speech_patterns": "说话风格和口头禅",
      "behavioral_patterns": "行为习惯和思维方式"
    }}
  ]
}}"""

        response = self.llm.generate(self.SYSTEM_PROMPT, user_prompt, max_tokens=8192)
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0:
            data = _safe_json_loads(response[json_start:json_end])
            return data.get("characters", [])
        return []

    def check_character_consistency(self) -> list[str]:
        """
        检测所有人物设定之间的矛盾和不合理之处
        返回问题列表
        """
        chars = self.memory.global_mem.get_all_characters()
        if len(chars) < 2:
            return []

        chars_text = "\n\n".join([c.to_profile_text() for c in chars])

        user_prompt = f"""请检查以下人物档案之间是否存在设定矛盾或不合理之处：

{chars_text}

检查维度：
1. 人际关系是否双向一致（A说是B的朋友，B是否也有对应描述）
2. 能力设定是否合理（是否有人能力过于全能）
3. 背景故事是否与世界观冲突
4. 年龄与经历是否匹配
5. 不同人物的设定是否雷同（是否有人物缺乏独特性）

以列表形式输出所有问题（每个问题一行），没有问题则输出"无明显矛盾"。"""

        response = self.llm.generate(self.SYSTEM_PROMPT, user_prompt)
        problems = [line.strip() for line in response.split("\n") if line.strip() and line.strip() != "无明显矛盾"]
        return problems

    def update_character_state(self, character_name: str,
                                  chapter_number: int,
                                  state_update: str):
        """
        更新人物当前状态（随剧情发展）
        """
        char = self.memory.global_mem.get_character(character_name)
        if char:
            char.current_state = f"（第{chapter_number}章后）{state_update}"
            self.memory.global_mem.db.commit()

    def close(self):
        self.memory.close()


# ======================================
# Agent 3: 写手部
# ======================================

class WriterAgent:
    """
    写手部 Agent
    职责：根据章纲、人设和历史内容逐章生成小说正文
    严格遵守人设和大纲，不随意偏离剧情
    """

    SYSTEM_PROMPT = """你是一位才华横溢的中文小说作家，擅长创作引人入胜的中长篇小说。

你的写作原则：
- **严格遵守**章纲中的核心事件和主要冲突，不能随意改变
- **保持人设**：每个人物的言行必须符合其档案设定
- **连续性优先**：写作前必须熟读前情，确保时间线、道具状态、人物位置的连贯
- **沉浸式描写**：多用感官细节、心理描写，少用直白叙述
- **节奏控制**：张弛有度，高潮前必须有铺垫，不要平铺直叙

写作规范：
- 对话要体现人物性格，避免口吻雷同
- 动作场面要清晰，空间感要强
- 情感描写要克制，通过细节表现，而非直接说"他很难过"
- 适度使用悬念，让每章结尾都有留人的钩子
- 避免大量重复使用同一个词或句式

输出要求：
- 直接输出小说正文，不要加解释或注释
- 字数控制在章纲要求的范围内（一般2000-4000字）
- 分段合理，对话独占一行"""

    def __init__(self, novel_id: int, model_id: str = None):
        self.novel_id = novel_id
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.llm = NovelLLM(model_id)

    def write_chapter(self, chapter_number: int,
                       word_target: int = 3000,
                       stream_callback=None) -> str:
        """
        生成指定章节的正文

        Args:
            chapter_number: 章节序号
            word_target: 目标字数
            stream_callback: 流式输出回调函数（可选）

        Returns:
            生成的章节正文
        """
        # 获取章节大纲
        chapter = self.memory.global_mem.get_chapter_outline(chapter_number)
        if not chapter:
            raise ValueError(f"第{chapter_number}章的章纲不存在，请先生成章纲")

        # 构建完整写作上下文（三层记忆整合）
        writing_context = self.memory.build_writing_context(chapter_number, chapter)

        # 注入风格档案（若已设置）
        novel = self.memory.global_mem.get_novel()
        style_block = ""
        if novel:
            style_profile = novel.get_style_profile()
            style_desc = novel.writing_style or ""
            if style_profile:
                _label_map = {
                    "overall_style":        "总体风格定位",
                    "sentence_patterns":    "句式特征",
                    "vocabulary":           "词汇风格",
                    "narrative_voice":      "叙述视角风格",
                    "dialogue_style":       "对话特点",
                    "description_style":    "描写特点",
                    "rhythm_pacing":        "节奏与节拍",
                    "emotion_expression":   "情感表达方式",
                    "signature_techniques": "标志性手法",
                    "polish_instructions":  "写作核心指令",
                }
                lines = [
                    f"- {lbl}：{style_profile[k]}"
                    for k, lbl in _label_map.items()
                    if style_profile.get(k)
                ]
                if lines:
                    style_block = (
                        "\n【全书写作风格档案（请严格遵循以保持前后风格一致）】\n"
                        + "\n".join(lines)
                    )
            elif style_desc:
                style_block = f"\n【写作风格要求】\n{style_desc}"

        user_prompt = f"""请根据以下所有资料，写作第{chapter_number}章：《{chapter.title or ''}》

{writing_context}{style_block}

【写作要求】
- 目标字数：约{word_target}字
- 必须完整呈现章纲中的核心事件
- 人物对话和行为必须符合其设定
- 注意与前几章的连贯性
- 章节结尾需要有合适的收束或钩子

请直接开始写作正文，从标题开始："""

        if stream_callback:
            # 流式生成
            content_parts = []
            for text_chunk in self.llm.generate_stream(
                self.SYSTEM_PROMPT, user_prompt, max_tokens=12000
            ):
                content_parts.append(text_chunk)
                stream_callback(text_chunk)
            return "".join(content_parts)
        else:
            # 非流式生成
            return self.llm.generate(
                self.SYSTEM_PROMPT, user_prompt, max_tokens=12000
            )

    def summarize_chapter(self, chapter_number: int, title: str,
                           content: str) -> tuple[str, list[str]]:
        """
        为已写完的章节生成摘要和关键事件列表
        摘要用于后续章节的 recent_context 注入，减少 token 消耗

        Returns:
            (summary: str, key_events: list[str])
        """
        user_prompt = f"""请为以下小说章节生成简短摘要和关键事件列表：

第{chapter_number}章《{title}》

【章节正文】
{content[:4000]}{"...(已截断)" if len(content) > 4000 else ""}

请输出JSON格式：
{{
  "summary": "100-150字的章节摘要，概括核心事件、人物状态变化、结尾钩子",
  "key_events": ["关键事件1", "关键事件2", "关键事件3"]
}}"""

        system = "你是一位专业的小说编辑，擅长提炼章节核心内容。输出合法JSON，不要有其他文字。"
        try:
            response = self.llm.generate(system, user_prompt, max_tokens=512, cache_system=False)
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0:
                data = _safe_json_loads(response[json_start:json_end])
                return data.get("summary", ""), data.get("key_events", [])
        except Exception as e:
            print(f"⚠️ 章节摘要生成失败：{e}")
        return "", []

    def regenerate_section(self, chapter_number: int, section_text: str,
                             instruction: str) -> str:
        """
        重新生成章节中的特定段落
        """
        chapter = self.memory.global_mem.get_chapter_outline(chapter_number)
        global_ctx = self.memory.global_mem.build_global_context()

        user_prompt = f"""相关设定：
{global_ctx[:2000]}

需要重写的段落：
{section_text}

修改要求：{instruction}

请重写这段内容，保持故事连贯性："""

        return self.llm.generate(self.SYSTEM_PROMPT, user_prompt)

    def close(self):
        self.memory.close()


# ======================================
# Agent 4: 审核师
# ======================================

class ReviewerAgent:
    """
    审核师 Agent（核心中的核心）
    职责：对每次生成的内容进行全面检测
    检测维度：设定冲突、OOC、大纲冲突、前后矛盾、逻辑漏洞
    """

    SYSTEM_PROMPT = """你是一位严格的小说质量审核专家，你的职责是守护故事的一致性和逻辑性。

你的审核原则：
- **零容忍主义**：任何与设定矛盾的细节都必须标记，无论多小
- **人设守护者**：人物的每一句话、每一个动作都必须符合其设定
- **时间线侦探**：仔细追踪时间线，确保没有时间悖论
- **逻辑探长**：每个情节都必须有合理的因果关系
- **读者代理人**：从读者角度思考每个可能引发困惑的地方

你的输出必须：
- 精确引用冲突位置的原文
- 明确说明与哪条设定冲突
- 提供至少2个可行的修改方案"""

    def __init__(self, novel_id: int, model_id: str = None):
        self.novel_id = novel_id
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.model_id = model_id
        self.detector = ConflictDetector(novel_id, model_id)

    def review_chapter(self, chapter_number: int, content: str) -> ReviewReport:
        """
        对章节内容进行全面审核
        返回详细的审核报告
        """
        return self.detector.detect_chapter_conflicts(chapter_number, content)

    def auto_fix_minor_issues(self, content: str,
                               report: ReviewReport,
                               novel_id: int) -> str:
        """
        自动修复轻微问题（严重程度 < 4 的问题）
        返回修复后的内容
        """
        minor_conflicts = [c for c in report.conflicts if c.severity < 4]
        if not minor_conflicts:
            return content

        llm = NovelLLM(self.model_id)
        conflicts_desc = "\n".join([
            f"- {c.conflict_type}（位置：{c.location[:100]}）：{c.description}。建议：{c.solutions[0] if c.solutions else ''}"
            for c in minor_conflicts
        ])

        system_prompt = """你是一位小说编辑，负责修复文本中的轻微问题。
只修复指定的问题，不改变故事情节和整体内容。
直接输出修复后的完整文本。"""

        user_prompt = f"""原文：
{content}

需要修复的问题（只修复这些，其他不变）：
{conflicts_desc}

请输出修复后的完整正文："""

        return llm.generate(system_prompt, user_prompt, max_tokens=12000)

    def fix_all_issues(self, content: str,
                        report: "ReviewReport",
                        novel_id: int) -> str:
        """
        根据完整审核报告修复所有问题（不限严重程度）。
        用于审核-修改自动循环中的每次改写。
        """
        if not report.conflicts:
            return content

        llm = NovelLLM(self.model_id)
        conflicts_desc = "\n".join([
            f"- [{c.severity}级] {c.conflict_type}"
            f"（位置：{c.location[:80]}）：{c.description}"
            + (f"。建议：{c.solutions[0]}" if c.solutions else "")
            for c in report.conflicts
        ])

        system_prompt = (
            "你是一位资深小说编辑，负责根据审核意见修改章节正文。\n"
            "修改原则：\n"
            "1. 严格按审核意见逐条修复问题\n"
            "2. 保持故事情节、人物关系、场景氛围不变\n"
            "3. 只改有问题的部分，其余内容保持原样\n"
            "4. 直接输出完整修改后正文，不加任何说明或标注"
        )
        user_prompt = (
            f"章节正文：\n{content}\n\n"
            f"本次审核评分：{report.overall_score:.1f}/10\n"
            f"需要修复的问题（共 {len(report.conflicts)} 条）：\n{conflicts_desc}\n\n"
            "请修复以上所有问题，直接输出完整修改后正文："
        )
        return llm.generate(system_prompt, user_prompt, max_tokens=12000)

    def close(self):
        self.memory.close()
        self.detector.close()


# ======================================
# Agent 5: 润色师
# ======================================

class PolisherAgent:
    """
    润色师 Agent
    职责：对审核通过的内容进行文笔润色
    - 降低AI痕迹
    - 统一写作风格
    - 保留原文剧情，只优化表达
    """

    SYSTEM_PROMPT = """你是一位资深的中文小说文学编辑，专注于将AI生成的文稿提升为高质量的文学作品。

你的润色原则：
1. **去AI痕迹**：消除以下典型AI写作特征：
   - 过度使用"同时"、"此时"、"然而"、"不得不"等连接词
   - 句式过于整齐，缺乏变化
   - 形容词堆砌，感情表达过于直白
   - 场景描写缺乏层次，只有视觉没有其他感官

2. **增强文学性**：
   - 加入更多感官细节（嗅觉、触觉、听觉）
   - 用隐喻和意象替代直白描述
   - 通过细节展现情感，而非直接说
   - 让对话更自然，有留白

3. **保持原意**：
   - 不改变情节、对话内容和人物关系
   - 不添加新的情节元素
   - 保持原有的段落结构

4. **风格一致**：
   - 保持全书统一的叙述人称
   - 保持人物独特的说话风格

输出要求：直接输出润色后的正文，不要加解释。"""

    STYLE_ANALYSIS_PROMPT = """你是一位资深文学风格分析专家，擅长从作品片段中精准提取作者的写作风格特征。

你的分析原则：
- 特征必须具体、可操作，能直接指导他人模仿
- 避免笼统的评价（如"文笔优美"），改用具体描述（如"惯用短句节奏，三至五字一句，营造急促感"）
- "润色指令"字段尤为重要：必须用行动导向语言，告诉写作者具体"该做什么"

输出格式：合法的JSON，不含其他文字。"""

    def __init__(self, novel_id: int, model_id: str = None):
        self.novel_id = novel_id
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.llm = NovelLLM(model_id)

    def polish_chapter(self, content: str,
                         style_reference: str = "",
                         stream_callback=None) -> str:
        """
        对章节内容进行全面润色

        Args:
            content: 待润色的章节内容
            style_reference: 风格参考（可以是前几章的优秀段落）
            stream_callback: 流式输出回调
        """
        novel = self.memory.global_mem.get_novel()
        style_desc = novel.writing_style or "" if novel else ""
        style_profile = novel.get_style_profile() if novel else {}
        style_profile_text = self._format_style_profile(style_profile) if style_profile else ""

        user_prompt = f"""请对以下小说章节进行文笔润色：

{f"【风格要求】{style_desc}" if style_desc else ""}
{f"【参考作者风格档案（请模仿以下风格特征进行润色）】\n{style_profile_text}" if style_profile_text else ""}
{f"【风格参考样例】\n{style_reference}" if style_reference else ""}

【待润色内容】
{content}

请在保持故事情节不变的前提下，提升文学质量，输出润色后的完整正文："""

        if stream_callback:
            content_parts = []
            for text_chunk in self.llm.generate_stream(
                self.SYSTEM_PROMPT, user_prompt, max_tokens=12000
            ):
                content_parts.append(text_chunk)
                stream_callback(text_chunk)
            return "".join(content_parts)
        else:
            return self.llm.generate(
                self.SYSTEM_PROMPT, user_prompt, max_tokens=12000
            )

    def apply_style_to_selection(self, selected_text: str,
                                   instruction: str) -> str:
        """
        对选中的文字片段应用特定风格指令
        """
        user_prompt = f"""对以下文字片段进行修改：

【原文】
{selected_text}

【修改要求】
{instruction}

请直接输出修改后的内容："""

        return self.llm.generate(self.SYSTEM_PROMPT, user_prompt)

    def _format_style_profile(self, profile: dict) -> str:
        """将风格档案转为多行文本，供注入润色提示词"""
        if not profile:
            return ""
        field_labels = [
            ("overall_style",        "总体风格"),
            ("sentence_patterns",    "句式特征"),
            ("vocabulary",           "词汇风格"),
            ("narrative_voice",      "叙述风格"),
            ("dialogue_style",       "对话风格"),
            ("description_style",    "描写特点"),
            ("rhythm_pacing",        "节奏特征"),
            ("emotion_expression",   "情感表达"),
            ("signature_techniques", "标志性手法"),
            ("polish_instructions",  "润色指令"),
        ]
        lines = []
        for field, label in field_labels:
            if profile.get(field):
                lines.append(f"- {label}：{profile[field]}")
        return "\n".join(lines)

    def analyze_style(self, reference_text: str) -> dict:
        """
        分析参考文本的写作风格，返回结构化风格档案（10个维度）

        Args:
            reference_text: 喜欢的作家作品片段（建议500-3000字）

        Returns:
            包含10个风格维度的dict，关键字段为 polish_instructions
        """
        user_prompt = f"""请分析以下参考文本的写作风格，提取10个维度的特征：

【参考文本】
{reference_text[:8000]}{"...(已截断)" if len(reference_text) > 8000 else ""}

请输出JSON格式的风格档案，每个字段的值必须具体、可操作：
{{
  "overall_style": "总体风格定位（一句话概括，如：张爱玲式的冷峻市井现实主义）",
  "sentence_patterns": "句式特征（长短句比例、句式结构偏好、标点使用习惯等，需举例）",
  "vocabulary": "词汇风格（雅俗程度、惯用词汇类型、文白比例等）",
  "narrative_voice": "叙述风格（叙述距离近/远、视角特点、信息呈现方式如暗示/直述）",
  "dialogue_style": "对话特点（频率高低、对话长短偏好、口语化程度、标点习惯）",
  "description_style": "描写特点（感官偏好如视听嗅触、比喻手法、景物描写密度）",
  "rhythm_pacing": "节奏特征（段落疏密规律、快慢切换方式、如何制造呼吸感）",
  "emotion_expression": "情感表达（直抒胸臆 vs 含蓄克制的程度、情绪调动手法）",
  "signature_techniques": "标志性手法（该作者特有的技巧、反复出现的意象或表达方式）",
  "polish_instructions": "润色指令（重要！请用行动导向语言列出5-8条具体指令，告诉润色者该做什么，如：①多用三至五字短句营造急促节奏 ②以嗅觉触觉替代纯视觉描写 ③对话后不加心理解释，让行为说话 ④比喻要接地气，取材日常器物而非自然意象）"
}}"""

        response = self.llm.generate(
            self.STYLE_ANALYSIS_PROMPT, user_prompt,
            max_tokens=4096, cache_system=False
        )
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            return _safe_json_loads(response[json_start:json_end])
        raise ValueError(f"风格分析返回格式错误：{response[:400]}")

    def close(self):
        self.memory.close()


# ======================================
# Agent 6: 读者模拟
# ======================================

class ReaderAgent:
    """
    读者模拟 Agent
    职责：模拟三种不同读者类型，对章节给出阅读体验评分和反馈
    - 爽文读者：关注爽感、节奏、代入感、升级感
    - 文学爱好者：关注文笔、人物深度、主题表达
    - 轻小说读者：关注趣味、角色魅力、轻松感
    """

    SYSTEM_PROMPT = """你是一位阅读体验分析专家，能够精准模拟不同类型读者的阅读感受。

你需要同时扮演三种截然不同的读者，分别给出评价：

**1. 爽文读者（power_fantasy）**
- 核心诉求：看得爽、节奏快、主角牛逼
- 评分维度：爽感（主角是否给力、打脸是否到位）、节奏感（是否拖沓）、悬念感（是否想追下一章）、代入感（是否能代入主角）、升级感（主角是否有成长/变强）
- 这类读者讨厌：大段心理描写、主角受委屈太久、节奏慢、说教

**2. 文学爱好者（literary）**
- 核心诉求：文笔好、有深度、有思考
- 评分维度：文笔质感（语言是否有美感）、人物深度（人物是否立体真实）、主题表达（是否有思想内涵）、情感共鸣（是否触动人心）、叙事技巧（结构、视角、留白等技法）
- 这类读者讨厌：套路化、人物扁平、直白说教、文笔粗糙

**3. 轻小说读者（light_novel）**
- 核心诉求：轻松有趣、角色讨喜、不用动脑
- 评分维度：趣味性（是否有趣好玩）、角色魅力（角色是否讨喜）、对话质感（对话是否自然有梗）、画面感（场景是否有画面）、轻松度（阅读是否无压力）
- 这类读者讨厌：沉重话题、复杂叙事、大段描写、没有互动感

评分标准（0-10）：
- 9-10：该类型读者会极力推荐
- 7-8：该类型读者会觉得不错，愿意追更
- 5-6：该类型读者勉强能看，但不会主动推荐
- 3-4：该类型读者会弃文
- 1-2：该类型读者会觉得浪费时间

输出格式：合法的JSON，不含其他文字。"""

    # 三种读者类型的评分维度定义
    READER_DIMENSIONS = {
        "power_fantasy": {
            "label": "爽文读者",
            "dimensions": ["爽感", "节奏感", "悬念感", "代入感", "升级感"],
        },
        "literary": {
            "label": "文学爱好者",
            "dimensions": ["文笔质感", "人物深度", "主题表达", "情感共鸣", "叙事技巧"],
        },
        "light_novel": {
            "label": "轻小说读者",
            "dimensions": ["趣味性", "角色魅力", "对话质感", "画面感", "轻松度"],
        },
    }

    def __init__(self, novel_id: int, model_id: str = None):
        self.novel_id = novel_id
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.llm = NovelLLM(model_id)

    def evaluate_chapter(self, chapter_number: int, content: str,
                          reader_types: list[str] = None) -> dict:
        """
        评估章节的阅读体验，返回三种读者的评分和反馈。

        Args:
            chapter_number: 章节序号
            content: 章节正文
            reader_types: 要模拟的读者类型（默认全部三种）

        Returns:
            包含三种读者评分、亮点和改进建议的 dict
        """
        if not reader_types:
            reader_types = ["power_fantasy", "literary", "light_novel"]

        # 获取章纲上下文
        chapter = self.memory.global_mem.get_chapter_outline(chapter_number)
        outline_text = chapter.to_outline_text() if chapter else ""

        # 构建评分维度描述
        dims_desc = ""
        for rtype in reader_types:
            info = self.READER_DIMENSIONS.get(rtype, {})
            if info:
                dims_desc += f"\n- {info['label']}（{rtype}）：{', '.join(info['dimensions'])}"

        # 截断正文防止 token 溢出
        content_truncated = content[:6000]
        truncated_note = "...(已截断)" if len(content) > 6000 else ""

        user_prompt = f"""请模拟以下读者类型阅读这一章节，给出各自的评分和评语：
{dims_desc}

【章节信息】
{outline_text}

【章节正文】
{content_truncated}{truncated_note}

请输出以下JSON格式（每个读者类型的 scores 字段必须包含该类型的所有评分维度）：
{{
  "readers": {{
    "power_fantasy": {{
      "label": "爽文读者",
      "scores": {{"爽感": 0, "节奏感": 0, "悬念感": 0, "代入感": 0, "升级感": 0}},
      "average": 0.0,
      "comment": "以爽文读者的口吻，用2-3句话评价这一章（口语化，如：这章主角太帅了/节奏有点拖）"
    }},
    "literary": {{
      "label": "文学爱好者",
      "scores": {{"文笔质感": 0, "人物深度": 0, "主题表达": 0, "情感共鸣": 0, "叙事技巧": 0}},
      "average": 0.0,
      "comment": "以文学爱好者的口吻评价（如：这段心理描写很细腻/叙事略显单调）"
    }},
    "light_novel": {{
      "label": "轻小说读者",
      "scores": {{"趣味性": 0, "角色魅力": 0, "对话质感": 0, "画面感": 0, "轻松度": 0}},
      "average": 0.0,
      "comment": "以轻小说读者的口吻评价（如：这章笑点不够/角色互动很有趣）"
    }}
  }},
  "overall_score": 0.0,
  "highlights": ["亮点1：摘录或概括让人印象深刻的段落", "亮点2"],
  "improvement_suggestions": ["建议1：具体可操作的改进方向", "建议2"]
}}

注意：
- overall_score 是三种读者平均分的加权平均（权重相等）
- average 是该读者类型各维度分数的算术平均
- comment 必须符合该类型读者的说话风格和关注点
- highlights 摘录原文中最出彩的2-3处
- improvement_suggestions 给出2-3条具体改进建议"""

        response = self.llm.generate(
            self.SYSTEM_PROMPT, user_prompt,
            max_tokens=4096, cache_system=False
        )

        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            return _safe_json_loads(response[json_start:json_end])
        raise ValueError(f"读者模拟返回格式错误：{response[:400]}")

    def close(self):
        self.memory.close()


# ======================================
# Agent 7: 灵感对话
# ======================================

class IdeaAgent:
    """
    灵感对话 Agent
    职责：通过多轮对话帮助用户将模糊灵感整理成结构化的小说方案
    - 每轮只问一个问题，自然引导
    - 用户自己决定何时进入下一步，随时可点击创建项目
    - 支持从对话历史提取结构化项目配置
    """

    SYSTEM_PROMPT = """你是一位亲切的资深小说编辑，正在和一位有创作灵感的作者聊天。

你的目标是通过轻松的对话，帮助对方把脑海中模糊的故事想法整理清楚。你需要了解：
- 故事的核心：发生了什么事、主角是谁、有什么冲突
- 大致风格：是爽文还是文学向，古风还是现代
- 规模感：短篇还是长篇

聊天原则：
1. 语气轻松自然，像朋友聊天，不像填表格
2. 每次只问一件事，不要一口气抛出多个问题
3. 先让对方说，多倾听，适时引导
4. 如果对方说得很笼统，帮他举例或追问细节
5. 不需要每个信息都集齐，故事核心（梗概）+ 风格方向 + 大致篇幅 就够了"""

    EXTRACT_PROMPT = """根据以下对话记录，提取小说项目的关键信息，输出JSON格式。

要求：
- title：从对话中提炼一个简洁有力的标题，如果用户没提就根据故事自拟
- logline：用一句话概括核心故事（主角+处境+目标/冲突），80字以内
- genre：从以下选项中选最合适的一个：玄幻、修仙、都市、言情、悬疑、历史、科幻、末世、游戏、其他
- writing_style：根据对话推断的风格偏好，例如"节奏明快，爽感优先"或"细腻写实，注重人物心理"，如无明显偏好则留空字符串
- total_chapters：根据故事规模推断，短篇50-80章，中篇100-150章，长篇200章以上，默认100

只输出JSON，不含其他文字：
{
  "title": "...",
  "logline": "...",
  "genre": "...",
  "writing_style": "...",
  "total_chapters": 100
}"""

    def __init__(self, model_id: str = None):
        self.llm = NovelLLM(model_id)

    def chat(self, messages: list) -> str:
        """
        多轮对话，返回 AI 回复
        messages: [{"role": "user"/"assistant", "content": "..."}]
        返回 AI 回复文本
        """
        return self.llm.generate_chat(self.SYSTEM_PROMPT, messages, max_tokens=1024)

    def extract_project_config(self, messages: list) -> dict:
        """
        从对话历史中提取结构化项目配置
        返回: {title, logline, genre, writing_style, total_chapters}
        """
        history_text = "\n".join(
            f"{'用户' if m['role'] == 'user' else 'AI'}：{m['content']}"
            for m in messages
        )
        user_prompt = f"【对话记录】\n{history_text}"
        response = self.llm.generate(
            self.EXTRACT_PROMPT, user_prompt,
            max_tokens=1024, cache_system=False
        )
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            return _safe_json_loads(response[json_start:json_end])
        raise ValueError(f"项目配置提取失败：{response[:300]}")


# ======================================
# Agent 8: Canvas 协作 Agent
# ======================================

class CanvasAgent:
    """
    通用 Canvas AI 协作 Agent。
    注入当前文档内容 + 小说全局上下文，支持多轮对话。
    当 AI 建议修改文档时，以 ```markdown ... ``` 代码块输出新版本。
    """

    _ROLE_PROMPTS = {
        "global": """你是贯穿全书创作的 AI 协作者，能处理大纲、世界观设定、章节写作的一切问题。

【输出规范】
普通讨论、分析和建议直接用正常文字，不加代码块。
当你要给出可直接写入文档的内容时，使用以下专属代码块格式（不要用普通 markdown 代码块代替）：

若要更新整体大纲（包含 ## 前提设定 / 核心主题 / 主要矛盾等节）：
```outline
（完整大纲 Markdown，保留现有内容并补充修改）
```

若要更新世界观/背景/系统设定文档：
```settings
（完整设定 Markdown 文档）
```

若要给出当前章节的正文或修改版本：
```chapter
（完整章节正文）
```

用户可以一键将代码块内容应用到对应位置。""",

        "outline": """你是一位专业的小说大纲编辑助手。

职责：
- 帮助用户理清故事方向，完善前提设定、核心主题、主要矛盾、人物弧光、三幕结构
- 发现大纲中的逻辑漏洞和节奏问题
- 建议章节安排和情节调整

**当你需要提供修改后的大纲文档时，请用如下格式输出完整新版本：**
```markdown
（完整的整体大纲 Markdown 文档，使用 ## 分节）
```
用户可一键应用你的建议。普通讨论时无需代码块格式。""",

        "settings": """你是一位专业的小说世界观设定顾问。

职责：
- 帮助完善背景设定、科技/魔法体系、人物设定
- 保证内部逻辑自洽，避免设定矛盾
- 根据故事类型提供专业的世界观建议

**当你需要提供修改后的设定文档时，请用如下格式输出完整新版本：**
```markdown
（完整的设定 Markdown 文档）
```
用户可一键应用你的建议。""",

        "writer": """你是一位专业的小说创作助手，正在协助用户打磨章节内容。

职责：
- 讨论章节的情节安排、节奏把控、人物刻画
- 针对用户的描述给出具体修改意见
- 在用户要求时生成修改后的段落或章节

**当你需要提供修改后的章节文本时，请严格使用如下格式输出（不要用其他代码块格式代替）：**
```chapter
（修改后的完整章节正文）
```
用户可一键将内容直接写入编辑器。普通讨论时无需代码块。""",
    }

    def __init__(self, novel_id: int, model_id: str = None, role: str = "outline"):
        from core.memory import MemoryManager
        from core.llm import DEFAULT_MODEL_ID
        self.novel_id = novel_id
        self.role = role
        self.memory = MemoryManager(novel_id)
        _model = model_id or self.memory.global_mem.get_novel().llm_model or DEFAULT_MODEL_ID
        self.llm = NovelLLM(_model)

    def chat(self, messages: list, document_content: str = "") -> str:
        """
        多轮对话。
        document_content: 当前文档内容（注入 system prompt）
        messages: [{"role": "user"/"assistant", "content": "..."}]
        """
        role_prompt = self._ROLE_PROMPTS.get(self.role, self._ROLE_PROMPTS["outline"])
        global_ctx = self.memory.global_mem.build_global_context()

        system_parts = [role_prompt, "", "---", "【小说上下文】", global_ctx]
        if document_content.strip():
            system_parts += [
                "", "---",
                "【当前文档内容（用户可能要求修改）】",
                f"```markdown\n{document_content}\n```",
            ]

        system_prompt = "\n".join(system_parts)
        return self.llm.generate_chat(system_prompt, messages, max_tokens=4096)

    def close(self):
        self.memory.close()
