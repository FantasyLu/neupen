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
# 共享后处理 Mixin（WriterAgent / PolisherAgent 共用）
# ======================================

class _ContentPostProcessMixin:
    """
    破折号修复 + 禁止句式修正 + 比喻密度精简，
    WriterAgent 和 PolisherAgent 共同继承，避免重复代码。

    子类需提供：
      self.llm        — NovelLLM 实例
      self.SYSTEM_PROMPT — 系统提示词字符串
      _agent_tag      — 日志前缀，如 "WriterAgent" / "PolisherAgent"
    """

    _agent_tag: str = "Agent"

    # 明确的比喻词（多字词精确匹配；单字"像"用正则避免误计"好像/像样"等）
    _METAPHOR_WORDS_MULTI = ["如同", "仿佛", "宛如", "好似", "犹如", "恰似", "有如"]
    # 每千字比喻词出现次数超过此阈值才触发 LLM 审查
    _METAPHOR_DENSITY_THRESHOLD = 3.0

    @staticmethod
    def _strip_reasoning(text: str) -> str:
        """剥离 LLM 返回内容中可能残留的 <!--reasoning...-->(思维链注释块)。"""
        import re
        return re.sub(r'<!--reasoning.*?-->\s*', '', text, flags=re.DOTALL).lstrip()

    def _fix_forbidden_syntax(self, content: str, chapter_number: int = 0) -> str:
        """
        后处理：
        0. 剥离残留的 <!--reasoning...--> 思维链注释（安全带）。
        1. 程序化替换滥用的破折号"——"（保留合法用法）。
        2. 检测"不是……而是……"等绝对禁止句式，循环 LLM 修正直至清零或达到最大轮次。
        3. 冗余比喻精简（_fix_redundant_metaphors）。
        """
        import re, sys

        tag = self._agent_tag
        ch_label = f"第{chapter_number}章" if chapter_number else ""

        # ── Step 0: 剥离残留思维链注释 ─────────────────────────────────────
        content = self._strip_reasoning(content)

        # ── Step 1: 破折号滥用程序化替换 ──────────────────────────────────
        def _fix_em_dash(text: str) -> str:
            protected = {}
            counter = [0]

            def protect(m):
                key = f'\x00EMDASH{counter[0]}\x00'
                protected[key] = m.group(0)
                counter[0] += 1
                return key

            # 保护①：引号内的话语打断（引号 + ≤10字 + ——，紧接引号结束符）
            text = re.sub(r'(["""\u300e\u300a][^"""\u300f\u300b]{0,10}——["""\u300f\u300b])', protect, text)
            # 保护②：拟声词/音效延长（1-4字 + ——，两侧是标点/空白/行边界）
            text = re.sub(r'((?:^|(?<=[，。！？\n\s]))[\u4e00-\u9fff]{1,4}——(?=[，。！？\n\s]|$))', protect, text)

            # 替换：循环处理所有滥用破折号 → 句号（最多 20 轮防死循环）
            prev = None
            _dash_iters = 0
            while prev != text and _dash_iters < 20:
                prev = text
                _dash_iters += 1
                text = re.sub(r'([^，。！？\n\x00]{1,})——', r'\1。', text)

            for key, val in protected.items():
                text = text.replace(key, val)
            return text

        original_dash_count = content.count("——")
        if original_dash_count > 0:
            fixed_content = _fix_em_dash(content)
            remaining_dash_count = fixed_content.count("——")
            replaced = original_dash_count - remaining_dash_count
            if replaced > 0:
                print(
                    f"[{tag}] {ch_label}破折号：原 {original_dash_count} 处，"
                    f"替换 {replaced} 处，保留合法用法 {remaining_dash_count} 处。",
                    file=sys.stderr,
                )
            content = fixed_content

        # ── Step 2: 对比转折句式循环 LLM 修正（最多 5 轮，直至清零）──────────
        FORBIDDEN_PATTERNS = [
            re.compile(r"不是.{1,30}[，,]?\s*而是.{1,30}"),
            re.compile(r"不是.{1,30}[，,]\s*是.{1,30}"),
            # 无标点版：「不是A是B」，排除副词紧接「是」的误匹配
            re.compile(r"不是[\u4e00-\u9fff，,、\s]{1,20}(?<![都也还只就更才已曾很太那这什么])是[\u4e00-\u9fff]{2,20}"),
            # 跨句版：「不是X。[换行]是Y」或「不是X。不是Y。是Z」（句号分隔、允许跨行）
            re.compile(
                r"不是[\u4e00-\u9fff，,、的了着过]{1,30}[。．]\s*"
                r"(?:不是[\u4e00-\u9fff，,、的了着过]{1,30}[。．]\s*)*"
                r"是[\u4e00-\u9fff，,、的了着过]{1,30}",
                re.MULTILINE,
            ),
            re.compile(r"与其说.{1,30}不如说"),
            re.compile(r"与其.{1,30}不如.{1,30}"),
        ]

        def _find_hits(text: str) -> list[str]:
            result = []
            for pat in FORBIDDEN_PATTERNS:
                result.extend(pat.findall(text))
            return result

        MAX_FORBIDDEN_ROUNDS = 5

        hits = _find_hits(content)

        if not hits:
            return self._fix_redundant_metaphors(content, chapter_number)

        hit_lines = "\n".join(f"  - {h.replace(chr(10), ' ↵ ')}" for h in hits[:8])
        print(
            f"[{tag}] {ch_label}检测到 {len(hits)} 处禁止句式，发起自动修正…\n{hit_lines}",
            file=sys.stderr,
        )

        for round_i in range(1, MAX_FORBIDDEN_ROUNDS + 1):
            print(
                f"[{tag}] {ch_label}禁止句式修正第 {round_i}/{MAX_FORBIDDEN_ROUNDS} 轮"
                f"（当前 {len(hits)} 处）…",
                file=sys.stderr,
            )

            hit_lines = "\n".join(f"  - {h.replace(chr(10), ' ↵ ')}" for h in hits[:8])
            fix_prompt = f"""以下是一段小说正文，其中存在绝对禁止的对比转折句式（"不是……而是……"/"与其说……不如说……"等变体）。

【检测到的违规句子】
{hit_lines}

【需要修改的完整正文】
{content}

修改规则：
1. 将所有"不是A而是B"/"不是A，是B"/"不是A是B"/"不是A。是B"（含跨行/换行版本）/"与其说A不如说B"等句式，拆成两个独立陈述句，只写事实和动作，删去对比评论。
2. 仅修改违规句子，其余内容原样保留，不得添加、删减、改写其他段落。
3. 直接输出修改后的完整正文，不加任何说明或标注。"""

            content = self.llm.generate(
                self.SYSTEM_PROMPT,
                fix_prompt,
                max_tokens=max(8000, min(32000, len(content) * 2)),
                temperature=0.3,
            )

            hits = _find_hits(content)
            if not hits:
                print(
                    f"[{tag}] {ch_label}禁止句式已全部清除（{round_i} 轮）。",
                    file=sys.stderr,
                )
                break
            else:
                print(
                    f"[{tag}] {ch_label}第 {round_i} 轮完成，剩余 {len(hits)} 处。",
                    file=sys.stderr,
                )
        else:
            # for-else：循环正常跑完（未 break），说明 3 轮后仍有残余
            print(
                f"[{tag}] {ch_label}已执行 {MAX_FORBIDDEN_ROUNDS} 轮修正，"
                f"仍剩 {len(hits)} 处（未完全清除，使用当前版本）。",
                file=sys.stderr,
            )

        return self._fix_redundant_metaphors(content, chapter_number)

    def _fix_redundant_metaphors(self, content: str, chapter_number: int = 0) -> str:
        """
        后处理：循环统计比喻词密度，超过阈值则发起一轮 LLM 精简，
        直到密度低于阈值或达到最大轮次（5轮）为止。
        """
        import re, sys

        tag = self._agent_tag
        ch_label = f"第{chapter_number}章" if chapter_number else ""
        MAX_ROUNDS = 5

        def _count_density(text: str) -> tuple[int, float]:
            total = sum(text.count(w) for w in self._METAPHOR_WORDS_MULTI)
            total += len(re.findall(r'(?<![好])像(?!样|是这|是那|这样|那样|这种|那种)', text))
            density = total / max(len(text), 1) * 1000
            return total, density

        def _find_metaphor_sentences(text: str) -> list[str]:
            """提取含比喻词的句子（按句号/换行切割）"""
            # 按常见句末标点或换行切分
            sentences = re.split(r'(?<=[。！？\n])', text)
            hits = []
            metaphor_re = re.compile(
                r'(?:' + '|'.join(re.escape(w) for w in self._METAPHOR_WORDS_MULTI) +
                r'|(?<![好])像(?!样|是这|是那|这样|那样|这种|那种))'
            )
            for s in sentences:
                s = s.strip()
                if s and metaphor_re.search(s):
                    hits.append(s[:80] + ('…' if len(s) > 80 else ''))
            return hits

        total, density = _count_density(content)
        print(
            f"[{tag}] {ch_label}比喻词统计：{total} 处"
            f"（密度 {density:.1f}/千字，阈值 {self._METAPHOR_DENSITY_THRESHOLD}/千字）",
            file=sys.stderr,
        )

        if density < self._METAPHOR_DENSITY_THRESHOLD:
            print(f"[{tag}] {ch_label}比喻密度正常，跳过精简。", file=sys.stderr)
            return content

        # 打印命中的比喻句子
        metaphor_hits = _find_metaphor_sentences(content)
        hit_lines = "\n".join(f"  - {s}" for s in metaphor_hits)
        print(
            f"[{tag}] {ch_label}检测到超密度比喻，发起精简（共 {len(metaphor_hits)} 句）：\n{hit_lines}",
            file=sys.stderr,
        )

        for round_i in range(1, MAX_ROUNDS + 1):
            print(
                f"[{tag}] {ch_label}比喻精简第 {round_i}/{MAX_ROUNDS} 轮"
                f"（当前密度 {density:.1f}/千字）…",
                file=sys.stderr,
            )
            fix_prompt = f"""以下小说正文中比喻用量偏多（全章约 {total} 处，密度 {density:.1f}/千字，目标低于 {self._METAPHOR_DENSITY_THRESHOLD}/千字），需要逐一审查并精简无效比喻。

【判断标准】
保留标准（满足其一即保留）：
✅ 比喻激活读者难以直接感知的通感体验（如气味、质地、层次感）
✅ 删去比喻后，该句的描述力/信息量明显下降

删除标准（满足其一即删）：
❌ 外貌/声音/动作已描述清楚，比喻只是重复说明（如"声音沙哑，像喉咙里塞了块砂纸"→直接写"声音沙哑"）
❌ 情绪/感觉已有身体反应描写，比喻画蛇添足（如"喉咙里有什么东西堵着，像一块没咽下去的药片"→保留前半句）
❌ 连续多个比喻描述同一事物，只保留最精准的一个，其余全删
❌ 出现以下高频 AI 套喻，无论上下文一律删除或替换为直接描写：
   "像砂纸"、"像石灰粉"、"像一块石头"、"像被人攥住"、"像被抽干"、"像溺水"、"像稻草"、"像刀割"、"像针扎"

【操作规则】
1. 逐句检查全文中所有含"像/如同/仿佛/宛如/好似/犹如/恰似/有如"的句子
2. 按上述标准判断，删除无效比喻，保留有效比喻
3. 删除比喻时直接去掉比喻部分，保留事实描写，不改写其他内容
4. 未含比喻的句子原样保留，不做任何修改
5. 直接输出修改后的完整正文，不加任何说明

【完整正文】
{content}"""

            content = self.llm.generate(
                self.SYSTEM_PROMPT,
                fix_prompt,
                max_tokens=max(8000, min(32000, len(content) * 2)),
                temperature=0.3,
            )

            total, density = _count_density(content)
            print(
                f"[{tag}] {ch_label}第 {round_i} 轮完成，"
                f"密度降至 {density:.1f}/千字（剩余 {total} 处）",
                file=sys.stderr,
            )

            if density < self._METAPHOR_DENSITY_THRESHOLD:
                print(f"[{tag}] {ch_label}比喻密度已达标，提前退出。", file=sys.stderr)
                break
        else:
            print(
                f"[{tag}] {ch_label}已执行 {MAX_ROUNDS} 轮精简，"
                f"当前密度 {density:.1f}/千字（未完全达标，使用当前版本）。",
                file=sys.stderr,
            )

        # 精简结束后打印仍保留的比喻句
        remaining_hits = _find_metaphor_sentences(content)
        if remaining_hits:
            remaining_lines = "\n".join(f"  - {s}" for s in remaining_hits)
            print(
                f"[{tag}] {ch_label}精简后仍保留的比喻句（共 {total} 处，{len(remaining_hits)} 句）：\n{remaining_lines}",
                file=sys.stderr,
            )

        return content


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
        sorted_fs = sorted(
            active_fs, key=lambda f: importance_order.get(f.importance, 1)
        )

        _MAX_FS = 30
        truncated = len(sorted_fs) > _MAX_FS
        display_fs = sorted_fs[:_MAX_FS]

        importance_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}
        lines = ["【待回收伏笔调度表（生成章纲时必须安排以下伏笔的回收，不得遗漏）】"]
        for f in display_fs:
            icon = importance_icon.get(f.importance, "🟡")
            deadline = (
                f"最晚第{f.collect_by_chapter}章回收。"
                if f.collect_by_chapter
                else "无截止时间要求。"
            )
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

    def generate_full_outline(
        self,
        logline: str,
        genre: str = "",
        world_setting: str = "",
        total_chapters: int = 100,
    ) -> dict:
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

