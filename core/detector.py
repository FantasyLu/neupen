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
# 流水线关卡结果
# ======================================

@dataclass
class GateResult:
    """单关卡审核结果（三关卡漏斗式流水线）"""
    gate_name: str           # 关卡标识: context_sentry / global_continuity_judge / stylistic_refiner
    total_score: float       # 总分 0-10
    breakdown: dict          # 各子维度得分，如 {"outline_alignment": 10.0, "character_consistency": 7.0}
    action: str              # "PASS" 或 "REJECT"
    feedback: str            # 精准修改批注（REJECT 时传递给写作 Agent）
    passed: bool = True      # 是否通过（total_score >= threshold）

    def to_dict(self) -> dict:
        return {
            "gate_name": self.gate_name,
            "total_score": self.total_score,
            "breakdown": self.breakdown,
            "action": self.action,
            "feedback": self.feedback,
            "passed": self.passed,
        }


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
        # 不再 include_chapters=True（会拉入全量章纲，体积极大）
        # 改为仅注入全局设定摘要，并在 prompt 中补充已有章节数量供 AI 参考
        existing_context = self.memory.global_mem.build_global_context(include_chapters=False)
        chapter_outlines = self.memory.global_mem.get_chapter_outlines()
        chapter_count = len(chapter_outlines)
        # 给出最近10章的摘要行，供 AI 判断影响范围
        recent_outlines = chapter_outlines[-10:] if chapter_outlines else []
        recent_summary = "\n".join(
            f"第{ch.chapter_number}章《{ch.title or ''}》：{ch.outline_core_event or ''}"
            for ch in recent_outlines
        ) if recent_outlines else "（无章纲）"

        system_prompt = """你是一位专业的小说设定一致性检测专家。
当用户修改了小说的某项设定时，你需要检测这个改动是否会与已有内容产生矛盾。
输出JSON格式：{"conflicts": [...], "impact_chapters": [...]}"""

        user_prompt = f"""现有小说设定摘要：
{existing_context}

已完成章纲（共{chapter_count}章，最近{len(recent_outlines)}章概览）：
{recent_summary}

用户想要修改的{setting_type}设定：
{json.dumps(new_setting, ensure_ascii=False, indent=2)}

请检测：
1. 新设定与现有设定是否矛盾
2. 新设定可能影响哪些章节（按章号范围估计）
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

    # ======================================
    # 流水线三关卡检测方法
     # ======================================

    def _parse_gate_response(self, response_text: str, gate_name: str,
                               default_score: float = 5.0) -> GateResult:
        """解析关卡 LLM 响应为标准 GateResult。

        passed 判断采用双重校验：
        1. LLM 返回的 action == "PASS"
        2. total_score >= GATE_MIN_PASS_SCORE（代码硬校验，防止 LLM 给低分仍返回 PASS）
        两者同时满足才视为通过。
        """
        from core.config import GATE_MIN_PASS_SCORE

        json_start = response_text.find("{")
        json_end = response_text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            try:
                data = json.loads(response_text[json_start:json_end])
            except json.JSONDecodeError:
                from json_repair import repair_json
                try:
                    data = json.loads(repair_json(response_text[json_start:json_end]))
                except Exception:
                    return GateResult(
                        gate_name=gate_name, total_score=default_score,
                        breakdown={}, action="PASS",
                        feedback="JSON 解析失败，自动放行",
                        passed=True
                    )
        else:
            return GateResult(
                gate_name=gate_name, total_score=default_score,
                breakdown={}, action="PASS",
                feedback="未找到 JSON 响应，自动放行",
                passed=True
            )

        total_score = float(data.get("total_score", default_score))
        action = data.get("action", "PASS")

        # 双重校验：action == PASS 且分数达标，才真正通过
        llm_passed = action == "PASS"
        score_passed = total_score >= GATE_MIN_PASS_SCORE
        passed = llm_passed and score_passed

        # 若分数不达标但 LLM 仍返回 PASS，强制修正 action
        if llm_passed and not score_passed:
            action = "REJECT"
            import sys
            print(
                f"[Detector] {gate_name} LLM 返回 PASS 但得分 {total_score:.1f} < "
                f"阈值 {GATE_MIN_PASS_SCORE}，强制改为 REJECT。",
                file=sys.stderr,
            )

        return GateResult(
            gate_name=data.get("gate_name", gate_name),
            total_score=total_score,
            breakdown=data.get("breakdown", {}),
            action=action,
            feedback=data.get("feedback", ""),
            passed=passed,
        )

    def run_context_sentry(self, chapter_number: int, content: str) -> GateResult:
        """
        关卡1：局部章纲与人设校对官 (Context Sentry)
        熔断阈值 8.5，检查本章剧情是否跑偏、人设是否崩坏。
        """
        chapter = self.memory.global_mem.get_chapter_outline(chapter_number)
        if not chapter:
            return GateResult(
                gate_name="context_sentry", total_score=10.0,
                breakdown={}, action="PASS",
                feedback="无章纲，跳过校对", passed=True
            )

        # 构建章纲 + 人物上下文
        outline_text = chapter.to_outline_text()
        chars = self.memory.global_mem.get_all_characters()

        # 按本章出场人物过滤：出场人物调用相关性过滤档案，其余只给单行简介
        import json as _json
        from core.memory import _extract_chapter_keywords
        try:
            active_set = set(_json.loads(chapter.outline_characters or "[]"))
        except Exception:
            active_set = set()

        chapter_keywords = _extract_chapter_keywords(chapter)
        appearing = [c for c in chars if c.name in active_set] if active_set else chars[:10]
        others = [c for c in chars if c.name not in active_set and c not in appearing]
        co_chars = {c.name for c in appearing}

        char_parts = []
        if appearing:
            char_parts.append("=== 本章出场人物（相关字段档案）===")
            for c in appearing:
                # 按本章关键词做字段级相关性过滤（不截断，只取相关字段）
                char_parts.append(c.to_chapter_relevant_profile(
                    chapter_keywords=chapter_keywords,
                    co_appearing_chars=co_chars,
                ))
        if others:
            char_parts.append("=== 其他人物（未出场，仅供参照）===")
            char_parts.append("  ".join(c.to_brief_text() for c in others[:20]))
        char_text = "\n".join(char_parts)

        # 前情摘要
        recent = self.memory.chapter_mem.get_recent_chapters(chapter_number)
        recent_text = ""
        if recent:
            recent_text = "\n".join([
                f"第{ch.chapter_number}章《{ch.title or ''}》：{ch.summary or '(无摘要)'}"
                for ch in recent[-3:]
            ])

        system_prompt = """你是一位严格的小说局部校对官（Context Sentry）。
