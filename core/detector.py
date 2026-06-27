"""
冲突检测与自动修正系统

支持以下检测场景：
1. 生成新章节后的自动检测
2. 用户修改任何内容后的触发检测
3. 用户修改设定或大纲后的触发检测

检测类型：
- 设定冲突：是否与世界观设定矛盾
- OOC（Out of Character）：人物言行是否符合人设
- 大纲冲突：是否偏离章纲
- 前后文冲突：是否与之前章节内容矛盾
- 逻辑漏洞：是否有无法解释的情节
"""

import json
from dataclasses import dataclass, field
from typing import Optional

from core.llm import NovelLLM
from core.memory import MemoryManager


# ======================================
# 冲突数据结构
# ======================================

@dataclass
class ConflictItem:
    """单个冲突项"""
    conflict_type: str      # 冲突类型
    severity: int           # 严重程度 1-10
    location: str           # 冲突位置（引用原文）
    description: str        # 冲突描述
    solutions: list[str] = field(default_factory=list)  # 建议解决方案


@dataclass
class ReviewReport:
    """审核报告"""
    chapter_number: int
    overall_score: float    # 综合评分 0-10
    conflicts: list[ConflictItem] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)  # 总体修改建议
    passed: bool = True     # 是否通过审核
    summary: str = ""       # 审核摘要

    def to_dict(self) -> dict:
        return {
            "chapter_number": self.chapter_number,
            "overall_score": self.overall_score,
            "passed": self.passed,
            "summary": self.summary,
            "conflicts": [
                {
                    "type": c.conflict_type,
                    "severity": c.severity,
                    "location": c.location,
                    "description": c.description,
                    "solutions": c.solutions
                }
                for c in self.conflicts
            ],
            "suggestions": self.suggestions
        }

    def to_markdown(self) -> str:
        """转为 Markdown 格式的审核报告"""
        lines = [
            f"## 第{self.chapter_number}章 审核报告",
            f"**综合评分：{self.overall_score:.1f}/10**  {'✅ 通过' if self.passed else '❌ 未通过'}",
            f"\n**摘要：** {self.summary}",
        ]

        if self.conflicts:
            lines.append("\n### 发现的冲突")
            for i, c in enumerate(self.conflicts, 1):
                severity_mark = "🔴" if c.severity >= 7 else ("🟡" if c.severity >= 4 else "🟢")
                lines.append(f"\n**{i}. [{severity_mark}严重度{c.severity}] {c.conflict_type}**")
                lines.append(f"- 位置：`{c.location[:100]}...`" if len(c.location) > 100 else f"- 位置：`{c.location}`")
                lines.append(f"- 描述：{c.description}")
                if c.solutions:
                    lines.append("- 建议方案：")
                    for j, sol in enumerate(c.solutions, 1):
                        lines.append(f"  {j}. {sol}")

        if self.suggestions:
            lines.append("\n### 总体建议")
            for sug in self.suggestions:
                lines.append(f"- {sug}")

        return "\n".join(lines)


# ======================================
# 冲突检测器核心类
# ======================================

