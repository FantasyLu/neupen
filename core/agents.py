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
        self.temperature = temperature
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.llm = NovelLLM(model_id, novel_id=self.novel_id)

    @staticmethod
    def _build_foreshadowing_schedule_prompt(active_fs: list) -> str:
        """将活跃伏笔列表转为调度提示文本，注入章纲生成 prompt。
        按重要度排序（high > medium > low），超过30条时截断并告知数量，
        避免大量伏笔撑爆章纲生成的 context。
        """
        if not active_fs:
            return ""
        importance_order = {"high": 0, "medium": 1, "low": 2}
        sorted_fs = sorted(active_fs, key=lambda f: importance_order.get(f.importance, 1))

        _MAX_FS = 30
        truncated = len(sorted_fs) > _MAX_FS
        display_fs = sorted_fs[:_MAX_FS]

        importance_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}
        lines = ["【待回收伏笔调度表（生成章纲时必须安排以下伏笔的回收，不得遗漏）】"]
        for f in display_fs:
            icon = importance_icon.get(f.importance, "🟡")
            deadline = f"最晚第{f.collect_by_chapter}章回收。" if f.collect_by_chapter else "无截止时间要求。"
            desc = f.description or "（无描述）"
            lines.append(
                f"- {icon} [{f.importance}重要] 《{f.name}》（第{f.set_chapter}章埋下）：{desc} {deadline}"
            )
        if truncated:
            omitted = len(sorted_fs) - _MAX_FS
            omit_names = "、".join(f"《{f.name}》" for f in sorted_fs[_MAX_FS:])
            lines.append(f"（另有 {omitted} 条低重要度伏笔未列出：{omit_names}）")
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

        active_fs = self.memory.global_mem.get_active_foreshadowings()
        fs_schedule_text = self._build_foreshadowing_schedule_prompt(active_fs)
        fs_block = f"\n{fs_schedule_text}\n" if fs_schedule_text else ""

        # 从本章章纲和用户反馈提取关键词，过滤无关世界观
        from core.memory import _extract_chapter_keywords
        chapter_keywords = _extract_chapter_keywords(chapter)
        # 把用户反馈中的词也加进去（简单分词）
        import re as _re
        for tok in _re.split(r"[，。！？、；：\s]+", user_feedback):
            tok = tok.strip()
            if len(tok) >= 2:
                chapter_keywords.add(tok)

        global_ctx = self.memory.global_mem.build_global_context(
            chapter_keywords=chapter_keywords if chapter_keywords else None
        )

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
            max_tokens=8192, cache_system=False, temperature=self.temperature
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
        active_fs = self.memory.global_mem.get_active_foreshadowings()
        fs_text = self._build_foreshadowing_schedule_prompt(active_fs)
        fs_block = f"\n{fs_text}\n" if fs_text else ""

        # 从用户描述提取关键词，过滤无关世界观
        import re as _re
        from core.memory import _extract_chapter_keywords
        desc_keywords: set[str] = set()
        for tok in _re.split(r"[，。！？、；：\s]+", description):
            tok = tok.strip()
            if len(tok) >= 2:
                desc_keywords.add(tok)

        global_ctx = self.memory.global_mem.build_global_context(
            chapter_keywords=desc_keywords if desc_keywords else None
        )

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
            max_tokens=min(4000 + ch_count * 600, 32000), temperature=self.temperature
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
        # 从章纲提取关键词，过滤无关世界观/伏笔
        chapter = self.memory.global_mem.get_chapter_outline(chapter_number)
        from core.memory import _extract_chapter_keywords
        chapter_keywords = _extract_chapter_keywords(chapter) if chapter else set()

        all_chars = self.memory.global_mem.get_all_characters()
        global_ctx = self.memory.global_mem.build_global_context(
            chapter_keywords=chapter_keywords if chapter_keywords else None,
            current_chapter=chapter_number,
        )
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

    def expand_outline_section(self, context: str, instruction: str,
                                chapter_number: int | None = None) -> str:
        """
        根据用户指令扩写/调整大纲内容，返回修改后的大纲 Markdown 文本。
        由 CanvasAgent.dispatch() 调用。
        """
        chapter_hint = f"当前聚焦章节：第{chapter_number}章。\n" if chapter_number else ""
        system_prompt = (
            "你是一位经验丰富的小说大纲师。根据用户指令，对现有大纲进行扩写或调整。\n"
            "保持大纲整体结构和风格，只修改用户明确要求的部分。\n"
            "直接输出调整后的完整大纲 Markdown 文本，不加任何说明。"
        )
        user_prompt = (
            f"{chapter_hint}"
            f"【当前大纲上下文】\n{context[:3000]}\n\n"
            f"【调整指令】\n{instruction}\n\n"
            "请输出调整后的大纲内容："
        )
        return self.llm.generate(system_prompt, user_prompt,
                                 max_tokens=4096, temperature=self.temperature)

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
        self.temperature = temperature
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.llm = NovelLLM(model_id, novel_id=self.novel_id)

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
        检测所有人物设定之间的矛盾和不合理之处。
        分批处理（每批最多10人），避免人物过多时上下文爆炸。
        返回问题列表。
        """
        chars = self.memory.global_mem.get_all_characters()
        if len(chars) < 2:
            return []

        _CHECK_INSTRUCTION = """检查维度：
1. 人际关系是否双向一致（A说是B的朋友，B是否也有对应描述）
2. 能力设定是否合理（是否有人能力过于全能）
3. 背景故事是否与世界观冲突
4. 年龄与经历是否匹配
5. 不同人物的设定是否雷同（是否有人物缺乏独特性）