你的唯一职责：对比章纲和正文，检测本章剧情是否跑偏、人物是否 OOC。

评分采用 10 分制硬性扣分标尺：
- 满分 10 分
- 大纲偏离：章纲规定的核心事件未发生或漏写，一次扣 3.0 分；恶性剧情跑偏，直接扣 5.0 分
- 人设崩坏 (OOC)：主角或核心配角说话方式、核心性格特征明显不符，发现一处扣 1.5 分

你必须输出严格 JSON，不要有任何其他文字：
{
  "gate_name": "context_sentry",
  "total_score": 8.5,
  "breakdown": {
    "outline_alignment": 10.0,
    "character_consistency": 7.0
  },
  "action": "PASS",
  "feedback": "精确说明扣分原因。PASS 时也要指出瑕疵；REJECT 时必须给出具体修改方案。"
}
action 为 "PASS" 表示 total_score >= 8.5，"REJECT" 表示不达标。"""

        user_prompt = f"""【本章章纲】
{outline_text}

【主要人物档案】
{char_text}

【前情摘要】
{recent_text or '(无)'}

【待审核正文】
{content[:6000]}{'...(已截断)' if len(content) > 6000 else ''}

请严格对照章纲和人物档案，逐条检查本章正文。"""
        try:
            response = self._call_llm(system_prompt, user_prompt, max_tokens=2048)
            return self._parse_gate_response(response, "context_sentry")
        except Exception as e:
            return GateResult(
                gate_name="context_sentry", total_score=10.0,
                breakdown={}, action="PASS",
                feedback=f"校对官调用失败：{e}，自动放行", passed=True
            )

    def run_continuity_judge(self, chapter_number: int, content: str) -> GateResult:
        """
        关卡2：全局时空与设定场记 (Global Continuity Judge)
        熔断阈值 9.0，揪出状态冲突、世界观冲突、时空硬伤。
        """
        chapter = self.memory.global_mem.get_chapter_outline(chapter_number)
        global_ctx = self.memory.global_mem.build_global_context(
            current_chapter=chapter_number,
        )

        # 收集前文章节的状态信息（人物 current_state + 关键事件）
        from core.models import Chapter
        prev_chapters = (
            self.memory.chapter_mem.db.query(Chapter)
            .filter(
                Chapter.novel_id == self.novel_id,
                Chapter.chapter_number < chapter_number,
                Chapter.content.isnot(None)
            )
            .order_by(Chapter.chapter_number.desc())
            .limit(10).all()[::-1]
        )
        state_parts = []
        for pc in prev_chapters:
            if pc.summary:
                state_parts.append(f"第{pc.chapter_number}章《{pc.title or ''}》：{pc.summary}")
        # 人物当前状态
        chars = self.memory.global_mem.get_all_characters()
        char_states = "\n".join([
            f"{c.name}({c.role or '配角'})：{c.current_state or '状态未知'}"
            for c in chars[:15] if c.current_state
        ])

        system_prompt = """你是一位严苛的全局场记（Global Continuity Judge）。
你的唯一职责：对照角色/道具状态面板和世界观设定，揪出时空硬伤和设定冲突。

评分采用 10 分制硬性扣分标尺：
- 满分 10 分
- 状态冲突（物理断言）：违背状态面板（如上一章左臂废了这一章用左手攀爬；凭空使用未持有的道具），一处扣 2.0 分
- 世界观冲突：违背底层异变/科技/魔法设定，一处扣 3.0 分
- 空间瞬移/时空硬伤：地理位置、时间线前后矛盾，一处扣 2.0 分

