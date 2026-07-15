"""
Agentic 工具执行器
为 Agent 提供可主动调用的数据库查询工具集。

工具清单（共8个）：
  1. query_character(name)          — 查询角色完整档案
  2. search_characters(trait)       — 按特征关键词搜索角色
  3. query_foreshadowing(keyword)   — 搜索相关伏笔
  4. query_world_setting(section)   — 查询世界观某个分类
  5. search_past_chapters(query, top_k) — 语义检索历史章节片段
  6. query_chapter_summary(chapter_num) — 查询某章摘要与关键事件
  7. query_timeline(event_keyword)  — 查询时间线相关事件
  8. query_outline_range(start, end) — 查询连续多章的章纲

所有工具返回格式化文本字符串，供 LLM 直接消化。
"""

import json
from typing import Optional

from core.memory import MemoryManager


# ──────────────────────────────────────────────────────────────
# 工具说明文本（注入 LLM System Prompt 中）
# ──────────────────────────────────────────────────────────────

TOOL_DEFINITIONS = """
━━━ 可用工具（按需调用，调用格式见下方）━━━

你在生成内容前可以主动查询数据库获取所需信息。
每次回复中可包含一个或多个工具调用，格式如下：

<tool_call>
{"tool": "工具名", "args": {参数字典}}
</tool_call>

工具列表：

1. query_character
   — 查询指定角色的完整档案（性格、背景、当前状态、人际关系、成长弧等）
   — 参数：{"name": "角色名"}
   — 示例：{"tool": "query_character", "args": {"name": "苏瑾"}}

2. search_characters
   — 按特征关键词搜索相关角色（支持职业、性格、关系类型等）
   — 参数：{"trait": "关键词，可空格分隔多个"}
   — 示例：{"tool": "search_characters", "args": {"trait": "反派 幕后主使"}}

3. query_foreshadowing
   — 搜索包含关键词的伏笔（包括已回收和未回收的）
   — 参数：{"keyword": "关键词"}
   — 示例：{"tool": "query_foreshadowing", "args": {"keyword": "玉佩"}}

4. query_world_setting
   — 查询世界观某个分类的详细设定
   — 参数：{"section": "分类关键词，如 magic_system、地理、政治"}
   — 示例：{"tool": "query_world_setting", "args": {"section": "修炼体系"}}

5. search_past_chapters
   — 语义检索历史章节中的相关片段（向量搜索，找最相似的段落）
   — 参数：{"query": "检索描述", "top_k": 数量（默认5，最多10）}
   — 示例：{"tool": "search_past_chapters", "args": {"query": "苏瑾初次动用灵力的场景", "top_k": 5}}

6. query_chapter_summary
   — 查询指定章节的摘要与关键事件列表
   — 参数：{"chapter_num": 章节号（整数）}
   — 示例：{"tool": "query_chapter_summary", "args": {"chapter_num": 8}}

7. query_timeline
   — 查询时间线中包含关键词的重大事件
   — 参数：{"event_keyword": "关键词"}
   — 示例：{"tool": "query_timeline", "args": {"event_keyword": "皇城大典"}}

8. query_outline_range
   — 查询当前章**之前**连续多章的章纲（了解前情走向，绝对不能查当前章及之后的章节）
   — 参数：{"start": 起始章号, "end": 结束章号}（end 必须小于当前章号）
   — 示例（假设当前写第14章）：{"tool": "query_outline_range", "args": {"start": 10, "end": 13}}

━━━ 使用规则 ━━━
- 每次回复可同时包含多个 <tool_call> 块（并行查询，推荐一次性查完所有需要的信息）
- 获取到足够信息后，直接输出最终内容，不要再调用工具
- ⚠️ query_outline_range / query_chapter_summary 只能查当前章节之前的章节，查当前章或之后章节会返回错误
"""


# ──────────────────────────────────────────────────────────────
# 工具执行器
# ──────────────────────────────────────────────────────────────