以列表形式输出所有问题（每个问题一行），没有问题则输出"无明显矛盾"。"""

        _MAX_CHAR_LEN = 800   # 单人档案最多800字，超出截断
        _BATCH_SIZE = 10      # 每批最多10人

        def _char_text(c) -> str:
            t = c.to_profile_text()
            if len(t) > _MAX_CHAR_LEN:
                t = t[:_MAX_CHAR_LEN] + "…（已截断）"
            return t

        all_problems: list[str] = []

        # 分批检测
        for batch_start in range(0, len(chars), _BATCH_SIZE):
            batch = chars[batch_start: batch_start + _BATCH_SIZE]
            chars_text = "\n\n".join(_char_text(c) for c in batch)
            batch_label = (
                f"（第{batch_start + 1}~{batch_start + len(batch)}人，"
                f"共{len(chars)}人）"
            ) if len(chars) > _BATCH_SIZE else ""

            user_prompt = (
                f"请检查以下人物档案{batch_label}之间是否存在设定矛盾或不合理之处：\n\n"
                f"{chars_text}\n\n{_CHECK_INSTRUCTION}"
            )
            response = self.llm.generate(self.SYSTEM_PROMPT, user_prompt, temperature=self.temperature)
            batch_problems = [
                line.strip()
                for line in response.split("\n")
                if line.strip() and line.strip() != "无明显矛盾"
            ]
            all_problems.extend(batch_problems)

        return all_problems

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

    def update_character_profile(self, char_name: str,
                                  existing_profile: str,
                                  instruction: str) -> dict | None:
        """
        根据用户指令定向更新人物档案，返回更新后的字段字典。
        由 CanvasAgent.dispatch() 调用。
        """
        system_prompt = (
            "你是一位专业的小说人物档案编辑。根据用户指令，对现有人物档案做定向修改。\n"
            "只修改用户明确要求改动的字段，其余字段原样保留。\n"
            "输出合法 JSON 对象，包含 name 字段及所有需要保留/修改的字段，不含其他文字。\n"
            "可用字段：name, role, age, gender, personality, background, appearance, "
            "growth_arc, current_state, motivations, speech_patterns, secrets, is_main, "
            "abilities（JSON数组字符串）, relationships（JSON对象字符串）"
        )
        user_prompt = (
            f"【人物姓名】{char_name}\n\n"
            f"【现有档案】\n{existing_profile or '（暂无）'}\n\n"
            f"【修改指令】\n{instruction}\n\n"
            "请输出修改后的完整人物档案 JSON："
        )
        try:
            raw = self.llm.generate(system_prompt, user_prompt,
                                    max_tokens=2048, temperature=self.temperature)
            js = raw[raw.find("{"):raw.rfind("}") + 1]
            data = _safe_json_loads(js)
            if isinstance(data, dict) and data.get("name"):
                return data
        except Exception:
            pass
        return None

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

输出要求：
- 直接输出小说正文，不要加解释或注释
- 字数控制在章纲要求的范围内（一般2000-4000字）
- 分段合理，对话独占一行"""

    def __init__(self, novel_id: int, model_id: str = None, temperature: float = None):
        self.novel_id = novel_id
        self.temperature = temperature
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.llm = NovelLLM(model_id, novel_id=self.novel_id)

    def write_chapter(self, chapter_number: int,
                       word_target: int = 3000,
                       word_count_tolerance: float = 0.30,
                       stream_callback=None,
                       review_feedback: str = "") -> str:
        """
        生成指定章节的正文。

        流式模式：直接生成并回调，不做字数重试（避免用户看到内容被清空重写）。
        非流式模式：生成后检测字数，超出范围则最多重试 2 次，
                    每次重试把实际字数和偏差告诉 LLM，引导精准修正。

        Args:
            chapter_number: 章节序号
            word_target: 目标字数
            word_count_tolerance: 字数容差（默认 0.30 = ±30%）
            stream_callback: 流式输出回调（提供时走流式路径，不重试）
            review_feedback: 上轮审核反馈（用于针对性改写）

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
            style_ref_text = (novel.style_reference_text or "").strip()

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
                # 追加参考原文片段作为感性对照（前400字）
                if style_ref_text:
                    _preview = style_ref_text[:400]
                    if len(style_ref_text) > 400:
                        _preview += "…"
                    style_block += (
                        "\n【风格参考原文片段（感受语感，不要直接复制）】\n"
                        + _preview
                    )
            elif style_ref_text:
                # 没有结构化档案但有参考文本：注入前600字让LLM直接感受风格
                _preview = style_ref_text[:600]
                if len(style_ref_text) > 600:
                    _preview += "…"
                style_block = (
                    "\n【写作风格参考（请模仿以下文本的语感、节奏和表达习惯，不要直接复制）】\n"
                    + _preview
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

        # 动态注入去AI味规则（读用户配置，fallback DEFAULT_DEAI_RULES）
        from core.config import DEFAULT_DEAI_RULES
        deai_rules = (
            novel.deai_rules.strip()
            if novel and novel.deai_rules and novel.deai_rules.strip()
            else DEFAULT_DEAI_RULES
        )
        deai_block = f"\n【去AI味规则】\n{deai_rules}\n"

        word_min = int(word_target * (1 - word_count_tolerance))
        word_max = int(word_target * (1 + word_count_tolerance))

        def _build_prompt(extra_feedback: str = "") -> str:
            """组装 user prompt，extra_feedback 用于重试时注入字数偏差提示。"""
            fb = review_feedback
            if extra_feedback:
                fb = (fb + "\n" + extra_feedback) if fb else extra_feedback
            feedback_block = (
                f"\n【上一稿审核反馈（本次必须针对性改进，这些问题不能再出现）】\n{fb}\n"
                if fb else ""
            )
            return f"""📌 本章任务：第{chapter_number}章《{chapter.title or ''}》

【字数硬约束】
- 最少：{word_min} 字（不足会显得情节仓促、铺垫缺失）
- 最多：{word_max} 字（超过此上限属于硬性违规，系统会截断导致内容残缺）
- 目标：{word_target} 字
{deai_block}{feedback_block}
【写作上下文（下方所有设定和前情均须遵守）】
{writing_context}{style_block}{platform_block}

【本章写作要求】
1. 【核心事件完整性】章纲所述的核心事件必须在正文中有完整的"开始→过程→结果"三阶段。
   不能只有结论句（如"他终于明白了"），要有完整经过（如"他翻开档案→逐行核对→手指停在那行字上"）。

2. 【人设一致性】每个出场人物的言行必须与其档案相符。
   寡言的人物对白不能冗长；冷静的人物不能轻易崩溃；能力边界不得超出设定。

3. 【叙事连贯】本章开头需自然衔接上章结尾的时间、地点与人物状态。
   章内场景转换须给出合理的物理过渡（不能无缘由地"场景切换"）。

4. 【章节收束】结尾需包含：
   ✓ 一个向下一章延伸的悬念或情感落点
   ✗ 说书人式总结（"这意味着……""命运的齿轮开始转动……"）

请直接输出正文，从标题开始："""

        # ── 流式路径：直接生成，不做字数重试 ──────────────────────────────
        if stream_callback:
            content_parts = []
            for text_chunk in self.llm.generate_stream(
                self.SYSTEM_PROMPT, _build_prompt(), max_tokens=12000,
                temperature=self.temperature
            ):
                content_parts.append(text_chunk)
                stream_callback(text_chunk)
            return "".join(content_parts)

        # ── 非流式路径：生成后检测字数，超出范围最多重试 2 次 ──────────────
        _MAX_WORD_RETRIES = 2
        content = ""
        for attempt in range(_MAX_WORD_RETRIES + 1):
            extra_fb = ""
            if attempt > 0:
                actual = len(content)
                if actual < word_min:
                    deficit = word_min - actual
                    extra_fb = (
                        f"⚠️ 字数不足重试（第{attempt}次）：上次生成 {actual} 字，"
                        f"距最低要求还差 {deficit} 字。请补充更多场景细节、对话过程或心理描写，"
                        f"使总字数达到 {word_min} 字以上。保持已有内容的风格和情节，"
                        f"直接输出完整的重写版本。"
                    )
                else:
                    surplus = actual - word_max
                    extra_fb = (
                        f"⚠️ 字数超限重试（第{attempt}次）：上次生成 {actual} 字，"
                        f"超出上限 {surplus} 字。请精简重复描写、压缩过渡段落，"
                        f"使总字数控制在 {word_max} 字以内。保持核心情节完整，"
                        f"直接输出完整的重写版本。"
                    )

            content = self.llm.generate(
                self.SYSTEM_PROMPT, _build_prompt(extra_fb),
                max_tokens=12000, temperature=self.temperature
            )
            actual = len(content)
            if word_min <= actual <= word_max:
                break  # 字数达标，退出重试
            if attempt < _MAX_WORD_RETRIES:
                import sys
                print(
                    f"[WriterAgent] 第{chapter_number}章字数偏差（{actual}字，"
                    f"目标 {word_min}~{word_max}），发起第{attempt + 1}次重试…",
                    file=sys.stderr
                )
        else:
            # 所有重试耗尽仍超范围，打印警告后返回最终结果
            import sys
            print(
                f"[WriterAgent] 第{chapter_number}章重试{_MAX_WORD_RETRIES}次后"
                f"字数仍为 {len(content)}（目标 {word_min}~{word_max}），使用当前版本。",
                file=sys.stderr
            )
        return content

    def summarize_chapter(self, chapter_number: int, title: str,
                           content: str) -> tuple[str, list[str]]:
        """
        为已写完的章节生成详细摘要和关键事件列表。
        摘要用于后续章节的写作上下文注入 —— 写手Agent不会看到原始正文，
        只会看到这里的摘要，因此必须足够详细和具体。

        Returns:
            (summary: str, key_events: list[str])
        """
        user_prompt = f"""请为以下小说章节生成详细摘要和关键事件列表。

⚠️ 重要：这份摘要是后续章节写作时唯一的前情参考。写手Agent不会看到原始正文，只能通过你的摘要了解之前发生了什么。
因此摘要必须足够详细、具体、可操作 —— 写手需要知道的不只是"发生了什么"，更是"怎么发生的"和"留下了什么"。

第{chapter_number}章《{title}》

【章节正文】
{content[:5000]}{"...(已截断)" if len(content) > 5000 else ""}

请按以下结构输出JSON格式：
{{
  "summary": "300-800字的详细章节摘要，必须包含：\\n"
            "1. 本章发生的所有重要情节事件（按时间顺序，写清楚起因-经过-结果）\\n"
            "2. 每个出场人物的关键行动、决策和对话要点（具体做了什么、说了什么）\\n"
            "3. 人物关系和状态的重要变化（谁的态度变了、谁获得了新能力/新信息）\\n"
            "4. 重要的场景/环境变化（地点转移、氛围转变）\\n"
            "5. 本章埋下的伏笔和悬而未决的问题\\n"
            "6. 本章回收的伏笔（如有）\\n"
            "7. 章节结尾的悬念或钩子（下一章写作需要接住什么）",
  "key_events": [
    "关键事件1：具体描述（谁+做了什么+结果如何）",
    "关键事件2：...",
    ...
  ]
}}