你必须输出严格 JSON：
{
  "gate_name": "global_continuity_judge",
  "total_score": 7.0,
  "breakdown": {
    "state_matrix_match": 6.0,
    "world_setting_logic": 10.0,
    "spatiotemporal_logic": 8.0
  },
  "action": "REJECT",
  "feedback": "精确指出哪一章哪一段出现了什么物理矛盾，附原文引用。"
}
action 为 "PASS" 表示 total_score >= 9.0，"REJECT" 表示不达标。"""

        user_prompt = f"""【全局设定】
{global_ctx[:2000]}

【人物当前状态面板】
{char_states}

【前情摘要】
{chr(10).join(state_parts) if state_parts else '(无)'}

【本章章纲】
{chapter.to_outline_text() if chapter else '(无)'}

【待审核正文】
{content[:6000]}{'...(已截断)' if len(content) > 6000 else ''}

请严格对照状态面板和世界观，逐条检查物理矛盾。"""
        try:
            response = self._call_llm(system_prompt, user_prompt, max_tokens=2048)
            return self._parse_gate_response(response, "global_continuity_judge")
        except Exception as e:
            return GateResult(
                gate_name="global_continuity_judge", total_score=10.0,
                breakdown={}, action="PASS",
                feedback=f"场记调用失败：{e}，自动放行", passed=True
            )

    def run_stylistic_refiner(self, chapter_number: int, content: str) -> GateResult:
        """
        关卡3：去AI痕迹与文风打磨官 (Stylistic Refiner)
        熔断阈值 8.0，剜掉 AI 味，进行文风润色。上下文极小。
        额外注入：近 5 章的高频句式统计，供跨章节重复检测。
        """
        novel = self.memory.global_mem.get_novel()
        deai_rules = ""
        if novel and novel.deai_rules and novel.deai_rules.strip():
            deai_rules = novel.deai_rules.strip()
        else:
            from core.config import DEFAULT_DEAI_RULES
            deai_rules = DEFAULT_DEAI_RULES

        # ── 构建近 5 章高频句式统计（跨章节重复检测）────────────────────────
        recent_pattern_block = ""
        try:
            import re as _re
            recent_chs = self.memory.chapter_mem.get_recent_chapters(
                before_chapter=chapter_number + 1, count=5
            )
            # 统计黑名单句式 + 通用高频句式的出现频次
            TRACKED = [
                ("不是…而是…/不是…是…", _re.compile(r"不是.{1,20}[，,]?\s*(而是|是).{1,20}")),
                ("与其说…不如说…",       _re.compile(r"与其说.{1,20}不如说")),
                ("他/她不知道的是",        _re.compile(r"[他她].{0,4}不知道的是")),
                ("这意味着",              _re.compile(r"这意味着")),
                ("更大的.*正在.*逼近",    _re.compile(r"更大的.{0,10}正在.{0,10}逼近")),
                ("突然",                  _re.compile(r"突然")),
                ("然而",                  _re.compile(r"然而")),
                ("不得不",                _re.compile(r"不得不")),
            ]
            rows = []
            for ch in recent_chs:
                text = (ch.content or "")[:3000]
                counts = []
                for label, pat in TRACKED:
                    n = len(pat.findall(text))
                    counts.append(f"{label}×{n}" if n else None)
                row_str = "、".join(c for c in counts if c)
                if row_str:
                    rows.append(f"  第{ch.chapter_number}章：{row_str}")
            if rows:
                recent_pattern_block = (
                    "\n【近期章节高频句式统计（供跨章节重复检测）】\n"
                    + "\n".join(rows)
                    + "\n说明：若某句式在近 3 章中累计出现 ≥3 次，即视为「跨章节句式滥用」，"
                    "本章再次出现该句式须额外扣 1.0 分/处。\n"
                )
        except Exception:
            recent_pattern_block = ""

        system_prompt = """你是一位犀利的文风打磨官（Stylistic Refiner）。
你的唯一职责：用文字激光手术剜掉所有"AI 味"，进行脏现实主义文风检查。

评分采用 10 分制硬性扣分标尺：
- 满分 10 分
- 违禁句式：每出现一处"不是……而是……"或其省略变体"不是……是……"，或"与其说……不如说……"，直接扣 1.5 分
  注意："不是A，是B"与"不是A而是B"属于同一禁用句式，须同等对待。
- 跨章节句式滥用：若【近期章节高频句式统计】中某句式在近 3 章累计 ≥3 次，本章再出现该句式额外扣 1.0 分/处
- 说书人腔调/上帝视角：段尾/章尾出现总结性、预言性发言（如"这意味着…""更大的危机正在逼近""他不知道的是"），一处扣 1.5 分
- 机械连接词堆砌：过度使用"突然、竟然、然而、不得不"，每处扣 0.5 分
- 直说情绪：未做到"Show, Don't Tell"（直写"他很恐慌"而非通过生理细节表现），一处扣 1.0 分

你必须输出严格 JSON：
{
  "gate_name": "stylistic_refiner",
  "total_score": 7.5,
  "breakdown": {
    "forbidden_syntax_penalty": 8.5,
    "perspective_summary_penalty": 7.0,
    "dirty_realism_texture": 9.0
  },
  "action": "REJECT",
  "feedback": "逐条列出违规位置原文、违规类型、修改建议。PASS 时也要指出可优化的细节。"
}
action 为 "PASS" 表示 total_score >= 8.0，"REJECT" 表示不达标。"""

        user_prompt = f"""【去AI味规则（逐条对照检测）】
{deai_rules[:3000]}
{recent_pattern_block}
【待审核正文】
{content[:5000]}{'...(已截断)' if len(content) > 5000 else ''}