class ConflictDetector:
    """
    冲突检测器
    通过调用 Claude API 进行智能冲突检测
    """

    def __init__(self, novel_id: int, model_id: str = None, temperature: float = None):
        self.novel_id = novel_id
        self.llm = NovelLLM(model_id, novel_id=self.novel_id)
        self.memory = MemoryManager(novel_id)
        self.temperature = temperature

    def _call_llm(self, system_prompt: str, user_prompt: str,
                   max_tokens: int = 4096) -> str:
        """调用 LLM（统一接口，支持多提供商）"""
        return self.llm.generate(system_prompt, user_prompt, max_tokens=max_tokens, temperature=self.temperature)

    def detect_chapter_conflicts(self, chapter_number: int,
                                   content: str) -> ReviewReport:
        """
        对指定章节内容进行全面冲突检测
        这是审核师 Agent 的核心功能
        """
        # 获取章节对象
        chapter = self.memory.global_mem.get_chapter_outline(chapter_number)
        if not chapter:
            return ReviewReport(
                chapter_number=chapter_number,
                overall_score=0.0,
                passed=False,
                summary="找不到章节大纲，无法审核"
            )

        # 构建审核上下文
        review_context = self.memory.build_review_context(chapter, content)

        # 注入项目自定义去AI味规则（供AI痕迹检测维度使用）
        novel = self.memory.global_mem.get_novel()
        if novel and novel.deai_rules and novel.deai_rules.strip():
            review_context += f"\n\n【去AI味规则（必须逐条对照检测违规）】\n{novel.deai_rules.strip()}"
        else:
            from core.config import DEFAULT_DEAI_RULES
            review_context += f"\n\n【去AI味规则（必须逐条对照检测违规）】\n{DEFAULT_DEAI_RULES}"

        # 构建审核提示词
        system_prompt = """你是一位专业的小说审核师，负责检测小说内容中的各类问题。
你必须严格、客观、全面地审核，不放过任何细节。

审核维度：
1. **设定冲突**：内容是否与世界观设定矛盾（如魔法体系、科技水平、地理信息等）
2. **OOC检测**：人物言行是否符合其人设（性格、说话方式、能力范围等）
3. **大纲冲突**：内容是否偏离章纲（核心事件是否发生、情节是否跑偏）
4. **前后矛盾**：是否与前几章的内容矛盾（事件顺序、人物位置、道具状态等）
5. **逻辑漏洞**：情节是否有无法解释的跳跃或不合理之处
6. **AI痕迹检测**：逐条对照下方「去AI味规则」检查原文是否存在违规：
   - 上帝视角总结句（"这意味着…""他不知道的是…""这就是命运的安排"）
   - 角色长篇大论式演讲或直白心理独白
   - 段尾总结性发言
   - AI常用的连接词堆砌（"同时""此时""然而""不得不"等过度使用）
   - 直说情绪而非通过动作/细节表现

输出要求：
必须以合法的JSON格式输出，结构如下：
{
  "overall_score": 8.5,
  "passed": true,
  "summary": "整体质量良好，存在少量问题",
  "conflicts": [
    {
      "type": "AI痕迹检测",
      "severity": 5,
      "location": "引用原文中的具体位置...",
      "description": "违反了哪条去AI味规则",
      "solutions": ["方案1", "方案2"]
    }
  ],
  "suggestions": ["总体建议1", "总体建议2"]
}

评分标准：
- 10分：完美，无任何问题
- 8-9分：优秀，有极少量小问题
- 6-7分：良好，有一些需要注意的问题（含轻度AI痕迹）
- 4-5分：及格，有明显问题需要修改（含中度AI痕迹）
- 1-3分：不及格，有严重问题必须重写（含重度AI痕迹）"""

        user_prompt = f"""请对以下小说内容进行全面审核：

{review_context}

请仔细对比章纲和实际内容，以及世界观设定和人物设定，找出所有冲突点。
输出严格的JSON格式审核报告。"""

        # 调用 Claude 进行审核
        try:
            response_text = self._call_llm(system_prompt, user_prompt)

            # 解析 JSON 响应
            # 提取 JSON 部分（Claude可能会在JSON前后有额外文字）
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                data = json.loads(json_str)
            else:
                raise ValueError("响应中未找到JSON数据")

            # 构建审核报告
            conflicts = []
            for c in data.get("conflicts", []):
                conflicts.append(ConflictItem(
                    conflict_type=c.get("type", "未知冲突"),
                    severity=int(c.get("severity", 5)),
                    location=c.get("location", ""),
                    description=c.get("description", ""),
                    solutions=c.get("solutions", [])
                ))

            report = ReviewReport(
                chapter_number=chapter_number,
                overall_score=float(data.get("overall_score", 5.0)),
                passed=bool(data.get("passed", True)),
                summary=data.get("summary", ""),
                conflicts=conflicts,
                suggestions=data.get("suggestions", [])
            )
            return report

        except (json.JSONDecodeError, ValueError) as e:
            # JSON 解析失败时，返回基础报告
            return ReviewReport(
                chapter_number=chapter_number,
                overall_score=5.0,
                passed=True,
                summary=f"审核完成（解析错误：{e}）。原始反馈：{response_text[:500]}"
            )

    def detect_setting_conflict(self, new_setting: dict,
                                  setting_type: str) -> list[ConflictItem]:
        """
        检测新设定与现有内容的冲突
        当用户修改世界观/人物设定时触发
        """
        existing_context = self.memory.global_mem.build_global_context(include_chapters=True)

        system_prompt = """你是一位专业的小说设定一致性检测专家。
当用户修改了小说的某项设定时，你需要检测这个改动是否会与已有内容产生矛盾。
输出JSON格式：{"conflicts": [...], "impact_chapters": [...]}"""

        user_prompt = f"""现有小说设定和内容：
{existing_context}

用户想要修改的{setting_type}设定：
{json.dumps(new_setting, ensure_ascii=False, indent=2)}

请检测：
1. 新设定与现有设定是否矛盾
2. 新设定会影响哪些已有章节（需要修改）
3. 对未来剧情的影响

输出JSON格式。"""

        try:
            response_text = self._call_llm(system_prompt, user_prompt)
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0:
                data = json.loads(response_text[json_start:json_end])
                return [
                    ConflictItem(
                        conflict_type="设定冲突",
                        severity=c.get("severity", 5),
                        location=c.get("location", ""),
                        description=c.get("description", ""),
                        solutions=c.get("solutions", [])
                    )
                    for c in data.get("conflicts", [])
                ]
        except Exception as e:
            print(f"⚠️ 设定冲突检测失败：{e}")

        return []

    def detect_character_ooc(self, character_name: str,
                               chapter_number: int,
                               dialogue_or_action: str) -> list[ConflictItem]:
        """
        检测特定人物的OOC（Out of Character）问题
        """
        char = self.memory.global_mem.get_character(character_name)
        if not char:
            return []

        system_prompt = """你是一位专业的小说OOC检测专家。
你需要判断给定的人物言行是否符合该人物的设定。
如果发现OOC，输出具体的冲突点和修改建议。
输出JSON格式：{"has_ooc": true/false, "ooc_details": [...]}"""

        user_prompt = f"""人物档案：
{char.to_profile_text()}

第{chapter_number}章中该人物的言行：
{dialogue_or_action}

请判断这些言行是否符合人设，如有OOC请指出具体位置和原因，并提供修改建议。"""

        try:
            response_text = self._call_llm(system_prompt, user_prompt)
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0:
                data = json.loads(response_text[json_start:json_end])
                if data.get("has_ooc"):
                    return [
                        ConflictItem(
                            conflict_type="OOC检测",
                            severity=8,
                            location=d.get("location", ""),
                            description=d.get("description", ""),
                            solutions=d.get("solutions", [])
                        )
                        for d in data.get("ooc_details", [])
                    ]
        except Exception as e:
            print(f"⚠️ OOC检测失败：{e}")

        return []

    def analyze_setting_impact(self, setting_changes: dict,
                                setting_type: str) -> dict:
        """
        分析设定变更对已写完章节的潜在影响
        轻量级分析（基于章纲+摘要，不传全文），快速定位受影响章节

        Returns:
            {
              "affected_chapters": [{"chapter_number": N, "reason": "...", "severity": "high/medium/low"}],
              "unaffected_count": N,
              "summary": "整体影响评估"
            }
        """
        from core.models import Chapter
        published = (
            self.memory.chapter_mem.db.query(Chapter)
            .filter(
                Chapter.novel_id == self.novel_id,
                Chapter.status == "published"
            )
            .order_by(Chapter.chapter_number)
            .all()
        )
        if not published:
            return {"affected_chapters": [], "unaffected_count": 0, "summary": "无已完成章节，变更不影响历史内容"}

        # 只传章纲+摘要（避免上下文过长）
        chapters_summary = "\n".join([
            f"第{ch.chapter_number}章《{ch.title or ''}》："
            f"{ch.outline_core_event or ''}"
            + (f" | 摘要：{ch.summary[:80]}" if ch.summary else "")
            + f" | 出场：{', '.join(ch.get_outline_characters())}"
            for ch in published
        ])

        system_prompt = """你是一位小说设定影响分析师。
分析某项设定变更，对哪些已写章节的内容可能产生矛盾或需要修改。

输出严格的JSON格式：
{
  "affected_chapters": [
    {"chapter_number": 1, "reason": "影响原因（一句话）", "severity": "high/medium/low"}
  ],
  "unaffected_count": 5,
  "summary": "整体影响评估（一到两句话）"
}"""

        changes_text = json.dumps(setting_changes, ensure_ascii=False, indent=2)
        user_prompt = f"""【变更的{setting_type}内容】
{changes_text}

【已完成章节列表（共{len(published)}章）】
{chapters_summary}

请分析：哪些已完成章节的内容可能因这个设定变更而需要修改？"""

        try:
            response_text = self._call_llm(system_prompt, user_prompt, max_tokens=2048)
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0:
                return json.loads(response_text[json_start:json_end])
        except Exception as e:
            print(f"⚠️ 设定影响分析失败：{e}")

        return {"affected_chapters": [], "unaffected_count": len(published), "summary": "影响分析失败，请手动检查"}

    def analyze_outline_change_impact(self, changed_chapter: int,
                                       old_outline_text: str,
                                       new_outline_updates: dict) -> list[dict]:
        """
        分析某章章纲被修改后，对后续已写章节的影响
        主要关注：核心事件变更是否导致后续章节的前提条件失效

        Returns:
            [{"chapter_number": N, "reason": "...", "severity": "high/medium/low"}]
        """
        from core.models import Chapter
        subsequent = (
            self.memory.chapter_mem.db.query(Chapter)
            .filter(
                Chapter.novel_id == self.novel_id,
                Chapter.chapter_number > changed_chapter,
                Chapter.status == "published"
            )
            .order_by(Chapter.chapter_number)
            .limit(15)
            .all()
        )
        if not subsequent:
            return []

        subsequent_text = "\n".join([
            f"第{ch.chapter_number}章《{ch.title or ''}》："
            f"{ch.outline_core_event or ''}"
            + (f" | 摘要：{ch.summary[:80]}" if ch.summary else "")
            for ch in subsequent
        ])

        system_prompt = """你是一位小说大纲影响分析师。
当某章的大纲（核心事件/人物/冲突）被修改时，分析对后续已写章节的影响。
关注：哪些后续章节的内容依赖了被修改章节的结果？

输出JSON格式：
{"affected": [{"chapter_number": N, "reason": "影响原因", "severity": "high/medium/low"}]}"""

        updates_text = json.dumps(new_outline_updates, ensure_ascii=False)
        user_prompt = f"""【第{changed_chapter}章原始章纲】
{old_outline_text}

【修改内容】
{updates_text}

【后续已写章节】
{subsequent_text}

这些已写章节中，哪些的内容可能因为第{changed_chapter}章的修改而变得不一致？"""

        try:
            response_text = self._call_llm(system_prompt, user_prompt, max_tokens=1024)
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0:
                data = json.loads(response_text[json_start:json_end])
                return data.get("affected", [])
        except Exception as e:
            print(f"⚠️ 大纲影响分析失败：{e}")

        return []

    def close(self):
        self.memory.close()