注意：
- 摘要不要写成章纲的复述，要写实际正文中发生的具体内容
- key_events 每一条都应该是可独立理解的动作描述，不要含糊
- 如果你看到的内容被截断了，请基于可见部分尽力提炼"""
        system = "你是一位专业的小说编辑，擅长从正文中提炼结构化的情节摘要。输出合法JSON，不要有其他文字。"

        try:
            response = self.llm.generate(system, user_prompt, max_tokens=1024, cache_system=False, temperature=self.temperature)

            # 尝试从响应中提取 JSON
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0:
                try:
                    data = _safe_json_loads(response[json_start:json_end])
                    if isinstance(data, dict):
                        summary = data.get("summary", "")
                        events = data.get("key_events", [])
                        if summary and len(summary.strip()) >= 50:
                            return summary, events
                    elif isinstance(data, list) and data and isinstance(data[0], dict):
                        summary = data[0].get("summary", "")
                        events = data[0].get("key_events", [])
                        if summary and len(summary.strip()) >= 50:
                            return summary, events
                except Exception as json_err:
                    print(f"⚠️ 第{chapter_number}章 JSON 解析失败：{json_err}，将使用全文作为摘要")

            # JSON 解析失败或 summary 为空时的 fallback：
            # 从响应中提取纯文本摘要（去掉 JSON 标记、代码块等）
            fallback = response.strip()
            # 去掉常见的 LLM 前缀/后缀
            for prefix in ["```json", "```", "好的", "以下是"]:
                if fallback.startswith(prefix):
                    fallback = fallback[len(prefix):].strip()
            # 如果包含 JSON 结构但解析失败，取前 500 字作为摘要
            if "{" in fallback and "}" in fallback:
                # 尝试提取 summary 字段的文本值
                import re as _re
                m = _re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', fallback)
                if m:
                    fallback = m.group(1).replace("\\n", "\n").replace('\\"', '"')
                else:
                    fallback = fallback[:500]
            elif len(fallback) > 800:
                fallback = fallback[:800]
            if fallback and len(fallback.strip()) >= 30:
                print(f"⚠️ 第{chapter_number}章使用 fallback 摘要（{len(fallback)}字）")
                return fallback.strip(), []

        except Exception as e:
            print(f"⚠️ 第{chapter_number}章摘要生成失败：{e}")
        return "", []

    def regenerate_chapter_summary(self, chapter_number: int) -> tuple[str, list[str]]:
        """
        为已有章节重新生成详细摘要并持久化到数据库。
        用于回填旧章节（之前可能只有简短摘要或没有摘要）。

        Args:
            chapter_number: 章节序号

        Returns:
            (summary: str, key_events: list[str])
        """
        chapter = self.memory.chapter_mem.get_chapter(chapter_number)
        if not chapter:
            raise ValueError(f"第{chapter_number}章不存在")
        if not chapter.content:
            raise ValueError(f"第{chapter_number}章尚未写入正文，无法生成摘要")

        summary_text, key_events = self.summarize_chapter(
            chapter_number, chapter.title or "", chapter.content
        )
        if summary_text:
            self.memory.chapter_mem.save_chapter_summary(
                chapter_number, summary_text, key_events
            )
        return summary_text, key_events

    def regenerate_all_summaries(self, progress_callback=None,
                                   chapter_numbers: list[int] = None) -> dict:
        """
        为当前小说的已有正文章节批量重新生成详细摘要。

        Args:
            progress_callback: 可选的回调函数，接收进度描述字符串
            chapter_numbers: 可选，指定要重新生成的章节号列表。
                             传 None 则处理所有已有正文的章节。

        Returns:
            {"success": N, "failed": M, "skipped": K}
        """
        from core.models import Chapter
        q = self.memory.chapter_mem.db.query(Chapter).filter(
            Chapter.novel_id == self.novel_id,
            Chapter.content.isnot(None),
            Chapter.content != ""
        )
        if chapter_numbers is not None:
            q = q.filter(Chapter.chapter_number.in_(chapter_numbers))
        chapters = q.order_by(Chapter.chapter_number).all()

        success, failed, skipped = 0, 0, 0
        for ch in chapters:
            try:
                if progress_callback:
                    progress_callback(f"📝 正在为第{ch.chapter_number}章生成摘要...")
                summary_text, key_events = self.summarize_chapter(
                    ch.chapter_number, ch.title or "", ch.content
                )
                if summary_text:
                    self.memory.chapter_mem.save_chapter_summary(
                        ch.chapter_number, summary_text, key_events
                    )
                    success += 1
                else:
                    skipped += 1
            except Exception as e:
                print(f"⚠️ 第{ch.chapter_number}章摘要回填失败：{e}")
                failed += 1

        return {"success": success, "failed": failed, "skipped": skipped}

    def regenerate_section(self, chapter_number: int, section_text: str,
                             instruction: str) -> str:
        """
        重新生成章节中的特定段落
        """
        chapter = self.memory.global_mem.get_chapter_outline(chapter_number)
        global_ctx = self.memory.global_mem.build_global_context()

        # 动态注入去AI味规则
        from core.config import DEFAULT_DEAI_RULES
        novel = self.memory.global_mem.get_novel()
        deai_rules = (
            novel.deai_rules.strip()
            if novel and novel.deai_rules and novel.deai_rules.strip()
            else DEFAULT_DEAI_RULES
        )

        user_prompt = f"""相关设定：
{global_ctx[:2000]}

需要重写的段落：
{section_text}

修改要求：{instruction}