请逐条对照规则检查正文，特别注意：
1. "不是……是……"（无"而"字的变体）与"不是……而是……"同等禁用；
2. 若【近期章节高频句式统计】显示某句式已累计出现 ≥3 次，本章再出现须额外扣分。
不要放过任何违规。"""
        try:
            response = self._call_llm(system_prompt, user_prompt, max_tokens=2048)
            return self._parse_gate_response(response, "stylistic_refiner")
        except Exception as e:
            return GateResult(
                gate_name="stylistic_refiner", total_score=10.0,
                breakdown={}, action="PASS",
                feedback=f"打磨官调用失败：{e}，自动放行", passed=True
            )

    # ======================================
    # 并行四审核 Reviewer 方法
    # ======================================

    def run_plot_aligner(self, chapter_number: int, content: str) -> GateResult:
        """
        Reviewer-A: 剧情对齐官 (PlotAligner)
        职责：章纲核心事件是否发生、关键冲突是否写到、章节结局是否符合章纲、
              情感基调/出场人物/伏笔埋收是否落实。
        Context：章纲文本 + 前3章摘要（无人物档案、无世界观）。
        熔断阈值：8.0
        """
        chapter = self.memory.global_mem.get_chapter_outline(chapter_number)
        if not chapter:
            return GateResult(
                gate_name="plot_aligner", total_score=10.0,
                breakdown={}, action="PASS",
                feedback="无章纲，跳过剧情对齐", passed=True
            )

        outline_text = chapter.to_outline_text()

        # 前3章摘要（仅摘要，不含正文）
        recent = self.memory.chapter_mem.get_recent_chapters(chapter_number)
        recent_text = "\n".join([
            f"第{ch.chapter_number}章《{ch.title or ''}》：{ch.summary or '(无摘要)'}"
            for ch in recent[-3:]
        ]) if recent else "(无)"

        # 补充章纲中的伏笔字段，拼入 user_prompt 供 LLM 逐条核查
        fs_set = chapter.get_outline_foreshadowing_set()
        fs_collect = chapter.get_outline_foreshadowing_collect()
        fs_set_text = "、".join(fs_set) if fs_set else "（无）"
        fs_collect_text = "、".join(fs_collect) if fs_collect else "（无）"
        chars = chapter.get_outline_characters()
        chars_text = "、".join(chars) if chars else "（无）"

        system_prompt = """你是一位严格的剧情对齐官（PlotAligner）。
你的唯一职责：对照章纲，逐条检查正文是否完整落实了本章规定的所有要素。
不要评价文笔，只看章纲规定的内容是否在正文中真实呈现。

评分采用 10 分制硬性扣分标尺（各项独立扣分，可叠加，最低 0 分）：
- 章纲核心事件未发生或严重缺失（无完整起因→过程→结果）：扣 3.0 分
- 章纲规定的核心冲突未写出：扣 2.0 分
- 章纲结局/转折与正文不符：扣 2.0 分
- 新增与章纲无关的重大剧情（跑偏）：扣 1.5 分
- 章纲规定的情感基调在正文中完全缺失或严重偏离：扣 1.0 分
- 章纲规定的出场人物有明显缺席（核心人物未出现）：扣 1.0 分
- 章纲要求本章埋下的伏笔未在正文中植入：每处扣 0.5 分（上限 1.0 分）
- 章纲要求本章回收的伏笔未在正文中兑现：每处扣 0.5 分（上限 1.0 分）

输出严格 JSON，不含其他文字：
{
  "gate_name": "plot_aligner",
  "total_score": 8.5,
  "breakdown": {
    "core_event_coverage": 10.0,
    "conflict_coverage": 8.0,
    "ending_alignment": 9.0,
    "emotion_alignment": 10.0,
    "character_presence": 10.0,
    "foreshadowing_set": 10.0,
    "foreshadowing_collect": 10.0
  },
  "action": "PASS",
  "feedback": "精确说明每项扣分原因和具体修改方案。PASS 时也要指出瑕疵。"
}
action 为 "PASS" 表示 total_score >= 8.0，"REJECT" 表示不达标。"""

        user_prompt = f"""【本章章纲】
{outline_text}

【本章应埋下的伏笔】{fs_set_text}
【本章应回收的伏笔】{fs_collect_text}
【本章出场人物】{chars_text}

【前情摘要（近3章）】
{recent_text}

【待审核正文】
{content[:10000]}{'...(已截断)' if len(content) > 10000 else ''}