**世界观提示：** {world_setting or "请自由发挥"}

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

        response = self.llm.generate(
            self.SYSTEM_PROMPT,
            user_prompt,
            max_tokens=32000,
            temperature=self.temperature,
        )

        # 提取 JSON
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0:
            outline_data = _safe_json_loads(response[json_start:json_end])
            return outline_data
        else:
            raise ValueError(f"大纲生成失败，无法解析JSON：{response[:500]}")

    def refine_chapter_outline(self, chapter_number: int, user_feedback: str) -> dict:
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
            chapter_keywords=chapter_keywords if chapter_keywords else None,
            active_chars=chapter.get_outline_characters() or None,
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

        response = self.llm.generate(
            self.SYSTEM_PROMPT, user_prompt, temperature=self.temperature
        )
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
            self.PARSE_DOCUMENT_PROMPT,
            user_prompt,
            max_tokens=8192,
            cache_system=False,
            temperature=self.temperature,
        )
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            return _safe_json_loads(response[json_start:json_end])
        raise ValueError(f"文档解析返回格式错误：{response[:300]}")

    def generate_chapter_range_outlines(
        self, start: int, end: int, description: str
    ) -> list[dict]:
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

        # 从 start~end 范围内已有章纲提取出场人物，过滤人物档案
        all_char_names = {c.name for c in self.memory.global_mem.get_all_characters()}
        range_active_chars: set[str] = set()
        for ch_num in range(start, end + 1):
            ch = self.memory.global_mem.get_chapter_outline(ch_num)
            if ch:
                range_active_chars.update(ch.get_outline_characters())
        # 同时把描述中提到的人物名也纳入（用于尚无章纲的情况）
        range_active_chars.update(desc_keywords & all_char_names)
        active_chars_filter = range_active_chars if range_active_chars else None

        global_ctx = self.memory.global_mem.build_global_context(
            chapter_keywords=desc_keywords if desc_keywords else None,
            active_chars=active_chars_filter,
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
            self.SYSTEM_PROMPT,
            user_prompt,
            max_tokens=min(4000 + ch_count * 600, 32000),
            temperature=self.temperature,
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
              "timeline_events": [{"event_name", "event_description", "in_story_time",
                                   "characters_involved", "impact"}],
              "foreshadowing_updates": [{"name", "description", "importance",
                                         "collect_by_chapter", "notes"}],
              "outline_updates": [{"field", "merged_content", "reason"}],
              "world_setting_updates": [{"key", "value", "reason"}]
            }

        归属规则（严格执行）：
          - 人物身体/状态/能力/关系变化  → character_updates
          - 本章重要事件（谁做了什么）   → timeline_events
          - 新揭示的伏笔/情报/线索       → foreshadowing_updates
          - 全书矛盾/弧光的结构性变化    → outline_updates（仅在根本性转折时）
          - 新揭示的世界观规则/设定      → world_setting_updates
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

        user_prompt = f"""请仔细阅读第{chapter_number}章正文，与现有大纲、设定和人物档案对照，找出需要同步记录的内容。

【第{chapter_number}章正文】
{content[:8000]}{"…（已截断）" if len(content) > 8000 else ""}

【现有大纲、设定与人物档案】
{global_ctx}

## 内容归属规则（必须严格遵守）

| 内容类型 | 放入哪个字段 |
|---|---|
| 人物身体状态、受伤、能力获得/失去、关系变化 | character_updates |
| 本章发生的重要事件（谁做了什么、结果如何） | timeline_events |
| 新埋下的伏笔、暗示、线索、情报 | foreshadowing_updates |
| 全书矛盾结构或主角弧光发生**根本性转折**（如主线矛盾从A变成B、主角价值观彻底颠覆） | outline_updates |
| 新揭示的世界运行规则、地名、势力、体系 | world_setting_updates |

**禁止将单章事件细节、人物状态变化、伏笔情报塞入 outline_updates！**

请按以下 JSON 格式输出，只列出章节中实际发生的变化，不要虚构：

{{
  "new_characters": [
    {{
      "name": "角色名",
      "role": "主角/配角/反派等",
      "personality": "性格特点",
      "background": "背景信息（从章节推断）",
      "relationships": {{"已有人物A": "关系描述"}},
      "reason": "为什么需要新增"
    }}
  ],
  "character_updates": [
    {{
      "name": "已有人物的姓名（必须在已有人物列表中）",
      "field": "current_state 或 growth_arc 或 abilities 或 relationships 之一",
      "new_value": "更新后的完整内容（替换旧值，非追加）",
      "reason": "本章中发生了什么导致此变化"
    }}
  ],
  "timeline_events": [
    {{
      "event_name": "事件名称（15字以内）",
      "event_description": "事件描述（谁在哪里做了什么，结果如何，100字以内）",
      "in_story_time": "故事内时间（如：第三年春、日落时分，不确定可留空）",
      "characters_involved": ["人物A", "人物B"],
      "impact": "事件对后续剧情的影响（50字以内）"
    }}
  ],
  "foreshadowing_updates": [
    {{
      "name": "伏笔名称（15字以内）",
      "description": "伏笔内容详细描述（100字以内）",
      "importance": "high 或 medium 或 low",
      "collect_by_chapter": null,
      "notes": "回收建议或关联线索（可留空）"
    }}
  ],
  "outline_updates": [
    {{
      "field": "main_conflict 或 protagonist_arc 或 ending_summary 等字段名",
      "merged_content": "替换原字段的完整新文本（≤300字，必须是一段连贯文字，体现结构性变化）",
      "reason": "为什么全书级矛盾/弧光发生了根本性变化（必须说明是结构性转折，不是单章细节）"
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

**outline_updates 极其严格**：绝大多数章节 outline_updates 应为 []。只有当本章造成全书矛盾的根本性结构改变（如：敌人变成盟友、主线目标彻底转变）时，才填写 outline_updates，且 merged_content 字数不超过 300 字。"""

        response = self.llm.generate(
            self.SYSTEM_PROMPT,
            user_prompt,
            max_tokens=4096,
            temperature=self.temperature,
        )
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            result = _safe_json_loads(response[json_start:json_end])
            # 过滤掉 character_updates 中不在已有人物列表里的条目（防止 AI 乱填）
            if isinstance(result, dict) and "character_updates" in result:
                result["character_updates"] = [
                    u
                    for u in result.get("character_updates", [])
                    if u.get("name") in existing_chars
                ]
            # 过滤掉 new_characters 中实际已存在的角色：
            #   ① 精确名命中（LLM 名字与库里完全一致但仍误报）
            #   ② 包含关系命中（别称/简称/尊称，如"老周"↔"周建国"），
            #      要求双方名字长度均 ≥ 2，避免单字误杀
            if isinstance(result, dict) and "new_characters" in result:
                def _is_existing(name: str) -> bool:
                    if name in existing_chars:
                        return True
                    if len(name) < 2:
                        return False
                    return any(
                        len(ex) >= 2 and (name in ex or ex in name)
                        for ex in existing_chars
                    )
                result["new_characters"] = [
                    c for c in result.get("new_characters", [])
                    if not _is_existing(c.get("name", ""))
                ]
            # 确保新字段存在（兼容旧版 LLM 未输出的情况）
            result.setdefault("timeline_events", [])
            result.setdefault("foreshadowing_updates", [])
            # outline_updates 字数兜底：超过 350 字的 merged_content 截断并警告
            for upd in result.get("outline_updates", []):
                mc = upd.get("merged_content", "")
                if len(mc) > 350:
                    upd["merged_content"] = mc[:350]
                    upd["_truncated"] = True
            return result
        return {
            "new_characters": [],
            "character_updates": [],
            "timeline_events": [],
            "foreshadowing_updates": [],
            "outline_updates": [],
            "world_setting_updates": [],
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
            rel_str = (
                ", ".join(f"{k}: {v}" for k, v in rels.items()) if rels else "（暂无）"
            )
            char_rel_lines.append(
                f"【{c.name}】（{c.role or ''}）— 现有关系：{rel_str}"
            )
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

        response = self.llm.generate(
            self.SYSTEM_PROMPT,
            user_prompt,
            max_tokens=4096,
            temperature=self.temperature,
        )
        arr_start = response.find("[")
        arr_end = response.rfind("]") + 1
        if arr_start >= 0 and arr_end > arr_start:
            result = _safe_json_loads(response[arr_start:arr_end])
            if isinstance(result, list):
                return [r for r in result if r.get("character") in existing_names]
        return []

    def expand_outline_section(
        self, context: str, instruction: str, chapter_number: int | None = None
    ) -> str:
        """
        根据用户指令扩写/调整大纲内容，返回修改后的大纲 Markdown 文本。
        由 CanvasAgent.dispatch() 调用。
        """
        chapter_hint = (
            f"当前聚焦章节：第{chapter_number}章。\n" if chapter_number else ""
        )
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
        return self.llm.generate(
            system_prompt, user_prompt, max_tokens=4096, temperature=self.temperature
        )

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

        response = self.llm.generate(
            self.SYSTEM_PROMPT,
            user_prompt,
            max_tokens=8192,
            temperature=self.temperature,
        )
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

        _MAX_CHAR_LEN = 800  # 单人档案最多800字，超出截断
        _BATCH_SIZE = 10  # 每批最多10人

        def _char_text(c) -> str:
            t = c.to_profile_text()
            if len(t) > _MAX_CHAR_LEN:
                t = t[:_MAX_CHAR_LEN] + "…（已截断）"
            return t

        all_problems: list[str] = []

        # 分批检测
        for batch_start in range(0, len(chars), _BATCH_SIZE):
            batch = chars[batch_start : batch_start + _BATCH_SIZE]
            chars_text = "\n\n".join(_char_text(c) for c in batch)
            batch_label = (
                (
                    f"（第{batch_start + 1}~{batch_start + len(batch)}人，"
                    f"共{len(chars)}人）"
                )
                if len(chars) > _BATCH_SIZE
                else ""
            )

            user_prompt = (
                f"请检查以下人物档案{batch_label}之间是否存在设定矛盾或不合理之处：\n\n"
                f"{chars_text}\n\n{_CHECK_INSTRUCTION}"
            )
            response = self.llm.generate(
                self.SYSTEM_PROMPT, user_prompt, temperature=self.temperature
            )
            batch_problems = [
                line.strip()
                for line in response.split("\n")
                if line.strip() and line.strip() != "无明显矛盾"
            ]
            all_problems.extend(batch_problems)

        return all_problems

    def update_character_state(
        self, character_name: str, chapter_number: int, state_update: str
    ):
        """
        更新人物当前状态（随剧情发展）
        """
        char = self.memory.global_mem.get_character(character_name)
        if char:
            char.current_state = f"（第{chapter_number}章后）{state_update}"
            self.memory.global_mem.db.commit()

    def update_character_profile(
        self, char_name: str, existing_profile: str, instruction: str
    ) -> dict | None:
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
            raw = self.llm.generate(
                system_prompt,
                user_prompt,
                max_tokens=2048,
                temperature=self.temperature,
            )
            js = raw[raw.find("{") : raw.rfind("}") + 1]
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


class WriterAgent(_ContentPostProcessMixin):
    """
    写手部 Agent
    职责：根据章纲、人设和历史内容逐章生成小说正文
    严格遵守人设和大纲，不随意偏离剧情
    """

    _agent_tag = "WriterAgent"

    SYSTEM_PROMPT = """你是一位才华横溢的中文小说作家，擅长创作引人入胜的中长篇小说。

⛔ 绝对禁止句式（违反即视为严重缺陷，无论任何理由）：
  × "不是……而是……"（含"不是A而是B"/"不是A，是B"/"不是A是B"等所有变体）
  × "与其说……不如说……"
  × "与其……不如……"
改写方法：拆成两句独立陈述，只写事实和动作，不写对比评论。
  × "那不是恐惧，是愤怒。" → ✓ "胸腔里堵着什么，像火。"
  × "不是他不想说，而是无从开口。" → ✓ "他张了张嘴。什么都没出来。"
请在下笔前将此禁令默念一遍，并在写完后逐句检查，发现即删改。

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
- 分段合理，对话独占一行
- **严禁在章节内部使用"第一节/第二节"、"（一）/（二）"、"1./2."等任何形式的小节标题或分节符号**，章节是一个整体，场景切换用空行或自然过渡句处理"""

    def __init__(self, novel_id: int, model_id: str = None, temperature: float = None):
        self.novel_id = novel_id
        self.temperature = temperature
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.llm = NovelLLM(model_id, novel_id=self.novel_id)

    def write_chapter(
        self,
        chapter_number: int,
        word_target: int = 3000,
        word_count_tolerance: float = 0.15,
        stream_callback=None,
        review_feedback: str = "",
    ) -> str:
        """
        生成指定章节的正文。

        流式模式：直接生成并回调，不做字数重试（避免用户看到内容被清空重写）。
        非流式模式：生成后检测字数，超出范围则最多重试 2 次，
                    每次重试把实际字数和偏差告诉 LLM，引导精准修正。

        Args:
            chapter_number: 章节序号
            word_target: 目标字数
            word_count_tolerance: 字数容差（默认 0.15 = ±15%）
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
                # 复用 PolisherAgent._format_style_profile() 保证 int 维度→语义文字转换一致
                _formatted = PolisherAgent.__new__(PolisherAgent)._format_style_profile(style_profile)
                if _formatted:
                    style_block = (
                        "\n【全书写作风格档案（请严格遵循以保持前后风格一致，这是最高优先级的风格指令）】\n"
                        + _formatted
                    )
                # 有 style_profile 时不再额外注入原文片段（结构化档案已涵盖风格信息）
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

        # 取上一章结尾状态，用于 prompt 中的具体衔接指令
        _prev_ending_state = ""
        if chapter_number > 1:
            _prev_ch = self.memory.chapter_mem.get_chapter(chapter_number - 1)
            if _prev_ch and getattr(_prev_ch, "ending_state", None) and _prev_ch.ending_state.strip():
                _prev_ending_state = _prev_ch.ending_state.strip()

        def _build_prompt(extra_feedback: str = "") -> str:
            """组装 user prompt，extra_feedback 用于重试时注入字数偏差提示。"""
            fb = review_feedback
            if extra_feedback:
                fb = (fb + "\n" + extra_feedback) if fb else extra_feedback
            feedback_block = (
                f"\n【上一稿审核反馈（本次必须针对性改进，这些问题不能再出现）】\n{fb}\n"
                if fb
                else ""
            )
            # 在 prompt 最顶部提取章纲关键字段，置顶强化 LLM 注意力
            _outline_lines = []
            if chapter.outline_core_event:
                _outline_lines.append(f"  ▶ 核心事件：{chapter.outline_core_event}")
            if chapter.outline_conflict:
                _outline_lines.append(f"  ▶ 主要冲突：{chapter.outline_conflict}")
            if chapter.outline_scene:
                _outline_lines.append(f"  ▶ 场景设定：{chapter.outline_scene}")
            if chapter.outline_emotion:
                _outline_lines.append(f"  ▶ 情感基调：{chapter.outline_emotion}")
            _chars = chapter.get_outline_characters()
            if _chars:
                _outline_lines.append(f"  ▶ 出场人物：{', '.join(_chars)}")
            _outline_mandate = (
                "\n\n🔒 本章章纲强制执行清单（以下内容必须全部体现在正文中，缺一项即视为章纲违规）：\n"
                + "\n".join(_outline_lines)
                + "\n写作完成后请逐项自检：每条 ▶ 是否在正文中有对应情节？"
            ) if _outline_lines else ""
            return f"""📌 本章任务：第{chapter_number}章《{chapter.title or ""}》{_outline_mandate}

⛔ 本章写作前再次确认：绝对禁止"不是……而是……""不是A，是B""与其说……不如说……"等对比转折句式。写完请自查，发现即改。

【字数硬约束】
- 最少：{word_min} 字（不足会显得情节仓促、铺垫缺失）
- 最多：{word_max} 字 ⚠️ 这是绝对上限，写到此处必须收尾，超出部分将被系统强制截断
- 目标：{word_target} 字
- 写作时每完成约 {word_target // 3} 字请自行估算剩余空间，不要等到写完才发现超限

【三段式字数分配参考】（总目标 {word_target} 字）
- 开篇铺垫（承接上章、建立场景氛围）：约 {word_target // 5} 字
- 核心事件展开（主要冲突/情节推进）：约 {word_target * 3 // 5} 字  ← 笔墨重心，但不要无限铺展细节
- 收束收尾（悬念钩子/情感落点）：约 {word_target // 5} 字
→ 写完每段请心算当前总字数，发现超出本段预算立即收笔推进下一段
{deai_block}{feedback_block}
【写作上下文（下方所有设定和前情均须遵守）】
{writing_context}{style_block}

【本章写作要求】
1. 【核心事件完整性】章纲所述的核心事件必须在正文中有完整的"开始→过程→结果"三阶段。
   不能只有结论句（如"他终于明白了"），要有完整经过（如"他翻开档案→逐行核对→手指停在那行字上"）。

2. 【人设一致性】每个出场人物的言行必须与其档案相符。
   寡言的人物对白不能冗长；冷静的人物不能轻易崩溃；能力边界不得超出设定。

3. 【叙事连贯】{"本章开头必须精准接续上章结尾状态：" + _prev_ending_state if _prev_ending_state else "本章开头需自然衔接上章结尾的时间、地点与人物状态。"}
   章内场景转换须给出合理的物理过渡（不能无缘由地"场景切换"）。

4. 【章节收束】结尾需包含：
   ✓ 一个向下一章延伸的悬念或情感落点
   ✗ 说书人式总结（"这意味着……""命运的齿轮开始转动……"）

5. 【禁止分节】不得在正文中使用任何小节标题或分节符号（如"第一节""（一）""1.""—·—"等）。
   多个场景之间用一个空行自然过渡，或用简短的衔接句切换，不要加标题。

请直接输出正文。正文第一行必须是固定格式的章节标题：# 第{chapter_number}章《{chapter.title or ""}》
章节编号严格锁定为 {chapter_number}，禁止写成其他数字。"""

        # ── 流式路径：直接生成，不做字数重试 ──────────────────────────────
        if stream_callback:
            content_parts = []
            for text_chunk in self.llm.generate_stream(
                self.SYSTEM_PROMPT,
                _build_prompt(),
                max_tokens=12000,
                temperature=self.temperature,
            ):
                content_parts.append(text_chunk)
                stream_callback(text_chunk)
            raw = "".join(content_parts)
            return self._fix_forbidden_syntax(raw, chapter_number)

        # ── 非流式路径：生成后检测字数，超出范围最多重试 2 次 ──────────────
        _MAX_WORD_RETRIES = 2
        # 根据字数上限估算 token 上限（中文约 1.5 字/token，留 20% 余量避免生成不完整）
        _max_tokens_write = max(4000, int(word_max / 1.5 * 1.2))
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
                        f"⚠️ 字数超限重试（第{attempt}次）：上次生成 {actual} 字，超出上限 {surplus} 字。\n"
                        f"请按以下优先级压缩，三阶段情节骨架保留完整：\n"
                        f"- 【最优先】删除冗余比喻：每处比喻问自己'去掉它信息量有损失吗'，没有损失就删\n"
                        f"- 【最优先】删除堆叠形容词：同一名词前保留最精准的一个定语，其余删去\n"
                        f"- 每个心理活动只保留最关键的一处，删去反复推敲/重复感受\n"
                        f"- 合并功能相同的过渡句和环境描写\n"
                        f"- 对话保留，但每句对话最多配一处动作/表情描写\n"
                        f"目标：使总字数控制在 {word_max} 字以内，开篇/核心/收尾结构完整。\n"
                        f"直接输出完整的压缩版本："
                    )

            content = self.llm.generate(
                self.SYSTEM_PROMPT,
                _build_prompt(extra_fb),
                max_tokens=_max_tokens_write,
                temperature=self.temperature,
            )
            actual = len(content)
            if word_min <= actual <= word_max:
                break  # 字数达标，退出重试
            if attempt < _MAX_WORD_RETRIES:
                import sys

                print(
                    f"[WriterAgent] 第{chapter_number}章字数偏差（{actual}字，"
                    f"目标 {word_min}~{word_max}），发起第{attempt + 1}次重试…",
                    file=sys.stderr,
                )
        else:
            # 所有重试耗尽仍超范围，打印警告后返回最终结果
            import sys

            print(
                f"[WriterAgent] 第{chapter_number}章重试{_MAX_WORD_RETRIES}次后"
                f"字数仍为 {len(content)}（目标 {word_min}~{word_max}），使用当前版本。",
                file=sys.stderr,
            )
        return self._fix_forbidden_syntax(content, chapter_number)

    def _truncate_to_limit(self, content: str, word_max: int, chapter_number: int) -> str:
        """
        按段落边界裁剪内容到字数上限以内。
        5% 容忍带内不截断。

        注意：当前不在自动写作流程中调用，保留供手动调试或外部工具使用。
        自动流程改为依赖 LLM 重写压缩，避免因截断造成章纲内容残缺。
        """
        import sys

        actual = len(content)
        # 5% 容忍带，轻微超限不截断
        if actual <= int(word_max * 1.05):
            return content

        print(
            f"[WriterAgent] 第{chapter_number}章字数 {actual} 超出上限 {word_max}，"
            f"执行段落截断…",
            file=sys.stderr,
        )

        paragraphs = content.split("\n")
        result_parts = []
        current_len = 0

        for para in paragraphs:
            para_len = len(para) + 1  # +1 for the newline
            if current_len + para_len > word_max:
                # 如果当前段落加进去会超限，检查是否已有足够内容
                if current_len >= int(word_max * 0.8):
                    break  # 已有足够内容，直接截断
                # 内容不足 80%，强行加入截断的段落保底
                remaining = word_max - current_len
                result_parts.append(para[:remaining])
                current_len += remaining
                break
            result_parts.append(para)
            current_len += para_len

        truncated = "\n".join(result_parts)
        print(
            f"[WriterAgent] 第{chapter_number}章截断后字数：{len(truncated)}",
            file=sys.stderr,
        )
        return truncated

    def summarize_chapter(
        self, chapter_number: int, title: str, content: str
    ) -> tuple[str, list[str], str]:
        """
        为已写完的章节生成详细摘要、关键事件列表和结尾状态速览。
        摘要用于后续章节的写作上下文注入 —— 写手Agent不会看到原始正文，
        只会看到这里的摘要，因此必须足够详细和具体。
        ending_state 是结构化的结尾状态（时间/地点/人物状态/悬念），
        专门用于下一章的衔接提示。

        Returns:
            (summary: str, key_events: list[str], ending_state: str)
        """
        user_prompt = f"""请为以下小说章节生成详细摘要、关键事件列表和结尾状态速览。

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
  ],
  "ending_state": "50-120字，专门描述章节最后的状态，格式：\\n"
                  "时间：（具体时刻/时段）\\n"
                  "地点：（具体场所）\\n"
                  "人物状态：（主要人物的情绪/身体/处境）\\n"
                  "悬念/钩子：（下一章必须接住的未解决问题或行动）"
}}

注意：
- 摘要不要写成章纲的复述，要写实际正文中发生的具体内容
- key_events 每一条都应该是可独立理解的动作描述，不要含糊
- ending_state 要精准，下一章写手靠它来判断应该从哪里开始
- 如果你看到的内容被截断了，请基于可见部分尽力提炼"""
        system = "你是一位专业的小说编辑，擅长从正文中提炼结构化的情节摘要。输出合法JSON，不要有其他文字。"

        try:
            response = self.llm.generate(
                system,
                user_prompt,
                max_tokens=1200,
                cache_system=False,
                temperature=self.temperature,
            )

            # 尝试从响应中提取 JSON
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0:
                try:
                    data = _safe_json_loads(response[json_start:json_end])
                    if isinstance(data, dict):
                        summary = data.get("summary", "")
                        events = data.get("key_events", [])
                        ending_state = data.get("ending_state", "")
                        if summary and len(summary.strip()) >= 50:
                            return summary, events, ending_state
                    elif isinstance(data, list) and data and isinstance(data[0], dict):
                        summary = data[0].get("summary", "")
                        events = data[0].get("key_events", [])
                        ending_state = data[0].get("ending_state", "")
                        if summary and len(summary.strip()) >= 50:
                            return summary, events, ending_state
                except Exception as json_err:
                    print(
                        f"⚠️ 第{chapter_number}章 JSON 解析失败：{json_err}，将使用全文作为摘要"
                    )

            # JSON 解析失败或 summary 为空时的 fallback：
            # 从响应中提取纯文本摘要（去掉 JSON 标记、代码块等）
            fallback = response.strip()
            # 去掉常见的 LLM 前缀/后缀
            for prefix in ["```json", "```", "好的", "以下是"]:
                if fallback.startswith(prefix):
                    fallback = fallback[len(prefix) :].strip()
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
                return fallback.strip(), [], ""

        except Exception as e:
            print(f"⚠️ 第{chapter_number}章摘要生成失败：{e}")
        return "", [], ""

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

        summary_text, key_events, ending_state = self.summarize_chapter(
            chapter_number, chapter.title or "", chapter.content
        )
        if summary_text:
            self.memory.chapter_mem.save_chapter_summary(
                chapter_number, summary_text, key_events, ending_state
            )
        return summary_text, key_events

    def regenerate_all_summaries(
        self, progress_callback=None, chapter_numbers: list[int] = None
    ) -> dict:
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
            Chapter.content != "",
        )
        if chapter_numbers is not None:
            q = q.filter(Chapter.chapter_number.in_(chapter_numbers))
        chapters = q.order_by(Chapter.chapter_number).all()

        success, failed, skipped = 0, 0, 0
        for ch in chapters:
            try:
                if progress_callback:
                    progress_callback(f"📝 正在为第{ch.chapter_number}章生成摘要...")
                summary_text, key_events, ending_state = self.summarize_chapter(
                    ch.chapter_number, ch.title or "", ch.content
                )
                if summary_text:
                    self.memory.chapter_mem.save_chapter_summary(
                        ch.chapter_number, summary_text, key_events, ending_state
                    )
                    success += 1
                else:
                    skipped += 1
            except Exception as e:
                print(f"⚠️ 第{ch.chapter_number}章摘要回填失败：{e}")
                failed += 1

        return {"success": success, "failed": failed, "skipped": skipped}

    def regenerate_section(
        self, chapter_number: int, section_text: str, instruction: str
    ) -> str:
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

        return self.llm.generate(
            self.SYSTEM_PROMPT, user_prompt, temperature=self.temperature
        )

    def write_chapter_agentic_gen(
        self,
        chapter_number: int,
        word_target: int = 3000,
        word_count_tolerance: float = 0.15,
    ):
        """
        Generator 版 Agentic 写章节。

        每个步骤以 (event_type, data) yield 出来，UI 可逐事件实时刷新。
        最终内容在 event_type == StepEvent.CONTENT_READY 的 data["content"] 里。

        字数重试和禁止句式修正在 generator 内部同步执行（不阻塞 UI，
        因为 generator 只有 yield 才让出控制权，两段代码都很短）。
        """
        from core.agentic_loop import AgenticLoop, StepEvent
        from core.tool_executor import ToolExecutor, TOOL_DEFINITIONS
        from core.config import DEFAULT_DEAI_RULES

        # 获取章节大纲（仅作为任务说明，不做全量上下文注入）
        chapter = self.memory.global_mem.get_chapter_outline(chapter_number)
        if not chapter:
            raise ValueError(f"第{chapter_number}章的章纲不存在，请先生成章纲")

        novel = self.memory.global_mem.get_novel()

        # 字数范围
        word_min = int(word_target * (1 - word_count_tolerance))
        word_max = int(word_target * (1 + word_count_tolerance))

        # ── 静态内容块（全部放入 System Prompt，利用 prompt cache）────────────

        # 去AI味规则
        deai_rules = (
            novel.deai_rules.strip()
            if novel and novel.deai_rules and novel.deai_rules.strip()
            else DEFAULT_DEAI_RULES
        )

        # 风格档案
        style_block = ""
        if novel:
            style_profile = novel.get_style_profile()
            style_ref_text = (novel.style_reference_text or "").strip()
            style_desc = novel.writing_style or ""
            _label_map = {
                "overall_style": "总体风格定位",
                "sentence_patterns": "句式特征",
                "vocabulary": "词汇风格",
                "narrative_voice": "叙述视角风格",
                "dialogue_style": "对话特点",
                "description_style": "描写特点",
                "rhythm_pacing": "节奏与节拍",
                "emotion_expression": "情感表达方式",
                "signature_techniques": "标志性手法",
                "polish_instructions": "写作核心指令",
            }
            if style_profile:
                lines = [
                    f"- {lbl}：{style_profile[k]}"
                    for k, lbl in _label_map.items()
                    if style_profile.get(k)
                ]
                if lines:
                    style_block = "\n【全书写作风格档案（请严格遵循）】\n" + "\n".join(
                        lines
                    )
            elif style_ref_text:
                _preview = style_ref_text[:600] + (
                    "…" if len(style_ref_text) > 600 else ""
                )
                style_block = (
                    "\n【写作风格参考（请模仿语感和节奏，不要直接复制）】\n" + _preview
                )
            elif style_desc:
                style_block = f"\n【写作风格要求】\n{style_desc}"

        # ── System Prompt：静态规则全在这里（prompt cache 生效区域）────────────
        # 工具定义移到 user prompt，兼容 DeepSeek 等对 system prompt 权重较低的模型
        agentic_system = f"""{self.SYSTEM_PROMPT}

在开始写作前，你必须通过工具查询信息，才能开始写正文。
章纲只提供了情节骨架，以下关键信息章纲里没有，只能从数据库获取：
- 人物的完整性格细节、当前心理状态、最新的人际关系变化
- 上一章结尾时各人物的具体处境与情绪
- 需要回收或埋下的伏笔的具体措辞和上下文背景
跳过查询直接写，会导致人设漂移、前后断层，这是不可接受的。

━━━ 写作硬性规则（每章必须遵守）━━━

【写作要求】
1. 核心事件完整性：章纲所述事件需有完整「起因→过程→结果」，不能只有结论句
2. 人设一致性：每个人物言行必须符合其档案设定，能力边界不得超出设定
3. 叙事连贯：开头自然衔接上章结尾的时间/地点/状态，场景转换要有物理过渡
4. 章节收束：结尾需包含悬念或情感落点，禁止说书人式总结
5. 禁止分节：不得在正文中使用任何小节标题或分节符号（"第一节""（一）""1.""—·—"等）；多场景之间用空行自然过渡{style_block}

【去AI味规则（必须遵守）】
{deai_rules}"""

        # ── User Prompt：工具定义 + 本次任务（动态部分）──────────────────────────
        # 工具定义放在 user prompt 开头，DeepSeek 等模型对 user message 遵循率更高
        # 这是每轮 Agent 对话中唯一变化的部分
        novel_info = ""
        if novel:
            novel_info = f"小说：《{novel.title}》 | 题材：{novel.genre or '未设定'}"
            if novel.logline:
                novel_info += f"\n简介：{novel.logline}"

        _agentic_outline_lines = []
        if chapter.outline_core_event:
            _agentic_outline_lines.append(f"  ▶ 核心事件：{chapter.outline_core_event}")
        if chapter.outline_conflict:
            _agentic_outline_lines.append(f"  ▶ 主要冲突：{chapter.outline_conflict}")
        if chapter.outline_scene:
            _agentic_outline_lines.append(f"  ▶ 场景设定：{chapter.outline_scene}")
        if chapter.outline_emotion:
            _agentic_outline_lines.append(f"  ▶ 情感基调：{chapter.outline_emotion}")
        _agentic_chars = chapter.get_outline_characters()
        if _agentic_chars:
            _agentic_outline_lines.append(f"  ▶ 出场人物：{', '.join(_agentic_chars)}")
        _agentic_outline_mandate = (
            "🔒 本章章纲强制执行清单（以下内容必须全部体现在正文中，缺一项即视为章纲违规）：\n"
            + "\n".join(_agentic_outline_lines)
            + "\n写作完成后请逐项自检：每条 ▶ 是否在正文中有对应情节？"
        ) if _agentic_outline_lines else ""

        initial_prompt = f"""{TOOL_DEFINITIONS}

{novel_info}

【本章任务】第{chapter_number}章《{chapter.title or ""}》
【字数要求】{word_min}~{word_max} 字（目标 {word_target} 字）⚠️ {word_max} 字是绝对上限，超出将被系统截断
【三段式分配参考】开篇铺垫约 {word_target // 5} 字 / 核心展开约 {word_target * 3 // 5} 字 / 收束收尾约 {word_target // 5} 字 → 写完每段心算总字数，超出本段预算立即推进
⛔ 写作前自查：绝对禁止"不是……而是……""不是A，是B""与其说……不如说……"等对比转折句式，写完请逐句检查，发现即改。

{_agentic_outline_mandate}

【章纲详情（工具查询后综合参考）】
{chapter.to_outline_text()}

【第一步：工具查询（必须执行，不可跳过）】
章纲中的出场人物名只是索引，人物的完整档案（性格/当前状态/人际关系）只在数据库中，章纲里没有。
上一章的结尾处境、伏笔的具体细节，同样只能通过工具获取。
请在同一次回复中并行调用以下工具（一次包含多个 <tool_call>，不要分多次）：
- 每位出场人物 → query_character（人物名见上方强制清单）
- 上一章摘要 → query_chapter_summary（chapter_num = {chapter_number - 1}）
- 如有需回收/埋下的伏笔 → query_foreshadowing
查询完成后再输出正文，正文第一行必须是固定格式的章节标题：# 第{chapter_number}章《{chapter.title or ""}》
章节编号严格锁定为 {chapter_number}，禁止写成其他数字。"""

        loop = AgenticLoop(
            llm=self.llm,
            tool_executor=ToolExecutor(self.memory, current_chapter=chapter_number),
        )

        # yield from run_gen()：每个事件让出控制权给 UI
        content = ""
        for event_type, data in loop.run_gen(
            system_prompt=agentic_system,
            initial_user_prompt=initial_prompt,
            max_tokens_per_call=max(4000, int(word_max / 1.5 * 1.2)),
            force_tool_first=True,  # 强制第一轮必须调用工具，否则注入提示重试
        ):
            yield (event_type, data)
            if event_type == StepEvent.FINAL_OUTPUT:
                content = data.get("text", "")

        # ── 字数校验 + 轻量重试（最多2次，不走完整 agentic loop）────────────────
        # 超限时先尝试程序截断（快速无损），截断后达标则直接返回；
        # 仍不达标（字数不足）才发起 LLM 补写重试。
        _MAX_WORD_RETRIES = 2
        import sys as _sys

        # 通知 UI：正文已生成，展示字数
        yield (StepEvent.STATUS_MSG, {"msg": f"✍️ 正文生成完成，共 {len(content)} 字，正在校验字数…"})

        for _attempt in range(_MAX_WORD_RETRIES):
            actual = len(content)
            if word_min <= actual <= word_max:
                break  # 字数达标，退出重试

            if actual < word_min:
                deficit = word_min - actual
                print(
                    f"[WriterAgent-Agentic] 第{chapter_number}章字数不足"
                    f"（{actual}/{word_min}~{word_max}），发起补写重试（第{_attempt + 1}次）…",
                    file=_sys.stderr,
                )
                yield (StepEvent.STATUS_MSG, {
                    "msg": f"⚠️ 字数不足（{actual} 字，目标 {word_min}~{word_max}），发起补写重试（{_attempt + 1}/{_MAX_WORD_RETRIES}）…"
                })
                fix_instruction = (
                    f"⚠️ 字数不足：当前 {actual} 字，距最低要求还差 {deficit} 字。\n"
                    f"请在保持原有风格和情节不变的前提下，扩充以下方向（选择最自然的方式）：\n"
                    f"- 补充场景的感官细节（视觉/听觉/触觉）\n"
                    f"- 延展对话的停顿、肢体反应和心理活动\n"
                    f"- 丰富动作过程（起因→细节→结果），不要只写结论\n"
                    f"目标：使总字数达到 {word_min} 字以上（目标 {word_target} 字）。\n"
                    f"直接输出完整的重写版本："
                )
            else:
                surplus = actual - word_max
                print(
                    f"[WriterAgent-Agentic] 第{chapter_number}章字数超限"
                    f"（{actual}/{word_min}~{word_max}），发起 LLM 精简重试（第{_attempt + 1}次）…",
                    file=_sys.stderr,
                )
                yield (StepEvent.STATUS_MSG, {
                    "msg": f"⚠️ 字数超限（{actual} 字，上限 {word_max}），发起精简重试（{_attempt + 1}/{_MAX_WORD_RETRIES}）…"
                })
                fix_instruction = (
                    f"⚠️ 字数超限：当前 {actual} 字，超出上限 {surplus} 字。\n"
                    f"请按以下优先级压缩，三阶段情节骨架保留完整：\n"
                    f"- 【最优先】删除冗余比喻：每处比喻问自己'去掉它信息量有损失吗'，没有损失就删\n"
                    f"- 【最优先】删除堆叠形容词：同一名词前保留最精准的一个定语，其余删去\n"
                    f"- 每个心理活动只保留最关键的一处，删去反复推敲/重复感受\n"
                    f"- 合并功能相同的过渡句和环境描写\n"
                    f"- 对话保留，但每句对话最多配一处动作/表情描写\n"
                    f"目标：使总字数控制在 {word_max} 字以内，开篇/核心/收尾结构完整。\n"
                    f"直接输出完整的压缩版本："
                )

            content_truncated = content[:10000]
            truncate_note = (
                "（内容过长，仅显示前10000字，请基于此范围调整）\n"
                if len(content) > 10000
                else ""
            )
            fix_prompt = (
                f"以下是第{chapter_number}章的正文：\n\n"
                f"{truncate_note}{content_truncated}\n\n"
                f"{fix_instruction}"
            )
            try:
                content = self.llm.generate(
                    agentic_system,
                    fix_prompt,
                    max_tokens=max(4000, int(word_max / 1.5 * 1.2)),
                    temperature=self.temperature,
                )
                yield (StepEvent.STATUS_MSG, {"msg": f"✅ 字数重试完成，当前 {len(content)} 字"})
            except Exception as e:
                print(
                    f"[WriterAgent-Agentic] 字数重试时 LLM 调用失败：{e}，使用当前版本",
                    file=_sys.stderr,
                )
                break
        else:
            # 重试耗尽，打印最终字数状态
            actual = len(content)
            if actual < word_min or actual > word_max:
                print(
                    f"[WriterAgent-Agentic] 第{chapter_number}章重试{_MAX_WORD_RETRIES}次后"
                    f"字数仍为 {actual}（目标 {word_min}~{word_max}），使用当前版本。",
                    file=_sys.stderr,
                )

        yield (StepEvent.STATUS_MSG, {"msg": "🔧 正在执行后处理（禁止句式修正）…"})
        final_content = self._fix_forbidden_syntax(content, chapter_number)
        yield (StepEvent.CONTENT_READY, {"content": final_content, "stage": "write"})

    def write_chapter_agentic(
        self,
        chapter_number: int,
        word_target: int = 3000,
        word_count_tolerance: float = 0.15,
        step_callback=None,
    ) -> str:
        """
        同步包装：消费 write_chapter_agentic_gen()，返回最终正文字符串。
        供批量写作等非 UI 场景调用；step_callback 仍然支持。
        """
        from core.agentic_loop import StepEvent
        content = ""
        for event_type, data in self.write_chapter_agentic_gen(
            chapter_number=chapter_number,
            word_target=word_target,
            word_count_tolerance=word_count_tolerance,
        ):
            if step_callback:
                try:
                    step_callback(event_type, data)
                except Exception:
                    pass
            if event_type == StepEvent.CONTENT_READY:
                content = data.get("content", "")
        return content

    def fix_chapter_with_feedback(
        self,
        chapter_number: int,
        content: str,
        feedback: str,
    ) -> str:
        """
        根据审核 feedback 修正章节正文（Agentic 审核重试专用）。

        与 auto_fix_minor_issues() 的区别：
        - 接受纯文本 feedback（而非 ReviewReport 对象）
        - 专为 agentic 审核的 REJECT→修正→重审 循环设计
        - 同样注入去AI味规则，保持风格一致

        Args:
            chapter_number: 章节号（用于获取章纲目标）
            content: 当前正文
            feedback: 审核 feedback 文本（问题清单 + 综合反馈）

        Returns:
            修正后的正文
        """
        from core.config import DEFAULT_DEAI_RULES

        novel = self.memory.global_mem.get_novel()
        deai_rules = (
            novel.deai_rules.strip()
            if novel and novel.deai_rules and novel.deai_rules.strip()
            else DEFAULT_DEAI_RULES
        )

        # 获取章纲目标
        chapter_goal_block = ""
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
                    + "\n".join(goal_lines)
                    + "\n"
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

        content_truncated = content[:8000]
        truncate_note = (
            "\n...(正文过长已截断，请仅修复可见部分)" if len(content) > 8000 else ""
        )

        user_prompt = (
            f"章节正文：\n{content_truncated}{truncate_note}\n\n"
            f"{chapter_goal_block}"
            f"【审核发现的问题（必须逐条修复）】\n{feedback}\n\n"
            f"【去AI味规则（修改时同样必须遵守）】\n{deai_rules}\n\n"
            "请修复以上所有问题并确保章纲目标得以实现，直接输出完整修改后正文："
        )
        return self.llm.generate(
            system_prompt, user_prompt, max_tokens=12000, temperature=self.temperature
        )

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
        # lazy llm，供 agentic 方法使用
        self._llm = None

    @property
    def llm(self):
        """懒加载 LLM 实例（供 agentic 方法调用）"""
        if self._llm is None:
            self._llm = NovelLLM(self.model_id, novel_id=self.novel_id)
        return self._llm

    def review_chapter(self, chapter_number: int, content: str) -> ReviewReport:
        """
        对章节内容进行全面审核（旧版，保留兼容）
        返回详细的审核报告
        """
        return self.detector.detect_chapter_conflicts(chapter_number, content)

    def pipeline_review(
        self, chapter_number: int, content: str, progress_callback=None
    ) -> dict:
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
            (
                "context_sentry",
                self.detector.run_context_sentry,
                GATE_CONTEXT_THRESHOLD,
            ),
            (
                "global_continuity_judge",
                self.detector.run_continuity_judge,
                GATE_CONTINUITY_THRESHOLD,
            ),
            (
                "stylistic_refiner",
                self.detector.run_stylistic_refiner,
                GATE_STYLISTIC_THRESHOLD,
            ),
        ]

        gate_results = []
        for gate_name, gate_fn, threshold in gates_config:
            if progress_callback:
                label_map = {
                    "context_sentry": "🎯 关卡1：局部校对（大纲+人设）",
                    "global_continuity_judge": "🌐 关卡2：全局场记（状态+时空）",
                    "stylistic_refiner": "✨ 关卡3：文风打磨（去AI痕迹）",
                }
                progress_callback(
                    f"{label_map.get(gate_name, gate_name)}（阈值 {threshold}）..."
                )

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
        max_rounds: int | None = None,
        writer_agent=None,
        word_target: int = 3000,
        word_count_tolerance: float = 0.3,
    ) -> dict:
        """
        并行四审核流水线（新版，替换三关卡串行方案）。

        四个 Reviewer 并行执行，合并所有 REJECT 的 feedback 后由 WriterAgent 一次性修正。
        已通过的 Reviewer 在后续轮次中不再重复审核。
        循环直到全部关卡通过或达到 max_rounds 为止。

        Args:
            max_rounds: 最大审核轮数（每轮含审核+修正）。None 时读取全局默认。
            writer_agent: WriterAgent 实例，用于审核未通过时修正内容。
                          为 None 时仅审核不修正（兼容旧调用）。
            word_target: 修正时的目标字数（传给 WriterAgent）。
            word_count_tolerance: 修正时的字数容差（传给 WriterAgent）。

        Returns:
            {
                "passed": bool,
                "final_score": float,
                "gates": [GateResult, ...],     # 最后一轮全部4个关卡的结果
                "all_gate_results": list[dict], # 所有轮次所有关卡结果
                "reject_feedbacks": str | None, # 最终仍未通过的 feedback（供调用方记录）
                "rounds": int,                  # 实际执行轮数
                "content": str,                 # 最终内容（可能经过多轮修正）
            }
        """
        from core.config import MAX_PARALLEL_REVIEW_ROUNDS

        _GATE_LABELS = {
            "plot_aligner": "🎯 剧情对齐",
            "character_guard": "🛡️ 人设世界观",
            "continuity_tracker": "🔗 时空状态",
            "style_refiner": "✨ 文风去AI",
        }
        _MAX_ROUNDS = max_rounds if max_rounds is not None else MAX_PARALLEL_REVIEW_ROUNDS

        all_gate_results: list[dict] = []
        final_score = 0.0
        last_review: dict = {}
        current_content = content

        for round_idx in range(_MAX_ROUNDS):
            if progress_callback:
                progress_callback(
                    f"🔍 第{round_idx + 1}/{_MAX_ROUNDS}轮全量并行审核..."
                )

            # 每轮始终全量审核（不 skip 已通过关卡），
            # 避免修正 B 时悄悄破坏 A 却因 A 被跳过而察觉不到
            last_review = self.detector.run_parallel_review(
                chapter_number, current_content, skip_gates=set()
            )

            # 记录本轮结果
            for gate_result in last_review["gates"]:
                all_gate_results.append(
                    {**gate_result.to_dict(), "round": round_idx + 1}
                )

            final_score = last_review["weighted_score"]

            # 打印每个关卡结果
            if progress_callback:
                for gate_result in last_review["gates"]:
                    icon = "✅" if gate_result.passed else "❌"
                    label = _GATE_LABELS.get(
                        gate_result.gate_name, gate_result.gate_name
                    )
                    progress_callback(
                        f"  {icon} {label}：{gate_result.total_score:.1f}/10"
                    )
                progress_callback(
                    f"  加权得分：{final_score:.1f}/10"
                    + (
                        "  ✅ 全部通过"
                        if last_review["all_passed"]
                        else f"  ❌ 未通过：{', '.join(_GATE_LABELS.get(g, g) for g in last_review['failed_gate_names'])}"
                    )
                )

            if last_review["all_passed"]:
                break

            # 审核未通过且还有剩余轮次：若有 writer_agent 则修正内容，否则仅继续重审
            remaining = _MAX_ROUNDS - round_idx - 1
            if remaining > 0 and writer_agent is not None:
                reject_feedbacks = last_review.get("reject_feedbacks") or ""
                if reject_feedbacks:
                    fix_prompt = (
                        f"【并行审核反馈（请针对以下所有问题统筹修改，保留无问题的内容）】\n\n"
                        f"{reject_feedbacks}\n\n"
                        f"请根据以上所有批注修改章节正文，统筹解决各类问题，"
                        f"直接输出修改后的完整正文。"
                    )
                    if progress_callback:
                        failed_labels = ", ".join(
                            _GATE_LABELS.get(g, g)
                            for g in last_review["failed_gate_names"]
                        )
                        progress_callback(
                            f"  ✏️ 根据审核反馈修正内容（{failed_labels}），"
                            f"剩余 {remaining} 轮..."
                        )
                    try:
                        current_content = writer_agent.write_chapter(
                            chapter_number=chapter_number,
                            word_target=word_target,
                            word_count_tolerance=word_count_tolerance,
                            review_feedback=fix_prompt,
                        )
                    except Exception as e:
                        if progress_callback:
                            progress_callback(f"  ⚠️ 修正失败（{e}），跳过本轮修正")
            elif remaining > 0 and writer_agent is None and progress_callback:
                progress_callback(
                    f"  ℹ️ 无 WriterAgent，第{round_idx + 2}轮直接重审（不修正内容）"
                )

        return {
            "passed": last_review.get("all_passed", False),
            "final_score": round(final_score, 2),
            "gates": list(last_review.get("gates", [])),
            "all_gate_results": all_gate_results,
            "reject_feedbacks": last_review.get("reject_feedbacks") or None,
            "rounds": round_idx + 1,  # 实际执行轮数
            "content": current_content,  # 最终内容（可能经过多轮修正）
        }

    def auto_fix_minor_issues(
        self, content: str, report: ReviewReport, novel_id: int
    ) -> str:
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
        conflicts_desc = "\n".join(
            [
                f"- {c.conflict_type}（位置：{c.location[:100]}）：{c.description}。建议：{c.solutions[0] if c.solutions else ''}"
                for c in minor_conflicts
            ]
        )

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
        truncate_note = (
            "\n...(正文过长已截断，请仅修复可见部分)" if len(content) > 8000 else ""
        )

        user_prompt = f"""原文：
{content_truncated}{truncate_note}

需要修复的问题（只修复这些，其他不变）：
{conflicts_desc}

【去AI味规则（修复时同样必须遵守）】
{deai_rules}

请输出修复后的完整正文："""

        return llm.generate(
            system_prompt, user_prompt, max_tokens=12000, temperature=self.temperature
        )

    def fix_all_issues(
        self,
        content: str,
        report: "ReviewReport",
        novel_id: int,
        chapter_number: int = 0,
    ) -> str:
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
        conflicts_desc = "\n".join(
            [
                f"- [{c.severity}级] {c.conflict_type}"
                f"（位置：{c.location[:80]}）：{c.description}"
                + (f"。建议：{c.solutions[0]}" if c.solutions else "")
                for c in report.conflicts
            ]
        )

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
                        + "\n".join(goal_lines)
                        + "\n"
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
        truncate_note = (
            "\n...(正文过长已截断，请仅修复可见部分)" if len(content) > 8000 else ""
        )

        user_prompt = (
            f"章节正文：\n{content_truncated}{truncate_note}\n\n"
            f"本次审核评分：{report.overall_score:.1f}/10\n"
            f"{chapter_goal_block}"
            f"需要修复的问题（共 {len(report.conflicts)} 条）：\n{conflicts_desc}\n\n"
            f"【去AI味规则（修改时同样必须遵守）】\n{deai_rules}\n\n"
            "请修复以上所有问题并确保章纲目标得以实现，直接输出完整修改后正文："
        )
        return llm.generate(
            system_prompt, user_prompt, max_tokens=12000, temperature=self.temperature
        )

    def review_chapter_agentic_gen(
        self,
        chapter_number: int,
        content: str,
        writer_agent=None,
    ):
        """
        Generator 版 Agentic 审核。

        每个步骤以 (event_type, data) yield 出来，UI 可逐事件实时刷新。
        最终审核结果在 event_type == StepEvent.CONTENT_READY 的 data["result"] 里。
        """
        import re as _re
        import sys as _sys
        from core.agentic_loop import AgenticLoop, StepEvent
        from core.tool_executor import ToolExecutor, TOOL_DEFINITIONS
        from core.config import MAX_GATE_RETRIES

        chapter = self.memory.global_mem.get_chapter_outline(chapter_number)
        chapter_outline_text = chapter.to_outline_text() if chapter else "（章纲缺失）"

        agentic_system = (
            self.SYSTEM_PROMPT
            + """

在审核前，你可以通过工具主动查询数据库中的信息，包括角色档案、历史摘要、伏笔状态等。
这让你能做出真正有依据的审核判断，而不是仅凭正文本身猜测设定。"""
        )

        def _build_review_prompt(current_content: str, prev_feedback: str = "") -> str:
            """构造审核 prompt（支持带前轮反馈的重审）"""
            content_preview = current_content[:6000] + (
                "...(内容过长已截断)" if len(current_content) > 6000 else ""
            )
            retry_block = ""
            if prev_feedback:
                retry_block = f"""
━━━ 上轮审核已 REJECT（本轮为修正后重审）━━━

上轮问题摘要：
{prev_feedback}

请重点核查上述问题是否已被修正，同时对全文做完整审核。
"""
            return f"""{TOOL_DEFINITIONS}

━━━ 你的审核任务 ━━━

请对以下章节进行全面质量审核。{retry_block}

【本章章纲（应有内容）】
{chapter_outline_text}

【待审核正文】
{content_preview}

━━━ 审核流程 ━━━

在给出审核结论前，请主动查询需要对比的信息，例如：
- 查询本章出场人物的完整档案（核对言行是否符合人设）
- 查询前几章的摘要（核对剧情连贯性、状态延续）
- 查询本章涉及的伏笔（核对埋下/回收是否正确）
- 查询世界观设定（核对细节是否与设定冲突）
- 语义检索历史相关片段（核对重要细节的前后一致性）

审核维度：
1. 【章纲一致性】正文核心事件是否与章纲吻合？有无遗漏或擅自改动？
2. 【人设一致性】每个出场人物的言行是否符合其档案设定？
3. 【剧情连贯性】状态、道具、时间线、地点是否与前章无缝衔接？
4. 【逻辑合理性】情节因果是否成立？有无不合理跳跃？
5. 【伏笔处理】本章应埋/应回收的伏笔是否正确处理？
6. 【文风质量】是否存在AI腔（说教式总结、机械转折词、上帝视角等）？

━━━ 输出格式 ━━━

查询完毕后，请输出以下格式的审核报告：

【审核评分】X.X/10

【审核结论】PASS 或 REJECT

【问题清单】（若有问题，每条格式：问题类别 | 原文引用 | 具体问题 | 修改建议）
- ...

【综合反馈】
（200字以内的整体评价，PASS时说明优点，REJECT时说明必须修改的核心问题）
"""

        def _parse_report(report_text: str) -> tuple[bool, float, str | None]:
            """解析审核报告，返回 (passed, score, feedback)"""
            passed = True
            final_score = 8.0
            reject_feedbacks = None

            score_match = _re.search(r"【审核评分】\s*([\d.]+)\s*/\s*10", report_text)
            if score_match:
                try:
                    final_score = float(score_match.group(1))
                except ValueError:
                    pass

            if _re.search(r"【审核结论】\s*REJECT", report_text):
                passed = False
                issues_match = _re.search(
                    r"【问题清单】([\s\S]+?)(?=【综合反馈】|$)", report_text
                )
                fb_match = _re.search(r"【综合反馈】\s*([\s\S]+?)$", report_text)
                parts = []
                if issues_match:
                    parts.append(issues_match.group(1).strip())
                if fb_match:
                    parts.append(fb_match.group(1).strip())
                reject_feedbacks = "\n\n".join(parts) if parts else report_text

            return passed, final_score, reject_feedbacks

        # ── 主循环：最多 1 + MAX_GATE_RETRIES 轮 ────────────────────────────
        current_content = content
        all_reports: list[str] = []
        prev_feedback: str = ""
        passed = False
        final_score = 0.0
        reject_feedbacks = None
        total_rounds = 1 + MAX_GATE_RETRIES

        for round_idx in range(total_rounds):
            is_retry = round_idx > 0
            round_label = f"第{round_idx + 1}轮{'重' if is_retry else ''}审核"
            print(
                f"[ReviewerAgent-Agentic] {round_label}（第{chapter_number}章）",
                file=_sys.stderr,
            )

            yield (StepEvent.STATUS_MSG, {
                "msg": f"🔍 {round_label}（第{chapter_number}章）",
                "round": round_idx + 1,
            })

            initial_prompt = _build_review_prompt(current_content, prev_feedback)

            loop = AgenticLoop(
                llm=self.llm,
                tool_executor=ToolExecutor(self.memory),
            )
            report_text = ""
            for event_type, data in loop.run_gen(
                system_prompt=agentic_system,
                initial_user_prompt=initial_prompt,
                max_tokens_per_call=4096,
            ):
                yield (event_type, data)
                if event_type == StepEvent.FINAL_OUTPUT:
                    report_text = data.get("text", "")

            all_reports.append(report_text)

            passed, final_score, reject_feedbacks = _parse_report(report_text)
            print(
                f"[ReviewerAgent-Agentic] {round_label} → {'PASS' if passed else 'REJECT'} {final_score:.1f}/10",
                file=_sys.stderr,
            )

            if passed:
                break

            if writer_agent is None or round_idx >= total_rounds - 1:
                break

            print(
                f"[ReviewerAgent-Agentic] 调用 WriterAgent 修正（问题反馈 {len(reject_feedbacks or '')} 字）",
                file=_sys.stderr,
            )
            yield (StepEvent.STATUS_MSG, {"msg": "✏️ 正在根据审核意见修正正文…"})
            try:
                current_content = writer_agent.fix_chapter_with_feedback(
                    chapter_number=chapter_number,
                    content=current_content,
                    feedback=reject_feedbacks,
                )
                prev_feedback = reject_feedbacks
            except Exception as e:
                print(
                    f"[ReviewerAgent-Agentic] WriterAgent 修正失败：{e}，跳过修正",
                    file=_sys.stderr,
                )
                break

        result = {
            "passed": passed,
            "final_score": round(final_score, 2),
            "gates": [],
            "all_gate_results": [],
            "reject_feedbacks": reject_feedbacks,
            "agentic_report": all_reports[-1] if all_reports else "",
            "all_reports": all_reports,
            "content": current_content,
            "rounds": len(all_reports),
        }
        yield (StepEvent.CONTENT_READY, {"result": result, "stage": "review"})

    def review_chapter_agentic(
        self,
        chapter_number: int,
        content: str,
        step_callback=None,
        writer_agent=None,
    ) -> dict:
        """
        同步包装：消费 review_chapter_agentic_gen()，返回审核结果 dict。
        供非 UI 场景调用；step_callback 仍然支持。
        """
        from core.agentic_loop import StepEvent
        result = {}
        for event_type, data in self.review_chapter_agentic_gen(
            chapter_number=chapter_number,
            content=content,
            writer_agent=writer_agent,
        ):
            if step_callback:
                try:
                    step_callback(event_type, data)
                except Exception:
                    pass
            if event_type == StepEvent.CONTENT_READY:
                result = data.get("result", {})
        return result

    def close(self):
        self.memory.close()
        self.detector.close()


# ======================================
# Agent 5: 润色师
# ======================================


class PolisherAgent(_ContentPostProcessMixin):
    """
    润色师 Agent
    职责：对审核通过的内容进行文笔润色
    - 降低AI痕迹
    - 统一写作风格
    - 保留原文剧情，只优化表达
    """

    _agent_tag = "PolisherAgent"

    SYSTEM_PROMPT = """你是一位资深的中文小说文学编辑，专注于将AI生成的文稿提升为高质量的文学作品。

你的润色原则：
1. **去AI痕迹**：严格按照下方注入的「去AI味规则」逐条处理，不得遗漏

2. **绝对禁用句式（无论任何情况，润色后的文本中不得出现）**：
   - "不是……而是……"及其省略变体"不是……是……"（"不是A，是B"与"不是A而是B"是同一句式，一律禁用）
   - "与其说……不如说……"
   - "这意味着……" / "更大的……正在逼近" / "他/她不知道的是"
   若原文存在上述句式，润色时必须主动改写为直接陈述。

3. **增强文学性**：
   - 加入更多感官细节（嗅觉、触觉、听觉）
   - **比喻密度硬性上限：全文每千字不超过 3 处比喻词（像/如同/仿佛/宛如/好似/犹如/恰似/有如）**；绝对不主动新增比喻，只删减无效比喻；若原文比喻已达标，保持原样
   - 通过细节展现情感，而非直接说
   - 让对话更自然，有留白

4. **保持原意**：
   - 不改变情节、对话内容和人物关系
   - 不添加新的情节元素
   - 保持原有的段落结构

5. **风格一致**：
   - 保持全书统一的叙述人称
   - 保持人物独特的说话风格

输出要求：直接输出润色后的正文，不要加解释。"""

    STYLE_ANALYSIS_PROMPT = """你是一位资深文学风格分析专家，擅长从作品片段中精准提取作者的写作风格特征。

你的分析原则：
- 特征必须具体、可操作，能直接指导他人模仿
- 避免笼统的评价（如"文笔优美"），改用具体描述（如"惯用短句节奏，三至五字一句，营造急促感"）
- "润色指令"字段尤为重要：必须用行动导向语言，告诉写作者具体"该做什么"

输出格式：合法的JSON，不含其他文字。"""

    # 7个可量化风格维度的1-5档语义映射表
    # key: 风格字段名，value: {1..5: 语义描述}
    _STYLE_SLIDER_MAP = {
        "sentence_patterns": {
            1: "以短句和碎句为主，节奏急促、跳跃、克制",
            2: "短句偏多，间以长句，节奏较为明快",
            3: "长短句均衡交替，节奏张弛有度",
            4: "长句偏多，偶有短句点缀，整体感觉从容不迫",
            5: "以长句为主，句式绵密舒展，从句嵌套，节奏沉稳流畅",
        },
        "vocabulary": {
            1: "口语化、生活化，大量俚语和日常用词，亲切直白",
            2: "偏口语，措辞自然随意，略带文学感",
            3: "雅俗均衡，日常用词为主，适度点缀文学词汇",
            4: "偏书面，用词精炼考究，文学质感明显",
            5: "高度书面化，措辞典雅，文白杂糅，带古典文学气息",
        },
        "narrative_voice": {
            1: "叙述者高度克制，冷眼旁观，几乎不透露人物内心，只呈现行为和现象",
            2: "保持一定距离，偶尔贴近人物视角，内心活动较少",
            3: "叙述距离适中，内外兼顾，既有观察也有内心",
            4: "贴近人物视角，大量内心流动，沉浸感强",
            5: "完全沉浸式，几乎等同于第一人称内心独白，意识流倾向明显",
        },
        "dialogue_style": {
            1: "对话极少，叙述为主，人物沉默多，言语克制",
            2: "对话较少，以场景叙述为主，对话简短有力",
            3: "叙述与对话均衡，对话推进情节",
            4: "对话较多，人物交流频繁，靠对话展现关系和性格",
            5: "大量对话，快速来回，接近剧本风格，叙述段落简短",
        },
        "description_style": {
            1: "描写极简，不做渲染，只交代必要信息，留白极多",
            2: "描写克制，仅在关键处点染，感官细节有限",
            3: "描写适度，场景有质感，感官细节有选择地出现",
            4: "描写较丰富，感官细节层叠，环境氛围渲染充分",
            5: "描写浓密，大量感官细节堆叠，意象丰富，浸入感强",
        },
        "rhythm_pacing": {
            1: "节奏极慢，大量留白和静场，克制平静，近似散文",
            2: "节奏偏慢，从容不迫，情节推进缓和",
            3: "节奏适中，张弛兼顾，高潮处加速，平静处收缓",
            4: "节奏偏快，情节推进迅速，悬念密集，动作性强",
            5: "节奏极快，场景切换频繁，事件密集，读者几乎没有喘息空间",
        },
        "emotion_expression": {
            1: "情感高度克制，从不直接表达，全靠动作/物件/环境暗示，留白给读者",
            2: "情感含蓄，偶有内心活动，但不做渲染",
            3: "情感适度外显，内心描写与行为描写均衡",
            4: "情感较为直白，内心活动较多，情绪渲染明显",
            5: "情感浓烈直白，大量内心独白，情绪高度外露",
        },
    }

    # 量化维度的中文标签（UI 展示用）
    _STYLE_SLIDER_LABELS = {
        "sentence_patterns": "句式长短",
        "vocabulary":        "词汇雅俗",
        "narrative_voice":   "叙述距离",
        "dialogue_style":    "对话密度",
        "description_style": "描写密度",
        "rhythm_pacing":     "叙事节奏",
        "emotion_expression":"情感表达方式",
    }

    def __init__(self, novel_id: int, model_id: str = None, temperature: float = None):
        self.novel_id = novel_id
        self.temperature = temperature
        self.memory = MemoryManager(novel_id)
        if not model_id:
            _novel = self.memory.global_mem.get_novel()
            model_id = (_novel.llm_model or None) if _novel else None
        self.llm = NovelLLM(model_id, novel_id=self.novel_id)

    # 分隔符：用于切分 LLM 返回的润色说明和正文
    _POLISH_REPORT_SEP = "===POLISH_TEXT==="

    def polish_chapter(
        self, content: str, style_reference: str = "", stream_callback=None
    ) -> tuple[str, str, str]:
        """
        对章节内容进行全面润色

        Args:
            content: 待润色的章节内容
            style_reference: 风格参考（可以是前几章的优秀段落）
            stream_callback: 流式输出回调

        Returns:
            (polished_text, reasoning, polish_report)
            - polished_text:  润色后的完整正文
            - reasoning:      模型思维链内容（不支持或无思维链时为空字符串）
            - polish_report:  LLM 输出的润色说明（思路 + 改动列表），无时为空字符串
        """
        novel = self.memory.global_mem.get_novel()
        style_desc = novel.writing_style or "" if novel else ""
        style_profile = novel.get_style_profile() if novel else {}
        style_profile_text = (
            self._format_style_profile(style_profile) if style_profile else ""
        )

        # 动态注入去AI味规则（读用户配置，fallback DEFAULT_DEAI_RULES）
        from core.config import DEFAULT_DEAI_RULES

        deai_rules = (
            novel.deai_rules.strip()
            if novel and novel.deai_rules and novel.deai_rules.strip()
            else DEFAULT_DEAI_RULES
        )

        # ── 润色前：先清理原文中的无效比喻，避免润色时又大量补回 ────────────
        content = self._fix_redundant_metaphors(content)

        # 统计润色前的比喻数，供 prompt 量化约束使用
        import re as _re
        _metaphor_count = sum(content.count(w) for w in self._METAPHOR_WORDS_MULTI)
        _metaphor_count += len(_re.findall(r'(?<![好])像(?!样|是这|是那|这样|那样|这种|那种)', content))
        _char_count = max(len(content), 1)
        _metaphor_limit = max(1, int(_char_count / 1000 * self._METAPHOR_DENSITY_THRESHOLD))

        sep = self._POLISH_REPORT_SEP
        user_prompt = f"""请对以下小说章节进行文笔润色：

{f"【风格要求】{style_desc}" if style_desc else ""}
{f"【全书写作风格档案（请严格按此风格润色）】\n{style_profile_text}" if style_profile_text else ""}
{f"【风格参考样例】\n{style_reference[:3000]}{'...(已截断)' if len(style_reference) > 3000 else ''}" if style_reference else ""}
【去AI味规则（润色时必须逐条执行，这是硬性要求）】
{deai_rules}

【待润色内容】
{content}

请在保持故事情节不变的前提下，提升文学质量。
⚠️ 比喻密度硬性约束：输入正文当前有 {_metaphor_count} 处比喻词，全文约 {_char_count} 字，允许上限为 {_metaphor_limit} 处（3处/千字）。润色后比喻数必须 ≤ {_metaphor_limit}，绝对不得新增比喻，只能删减无效比喻。
⚠️ 禁止句式：润色后不得出现"不是……而是……""不是……是……""与其说……不如说……"等对比转折句式，若原文有请一并改写。

【输出格式要求（必须严格遵守）】
先输出润色说明（不超过200字），格式如下：
润色思路：（一句话说明本次润色的核心目标和方向）
主要改动：
- （段落/位置）：（改了什么、为什么）
- （段落/位置）：（改了什么、为什么）

然后另起一行输出分隔符（单独占一行，前后无其他内容）：
{sep}

分隔符之后输出润色后的完整正文，不加任何说明或标注。"""

        # 动态估算 max_tokens：中文字符约 1.5 token，润色后长度接近原文，按 ×2 兜底，上限 32000
        _estimated_tokens = max(8000, min(32000, len(content) * 2))

        if stream_callback:
            content_parts = []
            for text_chunk in self.llm.generate_stream(
                self.SYSTEM_PROMPT,
                user_prompt,
                max_tokens=_estimated_tokens,
                temperature=self.temperature,
            ):
                content_parts.append(text_chunk)
                stream_callback(text_chunk)
            result = "".join(content_parts)
        else:
            result = self.llm.generate(
                self.SYSTEM_PROMPT,
                user_prompt,
                max_tokens=_estimated_tokens,
                temperature=self.temperature,
            )

        # 解析 LLM 输出：按分隔符切分润色说明和正文
        if sep in result:
            parts = result.split(sep, 1)
            polish_report = parts[0].strip()
            polished_text = parts[1].strip()
        else:
            # 兜底：LLM 未输出分隔符，整体视为正文
            polish_report = ""
            polished_text = result

        return self._fix_forbidden_syntax(polished_text), self.llm.last_reasoning, polish_report

    def apply_style_to_selection(self, selected_text: str, instruction: str) -> str:
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

        return self.llm.generate(
            self.SYSTEM_PROMPT, user_prompt, temperature=self.temperature
        )

    def _format_style_profile(self, profile: dict) -> str:
        """将风格档案转为多行文本，供注入润色提示词。

        量化维度（7个）：存储 int 1-5，查 _STYLE_SLIDER_MAP 取语义文字；
        若旧数据为 str，直接拼原文（向后兼容）。
        文本维度（overall_style / signature_techniques / polish_instructions / custom_notes）：直接拼。
        """
        if not profile:
            return ""
        lines = []

        # 总体风格（文本）
        if profile.get("overall_style"):
            lines.append(f"- 总体风格：{profile['overall_style']}")

        # 7个量化维度
        slider_label_map = {
            "sentence_patterns": "句式长短",
            "vocabulary":        "词汇雅俗",
            "narrative_voice":   "叙述距离",
            "dialogue_style":    "对话密度",
            "description_style": "描写密度",
            "rhythm_pacing":     "叙事节奏",
            "emotion_expression":"情感表达方式",
        }
        for field, label in slider_label_map.items():
            val = profile.get(field)
            if val is None:
                continue
            if isinstance(val, int) and val in self._STYLE_SLIDER_MAP.get(field, {}):
                semantic = self._STYLE_SLIDER_MAP[field][val]
                note = profile.get(f"{field}_note", "").strip()
                line = f"- {label}：{semantic}"
                if note:
                    line += f"（补充：{note}）"
                lines.append(line)
            elif isinstance(val, str) and val.strip():
                # 旧格式字符串，直接拼（_note 字段对旧格式同样生效）
                note = profile.get(f"{field}_note", "").strip()
                line = f"- {label}：{val}"
                if note:
                    line += f"（补充：{note}）"
                lines.append(line)

        # 标志性手法（文本）
        if profile.get("signature_techniques"):
            lines.append(f"- 标志性手法：{profile['signature_techniques']}")

        # 润色指令（文本）
        if profile.get("polish_instructions"):
            lines.append(f"- 润色指令：{profile['polish_instructions']}")

        # 补充说明（文本）
        if profile.get("custom_notes"):
            lines.append(f"- 补充说明：{profile['custom_notes']}")

        return "\n".join(lines)

    def analyze_style(self, reference_text: str) -> dict:
        """
        分析参考文本的写作风格，返回结构化风格档案。

        7个量化维度返回 1-5 整数，其余返回文本字符串。

        Args:
            reference_text: 喜欢的作家作品片段（建议500-3000字）

        Returns:
            包含风格维度的dict：
            - 量化维度（int 1-5）：sentence_patterns / vocabulary / narrative_voice /
              dialogue_style / description_style / rhythm_pacing / emotion_expression
            - 文本维度（str）：overall_style / signature_techniques / polish_instructions
            - 补充（str，默认空）：custom_notes
        """
        slider_guide = ""
        for field, label in self._STYLE_SLIDER_LABELS.items():
            levels = self._STYLE_SLIDER_MAP[field]
            desc_lines = "；".join(f"{k}={v}" for k, v in levels.items())
            slider_guide += f"  - {label}（{field}）：{desc_lines}\n"

        user_prompt = f"""请分析以下参考文本的写作风格，返回JSON风格档案。

【参考文本】
{reference_text[:8000]}{"...(已截断)" if len(reference_text) > 8000 else ""}

【输出要求】
请严格按照以下JSON结构输出，字段说明如下：

1. overall_style（str）：总体风格定位，一句话概括，如"张爱玲式的冷峻市井现实主义"

2. 以下7个维度请返回1-5的整数，对应语义如下：
{slider_guide}
3. signature_techniques（str）：该作者标志性手法，具体描述反复出现的意象、技巧或表达方式

4. polish_instructions（str）：重要！用行动导向语言列出5-8条具体润色指令，如：①多用三至五字短句营造急促节奏 ②以嗅觉触觉替代纯视觉描写

5. custom_notes（str）：留空字符串""

JSON结构：
{{
  "overall_style": "...",
  "sentence_patterns": 3,
  "vocabulary": 3,
  "narrative_voice": 3,
  "dialogue_style": 3,
  "description_style": 3,
  "rhythm_pacing": 3,
  "emotion_expression": 3,
  "signature_techniques": "...",
  "polish_instructions": "...",
  "custom_notes": ""
}}"""

        response = self.llm.generate(
            self.STYLE_ANALYSIS_PROMPT,
            user_prompt,
            max_tokens=4096,
            cache_system=False,
            temperature=self.temperature,
        )
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            raw = _safe_json_loads(response[json_start:json_end])
            # 确保量化维度为 int，容错字符串数字
            for field in self._STYLE_SLIDER_MAP:
                if field in raw:
                    try:
                        raw[field] = int(raw[field])
                        raw[field] = max(1, min(5, raw[field]))  # 钳制到1-5
                    except (ValueError, TypeError):
                        pass  # 保留原值，UI 层降级处理
            return raw
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

    def evaluate_chapter(
        self, chapter_number: int, content: str, reader_types: list[str] = None
    ) -> dict:
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
                dims_desc += (
                    f"\n- {info['label']}（{rtype}）：{', '.join(info['dimensions'])}"
                )

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
            self.SYSTEM_PROMPT,
            user_prompt,
            max_tokens=4096,
            cache_system=False,
            temperature=self.temperature,
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
            self.SYSTEM_PROMPT,
            effective_msgs,
            max_tokens=1024,
            temperature=self.temperature,
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
            self.EXTRACT_PROMPT,
            user_prompt,
            max_tokens=1024,
            cache_system=False,
            temperature=self.temperature,
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

    def compress(self, label: str, text: str, target_chars: int, hint: str = "") -> str:
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
        user_prompt = f"【字段名】{label}\n【目标字数上限】{target_chars} 字\n"
        if hint:
            user_prompt += f"【额外说明】{hint}\n"
        user_prompt += f"\n【原文】\n{text}"

        try:
            result = self.llm.generate(
                self._SYSTEM,
                user_prompt,
                max_tokens=1024,
                cache_system=False,
                temperature=0.0,
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
        outline,  # NovelOutline ORM 对象
        field_targets: dict[str, int],  # {field_name: target_chars}
        threshold: int,
    ) -> dict:
        """
        对 NovelOutline 指定字段按各自目标字数压缩。
        只处理超过 threshold 的字段，未超阈值的直接跳过（调用方读原文）。
        返回 {field_name: compressed_text}。
        """
        _FIELD_HINTS = {
            "theme": "这是全书核心主题，提炼为一句核心立意即可",
            "main_conflict": "这是全书主要矛盾，需保留所有冲突层次和对立关系",
            "protagonist_arc": "这是主角成长弧光，需保留每个关键转折点和阶段变化",
            "ending_summary": "这是结局概要，需保留各条故事线的收束方式和最终走向",
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
        "outline": ["outline", "volume", "foreshadowing", "world"],
        "writing": ["chapter", "style", "foreshadowing"],
        "characters": ["characters"],
        "settings": ["settings", "world"],
        "visualization": ["foreshadowing"],
        "export": [],
        None: [
            "outline",
            "settings",
            "world",
            "characters",
            "chapter",
            "volume",
            "foreshadowing",
            "style",
        ],  # sidebar/全局
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

        blocks_text = "\n\n".join(
            cls._BLOCK_TEMPLATES[k] for k in block_keys if k in cls._BLOCK_TEMPLATES
        )
        return (
            f"{cls._ROLE_BASE}\n\n"
            "【输出规范】\n"
            "普通讨论、分析和建议直接用正常文字，不加代码块。\n"
            "需要提供可写入系统的内容时，使用以下专属代码块格式：\n\n"
            f"{blocks_text}\n\n"
            "用户可一键将代码块内容应用到对应位置。"
        )

    def __init__(
        self,
        novel_id: int,
        model_id: str = None,
        role: str = "global",
        temperature: float = None,
    ):
        from core.memory import MemoryManager
        from core.llm import DEFAULT_MODEL_ID

        self.novel_id = novel_id
        self.role = role
        self.temperature = temperature
        self.memory = MemoryManager(novel_id)
        _model = (
            model_id or self.memory.global_mem.get_novel().llm_model or DEFAULT_MODEL_ID
        )
        self.llm = NovelLLM(_model, novel_id=self.novel_id)

    def chat(
        self,
        messages: list,
        document_content: str = "",
        page: str = None,
        chapter_number: int = None,
        hint: str = "",
    ) -> str:
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
        _MAX_ROUNDS = 10  # 超过此轮数触发压缩
        _RECENT_KEEP = 8  # 压缩后保留最近几轮原文
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
                f"[早期对话摘要（共{len(old_msgs) // 2}轮，已压缩）]\n"
                + "\n".join(old_summary_lines)
            )
            if len(old_summary) > _MAX_SUMMARY_CHARS:
                old_summary = old_summary[:_MAX_SUMMARY_CHARS] + "…（已截断）"

            # 把摘要作为第一条 assistant 消息插入
            effective_msgs = [
                {"role": "assistant", "content": old_summary}
            ] + recent_msgs
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

        return self.llm.generate_chat(
            system_prompt, _msgs, max_tokens=4096, temperature=self.temperature
        )

    def _classify_intent(
        self, user_message: str, chapter_number: int | None, document_content: str
    ) -> dict:
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
                _INTENT_SYSTEM,
                user_prompt,
                max_tokens=256,
                cache_system=False,
                temperature=0.0,
            )
            js = raw[raw.find("{") : raw.rfind("}") + 1]
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
        classified = self._classify_intent(
            user_message, chapter_number, document_content
        )
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
                _tol = float(
                    (novel.get_quality_config() if novel else {}).get(
                        "word_count_tolerance", 0.2
                    )
                )
                feedback_prompt = (
                    f"【用户修改意图】\n{instruction}\n\n"
                    f"【当前章节内容（在此基础上做定向修改）】\n"
                    f"{document_content[:4000] if document_content else '（无）'}\n\n"
                    f"请只修改用户指定的部分，保留其他内容不变，直接输出完整正文。"
                )
                revised = writer.write_chapter(
                    chapter_number=ch_num,
                    word_target=max(len(document_content), 1000)
                    if document_content
                    else 3000,
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
                    if existing
                    else ""
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
                        updated_data, ensure_ascii=False, indent=2
                    )
                    result_base["result_type"] = "character"
                    result_base["reply"] = (
                        f"已根据你的要求更新了「{char_name}」的设定，可在人物档案页查看。"
                    )
                else:
                    result_base["reply"] = (
                        f"「{char_name}」设定处理完成，但未返回结构化数据。"
                    )
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
                    outline_agent = OutlineAgent(
                        self.novel_id, temperature=self.temperature
                    )

                    # ── 组装参考摘要 ──────────────────────────────────────
                    # 将参考章节的摘要拼成一段文字，附加到用户指令里
                    reference_block = ""
                    if (
                        ref_range
                        and isinstance(ref_range, list)
                        and len(ref_range) == 2
                    ):
                        ref_start, ref_end = int(ref_range[0]), int(ref_range[1])
                        from core.memory import ChapterMemory

                        ch_mem = ChapterMemory(self.novel_id)
                        ref_chapters = ch_mem.get_chapters_by_range(ref_start, ref_end)
                        ch_mem.db.close()
                        if ref_chapters:
                            ref_lines = [
                                f"【第{ref_start}章至第{ref_end}章的摘要信息（请以此为基础修改章纲）】"
                            ]
                            for rc in ref_chapters:
                                ref_lines.append(
                                    f"\n第{rc.chapter_number}章《{rc.title or ''}》"
                                )
                                if rc.summary:
                                    ref_lines.append(f"摘要：{rc.summary}")
                                    if rc.key_events:
                                        try:
                                            import json as _json

                                            evs = _json.loads(rc.key_events)
                                            if evs:
                                                ref_lines.append(
                                                    "关键事件：" + "；".join(evs)
                                                )
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
                        full_instruction = (
                            f"{reference_block}\n\n【修改指令】{instruction}"
                        )

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
                        result_content = (
                            saved_ch.to_outline_text()
                            if saved_ch
                            else str(updated_data)
                        )
                        _log(f"✅ 第{ch_num}章章纲已更新并写入数据库")
                        result_base["result_content"] = result_content
                        result_base["result_type"] = "outline"
                        ref_hint = (
                            f"（参考了第{ref_range[0]}-{ref_range[1]}章摘要）"
                            if ref_range
                            else ""
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

    def chat_agentic(
        self,
        messages: list,
        page: str = None,
        chapter_number: int = None,
        step_callback=None,
    ) -> str:
        """
        Agentic 对话：CanvasAgent 在回复前自主查询数据库获取准确信息。

        适用于需要精确数据的问题，例如：
        - "第5章里苏瑾说了什么？"
        - "目前有哪些未回收的伏笔？"
        - "李明和苏瑾的关系是什么？"

        与 chat() 的区别：
        - chat() 一次性注入全局上下文然后回答
        - chat_agentic() 让 LLM 自主决定查什么，按需精准查询后回答

        Args:
            messages: 对话历史列表
            page: 当前页面
            chapter_number: 当前章节号（可选，提供后工具可感知当前章节）
            step_callback: (event_type, data) -> None，实时展示查询步骤

        Returns:
            AI 回复文本
        """
        from core.agentic_loop import AgenticLoop
        from core.tool_executor import ToolExecutor, TOOL_DEFINITIONS

        # 获取小说基础信息作为最小上下文
        novel = self.memory.global_mem.get_novel()
        novel_info = ""
        if novel:
            novel_info = f"小说：《{novel.title}》 | 题材：{novel.genre or '未设定'}"
            if novel.logline:
                novel_info += f"\n简介：{novel.logline}"

        chapter_ctx = ""
        if chapter_number:
            ch = self.memory.global_mem.get_chapter_outline(chapter_number)
            if ch:
                chapter_ctx = (
                    f"\n当前正在编辑：第{chapter_number}章《{ch.title or ''}》"
                )

        # 取最后一条用户消息
        last_user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user_msg = m.get("content", "")
                break

        # 构造对话历史摘要（除最后一条外）
        history_summary = ""
        history_msgs = [m for m in messages if m.get("role") in ("user", "assistant")]
        if len(history_msgs) > 1:
            prev = history_msgs[:-1][-6:]  # 最多保留近3轮
            lines = []
            for m in prev:
                role = "用户" if m["role"] == "user" else "助手"
                text = m["content"][:100] + ("…" if len(m["content"]) > 100 else "")
                lines.append(f"{role}：{text}")
            if lines:
                history_summary = "\n【近期对话记录】\n" + "\n".join(lines)

        initial_prompt = f"""{TOOL_DEFINITIONS}

━━━ 背景信息 ━━━
{novel_info}{chapter_ctx}
{history_summary}

━━━ 用户问题 ━━━
{last_user_msg}

━━━ 工作指引 ━━━
请先判断：回答这个问题需要查询哪些数据库信息？
- 如果涉及具体人物，查询其档案
- 如果涉及历史情节，检索相关章节摘要或片段
- 如果涉及伏笔/设定/时间线，查询对应信息
- 如果是纯创意讨论，可以直接回答

获取足够信息后，给出准确、详细的回复。回复风格：专业但亲切，像一位深度熟悉这部小说的责编。
"""

        agentic_system = (
            self._build_role_prompt(page)
            + """

你可以在回复前主动查询数据库，获取准确的角色信息、情节细节、伏笔状态等。
这让你的回答基于真实数据，而不是凭记忆推测。"""
        )

        loop = AgenticLoop(
            llm=self.llm,
            tool_executor=ToolExecutor(self.memory, current_chapter=chapter_number),
            step_callback=step_callback,
        )

        return loop.run(
            system_prompt=agentic_system,
            initial_user_prompt=initial_prompt,
            max_tokens_per_call=4096,
        )

    def close(self):
        self.memory.close()