【去AI味规则（重写时必须遵守）】
{deai_rules}

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

    def __init__(self, novel_id: int, model_id: str = None, temperature: float = None):
        self.novel_id = novel_id
        self.temperature = temperature
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.model_id = model_id
        self.detector = ConflictDetector(novel_id, model_id, temperature)

    def review_chapter(self, chapter_number: int, content: str) -> ReviewReport:
        """
        对章节内容进行全面审核（旧版，保留兼容）
        返回详细的审核报告
        """
        return self.detector.detect_chapter_conflicts(chapter_number, content)

    def pipeline_review(self, chapter_number: int, content: str,
                         progress_callback=None) -> dict:
        """
        三关卡漏斗式流水线审核（新版）。

        依次通过三个独立关卡，任一关 REJECT 则立即返回 feedback 供写作 Agent 修正。
        三关全 PASS 则计算最终加权得分。

        Returns:
            {
                "passed": bool,
                "final_score": float,
                "gates": [GateResult, ...],   # 已执行关卡的结果
                "reject_gate": str | None,    # 触发熔断的关卡名
                "reject_feedback": str | None, # 精准修改批注
            }
        """
        from core.config import (
            GATE_CONTEXT_THRESHOLD,
            GATE_CONTINUITY_THRESHOLD,
            GATE_STYLISTIC_THRESHOLD,
            FINAL_SCORE_WEIGHTS,
        )
        from core.detector import GateResult

        gates_config = [
            ("context_sentry", self.detector.run_context_sentry, GATE_CONTEXT_THRESHOLD),
            ("global_continuity_judge", self.detector.run_continuity_judge, GATE_CONTINUITY_THRESHOLD),
            ("stylistic_refiner", self.detector.run_stylistic_refiner, GATE_STYLISTIC_THRESHOLD),
        ]

        gate_results = []
        for gate_name, gate_fn, threshold in gates_config:
            if progress_callback:
                label_map = {
                    "context_sentry": "🎯 关卡1：局部校对（大纲+人设）",
                    "global_continuity_judge": "🌐 关卡2：全局场记（状态+时空）",
                    "stylistic_refiner": "✨ 关卡3：文风打磨（去AI痕迹）",
                }
                progress_callback(f"{label_map.get(gate_name, gate_name)}（阈值 {threshold}）...")

            result = gate_fn(chapter_number, content)
            result.passed = result.total_score >= threshold

            if result.action == "PASS" and not result.passed:
                result.action = "REJECT"

            gate_results.append(result)
            if progress_callback:
                action_icon = "✅" if result.passed else "❌"
                progress_callback(
                    f"  {action_icon} {result.total_score:.1f}/10"
                    + (f"（熔断！需 ≥{threshold}）" if not result.passed else "")
                )

            if not result.passed:
                return {
                    "passed": False,
                    "final_score": result.total_score,
                    "gates": gate_results,
                    "reject_gate": gate_name,
                    "reject_feedback": result.feedback,
                }

        # 全部通过：计算加权最终分数
        scores = [g.total_score for g in gate_results]
        final_score = (
            scores[0] * FINAL_SCORE_WEIGHTS[0]
            + scores[1] * FINAL_SCORE_WEIGHTS[1]
            + scores[2] * FINAL_SCORE_WEIGHTS[2]
        )
        return {
            "passed": True,
            "final_score": round(final_score, 2),
            "gates": gate_results,
            "reject_gate": None,
            "reject_feedback": None,
        }

    def parallel_pipeline_review(
        self,
        chapter_number: int,
        content: str,
        progress_callback=None,
    ) -> dict:
        """
        并行四审核流水线（新版，替换三关卡串行方案）。

        四个 Reviewer 并行执行，合并所有 REJECT 的 feedback 后由 WriterAgent 一次性修正。
        已通过的 Reviewer 在后续轮次中不再重复审核。
        每个 Reviewer 最多参与 2 次审核（初审 + 1次重审）。

        Returns:
            {
                "passed": bool,
                "final_score": float,
                "gates": [GateResult, ...],     # 最后一轮全部4个关卡的结果
                "all_gate_results": list[dict], # 所有轮次所有关卡结果
                "reject_feedbacks": str | None, # 最终仍未通过的 feedback（供调用方记录）
                "rounds": int,                  # 实际执行轮数
            }
        """
        from core.detector import GateResult

        _GATE_LABELS = {
            "plot_aligner":       "🎯 剧情对齐",
            "character_guard":    "🛡️ 人设世界观",
            "continuity_tracker": "🔗 时空状态",
            "style_refiner":      "✨ 文风去AI",
        }
        _MAX_ROUNDS = 2  # 每个 Reviewer 最多参与2次（初审+1次重审）

        all_gate_results: list[dict] = []
        passed_gate_names: set[str] = set()
        final_score = 0.0
        last_review: dict = {}

        for round_idx in range(_MAX_ROUNDS):
            if progress_callback:
                skip_info = (f"（跳过已通过：{', '.join(_GATE_LABELS.get(g, g) for g in passed_gate_names)}）"
                             if passed_gate_names else "")
                progress_callback(
                    f"🔍 第{round_idx + 1}轮并行审核{skip_info}..."
                )

            last_review = self.detector.run_parallel_review(
                chapter_number, content, skip_gates=passed_gate_names
            )

            # 记录本轮结果
            for gate_result in last_review["gates"]:
                all_gate_results.append({**gate_result.to_dict(), "round": round_idx + 1})

            # 更新已通过的关卡
            passed_gate_names = last_review["passed_gate_names"]
            final_score = last_review["weighted_score"]

            # 打印每个关卡结果
            if progress_callback:
                for gate_result in last_review["gates"]:
                    icon = "✅" if gate_result.passed else "❌"
                    label = _GATE_LABELS.get(gate_result.gate_name, gate_result.gate_name)
                    progress_callback(
                        f"  {icon} {label}：{gate_result.total_score:.1f}/10"
                    )
                progress_callback(
                    f"  加权得分：{final_score:.1f}/10"
                    + ("  ✅ 全部通过" if last_review["all_passed"] else
                       f"  ❌ 未通过：{', '.join(_GATE_LABELS.get(g, g) for g in last_review['failed_gate_names'])}")
                )

            if last_review["all_passed"]:
                break

        # 补全未执行到的关卡（被 skip 的）用满分占位，保证 gates 始终有4条
        executed_names = {g.gate_name for g in last_review.get("gates", [])}
        all_four_gates = list(last_review.get("gates", []))
        for gate_name, label in _GATE_LABELS.items():
            if gate_name not in executed_names:
                # 该关卡在本轮被跳过（已通过），补一条满分占位
                all_four_gates.append(GateResult(
                    gate_name=gate_name, total_score=10.0,
                    breakdown={}, action="PASS",
                    feedback="本轮已通过，跳过", passed=True
                ))

        return {
            "passed": last_review.get("all_passed", False),
            "final_score": round(final_score, 2),
            "gates": all_four_gates,
            "all_gate_results": all_gate_results,
            "reject_feedbacks": last_review.get("reject_feedbacks") or None,
            "rounds": _MAX_ROUNDS if not last_review.get("all_passed") else (
                next((i + 1 for i, _ in enumerate(range(_MAX_ROUNDS))
                      if last_review.get("all_passed")), _MAX_ROUNDS)
            ),
        }

    def auto_fix_minor_issues(self, content: str,
                               report: ReviewReport,
                               novel_id: int) -> str:
        """
        自动修复轻微问题（严重程度 < 4 的问题）。
        返回修复后的内容。

        .. deprecated::
            主流程已切换至并行四审核架构（WriterAgent 直接改写），
            此方法仅保留供外部兼容调用，不再由写作流水线内部使用。
        """
        minor_conflicts = [c for c in report.conflicts if c.severity < 4]
        if not minor_conflicts:
            return content

        llm = NovelLLM(self.model_id, novel_id=self.novel_id)
        conflicts_desc = "\n".join([
            f"- {c.conflict_type}（位置：{c.location[:100]}）：{c.description}。建议：{c.solutions[0] if c.solutions else ''}"
            for c in minor_conflicts
        ])

        system_prompt = (
            "你是一位小说编辑，负责修复文本中的轻微问题。\n"
            "修复原则：\n"
            "1. 只修复指定的问题，不改变故事情节和整体内容\n"
            "2. 修复过程中同样必须遵守去AI味规则，不得引入新的AI痕迹\n"
            "3. 直接输出修复后的完整文本"
        )

        # 动态注入去AI味规则
        from core.config import DEFAULT_DEAI_RULES
        novel = self.memory.global_mem.get_novel()
        deai_rules = (
            novel.deai_rules.strip()
            if novel and novel.deai_rules and novel.deai_rules.strip()
            else DEFAULT_DEAI_RULES
        )

        # 正文截断至 8000 字，防止超长章节撑爆 context
        content_truncated = content[:8000]
        truncate_note = "\n...(正文过长已截断，请仅修复可见部分)" if len(content) > 8000 else ""

        user_prompt = f"""原文：
{content_truncated}{truncate_note}

需要修复的问题（只修复这些，其他不变）：
{conflicts_desc}

【去AI味规则（修复时同样必须遵守）】
{deai_rules}

请输出修复后的完整正文："""

        return llm.generate(system_prompt, user_prompt, max_tokens=12000, temperature=self.temperature)

    def fix_all_issues(self, content: str,
                        report: "ReviewReport",
                        novel_id: int,
                        chapter_number: int = 0) -> str:
        """
        根据完整审核报告修复所有问题（不限严重程度）。
        用于审核-修改自动循环中的每次改写。

        .. deprecated::
            主流程已切换至并行四审核架构（WriterAgent 直接改写），
            此方法仅保留供外部兼容调用，不再由写作流水线内部使用。
        """
        if not report.conflicts:
            return content

        llm = NovelLLM(self.model_id, novel_id=self.novel_id)
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
            "4. 修改过程中同样必须遵守去AI味规则，不得引入新的AI痕迹\n"
            "5. 直接输出完整修改后正文，不加任何说明或标注"
        )

        # 动态注入去AI味规则
        from core.config import DEFAULT_DEAI_RULES
        novel = self.memory.global_mem.get_novel()
        deai_rules = (
            novel.deai_rules.strip()
            if novel and novel.deai_rules and novel.deai_rules.strip()
            else DEFAULT_DEAI_RULES
        )

        # 正文截断至 8000 字，防止超长章节撑爆 context
        content_truncated = content[:8000]
        truncate_note = "\n...(正文过长已截断，请仅修复可见部分)" if len(content) > 8000 else ""

        user_prompt = (
            f"章节正文：\n{content_truncated}{truncate_note}\n\n"
            f"本次审核评分：{report.overall_score:.1f}/10\n"
            f"{chapter_goal_block}"
            f"需要修复的问题（共 {len(report.conflicts)} 条）：\n{conflicts_desc}\n\n"
            f"【去AI味规则（修改时同样必须遵守）】\n{deai_rules}\n\n"
            "请修复以上所有问题并确保章纲目标得以实现，直接输出完整修改后正文："
        )
        return llm.generate(system_prompt, user_prompt, max_tokens=12000, temperature=self.temperature)

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
1. **去AI痕迹**：严格按照下方注入的「去AI味规则」逐条处理，不得遗漏

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
        self.temperature = temperature
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.llm = NovelLLM(model_id, novel_id=self.novel_id)

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

        # 动态注入去AI味规则（读用户配置，fallback DEFAULT_DEAI_RULES）
        from core.config import DEFAULT_DEAI_RULES
        deai_rules = (
            novel.deai_rules.strip()
            if novel and novel.deai_rules and novel.deai_rules.strip()
            else DEFAULT_DEAI_RULES
        )

        user_prompt = f"""请对以下小说章节进行文笔润色：

{f"【风格要求】{style_desc}" if style_desc else ""}
{f"【目标平台写作风格（润色时需符合此平台和标签的读者审美）】\n{platform_style_text}" if platform_style_text else ""}
{f"【参考作者风格档案（请模仿以下风格特征进行润色）】\n{style_profile_text}" if style_profile_text else ""}
{f"【风格参考样例】\n{style_reference[:3000]}{'...(已截断)' if len(style_reference) > 3000 else ''}" if style_reference else ""}
【去AI味规则（润色时必须逐条执行，这是硬性要求）】
{deai_rules}

【待润色内容】
{content[:8000]}{"...(内容过长已截断，请润色可见部分)" if len(content) > 8000 else ""}

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
        # 动态注入去AI味规则
        from core.config import DEFAULT_DEAI_RULES
        novel = self.memory.global_mem.get_novel()
        deai_rules = (
            novel.deai_rules.strip()
            if novel and novel.deai_rules and novel.deai_rules.strip()
            else DEFAULT_DEAI_RULES
        )

        user_prompt = f"""对以下文字片段进行修改：