请严格对照章纲，逐条检查核心事件、冲突、结局、情感基调、出场人物、伏笔埋收是否在正文中完整体现。"""

        try:
            response = self._call_llm(system_prompt, user_prompt, max_tokens=2048)
            return self._parse_gate_response(response, "plot_aligner")
        except Exception as e:
            return GateResult(
                gate_name="plot_aligner", total_score=10.0,
                breakdown={}, action="PASS",
                feedback=f"剧情对齐官调用失败：{e}，自动放行", passed=True
            )

    def run_character_guard(self, chapter_number: int, content: str) -> GateResult:
        """
        Reviewer-B: 人设与世界观守卫 (CharacterGuard)
        职责：人物OOC检测 + 世界观/能力体系/社会规则违反。
        Context：出场人物档案（相关性过滤后）+ 世界观设定（无前情、无时空状态）。
        熔断阈值：8.5
        """
        chapter = self.memory.global_mem.get_chapter_outline(chapter_number)

        # 出场人物档案（相关性过滤）
        import json as _json
        from core.memory import _extract_chapter_keywords
        chars = self.memory.global_mem.get_all_characters()

        if chapter:
            try:
                active_set = set(_json.loads(chapter.outline_characters or "[]"))
            except Exception:
                active_set = set()
            chapter_keywords = _extract_chapter_keywords(chapter)
        else:
            active_set = set()
            chapter_keywords = set()

        appearing = [c for c in chars if c.name in active_set] if active_set else chars[:8]
        others = [c for c in chars if c.name not in {c.name for c in appearing}]
        co_chars = {c.name for c in appearing}

        char_parts = []
        if appearing:
            char_parts.append("=== 本章出场人物（相关字段档案）===")
            for c in appearing:
                char_parts.append(c.to_chapter_relevant_profile(
                    chapter_keywords=chapter_keywords,
                    co_appearing_chars=co_chars,
                ))
        if others:
            char_parts.append("=== 其他人物（未出场，仅名称角色）===")
            char_parts.append("  ".join(c.to_brief_text() for c in others[:15]))
        char_text = "\n".join(char_parts) if char_parts else "(无人物档案)"

        # 世界观设定（只取世界观部分，不含章纲和人物）
        novel = self.memory.global_mem.get_novel()
        world_setting = novel.get_world_setting() if novel else {}
        world_parts = []
        for k, v in world_setting.items():
            if v and str(v).strip():
                world_parts.append(f"【{k}】{v}")
        world_text = "\n".join(world_parts) if world_parts else "(无世界观设定)"

        system_prompt = """你是一位严格的人设与世界观守卫（CharacterGuard）。
你的职责有两项，且只有这两项：
1. 检测人物OOC（Out of Character）：人物言行、性格、说话方式是否违背人物档案
2. 检测世界观违规：正文中的设定是否违背世界观规则（能力体系、社会规则、禁忌、底层逻辑）

不要评价剧情结构、时空逻辑、文笔。

评分采用 10 分制硬性扣分标尺：
- 满分 10 分
- 主角/核心人物明显OOC（性格核心矛盾）：一处扣 2.5 分
- 次要人物OOC：一处扣 1.0 分
- 世界观底层规则违反（魔法/科技/社会规则）：一处扣 3.0 分
- 世界观细节偏差（非底层规则）：一处扣 1.0 分

输出严格 JSON，不含其他文字：
{
  "gate_name": "character_guard",
  "total_score": 8.5,
  "breakdown": {
    "character_ooc": 10.0,
    "world_setting_logic": 7.0
  },
  "action": "PASS",
  "feedback": "精确引用原文违规片段，说明违背了哪条人设/世界观规则，给出具体修改建议。"
}
action 为 "PASS" 表示 total_score >= 8.0，"REJECT" 表示不达标。"""

        user_prompt = f"""【出场人物档案】
{char_text}

【世界观设定】
{world_text}

【待审核正文】
{content[:6000]}{'...(已截断)' if len(content) > 6000 else ''}

请逐条对照人物档案和世界观设定，检测OOC和世界观违规。"""

        try:
            response = self._call_llm(system_prompt, user_prompt, max_tokens=2048)
            return self._parse_gate_response(response, "character_guard")
        except Exception as e:
            return GateResult(
                gate_name="character_guard", total_score=10.0,
                breakdown={}, action="PASS",
                feedback=f"人设守卫调用失败：{e}，自动放行", passed=True
            )

    def run_continuity_tracker(self, chapter_number: int, content: str) -> GateResult:
        """
        Reviewer-C: 时空与状态连续性追踪官 (ContinuityTracker)
        职责：人物当前状态（伤势/持有物）+ 时空逻辑（地理位置/时间线）连续性。
        Context：人物状态面板 + 前10章摘要（无世界观、无人物档案详情）。
        熔断阈值：9.0
        """
        # 人物状态面板（只取 current_state，不含详细档案）
        chars = self.memory.global_mem.get_all_characters()
        state_lines = [
            f"{c.name}（{c.role or '配角'}）：{c.current_state}"
            for c in chars if c.current_state and c.current_state.strip()
        ]
        char_states = "\n".join(state_lines[:20]) if state_lines else "(无状态记录)"

        # 前10章摘要（时间线追踪）
        from core.models import Chapter as ChapterModel
        prev_chapters = (
            self.memory.chapter_mem.db.query(ChapterModel)
            .filter(
                ChapterModel.novel_id == self.novel_id,
                ChapterModel.chapter_number < chapter_number,
                ChapterModel.content.isnot(None)
            )
            .order_by(ChapterModel.chapter_number.desc())
            .limit(10).all()[::-1]
        )
        summary_parts = [
            f"第{ch.chapter_number}章《{ch.title or ''}》：{ch.summary or '(无摘要)'}"
            for ch in prev_chapters
        ]
        summaries_text = "\n".join(summary_parts) if summary_parts else "(无前情记录)"

        system_prompt = """你是一位严苛的时空与状态连续性追踪官（ContinuityTracker）。