class ToolExecutor:
    """
    工具执行器：解析工具调用请求并执行对应的数据库查询。
    所有工具均返回格式化文本字符串，方便 LLM 直接理解。
    """

    def __init__(self, memory: MemoryManager, current_chapter: Optional[int] = None):
        self.memory = memory
        # 当前正在写作/处理的章节号，向量检索时用于屏蔽未来章节
        self.current_chapter = current_chapter
        # 工具路由表
        self._registry = {
            "query_character": self._query_character,
            "search_characters": self._search_characters,
            "query_foreshadowing": self._query_foreshadowing,
            "query_world_setting": self._query_world_setting,
            "search_past_chapters": self._search_past_chapters,
            "query_chapter_summary": self._query_chapter_summary,
            "query_timeline": self._query_timeline,
            "query_outline_range": self._query_outline_range,
        }

    def execute(self, tool_name: str, args: dict) -> str:
        """
        执行指定工具，返回格式化结果文本。
        未知工具返回错误提示字符串（不抛异常，LLM 可从错误中恢复）。
        """
        handler = self._registry.get(tool_name)
        if not handler:
            available = ", ".join(self._registry.keys())
            return f"[错误] 未知工具 '{tool_name}'。可用工具：{available}"
        try:
            return handler(**args)
        except TypeError as e:
            return f"[错误] 工具 '{tool_name}' 参数错误：{e}"
        except Exception as e:
            return f"[错误] 工具 '{tool_name}' 执行失败：{e}"

    # ── 工具实现 ──────────────────────────────────────────────

    def _query_character(self, name: str) -> str:
        """查询角色完整档案"""
        if not name or not name.strip():
            return "[错误] 请提供角色名称"

        char = self.memory.global_mem.get_character(name.strip())
        if not char:
            # 尝试模糊搜索
            candidates = self.memory.global_mem.search_characters_by_trait(name.strip())
            if candidates:
                names = "、".join(c.name for c in candidates[:5])
                return f"[未找到角色 '{name}'，相似角色：{names}]\n请用 search_characters 进一步搜索，或检查名称是否正确。"
            return f"[未找到角色 '{name}'，数据库中暂无此角色]"

        return f"[角色档案：{char.name}]\n{char.to_profile_text()}"

    def _search_characters(self, trait: str) -> str:
        """按特征关键词搜索角色"""
        if not trait or not trait.strip():
            # 返回所有角色简介
            chars = self.memory.global_mem.get_all_characters()
            if not chars:
                return "[数据库中暂无角色]"
            lines = [f"[所有角色（共 {len(chars)} 个）]"]
            for c in chars:
                lines.append(f"• {c.to_brief_text()}")
            return "\n".join(lines)

        chars = self.memory.global_mem.search_characters_by_trait(trait.strip())
        if not chars:
            return f"[未找到与 '{trait}' 相关的角色]"

        lines = [f"[与 '{trait}' 相关的角色（共 {len(chars)} 个）]"]
        for char in chars:
            lines.append(f"\n{'─' * 40}")
            lines.append(char.to_profile_text())
        return "\n".join(lines)

    def _query_foreshadowing(self, keyword: str) -> str:
        """搜索相关伏笔。
        - keyword 非空：按关键词精确检索，返回匹配项完整内容
        - keyword 为空：返回分级摘要（紧急项完整 + 其余单行），避免全量噪音
        """
        if not keyword or not keyword.strip():
            foreshadowings = self.memory.global_mem.get_active_foreshadowings()
            if not foreshadowings:
                return "[当前没有活跃伏笔]"

            # 按到期章节分级：有截止且 ≤ 当前章+5 的为紧急，其余只给单行摘要
            current_ch = self.current_chapter
            urgent, others = [], []
            for f in foreshadowings:
                is_urgent = (
                    current_ch is not None
                    and f.collect_by_chapter is not None
                    and f.collect_by_chapter <= current_ch + 5
                )
                (urgent if is_urgent else others).append(f)

            lines = [f"[活跃伏笔摘要（共 {len(foreshadowings)} 条，请用关键词精确查询以获取完整内容）]"]
            if urgent:
                lines.append("【即将到期，必须处理】")
                for f in urgent:
                    lines.append(f.to_full_text())
            if others:
                brief = "、".join(
                    f"{f.name}（{f.description[:20] if f.description else ''}…）"
                    for f in others
                )
                lines.append(f"【其余 {len(others)} 条】{brief}")
                lines.append("→ 如需某条完整详情，请用关键词再次查询，例如 query_foreshadowing(keyword='玉佩')")
            return "\n".join(lines)

        results = self.memory.global_mem.search_foreshadowings_by_keyword(
            keyword.strip()
        )
        if not results:
            return f"[未找到与 '{keyword}' 相关的伏笔]"

        lines = [f"[与 '{keyword}' 相关的伏笔（共 {len(results)} 条）]"]
        for f in results:
            status_label = {
                "active": "待回收",
                "collected": "已回收",
                "abandoned": "已废弃",
            }.get(f.status, f.status)
            lines.append(f"\n[{status_label}] {f.to_full_text()}")
        return "\n".join(lines)

    def _query_world_setting(self, section: str = "") -> str:
        """查询世界观设定"""
        world = self.memory.global_mem.query_world_setting_section(section)
        if not world:
            return "[世界观设定为空]"

        if section and section.strip():
            header = f"[世界观 - '{section}' 相关设定（共 {len(world)} 条）]"
        else:
            header = f"[完整世界观设定（共 {len(world)} 条）]"

        lines = [header]
        for k, v in world.items():
            lines.append(f"\n{k}：{v}")
        return "\n".join(lines)

    def _search_past_chapters(self, query: str, top_k: int = 5) -> str:
        """语义检索历史章节片段"""
        if not query or not query.strip():
            return "[错误] 请提供检索描述"

        top_k = max(1, min(int(top_k), 10))  # 限制在 1-10 之间

        fragments = self.memory.fragment_mem.search_relevant(
            query.strip(), n_results=top_k, before_chapter=self.current_chapter
        )
        if not fragments:
            return f"[未找到与 '{query}' 相关的历史片段（向量索引可能为空，需先完成章节写作）]"

        lines = [f"[语义检索结果：'{query}'（共 {len(fragments)} 个片段）]"]
        for f in fragments:
            relevance_bar = "█" * int(f["relevance"] * 10) + "░" * (
                10 - int(f["relevance"] * 10)
            )
            lines.append(
                f"\n[第{f['chapter_number']}章《{f['title']}》| 相关度 {f['relevance']:.2f} {relevance_bar}]\n{f['content']}"
            )
        return "\n".join(lines)

    def _query_chapter_summary(self, chapter_num: int) -> str:
        """查询某章摘要与关键事件（只允许查当前章之前的章节）"""
        try:
            chapter_num = int(chapter_num)
        except (ValueError, TypeError):
            return "[错误] chapter_num 必须是整数"

        # 硬拦截：不允许查当前章及之后的章节
        if self.current_chapter is not None and chapter_num >= self.current_chapter:
            return f"[错误] query_chapter_summary 只能查当前章（第{self.current_chapter}章）之前的历史章节，不能查当前章或之后的章节"

        chapter = self.memory.chapter_mem.get_chapter(chapter_num)
        if not chapter:
            return f"[第 {chapter_num} 章不存在]"

        lines = [f"[第{chapter_num}章《{chapter.title or ''}》摘要]"]

        if chapter.summary:
            lines.append(f"\n摘要：{chapter.summary}")
        else:
            lines.append("\n摘要：（该章尚未生成摘要）")
            if chapter.content:
                preview = chapter.content[:300]
                lines.append(
                    f"正文片段（前300字）：{preview}{'...' if len(chapter.content) > 300 else ''}"
                )

        if chapter.key_events:
            try:
                events = json.loads(chapter.key_events)
                if events:
                    lines.append("\n关键事件：")
                    for ev in events:
                        lines.append(f"  • {ev}")
            except (json.JSONDecodeError, TypeError):
                pass

        # 章纲信息
        if chapter.outline_core_event:
            lines.append(f"\n章纲核心事件：{chapter.outline_core_event}")
        if chapter.outline_characters:
            try:
                chars = json.loads(chapter.outline_characters)
                if chars:
                    lines.append(f"出场人物：{'、'.join(chars)}")
            except (json.JSONDecodeError, TypeError):
                pass

        lines.append(
            f"\n状态：{chapter.status or '未知'} | 字数：{chapter.word_count or 0}"
        )

        return "\n".join(lines)

    def _query_timeline(self, event_keyword: str = "") -> str:
        """查询时间线相关事件"""
        events = self.memory.global_mem.search_timeline_by_keyword(
            event_keyword.strip()
        )
        if not events:
            if event_keyword.strip():
                return f"[未找到与 '{event_keyword}' 相关的时间线事件]"
            return "[时间线为空]"

        header = (
            f"[时间线 - '{event_keyword}' 相关事件（共 {len(events)} 条）]"
            if event_keyword.strip()
            else f"[完整时间线（共 {len(events)} 条事件）]"
        )
        lines = [header]
        for ev in events:
            lines.append(
                f"\n第{ev.chapter_number}章 [{ev.in_story_time or '时间未知'}] {ev.event_name}"
            )
            if ev.event_description:
                lines.append(f"  描述：{ev.event_description}")
            if ev.characters_involved:
                try:
                    chars = json.loads(ev.characters_involved)
                    if chars:
                        lines.append(f"  涉及人物：{'、'.join(chars)}")
                except (json.JSONDecodeError, TypeError):
                    lines.append(f"  涉及人物：{ev.characters_involved}")
            if ev.impact:
                lines.append(f"  影响：{ev.impact}")
        return "\n".join(lines)

    def _query_outline_range(self, start: int, end: int) -> str:
        """查询连续多章章纲（只允许查当前章之前的章节）"""
        try:
            start, end = int(start), int(end)
        except (ValueError, TypeError):
            return "[错误] start 和 end 必须是整数"

        if end < start:
            start, end = end, start
        if end - start > 20:
            end = start + 20  # 防止一次查询过多
            note = f"\n（查询范围超过20章，已自动截断至第 {end} 章）"
        else:
            note = ""

        # 硬拦截：不允许查当前章及之后的章节，防止 LLM 把未来章纲当作本章任务
        if self.current_chapter is not None:
            if start >= self.current_chapter:
                return f"[错误] query_outline_range 只能查当前章（第{self.current_chapter}章）之前的历史章节，不能查当前章或之后的章节"
            if end >= self.current_chapter:
                end = self.current_chapter - 1
                note += f"\n（end 已自动截断至第 {end} 章，不可查询当前章及之后）"

        chapters = self.memory.chapter_mem.get_chapters_by_range(start, end)
        if not chapters:
            return f"[第 {start}-{end} 章无章纲数据]"

        lines = [f"[第{start}-{end}章章纲（共 {len(chapters)} 章）]{note}"]
        for ch in chapters:
            lines.append(f"\n{'─' * 40}")
            lines.append(ch.to_outline_text())
        return "\n".join(lines)