【原文】
{selected_text}

【修改要求】
{instruction}

【去AI味规则（修改时同样必须遵守）】
{deai_rules}

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
            max_tokens=4096, cache_system=False, temperature=self.temperature
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
        self.temperature = temperature
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.llm = NovelLLM(model_id, novel_id=self.novel_id)

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
            max_tokens=4096, cache_system=False, temperature=self.temperature
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
        self.temperature = temperature
        self.llm = NovelLLM(model_id)

    def chat(self, messages: list) -> str:
        """
        多轮对话，返回 AI 回复。
        messages: [{"role": "user"/"assistant", "content": "..."}]

        历史压缩：超过 _MAX_ROUNDS 轮时，把早期消息压缩成一条摘要 assistant 消息，
        只将最近 _RECENT_KEEP 轮原文传给 LLM，避免长对话撑爆 context window。
        """
        _MAX_ROUNDS = 10
        _RECENT_KEEP = 8
        _MAX_SUMMARY_CHARS = 500

        total_rounds = len(messages) // 2
        if total_rounds > _MAX_ROUNDS:
            keep_msgs = _RECENT_KEEP * 2
            old_msgs = messages[:-keep_msgs]
            recent_msgs = messages[-keep_msgs:]
            # 把早期对话拼成纯文本摘要（不调用 LLM，直接截取前 150 字）
            summary_lines = []
            for m in old_msgs:
                role_label = "用户" if m["role"] == "user" else "助手"
                text = m["content"][:150].replace("\n", " ")
                if len(m["content"]) > 150:
                    text += "…"
                summary_lines.append(f"{role_label}：{text}")
            summary = (
                f"[早期对话摘要（共{len(old_msgs) // 2}轮，已压缩）]\n"
                + "\n".join(summary_lines)
            )
            if len(summary) > _MAX_SUMMARY_CHARS:
                summary = summary[:_MAX_SUMMARY_CHARS] + "…（已截断）"
            effective_msgs = [{"role": "assistant", "content": summary}] + recent_msgs
        else:
            effective_msgs = messages

        return self.llm.generate_chat(
            self.SYSTEM_PROMPT, effective_msgs,
            max_tokens=1024, temperature=self.temperature
        )

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
            max_tokens=1024, cache_system=False, temperature=self.temperature
        )
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            return _safe_json_loads(response[json_start:json_end])
        raise ValueError(f"项目配置提取失败：{response[:300]}")


# ======================================
# Agent 8: Canvas 协作 Agent
# ======================================


# ======================================
# 工具类: 字段级内容压缩器
# ======================================

class FieldCompressor:
    """
    字段级 LLM 压缩器。
    用于把世界观 value / 大纲字段等长文本提炼为精简注入版本。
    压缩结果仅用于 prompt 注入，原始内容不受影响。

    世界观：LLM 根据 key 名自动判断信息密度需求，在给定上限内自由决定保留多少。
    大纲字段：按字段分别传入目标字数（theme≤100、conflict≤300、arc/ending≤500）。
    """

    _SYSTEM = (
        "你是一位专业的小说设定编辑。你的任务是将一段详细的设定文字提炼为精简版本，"
        "供 AI 写作助手快速参考。\n\n"
        "核心原则：\n"
        "- 【必须保留】所有关键规则、核心矛盾、重要限制、独特概念、不可违反的设定约束\n"
        "- 【可以删除】举例说明、重复表述、修辞性语言、背景铺垫\n"
        "- 规则密集的内容（力量体系、世界规则、社会结构等）保留更多细节\n"
        "- 背景描述类内容（历史事件、地理环境等）可以更大幅压缩\n"
        "- 以简洁的要点或短句输出，直接输出结果，不加引导语"
    )

    def __init__(self, novel_id: int, model_id: str = None):
        from core.llm import NovelLLM, DEFAULT_MODEL_ID
        _model = model_id or DEFAULT_MODEL_ID
        self.llm = NovelLLM(_model, novel_id=novel_id)

    def compress(self, label: str, text: str, target_chars: int,
                 hint: str = "") -> str:
        """
        压缩单个字段文本。

        Args:
            label:        字段标签（如「力量体系」），帮助模型理解语境
            text:         原始文本
            target_chars: 目标字数上限（供 LLM 参考，非硬截断）
            hint:         额外提示（如「这是全书主要矛盾，需保留所有冲突层次」）

        Returns:
            压缩后文本；若压缩失败则返回原文。
        """
        user_prompt = (
            f"【字段名】{label}\n"
            f"【目标字数上限】{target_chars} 字\n"
        )
        if hint:
            user_prompt += f"【额外说明】{hint}\n"
        user_prompt += f"\n【原文】\n{text}"

        try:
            result = self.llm.generate(
                self._SYSTEM, user_prompt,
                max_tokens=1024, cache_system=False, temperature=0.0
            )
            return result.strip()
        except Exception:
            # 压缩失败时回退原文（不硬截断，保留完整内容）
            return text

    def compress_world_setting(
        self,
        world: dict,
        threshold: int,
        target_max: int,
    ) -> dict:
        """
        对世界观 dict 中超过阈值的 value 逐条压缩。
        target_max 是给 LLM 的参考上限，LLM 会根据 key 名（力量体系/地理/历史等）
        自行判断应保留的密度，在上限内自由决定实际长度。
        短于阈值的条目直接保留原文。
        """
        compressed = {}
        for k, v in world.items():
            v_str = str(v)
            if len(v_str) > threshold:
                compressed[k] = self.compress(
                    label=k, text=v_str, target_chars=target_max
                )
            else:
                compressed[k] = v_str
        return compressed

    def compress_outline_fields(
        self,
        outline,                        # NovelOutline ORM 对象
        field_targets: dict[str, int],  # {field_name: target_chars}
        threshold: int,
    ) -> dict:
        """
        对 NovelOutline 指定字段按各自目标字数压缩。
        只处理超过 threshold 的字段，未超阈值的直接跳过（调用方读原文）。
        返回 {field_name: compressed_text}。
        """
        _FIELD_HINTS = {
            "theme":           "这是全书核心主题，提炼为一句核心立意即可",
            "main_conflict":   "这是全书主要矛盾，需保留所有冲突层次和对立关系",
            "protagonist_arc": "这是主角成长弧光，需保留每个关键转折点和阶段变化",
            "ending_summary":  "这是结局概要，需保留各条故事线的收束方式和最终走向",
        }
        result = {}
        for field, target in field_targets.items():
            val = getattr(outline, field, None) or ""
            if len(val) > threshold:
                result[field] = self.compress(
                    label=field,
                    text=val,
                    target_chars=target,
                    hint=_FIELD_HINTS.get(field, ""),
                )
        return result