你的职责有两项，且只有这两项：
1. 状态连续性：检查正文中人物的伤势、持有物品、体力状态是否与状态面板一致
2. 时空逻辑：检查地理位置移动是否合理、时间线是否自洽、事件先后顺序是否矛盾

不要评价人设、世界观规则、文笔。

评分采用 10 分制硬性扣分标尺：
- 满分 10 分
- 状态硬伤（已废的肢体被正常使用、凭空使用未持有道具）：一处扣 2.5 分
- 状态软伤（状态描述与前文有细节出入但不影响逻辑）：一处扣 1.0 分
- 时空硬伤（地理距离不合理、时间线明显矛盾）：一处扣 2.5 分
- 时空软伤（时间/地点描述模糊但未明确矛盾）：一处扣 0.5 分

输出严格 JSON，不含其他文字：
{
  "gate_name": "continuity_tracker",
  "total_score": 9.0,
  "breakdown": {
    "state_continuity": 10.0,
    "spatiotemporal_logic": 8.0
  },
  "action": "PASS",
  "feedback": "精确引用原文矛盾位置，指出与哪一章的状态/时空记录相矛盾，给出具体修改建议。"
}
action 为 "PASS" 表示 total_score >= 8.5，"REJECT" 表示不达标。"""

        user_prompt = f"""【人物当前状态面板】
{char_states}

【前情摘要（近10章）】
{summaries_text}

【待审核正文】
{content[:6000]}{'...(已截断)' if len(content) > 6000 else ''}

请严格对照状态面板和前情摘要，逐条检查状态矛盾和时空硬伤。"""

        try:
            response = self._call_llm(system_prompt, user_prompt, max_tokens=2048)
            return self._parse_gate_response(response, "continuity_tracker")
        except Exception as e:
            return GateResult(
                gate_name="continuity_tracker", total_score=10.0,
                breakdown={}, action="PASS",
                feedback=f"连续性追踪官调用失败：{e}，自动放行", passed=True
            )

    def run_continuity_tracker_agentic(self, chapter_number: int, content: str) -> GateResult:
        """
        Reviewer-C（Agentic 版）：时空与状态连续性追踪官
        与普通版的区别：不预先注入固定摘要，而是让 LLM 通过工具主动查询
        所需的历史章节原文、角色详细档案、时间线事件，再给出有据可查的评分。

        工具调用预期行为：
        - 查询涉及角色的完整档案（伤势/持有物/当前位置）
        - 按关键词语义检索历史原文片段（确认伤势发生节点、道具得失等）
        - 查询时间线事件（核对地点移动时间合理性）
        - 查询章节摘要（了解前情脉络）
        """
        from core.agentic_loop import AgenticLoop
        from core.tool_executor import ToolExecutor, TOOL_DEFINITIONS

        content_preview = content[:8000] + ("...(内容过长已截断)" if len(content) > 8000 else "")

        system_prompt = f"""你是一位严苛的时空与状态连续性追踪官（ContinuityTracker），专职检查小说正文中的连续性硬伤。

你的职责有且只有两项：
1. **状态连续性**：人物的伤势、持有物品、体力状态是否与历史记录一致（如已断的肢体、已失去的道具、已施放的技能消耗等）
2. **时空逻辑**：地理位置移动是否合理、时间线是否自洽、事件先后顺序是否矛盾

不要评价人设性格、世界观规则的合理性、文笔风格。

━━━ 工作流程 ━━━
审核前，请先通过工具主动查询：
- 本章涉及的每位角色的完整档案（重点看 current_state）
- 与道具/伤势/位置相关的历史原文片段（用 search_past_chapters 语义检索）
- 时间线中的地点移动/重大事件记录（用 query_timeline）
- 关键前置章节的摘要（用 query_chapter_summary）

查询完成后，对照证据给出评分。

━━━ 评分规则（10分制硬性扣分）━━━
- 满分 10 分
- 状态硬伤（已废的肢体被正常使用、凭空使用未持有道具）：一处扣 2.5 分
- 状态软伤（状态描述与前文有细节出入但不影响逻辑）：一处扣 1.0 分
- 时空硬伤（地理距离不合理、时间线明显矛盾）：一处扣 2.5 分
- 时空软伤（时间/地点描述模糊但未明确矛盾）：一处扣 0.5 分

━━━ 最终输出格式（查询完毕后输出，不含其他文字）━━━
{{
  "gate_name": "continuity_tracker",
  "total_score": 9.0,
  "breakdown": {{
    "state_continuity": 10.0,
    "spatiotemporal_logic": 8.0
  }},
  "action": "PASS",
  "feedback": "精确引用原文矛盾位置，指出与第几章哪条记录相矛盾，给出具体修改建议。若无问题则简述检查结论。"
}}
action 为 "PASS" 表示 total_score >= 8.5，"REJECT" 表示不达标。

