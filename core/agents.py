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
from core.platform_styles import get_style_description


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

如果用户提供的信息中包含完整规范的大纲内容，你应该尽量整体保留这些部分，不要做过多删减

输出格式：必须是合法的JSON格式。"""

    def __init__(self, novel_id: int, model_id: str = None, temperature: float = None):
        self.novel_id = novel_id
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.llm = NovelLLM(model_id)
        self.temperature = temperature  # None = 使用模型默认值

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

        response = self.llm.generate(self.SYSTEM_PROMPT, user_prompt, max_tokens=32000, temperature=self.temperature)

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

        response = self.llm.generate(self.SYSTEM_PROMPT, user_prompt, temperature=self.temperature)
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
            max_tokens=8192, cache_system=False,
            temperature=self.temperature
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
            max_tokens=min(4000 + ch_count * 600, 32000),
            temperature=self.temperature
        )

        arr_start = response.find("[")
        if arr_start < 0:
            raise ValueError(f"章纲生成返回格式错误：{response[:300]}")

        arr_end = response.rfind("]") + 1
        if arr_end > arr_start:
            # 正常情况：有完整的 JSON 数组
            result = _safe_json_loads(response[arr_start:arr_end])
            if isinstance(result, list) and result:
                return result

        # 截断兜底：尝试用 json_repair 修复不完整的 JSON
        try:
            from json_repair import repair_json
            partial = response[arr_start:]
            result = json.loads(repair_json(partial))
            if isinstance(result, list) and result:
                return result
        except Exception:
            pass

        raise ValueError(f"章纲生成返回格式错误：{response[:300]}")

    def analyze_chapter_consistency(self, chapter_number: int, content: str) -> dict:
        """
        分析章节内容，检测是否需要同步更新大纲或设定。

        Returns:
            {
              "new_characters": [{"name", "role", "personality", "background", "reason"}],
              "character_updates": [{"name", "field", "new_value", "reason"}],
              "outline_updates": [{"field", "merged_content", "added_info", "reason"}],
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
            rels = c.get_relationships()
            if rels:
                rel_strs = [f"{k}:{v}" for k, v in rels.items()]
                parts.append(f"人际关系：{', '.join(rel_strs)[:120]}")
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
      "relationships": {{"已有人物A": "关系描述", "已有人物B": "关系描述"}},
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
      "merged_content": "将本章新信息有机融入原有内容后的完整文本（不要把新旧内容拆成两段，必须融合成一段连贯文字）",
      "added_info": "本章新增的关键信息（一句话概括）",
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

只返回 JSON，不包含其他文字。某类没有需要更新时对应数组留空 []。

重要约束：outline_updates 的 merged_content 必须是一段完整的、连贯的文本，将大纲中原有的内容与本章新增的信息有机融合。禁止只写"新增：xxx"这样拆成两段的格式。"""

        response = self.llm.generate(self.SYSTEM_PROMPT, user_prompt, max_tokens=4096, temperature=self.temperature)
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

    def extract_relationships(self, chapter_number: int, content: str) -> list[dict]:
        """
        从章节内容中专门提取人物关系。
        比 analyze_chapter_consistency 更聚焦，提取率更高。

        Returns:
            [{"character": "人物名", "relationships": {"人物B": "关系描述", ...}}]
        """
        all_chars = self.memory.global_mem.get_all_characters()
        existing_names = [c.name for c in all_chars]

        char_rel_lines = []
        for c in all_chars:
            rels = c.get_relationships()
            rel_str = ", ".join(f"{k}: {v}" for k, v in rels.items()) if rels else "（暂无）"
            char_rel_lines.append(f"【{c.name}】（{c.role or ''}）— 现有关系：{rel_str}")
        char_rel_summary = "\n".join(char_rel_lines) or "（无人物）"

        user_prompt = f"""请仔细阅读第{chapter_number}章正文，提取所有人物之间的关系。

【第{chapter_number}章正文】
{content[:8000]}{"…（已截断）" if len(content) > 8000 else ""}

【已有人物及现有关系】
{char_rel_summary}

任务：找出本章中体现的人物关系（包括新出现的关系和已有关系的变化）。
- 关注对话、互动、称呼、情感、冲突、合作等细节
- 关系描述要具体（如"师徒"、"青梅竹马"、"宿敌"、"暗恋对象"），不要笼统写"认识"
- 同时输出双向关系（A对B是师父，B对A是徒弟）
- 只提取章节中有实际依据的关系，不要虚构

请输出 JSON 数组，每项表示一个人物需要更新的关系：
[
  {{
    "character": "人物姓名",
    "relationships": {{
      "人物B": "关系描述",
      "人物C": "关系描述"
    }}
  }}
]

只返回 JSON 数组，不包含其他文字。如果本章没有体现任何人物关系，返回空数组 []。"""

        response = self.llm.generate(self.SYSTEM_PROMPT, user_prompt, max_tokens=4096, temperature=self.temperature)
        arr_start = response.find("[")
        arr_end = response.rfind("]") + 1
        if arr_start >= 0 and arr_end > arr_start:
            result = _safe_json_loads(response[arr_start:arr_end])
            if isinstance(result, list):
                return [r for r in result if r.get("character") in existing_names]
        return []

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

    def __init__(self, novel_id: int, model_id: str = None, temperature: float = None):
        self.novel_id = novel_id
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.llm = NovelLLM(model_id)
        self.temperature = temperature

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

        response = self.llm.generate(self.SYSTEM_PROMPT, user_prompt, max_tokens=8192, temperature=self.temperature)
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

        response = self.llm.generate(self.SYSTEM_PROMPT, user_prompt, temperature=self.temperature)
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
- **字数约束**：严格按用户指定的字数范围写作，这是硬性要求，不可超出上限

写作规范：
- 对话要体现人物性格，避免口吻雷同
- 动作场面要清晰，空间感要强
- 情感描写要克制，通过细节表现，而非直接说"他很难过"
- 适度使用悬念，让每章结尾都有留人的钩子
- 避免大量重复使用同一个词或句式

去AI味规则（必须严格遵守）：
- 坚决使用主观视角描写：不要以上帝视角说"这个房间很冷"，而是写"陈默打了个冷颤，把领口往上拉了拉"
- 拒绝大道理：角色不要发表长篇大论的演讲，人类说话是零碎的、有错漏的，允许出现半句话、结巴、或者口头禅
- 严禁收尾综合征：每一章、每一段的结尾，绝对不要出现"这意味着……"、"他不知道的是，更大的危机正在逼近……"、"这就是命运的安排"等总结性发言
- 增强潜台词：人类很少直接说出心里话。想表达愤怒时，写他捏碎了纸杯；想表达关心时，写他把烟头掐灭

输出要求：
- 直接输出小说正文，不要加解释或注释
- 字数控制在章纲要求的范围内（一般2000-4000字）
- 分段合理，对话独占一行"""

    def __init__(self, novel_id: int, model_id: str = None, temperature: float = None):
        self.novel_id = novel_id
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.llm = NovelLLM(model_id)
        self.temperature = temperature

    def write_chapter(self, chapter_number: int,
                       word_target: int = 3000,
                       word_count_tolerance: float = 0.30,
                       stream_callback=None,
                       review_feedback: str = "") -> str:
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

        # 平台/标签风格块
        platform_block = ""
        if novel:
            _pt = novel.target_platform or ""
            _tg = novel.get_target_tags()
            _ps = get_style_description(_pt, _tg)
            if _ps:
                platform_block = f"\n【目标平台写作风格要求（请严格按照此平台和标签的读者偏好来写作）】\n{_ps}\n"

        feedback_block = ""
        if review_feedback:
            feedback_block = f"\n【上一稿审核反馈（请在本次写作中针对性改进，避免重复犯同样的问题）】\n{review_feedback}\n"

        # 项目自定义去AI味规则（覆盖系统默认）
        deai_block = ""
        if novel and novel.deai_rules and novel.deai_rules.strip():
            deai_block = f"\n【去AI味写作规则（项目自定义，必须严格遵守）】\n{novel.deai_rules.strip()}\n"

        user_prompt = f"""📏 字数硬性约束：本章必须控制在 {int(word_target * (1 - word_count_tolerance))}~{int(word_target * (1 + word_count_tolerance))} 字之间（目标 {word_target} 字），不得超过上限。

请根据以下所有资料，写作第{chapter_number}章：《{chapter.title or ''}》

{writing_context}{style_block}{platform_block}{feedback_block}{deai_block}
【写作要求】
- 必须完整呈现章纲中的核心事件
- 人物对话和行为必须符合其设定
- 注意与前几章的连贯性
- 章节结尾需要有合适的收束或钩子

⚠️ 再次强调：本章字数必须控制在 {int(word_target * (1 - word_count_tolerance))}~{int(word_target * (1 + word_count_tolerance))} 字之间，不得超过 {int(word_target * (1 + word_count_tolerance))} 字。

请直接开始写作正文，从标题开始："""

        if stream_callback:
            # 流式生成
            content_parts = []
            for text_chunk in self.llm.generate_stream(
                self.SYSTEM_PROMPT, user_prompt, max_tokens=12000, temperature=self.temperature
            ):
                content_parts.append(text_chunk)
                stream_callback(text_chunk)
            return "".join(content_parts)
        else:
            # 非流式生成
            return self.llm.generate(
                self.SYSTEM_PROMPT, user_prompt, max_tokens=12000, temperature=self.temperature
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
            response = self.llm.generate(system, user_prompt, max_tokens=512, cache_system=False, temperature=self.temperature)
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0:
                data = _safe_json_loads(response[json_start:json_end])
                if isinstance(data, dict):
                    return data.get("summary", ""), data.get("key_events", [])
                # LLM 可能返回数组或纯字符串，尝试从数组第一个元素提取
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    return data[0].get("summary", ""), data[0].get("key_events", [])
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

        return self.llm.generate(self.SYSTEM_PROMPT, user_prompt, temperature=self.temperature)

    def close(self):
        self.memory.close()


# ======================================
# Agent 4: 审核师
# ======================================

class ReviewerAgent:
    """
    审核师 Agent（核心中的核心）
    职责：对每次生成的内容进行全面检测
    检测维度：设定冲突、OOC、大纲冲突、前后矛盾、逻辑漏洞、AI痕迹
    """

    SYSTEM_PROMPT = """你是一位严格的小说质量审核专家，你的职责是守护故事的一致性和逻辑性。

你的审核原则：
- **零容忍主义**：任何与设定矛盾的细节都必须标记，无论多小
- **人设守护者**：人物的每一句话、每一个动作都必须符合其设定
- **时间线侦探**：仔细追踪时间线，确保没有时间悖论
- **逻辑探长**：每个情节都必须有合理的因果关系
- **读者代理人**：从读者角度思考每个可能引发困惑的地方
- **AI痕迹猎手**：逐条对照去AI味规则，标注每处机器人写作痕迹

你的输出必须：
- 精确引用冲突位置的原文
- 明确说明与哪条设定冲突或违反哪条规则
- 提供至少2个可行的修改方案"""

    def __init__(self, novel_id: int, model_id: str = None, temperature: float = None):
        self.novel_id = novel_id
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.model_id = model_id
        self.temperature = temperature
        self.detector = ConflictDetector(novel_id, model_id, temperature=temperature)

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
                        novel_id: int,
                        chapter_number: int = 0) -> str:
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

        # 获取章纲目标，让编辑明确本章应当实现什么
        chapter_goal_block = ""
        if chapter_number:
            ch = self.memory.global_mem.get_chapter_outline(chapter_number)
            if ch:
                goal_lines = []
                if ch.outline_core_event:
                    goal_lines.append(f"核心事件：{ch.outline_core_event}")
                if ch.outline_conflict:
                    goal_lines.append(f"主要冲突：{ch.outline_conflict}")
                if ch.outline_scene:
                    goal_lines.append(f"场景设定：{ch.outline_scene}")
                if ch.outline_emotion:
                    goal_lines.append(f"情感基调：{ch.outline_emotion}")
                if ch.outline_ending:
                    goal_lines.append(f"结尾方式：{ch.outline_ending}")
                if goal_lines:
                    chapter_goal_block = (
                        "\n【本章章纲目标（修改后的内容必须完整实现这些目标）】\n"
                        + "\n".join(goal_lines) + "\n"
                    )

        system_prompt = (
            "你是一位资深小说编辑，负责根据审核意见修改章节正文。\n"
            "修改原则：\n"
            "1. 严格按审核意见逐条修复问题\n"
            "2. 确保修改后的内容完整实现本章章纲目标\n"
            "3. 保持人物关系、场景氛围的一致性\n"
            "4. 直接输出完整修改后正文，不加任何说明或标注"
        )
        user_prompt = (
            f"章节正文：\n{content}\n\n"
            f"本次审核评分：{report.overall_score:.1f}/10\n"
            f"{chapter_goal_block}"
            f"需要修复的问题（共 {len(report.conflicts)} 条）：\n{conflicts_desc}\n\n"
            "请修复以上所有问题并确保章纲目标得以实现，直接输出完整修改后正文："
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

    def __init__(self, novel_id: int, model_id: str = None, temperature: float = None):
        self.novel_id = novel_id
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.llm = NovelLLM(model_id)
        self.temperature = temperature

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

        # 平台/标签风格
        platform_style_text = ""
        if novel:
            _pt = novel.target_platform or ""
            _tg = novel.get_target_tags()
            platform_style_text = get_style_description(_pt, _tg)

        # 项目自定义去AI味规则
        deai_block = ""
        if novel and novel.deai_rules and novel.deai_rules.strip():
            deai_block = f"\n【去AI味写作规则（项目自定义，润色时必须严格遵守）】\n{novel.deai_rules.strip()}\n"

        user_prompt = f"""请对以下小说章节进行文笔润色：

{f"【风格要求】{style_desc}" if style_desc else ""}
{f"【目标平台写作风格（润色时需符合此平台和标签的读者审美）】\n{platform_style_text}" if platform_style_text else ""}
{f"【参考作者风格档案（请模仿以下风格特征进行润色）】\n{style_profile_text}" if style_profile_text else ""}
{f"【风格参考样例】\n{style_reference}" if style_reference else ""}
{deai_block}
【待润色内容】
{content}

请在保持故事情节不变的前提下，提升文学质量，输出润色后的完整正文："""

        if stream_callback:
            content_parts = []
            for text_chunk in self.llm.generate_stream(
                self.SYSTEM_PROMPT, user_prompt, max_tokens=12000, temperature=self.temperature
            ):
                content_parts.append(text_chunk)
                stream_callback(text_chunk)
            return "".join(content_parts)
        else:
            return self.llm.generate(
                self.SYSTEM_PROMPT, user_prompt, max_tokens=12000, temperature=self.temperature
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

        return self.llm.generate(self.SYSTEM_PROMPT, user_prompt, temperature=self.temperature)

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

    def __init__(self, novel_id: int, model_id: str = None, temperature: float = None):
        self.novel_id = novel_id
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.llm = NovelLLM(model_id)
        self.temperature = temperature

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

    def __init__(self, model_id: str = None, temperature: float = None):
        self.llm = NovelLLM(model_id)
        self.temperature = temperature

    def chat(self, messages: list) -> str:
        """
        多轮对话，返回 AI 回复
        messages: [{"role": "user"/"assistant", "content": "..."}]
        返回 AI 回复文本
        """
        return self.llm.generate_chat(self.SYSTEM_PROMPT, messages, max_tokens=1024, temperature=self.temperature)

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
            max_tokens=1024, cache_system=False,
            temperature=self.temperature
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
    页面感知 + 意图识别 + 委托执行：将用户意图路由到对应专职 Agent，
    确保大纲生成、人物设计、章节写作、润色审核等操作与主工作流一致。
    一般性讨论和轻量建议仍由 CanvasAgent 自身 LLM 处理。
    """

    # ── 意图关键词 ──────────────────────────────────────────────
    _INTENT_WRITE        = {"写", "生成", "创作", "开始写", "写一下"}
    _INTENT_MODIFY       = {"修改", "改", "调整", "优化", "重写"}
    _INTENT_POLISH       = {"润色", "润饰", "打磨", "提升文笔"}
    _INTENT_REVIEW       = {"审核", "审阅", "检查", "冲突检测"}
    _INTENT_WRITE_REVIEW  = {"写并审", "写然后审", "写并审核", "写完审核", "写后审核", "写加审", "生成并审核"}
    _INTENT_OUTLINE      = {"大纲", "章纲", "卷纲"}
    _INTENT_CHARACTER    = {"人物", "角色", "人设", "主角", "配角", "反派"}

    _ROLE_PROMPTS = {
        "global": """你是贯穿全书创作的 AI 协作者，能处理大纲、世界观、人物设定、章节写作的一切问题。

【输出规范】
普通讨论、分析和建议直接用正常文字，不加代码块。
当你需要提供可直接写入系统的结构化内容时，使用以下专属代码块格式（每种代码块对应一个明确的写入目标）：

若要更新整体大纲（包含前提设定、核心主题、主要矛盾等）：
```outline
（完整大纲 Markdown，保留现有内容并补充修改）
```

若要更新世界观/背景/系统设定文档：
```settings
（完整设定 Markdown 文档）
```

若要对世界观做结构化键值更新（如"世界规则"、"力量体系"、"社会结构"等键）：
```world
{"键名": "完整的设定内容", "另一个键": "完整内容"}
```

若要创建或更新人物档案（必须包含 name 字段，其余字段可选填）：
```characters
[
  {
    "name": "人物姓名",
    "role": "主角/配角/反派",
    "age": "年龄",
    "gender": "性别",
    "personality": "性格特征",
    "background": "背景故事",
    "abilities": ["能力1", "能力2"],
    "appearance": "外貌描述",
    "growth_arc": "成长弧光",
    "current_state": "当前状态",
    "motivations": "动机",
    "speech_patterns": "说话风格",
    "secrets": "隐藏信息",
    "is_main": true
  }
]
```

若要删除人物档案，在 characters 块中传入 action 字段：
```characters
[
  {"name": "要删除的人物姓名", "action": "delete"}
]
```
增删可混在同一数组中。

若要给出当前章节的正文或修改版本：
```chapter
（完整章节正文）
```

若要创建或更新卷大纲：
```volume
{"volume_number": 1, "title": "卷名", "summary": "卷简介", "main_conflict": "核心矛盾", "arc_goal": "目标主题", "start_chapter": 1, "end_chapter": 30}
```

若要创建新的伏笔条目：
```foreshadowing
[
  {"name": "伏笔名", "description": "伏笔内容描述", "importance": "high/medium/low", "set_chapter": 1, "collect_by_chapter": 10}
]
```

用户可一键将代码块内容应用到对应位置。请根据用户意图选择最合适的类型——讨论人物时用 characters，讨论世界观规则时用 world，讨论背景文档时用 settings。

【去AI味写作规则（使用 chapter 或修改章节正文时必须严格遵守）】
- 主观视角描写：不要以上帝视角说"这个房间很冷"，写"陈默打了个冷颤，把领口往上拉了拉"
- 拒绝大道理：角色不说长篇大论，人类说话是零碎的、有错漏的，允许半句话、结巴、口头禅
- 严禁收尾综合征：每章每段结尾，绝对不要出现"这意味着……"、"他不知道的是……"、"这就是命运的安排"等总结句
- 增强潜台词：不直说心里话。表达愤怒→写捏碎纸杯；表达关心→写掐灭烟头""",
    }

    def __init__(self, novel_id: int, model_id: str = None, role: str = "global", temperature: float = None):
        from core.memory import MemoryManager
        from core.llm import DEFAULT_MODEL_ID
        self.novel_id = novel_id
        self.role = role
        self.memory = MemoryManager(novel_id)
        _model = model_id or self.memory.global_mem.get_novel().llm_model or DEFAULT_MODEL_ID
        self.llm = NovelLLM(_model)
        self.temperature = temperature
        # 懒加载的专职 Agent 引用
        self._outline_agent = None
        self._character_agent = None
        self._writer_agent = None
        self._reviewer_agent = None
        self._polisher_agent = None

    # ── 懒加载 Agent 属性 ────────────────────────────────────────
    @property
    def outline_agent(self):
        if not self._outline_agent:
            self._outline_agent = OutlineAgent(self.novel_id, temperature=self.temperature)
        return self._outline_agent

    @property
    def character_agent(self):
        if not self._character_agent:
            self._character_agent = CharacterAgent(self.novel_id, temperature=self.temperature)
        return self._character_agent

    @property
    def writer_agent(self):
        if not self._writer_agent:
            self._writer_agent = WriterAgent(self.novel_id, temperature=self.temperature)
        return self._writer_agent

    @property
    def reviewer_agent(self):
        if not self._reviewer_agent:
            self._reviewer_agent = ReviewerAgent(self.novel_id, temperature=self.temperature)
        return self._reviewer_agent

    @property
    def polisher_agent(self):
        if not self._polisher_agent:
            self._polisher_agent = PolisherAgent(self.novel_id, temperature=self.temperature)
        return self._polisher_agent

    # ── 意图识别 ─────────────────────────────────────────────────
    def _recognize_intent(self, user_msg: str, page: str, chapter: int = None) -> str:
        """从用户消息 + 页面上下文识别意图，返回 intent 字符串"""
        msg = user_msg
        # 章节操作（需在写作页）
        if page == "写作" and chapter:
            if any(kw in msg for kw in self._INTENT_WRITE_REVIEW):
                return "write_review_chapter"
            if any(kw in msg for kw in self._INTENT_WRITE):
                return "write_chapter"
            if any(kw in msg for kw in self._INTENT_POLISH):
                return "polish_chapter"
            if any(kw in msg for kw in self._INTENT_REVIEW):
                return "review_chapter"
            if any(kw in msg for kw in self._INTENT_MODIFY):
                return "modify_chapter"
        # 大纲操作
        if any(kw in msg for kw in self._INTENT_OUTLINE):
            return "outline_op"
        # 人物操作
        if any(kw in msg for kw in self._INTENT_CHARACTER):
            return "character_op"
        return "general"

    # ── 委托处理器 ───────────────────────────────────────────────
    def _delegate_write(self, chapter: int) -> str:
        """委托 WriterAgent 写新章节"""
        try:
            result = self.writer_agent.write_chapter(chapter)
            return f"```chapter\n{result}\n```"
        except ValueError as e:
            return f"⚠️ 无法生成第{chapter}章：{e}"

    def _delegate_modify(self, chapter: int, instruction: str, current_content: str) -> str:
        """委托 WriterAgent 修改指定章节"""
        section = current_content or ""
        try:
            result = self.writer_agent.rewrite_section(chapter, section, instruction)
            return f"```chapter\n{result}\n```"
        except Exception as e:
            return f"⚠️ 修改失败：{e}\n\n将使用对话模式提供建议。"

    def _delegate_polish(self, chapter: int, current_content: str) -> str:
        """委托 PolisherAgent 润色章节"""
        if not current_content.strip():
            return "⚠️ 当前没有章节内容可供润色，请先生成或编辑章节。"
        try:
            result = self.polisher_agent.polish_chapter(current_content)
            return f"```chapter\n{result}\n```"
        except Exception as e:
            return f"⚠️ 润色失败：{e}"

    def _delegate_review(self, chapter: int, current_content: str) -> str:
        """委托 ReviewerAgent 审核章节"""
        if not current_content.strip():
            return "⚠️ 当前没有章节内容可供审核。"
        try:
            report = self.reviewer_agent.review_chapter(chapter, current_content)
            lines = ["## 📋 章节审核报告", ""]
            if hasattr(report, 'overall_score'):
                lines.append(f"**综合评分**：{report.overall_score}/10")
            if hasattr(report, 'conflicts') and report.conflicts:
                lines.append(f"\n**发现 {len(report.conflicts)} 个冲突：**")
                for c in report.conflicts:
                    sev = getattr(c, 'severity', '?')
                    desc = getattr(c, 'description', str(c))
                    lines.append(f"- 🔴 严重度 {sev}：{desc}")
            else:
                lines.append("\n✅ 未发现明显冲突。")
            if hasattr(report, 'suggestions') and report.suggestions:
                lines.append(f"\n**改进建议：**")
                for s in report.suggestions:
                    lines.append(f"- {s}")
            return "\n".join(lines)
        except Exception as e:
            return f"⚠️ 审核失败：{e}"

    def _delegate_write_and_review(self, chapter: int, word_target: int = 3000) -> str:
        """委托 NovelWorkflow 执行完整写审流水线（写→审→自修复→润色→保存）"""
        from core.workflow import load_novel
        wf = None
        try:
            wf = load_novel(self.novel_id)
            result = wf.write_and_review_chapter(
                chapter_number=chapter,
                word_target=word_target,
                auto_polish=True
            )
            wf.close()
            wf = None
            if result.success:
                report_text = ""
                if result.data.get("review_report"):
                    r = result.data["review_report"]
                    score = getattr(r, 'overall_score', '?') if hasattr(r, 'overall_score') else '?'
                    report_text = f"\n\n## 📋 审核报告\n**综合评分**：{score}/10"
                    if hasattr(r, 'conflicts') and r.conflicts:
                        report_text += f"\n自动修复了 {len(r.conflicts)} 个冲突"
                return f"```chapter\n{result.data.get('content', '')}\n```{report_text}"
            else:
                return f"⚠️ 生成失败：{result.message}"
        except Exception as e:
            return f"⚠️ 写审流水线失败：{e}"
        finally:
            if wf is not None:
                try:
                    wf.close()
                except Exception:
                    pass

    # ── 页面上下文构建 ───────────────────────────────────────────
    @staticmethod
    def _build_page_context(page: str, chapter: int = None, selected_text: str = "") -> str:
        """构建页面感知的上下文说明"""
        parts = []
        if page:
            parts.append(f"用户当前在【{page}】页面")
        if chapter:
            parts.append(f"正在查看【第{chapter}章】")
        if selected_text.strip():
            parts.append(f"用户选中了以下文字：\n```\n{selected_text[:500]}\n```")
        return "；".join(parts) if parts else ""

    def chat(self, messages: list, document_content: str = "",
             page: str = "", chapter_number: int = None,
             selected_text: str = "") -> str:
        """
        多轮对话（支持页面感知 + 意图委托）。
        page: 当前页面名（"写作"/"大纲管理"/"设定管理"等）
        chapter_number: 当前章节号（在写作页时传入）
        selected_text: 用户选中的文本片段
        """
        user_msg = messages[-1]["content"].strip() if messages else ""
        page_context = self._build_page_context(page, chapter_number, selected_text)

        # ── 意图识别与委托 ──
        intent = self._recognize_intent(user_msg, page, chapter_number)
        if intent == "write_review_chapter":
            return self._delegate_write_and_review(chapter_number)
        elif intent == "write_chapter":
            return self._delegate_write(chapter_number)
        elif intent == "modify_chapter":
            return self._delegate_modify(chapter_number, user_msg, document_content)
        elif intent == "polish_chapter":
            return self._delegate_polish(chapter_number, document_content)
        elif intent == "review_chapter":
            return self._delegate_review(chapter_number, document_content)
        # outline_op / character_op 走通用对话（AI 会输出对应代码块）

        # ── 通用对话 ──
        role_prompt = self._ROLE_PROMPTS.get(self.role, self._ROLE_PROMPTS["global"])
        global_ctx = self.memory.global_mem.build_global_context()

        # 注入风格档案和平台约束（与 WriterAgent 对齐）
        style_block = ""
        platform_block = ""
        novel = self.memory.global_mem.get_novel()
        if novel:
            style_profile = novel.get_style_profile()
            if style_profile:
                _labels = {
                    "overall_style": "总体风格", "sentence_patterns": "句式", "vocabulary": "词汇",
                    "narrative_voice": "叙述视角", "dialogue_style": "对话", "description_style": "描写",
                    "rhythm_pacing": "节奏", "emotion_expression": "情感表达",
                    "signature_techniques": "标志手法", "polish_instructions": "核心指令",
                }
                sl = [f"- {lbl}：{style_profile[k]}" for k, lbl in _labels.items() if style_profile.get(k)]
                if sl:
                    style_block = "\n【全书写作风格档案（严格遵循）】\n" + "\n".join(sl)
            pt = novel.target_platform or ""
            tg = novel.get_target_tags()
            from core.platform_styles import get_style_description
            ps = get_style_description(pt, tg)
            if ps:
                platform_block = f"\n【目标平台风格要求】\n{ps}\n"

        system_parts = [role_prompt]
        if page_context:
            system_parts += ["", f"【当前页面】{page_context}"]
        system_parts += ["", "---", "【小说上下文】", global_ctx]
        if style_block:
            system_parts.append(style_block)
        if platform_block:
            system_parts.append(platform_block)
        # 项目自定义去AI味规则
        if novel and novel.deai_rules and novel.deai_rules.strip():
            system_parts.append(f"\n【去AI味写作规则（项目自定义，必须严格遵守）】\n{novel.deai_rules.strip()}")
        if document_content.strip():
            system_parts += [
                "", "---",
                "【当前文档内容（用户可能要求修改）】",
                f"```markdown\n{document_content}\n```",
            ]

        system_prompt = "\n".join(system_parts)
        return self.llm.generate_chat(system_prompt, messages, max_tokens=4096, temperature=self.temperature)

    def close(self):
        """释放所有 Agent 的资源"""
        for attr in ("_outline_agent", "_character_agent", "_writer_agent",
                     "_reviewer_agent", "_polisher_agent"):
            agent = getattr(self, attr, None)
            if agent:
                try:
                    agent.close()
                except Exception:
                    pass
        self.memory.close()