class CanvasAgent:
    """
    通用 Canvas AI 协作 Agent。
    注入当前文档内容 + 小说全局上下文，支持多轮对话。
    当 AI 建议修改文档时，以 ```markdown ... ``` 代码块输出新版本。
    """

    # ── 角色基础定位（不含代码块模板，保持简短）────────────────────────
    _ROLE_BASE = "你是贯穿全书创作的 AI 协作者，能处理大纲、世界观、人物设定、章节写作的一切问题。"

    # ── 代码块模板（按类型分组，按需拼入）────────────────────────────────
    _BLOCK_TEMPLATES = {
        "outline": """\
若要更新整体大纲（包含前提设定、核心主题、主要矛盾等）：
```outline
（完整大纲 Markdown，保留现有内容并补充修改）
```""",
        "settings": """\
若要更新世界观/背景/系统设定文档：
```settings
（完整设定 Markdown 文档）
```""",
        "world": """\
若要对世界观做结构化键值更新（如"世界规则"、"力量体系"、"社会结构"等键）：
```world
{"键名": "完整的设定内容", "另一个键": "完整内容"}
```""",
        "characters": """\
若要创建或更新人物档案（必须包含 name 字段，其余字段可选填）：
```characters
[{"name": "姓名", "role": "主角/配角/反派", "personality": "性格", "background": "背景", "is_main": true}]
```
删除人物：`[{"name": "姓名", "action": "delete"}]`""",
        "chapter": """\
若要给出当前章节的正文或修改版本：
```chapter
（完整章节正文）
```""",
        "volume": """\
若要创建或更新卷大纲：
```volume
{"volume_number": 1, "title": "卷名", "summary": "卷简介", "main_conflict": "核心矛盾", "start_chapter": 1, "end_chapter": 30}
```""",
        "foreshadowing": """\
若要创建新的伏笔条目：
```foreshadowing
[{"name": "伏笔名", "description": "描述", "importance": "high/medium/low", "set_chapter": 1, "collect_by_chapter": 10}]
```""",
        "style": """\
若要记录写作风格偏好（用户对文笔/节奏/对话提出意见时输出）：
```style
{"overall_style": "...", "dialogue_style": "...", "polish_instructions": "..."}
```
只填本次涉及的维度，未提及的不输出。""",
    }

    # ── 按页面决定需要哪些代码块 ──────────────────────────────────────────
    # page 值对应 UI 路由（None = 通用/sidebar）
    _PAGE_BLOCKS: dict[str | None, list[str]] = {
        "outline":       ["outline", "volume", "foreshadowing", "world"],
        "writing":       ["chapter", "style", "foreshadowing"],
        "characters":    ["characters"],
        "settings":      ["settings", "world"],
        "visualization": ["foreshadowing"],
        "export":        [],
        None:            ["outline", "settings", "world", "characters", "chapter",
                          "volume", "foreshadowing", "style"],   # sidebar/全局
    }

    _ROLE_PROMPTS = {
        "global": None,  # 占位，实际由 _build_role_prompt() 动态生成
    }

    @classmethod
    def _build_role_prompt(cls, page: str | None) -> str:
        """根据当前页面动态拼装 role_prompt，只包含本页面可能用到的代码块模板。"""
        block_keys = cls._PAGE_BLOCKS.get(page, cls._PAGE_BLOCKS[None])
        if not block_keys:
            return cls._ROLE_BASE

        blocks_text = "\n\n".join(cls._BLOCK_TEMPLATES[k] for k in block_keys
                                  if k in cls._BLOCK_TEMPLATES)
        return (
            f"{cls._ROLE_BASE}\n\n"
            "【输出规范】\n"
            "普通讨论、分析和建议直接用正常文字，不加代码块。\n"
            "需要提供可写入系统的内容时，使用以下专属代码块格式：\n\n"
            f"{blocks_text}\n\n"
            "用户可一键将代码块内容应用到对应位置。"
        )

    def __init__(self, novel_id: int, model_id: str = None, role: str = "global", temperature: float = None):
        from core.memory import MemoryManager
        from core.llm import DEFAULT_MODEL_ID
        self.novel_id = novel_id
        self.role = role
        self.temperature = temperature
        self.memory = MemoryManager(novel_id)
        _model = model_id or self.memory.global_mem.get_novel().llm_model or DEFAULT_MODEL_ID
        self.llm = NovelLLM(_model, novel_id=self.novel_id)

    def chat(self, messages: list, document_content: str = "",
             page: str = None, chapter_number: int = None,
             hint: str = "") -> str:
        """
        多轮对话。
        document_content: 当前文档内容（注入上下文，截断至6000字）
        page: 当前所在页面（用于按页面裁剪 role_prompt 中的代码块模板）
        chapter_number: 当前章节号（若提供则按章纲关键词过滤世界观）
        hint: 额外上下文提示（dispatch 降级时传入识别到的 intent 说明）
        messages: [{"role": "user"/"assistant", "content": "..."}]

        历史压缩：当对话超过 MAX_CANVAS_HISTORY 轮时，把早期消息压缩成
        一条摘要 assistant 消息，只将最近 CANVAS_RECENT_ROUNDS 轮原文传给 LLM，
        避免长对话撑爆 context window。
        """
        _MAX_ROUNDS = 10       # 超过此轮数触发压缩
        _RECENT_KEEP = 8       # 压缩后保留最近几轮原文
        _MAX_SUMMARY_CHARS = 600  # 历史摘要最多占用字符数

        role_prompt = self._build_role_prompt(page)

        # ── 历史消息压缩 ─────────────────────────────────────────────
        # messages 是 [user, assistant, user, assistant, ...] 的列表
        # 每"轮"= 一条 user + 一条 assistant = 2 条消息
        total_rounds = len(messages) // 2
        if total_rounds > _MAX_ROUNDS:
            # 需要压缩的早期消息数量（保留最近 _RECENT_KEEP 轮）
            keep_msgs = _RECENT_KEEP * 2
            old_msgs = messages[:-keep_msgs]
            recent_msgs = messages[-keep_msgs:]

            # 把早期对话压缩为一段文字摘要（不再调用 LLM，直接拼文本）
            old_summary_lines = []
            for m in old_msgs:
                role_label = "用户" if m["role"] == "user" else "助手"
                text = m["content"][:150].replace("\n", " ")
                if len(m["content"]) > 150:
                    text += "…"
                old_summary_lines.append(f"{role_label}：{text}")
            old_summary = (
                f"[早期对话摘要（共{len(old_msgs)//2}轮，已压缩）]\n"
                + "\n".join(old_summary_lines)
            )
            if len(old_summary) > _MAX_SUMMARY_CHARS:
                old_summary = old_summary[:_MAX_SUMMARY_CHARS] + "…（已截断）"

            # 把摘要作为第一条 assistant 消息插入
            effective_msgs = [{"role": "assistant", "content": old_summary}] + recent_msgs
        else:
            effective_msgs = messages

        # 若提供章节号，从章纲提取关键词和出场人物，过滤世界观设定和人物档案（减少无关 token）
        chapter_keywords: set[str] | None = None
        canvas_active_chars: list[str] | None = None
        if chapter_number:
            ch = self.memory.global_mem.get_chapter_outline(chapter_number)
            if ch:
                from core.memory import _extract_chapter_keywords
                chapter_keywords = _extract_chapter_keywords(ch)
                canvas_active_chars = ch.get_outline_characters() or None

        global_ctx = self.memory.global_mem.build_global_context(
            chapter_keywords=chapter_keywords,
            active_chars=canvas_active_chars,
            current_chapter=chapter_number,
        )

        system_parts = [role_prompt, "", "---", "【小说上下文】", global_ctx]
        system_prompt = "\n".join(system_parts)

        # 当前文档内容 + 降级 hint 附加到最后一条 user 消息末尾
        _msgs = list(effective_msgs)
        suffix = ""
        if hint:
            suffix += f"\n\n---\n【系统提示】{hint}"
        if document_content.strip():
            doc_truncated = document_content[:6000]
            doc_note = "...(内容过长已截断)" if len(document_content) > 6000 else ""
            suffix += (
                f"\n\n---\n【当前文档内容（你可能需要据此修改）】\n"
                f"```markdown\n{doc_truncated}{doc_note}\n```"
            )
        if suffix:
            if _msgs and _msgs[-1]["role"] == "user":
                _msgs = _msgs[:-1] + [
                    {**_msgs[-1], "content": _msgs[-1]["content"] + suffix}
                ]
            else:
                _msgs.append({"role": "user", "content": suffix.strip()})

        return self.llm.generate_chat(system_prompt, _msgs, max_tokens=4096, temperature=self.temperature)

    def _classify_intent(self, user_message: str, chapter_number: int | None,
                          document_content: str) -> dict:
        """
        轻量意图分类：一次极短的 LLM 调用，返回 intent + params。

        intent 枚举：
          chat                    — 普通对话/讨论/建议，Canvas 自身回复
          rewrite_chapter_section — 修改/改写本章某部分内容
          rewrite_chapter_full    — 重写/重新生成整章
          review_chapter          — 审核/检查/分析当前章节
          update_character        — 修改/新增某人物属性/设定
          expand_outline          — 扩写/调整章纲或大纲

        返回格式：
          {
            "intent": "chat",
            "params": {
              "chapter_number": 3,       # 适用时
              "character_name": "李明",  # 适用时
              "instruction": "..."       # 用户意图的结构化描述
            },
            "confidence": 0.9            # 0~1，低于 0.6 降级为 chat
          }
        """
        _INTENT_SYSTEM = """你是一个意图分类器。根据用户消息，判断其意图类型并输出 JSON。

意图类型（只能选一个）：
- chat：普通讨论、建议、提问、设定分析，不需要执行任何写作/修改操作
- rewrite_chapter_section：明确要求修改/改写/调整当前章节的某个部分（非整章重写）
- rewrite_chapter_full：明确要求重写整章或重新生成本章
- review_chapter：明确要求检查/审核/分析当前章节的问题
- update_character：明确要求修改、完善或新增某个人物的设定属性
- expand_outline：明确要求扩写、修改或重新生成章纲/大纲内容

判断原则：
- 用户只是"讨论"或"建议"某件事 → chat
- 用户明确说"帮我改""修改一下""重写""生成" → 对应操作类 intent
- 不确定时选 chat，confidence 填 0.5

【重要】chapter_number 字段的含义：
- 填用户想要【修改/操作】的目标章节号，不是参考章节
- 例："根据第7-10章的摘要修改第11章章纲" → chapter_number = 11（11是目标，7-10是参考）
- 例："重写第3章" → chapter_number = 3
- 若用户提到多个章节，仔细区分"被修改的"和"作为参考的"

【重要】reference_chapters 字段：
- 用户明确提到要参考/基于某些章节的内容时填写，格式为 [start, end]（整数）
- 例："根据第7-10章的摘要" → reference_chapters = [7, 10]
- 没有明确提到参考章节时填 null

输出严格 JSON，不含其他文字：
{
  "intent": "chat",
  "params": {
    "chapter_number": null,
    "reference_chapters": null,
    "character_name": null,
    "instruction": "用一句话描述用户想做什么（操作类 intent 必填）"
  },
  "confidence": 0.9
}"""

        ctx_hint = ""
        if chapter_number:
            ctx_hint += f"当前所在章节：第{chapter_number}章。"
        if document_content and document_content.strip():
            ctx_hint += f"当前文档有内容（{len(document_content)}字）。"

        user_prompt = f"{ctx_hint}\n\n用户消息：{user_message[:500]}"

        try:
            raw = self.llm.generate(
                _INTENT_SYSTEM, user_prompt,
                max_tokens=256, cache_system=False, temperature=0.0
            )
            js = raw[raw.find("{"):raw.rfind("}") + 1]
            data = json.loads(js)
            if float(data.get("confidence", 1.0)) < 0.6:
                data["intent"] = "chat"
            return data
        except Exception:
            return {"intent": "chat", "params": {}, "confidence": 0.5}

    def dispatch(
        self,
        messages: list,
        document_content: str = "",
        page: str = None,
        chapter_number: int = None,
        progress_callback=None,
    ) -> dict:
        """
        Canvas 主入口：意图分类 → 路由执行 → 返回结果。

        流程：
          1. _classify_intent() 判断意图
          2. 操作类 intent → 调用对应 Agent/workflow 执行，出错则降级 chat
          3. chat intent → 直接调用 self.chat() 生成回复

        progress_callback(message: str)：供 UI 追加进度消息（异步任务用）

        返回：
          {
            "intent": str,
            "reply": str,                  # 主回复文本
            "progress_log": [str, ...],    # 执行过程日志（操作类路径）
            "result_content": str | None,  # 操作类结果正文
            "result_type": str | None,     # chapter / character / outline / None
            "degraded": bool,              # 是否发生了降级
          }
        """
        def _log(msg: str):
            if progress_callback:
                progress_callback(msg)

        user_message = messages[-1]["content"] if messages else ""
        result_base = {
            "intent": "chat",
            "reply": "",
            "progress_log": [],
            "result_content": None,
            "result_type": None,
            "degraded": False,
        }

        # ── Step 1: 意图分类 ──────────────────────────────────────
        classified = self._classify_intent(user_message, chapter_number, document_content)
        intent = classified.get("intent", "chat")
        params = classified.get("params", {})
        result_base["intent"] = intent

        # ── Step 2: 操作类路由 ────────────────────────────────────
        if intent == "rewrite_chapter_section":
            ch_num = params.get("chapter_number") or chapter_number or 1
            instruction = params.get("instruction") or user_message
            _log(f"✍️ 正在改写第{ch_num}章相关段落…")
            try:
                from core.workflow import NovelWorkflow
                wf = NovelWorkflow(self.novel_id)
                writer = wf.writer_agent
                novel = self.memory.global_mem.get_novel()
                _tol = float((novel.get_quality_config() if novel else {}).get(
                    "word_count_tolerance", 0.2))
                feedback_prompt = (
                    f"【用户修改意图】\n{instruction}\n\n"
                    f"【当前章节内容（在此基础上做定向修改）】\n"
                    f"{document_content[:4000] if document_content else '（无）'}\n\n"
                    f"请只修改用户指定的部分，保留其他内容不变，直接输出完整正文。"
                )
                revised = writer.write_chapter(
                    chapter_number=ch_num,
                    word_target=max(len(document_content), 1000) if document_content else 3000,
                    word_count_tolerance=_tol,
                    review_feedback=feedback_prompt,
                )
                wf.memory.chapter_mem.save_chapter_content(ch_num, revised, "content")
                wf.close()
                _log(f"✅ 改写完成（{len(revised)}字）")
                result_base["result_content"] = revised
                result_base["result_type"] = "chapter"
                result_base["reply"] = (
                    f"已根据你的要求完成改写（第{ch_num}章，共{len(revised)}字）。"
                    f"内容已自动保存，可在写作页查看。"
                )
                return result_base
            except Exception as e:
                _log(f"⚠️ 改写失败（{e}），切换为普通回复…")
                result_base["degraded"] = True

        elif intent == "rewrite_chapter_full":
            ch_num = params.get("chapter_number") or chapter_number or 1
            _log(f"✍️ 正在重写第{ch_num}章（含完整审核流程）…")
            try:
                from core.workflow import NovelWorkflow
                wf = NovelWorkflow(self.novel_id)
                novel = wf.memory.global_mem.get_novel()
                qcfg = novel.get_quality_config() if novel else {}
                _tol = float(qcfg.get("word_count_tolerance", 0.2))
                word_target = int(qcfg.get("word_target", 3000))
                wr = wf.write_and_review_chapter(
                    chapter_number=ch_num,
                    word_target=word_target,
                    word_count_tolerance=_tol,
                    auto_polish=True,
                    progress_callback=_log,
                )
                wf.close()
                if wr.success:
                    content = wr.data.get("content", "")
                    score = wr.data.get("overall_score", 0)
                    result_base["result_content"] = content
                    result_base["result_type"] = "chapter"
                    result_base["reply"] = (
                        f"第{ch_num}章已重写完成（{len(content)}字，得分 {score:.1f}/10）。"
                        f"内容已保存，可在写作页查看。"
                    )
                else:
                    result_base["reply"] = f"重写失败：{wr.message}"
                return result_base
            except Exception as e:
                _log(f"⚠️ 重写失败（{e}），切换为普通回复…")
                result_base["degraded"] = True

        elif intent == "review_chapter":
            ch_num = params.get("chapter_number") or chapter_number or 1
            _log(f"🔍 正在并行审核第{ch_num}章…")
            try:
                from core.workflow import NovelWorkflow
                wf = NovelWorkflow(self.novel_id)
                ch = wf.memory.chapter_mem.get_chapter(ch_num)
                content = (ch.content if ch and ch.content else "") or document_content
                if not content:
                    raise ValueError("找不到章节内容")
                parallel_result = wf.reviewer_agent.parallel_pipeline_review(
                    ch_num, content, progress_callback=_log
                )
                wf.close()
                gates = parallel_result.get("gates", [])
                score = parallel_result.get("final_score", 0)
                passed = parallel_result.get("passed", False)
                label_map = {
                    "plot_aligner": "剧情对齐",
                    "character_guard": "人设/世界观",
                    "continuity_tracker": "时空/状态",
                    "style_refiner": "文风去AI",
                }
                lines = [
                    f"**第{ch_num}章审核报告**（{'✅ 通过' if passed else '❌ 未通过'}，"
                    f"综合得分 {score:.1f}/10）\n"
                ]
                for g in gates:
                    icon = "✅" if g.passed else "❌"
                    lbl = label_map.get(g.gate_name, g.gate_name)
                    lines.append(f"{icon} **{lbl}**：{g.total_score:.1f}/10")
                    if not g.passed and g.feedback:
                        lines.append(f"  > {g.feedback[:200]}")
                result_base["reply"] = "\n".join(lines)
                return result_base
            except Exception as e:
                _log(f"⚠️ 审核失败（{e}），切换为普通回复…")
                result_base["degraded"] = True

        elif intent == "update_character":
            char_name = params.get("character_name", "")
            instruction = params.get("instruction") or user_message
            _log(f"👤 正在更新人物「{char_name}」的设定…")
            try:
                if not char_name:
                    raise ValueError("未识别到人物姓名")
                char_agent = CharacterAgent(self.novel_id, temperature=self.temperature)
                existing = self.memory.global_mem.get_character(char_name)
                existing_text = (
                    existing.to_chapter_relevant_profile(set(), set())
                    if existing else ""
                )
                updated_data = char_agent.update_character_profile(
                    char_name=char_name,
                    existing_profile=existing_text,
                    instruction=instruction,
                )
                char_agent.close()
                if updated_data:
                    self.memory.global_mem.save_character(updated_data)
                    _log(f"✅ 人物「{char_name}」设定已更新")
                    result_base["result_content"] = json.dumps(
                        updated_data, ensure_ascii=False, indent=2)
                    result_base["result_type"] = "character"
                    result_base["reply"] = (
                        f"已根据你的要求更新了「{char_name}」的设定，可在人物档案页查看。"
                    )
                else:
                    result_base["reply"] = f"「{char_name}」设定处理完成，但未返回结构化数据。"
                return result_base
            except Exception as e:
                _log(f"⚠️ 人物更新失败（{e}），切换为普通回复…")
                result_base["degraded"] = True

        elif intent == "expand_outline":
            instruction = params.get("instruction") or user_message
            ch_num = params.get("chapter_number") or chapter_number
            ref_range = params.get("reference_chapters")  # [start, end] 或 null

            if not ch_num:
                # 连目标章节都无法确定，降级 chat
                _log("⚠️ 无法确定要修改的章节号，切换为普通回复…")
                result_base["degraded"] = True
            else:
                _log(f"📖 正在修改第{ch_num}章章纲…")
                try:
                    outline_agent = OutlineAgent(self.novel_id, temperature=self.temperature)

                    # ── 组装参考摘要 ──────────────────────────────────────
                    # 将参考章节的摘要拼成一段文字，附加到用户指令里
                    reference_block = ""
                    if ref_range and isinstance(ref_range, list) and len(ref_range) == 2:
                        ref_start, ref_end = int(ref_range[0]), int(ref_range[1])
                        from core.memory import ChapterMemory
                        ch_mem = ChapterMemory(self.novel_id)
                        ref_chapters = ch_mem.get_chapters_by_range(ref_start, ref_end)
                        ch_mem.db.close()
                        if ref_chapters:
                            ref_lines = [f"【第{ref_start}章至第{ref_end}章的摘要信息（请以此为基础修改章纲）】"]
                            for rc in ref_chapters:
                                ref_lines.append(f"\n第{rc.chapter_number}章《{rc.title or ''}》")
                                if rc.summary:
                                    ref_lines.append(f"摘要：{rc.summary}")
                                    if rc.key_events:
                                        try:
                                            import json as _json
                                            evs = _json.loads(rc.key_events)
                                            if evs:
                                                ref_lines.append("关键事件：" + "；".join(evs))
                                        except Exception:
                                            pass
                                elif rc.content:
                                    ref_lines.append(f"正文片段：{rc.content[:300]}…")
                                else:
                                    ref_lines.append("（暂无摘要或正文）")
                            reference_block = "\n".join(ref_lines)
                        else:
                            reference_block = (
                                f"（注：第{ref_start}-{ref_end}章暂无摘要数据，"
                                f"请根据已有上下文推断修改方向）"
                            )

                    # 将参考摘要拼入修改指令
                    full_instruction = instruction
                    if reference_block:
                        full_instruction = f"{reference_block}\n\n【修改指令】{instruction}"

                    # ── 调用精准章纲修改（写回 DB 的 structured 路径） ──────
                    updated_data = outline_agent.refine_chapter_outline(
                        chapter_number=ch_num,
                        user_feedback=full_instruction,
                    )
                    outline_agent.close()

                    if updated_data and isinstance(updated_data, dict):
                        # 确保 chapter_number 不被意外改掉
                        updated_data["chapter_number"] = ch_num
                        # 写回数据库
                        self.memory.global_mem.save_chapter_outline(updated_data)
                        # 构造展示用的摘要文本
                        saved_ch = self.memory.global_mem.get_chapter_outline(ch_num)
                        result_content = saved_ch.to_outline_text() if saved_ch else str(updated_data)
                        _log(f"✅ 第{ch_num}章章纲已更新并写入数据库")
                        result_base["result_content"] = result_content
                        result_base["result_type"] = "outline"
                        ref_hint = (
                            f"（参考了第{ref_range[0]}-{ref_range[1]}章摘要）"
                            if ref_range else ""
                        )
                        result_base["reply"] = (
                            f"已根据你的要求修改第{ch_num}章章纲{ref_hint}，"
                            f"结果已直接写入数据库，可前往章节大纲页查看。"
                        )
                    else:
                        _log("⚠️ 章纲修改返回数据为空，切换为普通回复…")
                        result_base["degraded"] = True

                    return result_base
                except Exception as e:
                    _log(f"⚠️ 章纲修改失败（{e}），切换为普通回复…")
                    result_base["degraded"] = True

        # ── Step 3: chat 路径（含降级） ───────────────────────────
        result_base["intent"] = "chat"
        # 降级时把识别到的原始意图作为 hint 传入，帮助 chat() 聚焦上下文
        _degraded_hint = ""
        if result_base.get("degraded") and intent != "chat":
            _instruction = params.get("instruction", "")
            _ch = params.get("chapter_number") or chapter_number
            _degraded_hint = (
                f"用户原始意图为「{intent}」"
                + (f"（第{_ch}章）" if _ch else "")
                + (f"，指令：{_instruction}" if _instruction else "")
                + "，但自动执行失败，请以对话方式协助用户完成该任务。"
            )
        result_base["reply"] = self.chat(
            messages=messages,
            document_content=document_content,
            page=page,
            chapter_number=chapter_number,
            hint=_degraded_hint,
        )
        return result_base

    def close(self):
        self.memory.close()