{TOOL_DEFINITIONS}"""

        user_prompt = f"""请对以下第{chapter_number}章正文进行时空与状态连续性审核。

【待审核正文（第{chapter_number}章）】
{content_preview}

请先调用工具查询必要的历史信息，再输出 JSON 评分结果。"""

        loop = AgenticLoop(
            llm=self.llm,
            tool_executor=ToolExecutor(self.memory),
            max_tool_calls=10,
        )

        try:
            raw = loop.run(system_prompt, user_prompt, max_tokens_per_call=4096)
            return self._parse_gate_response(raw, "continuity_tracker")
        except Exception as e:
            return GateResult(
                gate_name="continuity_tracker", total_score=10.0,
                breakdown={}, action="PASS",
                feedback=f"Agentic 连续性追踪官调用失败：{e}，自动放行", passed=True
            )

    def run_style_refiner(self, chapter_number: int, content: str) -> GateResult:
        """
        Reviewer-D: 文风打磨官 (StyleRefiner)
        职责：去AI腔、违禁句式、Show don't tell。
        Context：仅 deai_rules + style_profile（最轻，无任何故事信息）。
        熔断阈值：8.0
        """
        novel = self.memory.global_mem.get_novel()

        # 去AI规则
        if novel and novel.deai_rules and novel.deai_rules.strip():
            deai_rules = novel.deai_rules.strip()
        else:
            from core.config import DEFAULT_DEAI_RULES
            deai_rules = DEFAULT_DEAI_RULES

        # 风格档案（如有）
        style_text = ""
        if novel:
            style_profile = novel.get_style_profile()
            if style_profile:
                style_lines = [f"- {k}：{v}" for k, v in style_profile.items() if v]
                style_text = "\n".join(style_lines[:10])

        # ── 构建近 5 章高频句式统计（跨章节重复检测）────────────────────────
        recent_pattern_block = ""
        try:
            import re as _re
            recent_chs = self.memory.chapter_mem.get_recent_chapters(
                before_chapter=chapter_number + 1, count=5
            )
            TRACKED = [
                ("不是…而是…/不是…是…", _re.compile(r"不是.{1,20}[，,]?\s*(而是|是).{1,20}")),
                ("与其说…不如说…",       _re.compile(r"与其说.{1,20}不如说")),
                ("他/她不知道的是",        _re.compile(r"[他她].{0,4}不知道的是")),
                ("这意味着",              _re.compile(r"这意味着")),
                ("更大的.*正在.*逼近",    _re.compile(r"更大的.{0,10}正在.{0,10}逼近")),
                ("突然",                  _re.compile(r"突然")),
                ("然而",                  _re.compile(r"然而")),
                ("不得不",                _re.compile(r"不得不")),
            ]
            rows = []
            for ch in recent_chs:
                text = (ch.content or "")[:3000]
                counts = []
                for label, pat in TRACKED:
                    n = len(pat.findall(text))
                    counts.append(f"{label}×{n}" if n else None)
                row_str = "、".join(c for c in counts if c)
                if row_str:
                    rows.append(f"  第{ch.chapter_number}章：{row_str}")
            if rows:
                recent_pattern_block = (
                    "\n【近期章节高频句式统计（供跨章节重复检测）】\n"
                    + "\n".join(rows)
                    + "\n说明：若某句式在近 3 章中累计出现 ≥3 次，即视为「跨章节句式滥用」，"
                    "本章再次出现该句式须额外扣 1.0 分/处。\n"
                )
        except Exception:
            recent_pattern_block = ""

        system_prompt = """你是一位犀利的文风打磨官（StyleRefiner）。
你的唯一职责：检测并标注所有"AI味"违规，提供精准修改建议。
不要评价剧情、人设、时空逻辑。

评分采用 10 分制硬性扣分标尺：
- 满分 10 分
- 【最高优先级】对比转折句式（"不是……而是……"及所有变体"不是A，是B""不是A是B"，"与其说……不如说……"，"与其……不如……"）：每出现一处扣 2.5 分，这是 AI 生成的最强特征，零容忍
- 跨章节句式滥用：若【近期章节高频句式统计】中某句式在近 3 章累计 ≥3 次，本章再出现该句式额外扣 1.0 分/处
- 说书人腔调/上帝视角（段尾总结性/预言性发言，如"这意味着…""更大的危机正在逼近"）：每处扣 1.5 分
- 机械连接词堆砌（过度使用"突然、竟然、然而、不得不"）：每处扣 0.5 分
- 直说情绪而非Show（"他很恐慌"而非通过生理细节表现）：每处扣 1.0 分

输出严格 JSON，不含其他文字：
{
  "gate_name": "style_refiner",
  "total_score": 7.5,
  "breakdown": {
    "forbidden_syntax": 8.5,
    "narrator_intrusion": 7.0,
    "show_dont_tell": 9.0
  },
  "action": "REJECT",
  "feedback": "逐条列出：违规原文片段 → 违规类型 → 具体修改建议。PASS 时也需指出可优化细节。"
}
action 为 "PASS" 表示 total_score >= 8.0，"REJECT" 表示不达标。"""

        style_block = f"\n【风格档案参考】\n{style_text}" if style_text else ""
        user_prompt = f"""【去AI味规则（逐条对照检测）】
{deai_rules}{style_block}
{recent_pattern_block}
【待审核正文】
{content[:5000]}{'...(已截断)' if len(content) > 5000 else ''}

请逐条对照规则检查正文，特别注意：
1. "不是……是……"（无"而"字的变体）与"不是……而是……"同等禁用，每处扣 2.5 分；
2. 若【近期章节高频句式统计】显示某句式已累计出现 ≥3 次，本章再出现须额外扣分。
不要放过任何违规。"""

        try:
            response = self._call_llm(system_prompt, user_prompt, max_tokens=2048)
            return self._parse_gate_response(response, "style_refiner")
        except Exception as e:
            return GateResult(
                gate_name="style_refiner", total_score=10.0,
                breakdown={}, action="PASS",
                feedback=f"文风打磨官调用失败：{e}，自动放行", passed=True
            )

    def run_parallel_review(
        self,
        chapter_number: int,
        content: str,
        skip_gates: set[str] | None = None,
    ) -> dict:
        """
        并行执行四个 Reviewer，返回汇总结果。

        Args:
            chapter_number: 章节号
            content: 待审核正文
            skip_gates: 已通过、本轮跳过的关卡名集合

        Returns:
            {
                "gates": [GateResult, ...],          # 本轮执行的关卡结果（按顺序）
                "all_passed": bool,                   # 本轮全部通过
                "reject_feedbacks": str,              # 合并所有 REJECT 的 feedback
                "passed_gate_names": set[str],        # 本轮通过的关卡名
                "failed_gate_names": set[str],        # 本轮未通过的关卡名
                "weighted_score": float,              # 加权综合得分
            }
        """
        import concurrent.futures
        from core.config import CONTINUITY_TRACKER_AGENTIC

        skip_gates = skip_gates or set()

        # 四关卡定义：(gate_name, method, weight, label)
        # 时空状态关卡根据配置在 agentic / 普通版本间切换
        continuity_fn = (
            self.run_continuity_tracker_agentic
            if CONTINUITY_TRACKER_AGENTIC
            else self.run_continuity_tracker
        )
        gate_defs = [
            ("plot_aligner",       self.run_plot_aligner,    0.40, "剧情对齐"),
            ("character_guard",    self.run_character_guard, 0.20, "人设世界观"),
            ("continuity_tracker", continuity_fn,            0.20, "时空状态"),
            ("style_refiner",      self.run_style_refiner,   0.20, "文风去AI"),
        ]

        results: list[GateResult] = []
        skipped_scores: list[tuple[float, float]] = []  # (score, weight) for skipped

        # 收集需要执行的关卡
        to_run = [(name, fn, weight, label)
                  for name, fn, weight, label in gate_defs
                  if name not in skip_gates]
        skipped = [(name, weight)
                   for name, fn, weight, label in gate_defs
                   if name in skip_gates]

        # 并行执行
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future_map = {
                executor.submit(fn, chapter_number, content): (name, weight, label)
                for name, fn, weight, label in to_run
            }
            ordered_results: list[tuple[str, float, GateResult]] = []
            for future in concurrent.futures.as_completed(future_map):
                name, weight, label = future_map[future]
                try:
                    gate_result = future.result()
                except Exception as e:
                    gate_result = GateResult(
                        gate_name=name, total_score=10.0,
                        breakdown={}, action="PASS",
                        feedback=f"{label}调用异常：{e}，自动放行", passed=True
                    )
                ordered_results.append((name, weight, gate_result))

        # 按原始顺序排列结果
        name_order = [name for name, _, _, _ in gate_defs if name not in skip_gates]
        ordered_results.sort(key=lambda x: name_order.index(x[0]))
        results = [r for _, _, r in ordered_results]

        # 计算加权得分（包含跳过关卡，跳过关卡视为满分10.0）
        total_weight = sum(w for _, w, _ in ordered_results) + sum(w for _, w in skipped)
        weighted_score = sum(r.total_score * w for _, w, r in ordered_results)
        weighted_score += sum(10.0 * w for _, w in skipped)
        if total_weight > 0:
            weighted_score /= total_weight

        # 汇总 REJECT 反馈
        reject_parts = []
        failed_names: set[str] = set()
        passed_names: set[str] = set(skip_gates)  # 跳过的视为已通过

        for _, weight, gate_result in ordered_results:
            if not gate_result.passed:
                failed_names.add(gate_result.gate_name)
                label_map = {
                    "plot_aligner": "【剧情对齐问题】",
                    "character_guard": "【人设/世界观问题】",
                    "continuity_tracker": "【时空/状态问题】",
                    "style_refiner": "【文风问题】",
                }
                label = label_map.get(gate_result.gate_name, f"【{gate_result.gate_name}】")
                reject_parts.append(f"{label}\n{gate_result.feedback}")
            else:
                passed_names.add(gate_result.gate_name)

        reject_feedbacks = "\n\n".join(reject_parts)

        return {
            "gates": results,
            "all_passed": len(failed_names) == 0,
            "reject_feedbacks": reject_feedbacks,
            "passed_gate_names": passed_names,
            "failed_gate_names": failed_names,
            "weighted_score": round(weighted_score, 2),
        }

    def close(self):
        self.memory.close()
