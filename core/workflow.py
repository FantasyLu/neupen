"""
工作流编排模块
实现从灵感到完稿的全流程支持

阶段：
1. 设定阶段 - 引导用户完善世界观和人物设定
2. 大纲阶段 - 生成总大纲、卷大纲、章纲
3. 写作阶段 - 按章生成内容，自动审核和润色
4. 修改阶段 - 支持用户修改，自动更新并检测冲突
5. 导出阶段 - 支持多种格式导出
"""

import json
from typing import Optional, Callable
from dataclasses import dataclass

from core.models import get_db, Novel, NovelOutline
from core.memory import MemoryManager
from core.agents import OutlineAgent, CharacterAgent, WriterAgent, ReviewerAgent, PolisherAgent
from core.detector import ConflictDetector, ReviewReport
from core.config import AUTO_APPROVE_THRESHOLD, MAX_REVIEW_ITERATIONS, REVIEW_SCORE_THRESHOLD


# ======================================
# 工作流结果数据结构
# ======================================

@dataclass
class WorkflowResult:
    """工作流执行结果"""
    success: bool
    message: str
    data: dict = None

    def __post_init__(self):
        if self.data is None:
            self.data = {}


# ======================================
# 核心工作流类
# ======================================

class NovelWorkflow:
    """
    小说创作工作流编排器
    管理整个创作流程的状态转换和 Agent 调用
    """

    def __init__(self, novel_id: int):
        self.novel_id = novel_id
        self.memory = MemoryManager(novel_id)
        self.db = get_db()

        # 读取项目级别的模型选择（覆盖全局默认）
        novel = self.memory.global_mem.get_novel()
        self.model_id = (novel.llm_model if novel and novel.llm_model else None)

        # 各 Agent 独立模型（三级回退：per-agent → llm_model → None/全局默认）
        if novel:
            self.model_outline   = novel.get_agent_model("outline")
            self.model_character = novel.get_agent_model("character")
            self.model_writer    = novel.get_agent_model("writer")
            self.model_reviewer  = novel.get_agent_model("reviewer")
            self.model_polisher  = novel.get_agent_model("polisher")
            self.model_reader    = novel.get_agent_model("reader")
        else:
            self.model_outline = self.model_character = self.model_writer = \
                self.model_reviewer = self.model_polisher = self.model_reader = None

        # 按需初始化 Agent（避免不必要的资源消耗）
        self._outline_agent = None
        self._character_agent = None
        self._writer_agent = None
        self._reviewer_agent = None
        self._polisher_agent = None
        self._reader_agent = None

    @property
    def outline_agent(self) -> OutlineAgent:
        if not self._outline_agent:
            self._outline_agent = OutlineAgent(self.novel_id, self.model_outline)
        return self._outline_agent

    @property
    def character_agent(self) -> CharacterAgent:
        if not self._character_agent:
            self._character_agent = CharacterAgent(self.novel_id, self.model_character)
        return self._character_agent

    @property
    def writer_agent(self) -> WriterAgent:
        if not self._writer_agent:
            self._writer_agent = WriterAgent(self.novel_id, self.model_writer)
        return self._writer_agent

    @property
    def reviewer_agent(self) -> ReviewerAgent:
        if not self._reviewer_agent:
            self._reviewer_agent = ReviewerAgent(self.novel_id, self.model_reviewer)
        return self._reviewer_agent

    @property
    def polisher_agent(self) -> PolisherAgent:
        if not self._polisher_agent:
            self._polisher_agent = PolisherAgent(self.novel_id, self.model_polisher)
        return self._polisher_agent

    @property
    def reader_agent(self):
        if not self._reader_agent:
            from core.agents import ReaderAgent
            self._reader_agent = ReaderAgent(self.novel_id, self.model_reader)
        return self._reader_agent

    # ======================================
    # 阶段 1: 项目创建
    # ======================================

    @staticmethod
    def create_novel(title: str, logline: str, genre: str = "",
                      writing_style: str = "",
                      llm_model: str = None) -> int:
        """
        创建新小说项目
        返回新建项目的 ID
        """
        db = get_db()
        novel = Novel(
            title=title,
            logline=logline,
            genre=genre,
            writing_style=writing_style,
            llm_model=llm_model or None,
            status="planning"
        )
        db.add(novel)
        db.commit()
        db.refresh(novel)
        db.close()
        return novel.id

    # ======================================
    # 阶段 2: 大纲生成
    # ======================================

    def generate_outline(self, total_chapters: int = 100,
                           progress_callback: Callable = None) -> WorkflowResult:
        """
        从一句话灵感生成完整大纲
        自动保存到数据库
        """
        novel = self.memory.global_mem.get_novel()
        if not novel:
            return WorkflowResult(success=False, message="小说项目不存在")

        if progress_callback:
            progress_callback("🎯 正在生成总大纲...")

        try:
            # 调用大纲师 Agent
            outline_data = self.outline_agent.generate_full_outline(
                logline=novel.logline or "",
                genre=novel.genre or "",
                world_setting=json.dumps(novel.get_world_setting(), ensure_ascii=False),
                total_chapters=total_chapters
            )

            if progress_callback:
                progress_callback("💾 正在保存大纲数据...")

            # 保存总大纲
            total = outline_data.get("total_outline", {})
            world_setting = total.pop("world_setting", {})
            self.memory.global_mem.save_outline({
                "novel_id": self.novel_id,
                **total,
                "total_chapters": total_chapters,
                "full_outline_text": json.dumps(outline_data, ensure_ascii=False)
            })

            # 更新世界观设定
            if world_setting:
                self.memory.global_mem.save_world_setting(world_setting)

            # 保存卷大纲
            for vol_data in outline_data.get("volumes", []):
                self.memory.global_mem.save_volume(vol_data)

            # 保存章纲
            chapters_data = outline_data.get("chapters", [])
            for ch_data in chapters_data:
                # 处理JSON数组字段
                for list_field in ["outline_characters", "outline_foreshadowing_set",
                                    "outline_foreshadowing_collect"]:
                    if list_field in ch_data and isinstance(ch_data[list_field], list):
                        ch_data[list_field] = json.dumps(ch_data[list_field], ensure_ascii=False)
                ch_data["novel_id"] = self.novel_id
                ch_data["status"] = "outlined"
                self.memory.global_mem.save_chapter_outline(ch_data)

            # 同步章纲中的伏笔字符串到 Foreshadowing 表（新建不存在的行）
            synced_count = self.memory.global_mem.sync_foreshadowings_from_outlines()
            if synced_count and progress_callback:
                progress_callback(f"🔖 同步伏笔库：新增 {synced_count} 条伏笔记录")

            # 更新小说状态
            novel.status = "outlining"
            self.db.commit()

            if progress_callback:
                progress_callback("✅ 大纲生成完成！")

            return WorkflowResult(
                success=True,
                message=f"成功生成大纲：{len(chapters_data)}章详细章纲",
                data={"chapters_count": len(chapters_data)}
            )

        except Exception as e:
            return WorkflowResult(success=False, message=f"大纲生成失败：{e}")

    def generate_characters_from_outline(self,
                                           progress_callback: Callable = None) -> WorkflowResult:
        """
        根据大纲自动生成人物档案
        """
        outline = self.memory.global_mem.get_outline()
        if not outline:
            return WorkflowResult(success=False, message="请先生成大纲")

        if progress_callback:
            progress_callback("👤 正在生成人物档案...")

        try:
            # 构建大纲文本
            outline_text = self.memory.global_mem.build_global_context(include_chapters=True)

            # 调用人设师 Agent
            characters_data = self.character_agent.generate_characters(outline_text)

            # 保存人物档案
            for char_data in characters_data:
                # 处理JSON字段
                for json_field in ["abilities", "relationships"]:
                    if json_field in char_data and not isinstance(char_data[json_field], str):
                        char_data[json_field] = json.dumps(char_data[json_field], ensure_ascii=False)
                if "aliases" in char_data and not isinstance(char_data["aliases"], str):
                    char_data["aliases"] = json.dumps(char_data["aliases"], ensure_ascii=False)
                char_data["novel_id"] = self.novel_id
                self.memory.global_mem.save_character(char_data)

            if progress_callback:
                progress_callback(f"✅ 成功创建 {len(characters_data)} 个人物档案！")

            return WorkflowResult(
                success=True,
                message=f"成功创建 {len(characters_data)} 个人物档案",
                data={"count": len(characters_data)}
            )

        except Exception as e:
            return WorkflowResult(success=False, message=f"人物生成失败：{e}")

    # ======================================
    # 阶段 3: 章节写作（核心工作流）
    # ======================================

    def write_and_review_chapter(
        self,
        chapter_number: int,
        word_target: int = 3000,
        auto_polish: bool = True,
        progress_callback: Callable = None,
        stream_callback: Callable = None
    ) -> WorkflowResult:
        """
        完整的章节生成流水线：
        写作 → 审核 → 自动修复轻微问题 → 润色 → 保存

        Args:
            chapter_number: 章节序号
            word_target: 目标字数
            auto_polish: 是否自动润色
            progress_callback: 进度回调 (message: str)
            stream_callback: 流式输出回调 (chunk: str)

        Returns:
            WorkflowResult，包含最终内容和审核报告
        """
        try:
            # Step 1: 生成章节草稿
            if progress_callback:
                progress_callback(f"✍️ 正在写作第{chapter_number}章...")

            draft_content = self.writer_agent.write_chapter(
                chapter_number=chapter_number,
                word_target=word_target,
                stream_callback=stream_callback
            )

            # 保存草稿
            self.memory.save_new_chapter(chapter_number, draft_content, "draft")

            # Step 2-3: 审核 → 修改循环，直到评分 ≥ REVIEW_SCORE_THRESHOLD
            chapter = self.memory.global_mem.get_chapter_outline(chapter_number)
            import json as json_module

            current_content = draft_content
            report = None
            for iteration in range(MAX_REVIEW_ITERATIONS):
                if progress_callback:
                    round_label = "" if iteration == 0 else f"（第{iteration + 1}轮）"
                    progress_callback(f"🔍 AI 审核{round_label}...")

                report = self.reviewer_agent.review_chapter(chapter_number, current_content)

                # 保存本轮审核报告
                if chapter:
                    chapter.review_report = json_module.dumps(report.to_dict(), ensure_ascii=False)
                    chapter.review_score = report.overall_score
                    chapter.status = "review_pending" if not report.passed else "reviewed"
                    self.db.commit()

                # 判断是否达标
                has_major = any(c.severity >= AUTO_APPROVE_THRESHOLD for c in report.conflicts)
                if report.overall_score >= REVIEW_SCORE_THRESHOLD and not has_major:
                    if progress_callback:
                        progress_callback(
                            f"✅ 审核通过（第{iteration + 1}轮）"
                            f"，评分：{report.overall_score:.1f}/10"
                        )
                    break

                # 未达标且还有迭代次数 → 修复所有问题
                if iteration < MAX_REVIEW_ITERATIONS - 1:
                    if progress_callback:
                        progress_callback(
                            f"🔧 修改中（当前评分 {report.overall_score:.1f}/10，"
                            f"第{iteration + 1}/{MAX_REVIEW_ITERATIONS}轮）..."
                        )
                    current_content = self.reviewer_agent.fix_all_issues(
                        current_content, report, self.novel_id
                    )
                else:
                    if progress_callback:
                        progress_callback(
                            f"⚠️ 已达最大修改次数，最终评分：{report.overall_score:.1f}/10"
                        )

            # Step 4: 润色
            final_content = current_content
            if auto_polish:
                if progress_callback:
                    progress_callback(f"✨ 正在润色第{chapter_number}章...")
                final_content = self.polisher_agent.polish_chapter(current_content)

            # Step 5: 保存最终内容
            self.memory.save_new_chapter(chapter_number, final_content, "content")

            # 保存版本历史
            if chapter:
                self.memory.chapter_mem.save_version(
                    chapter.id, draft_content, "draft", "AI初稿"
                )
                self.memory.chapter_mem.save_version(
                    chapter.id, final_content, "polished", "经审核润色后的版本"
                )

                # 更新章节状态
                chapter.status = "published"
                chapter.word_count = len(final_content)
                chapter.approval_status = "pending"
                self.db.commit()

            # 生成章节摘要（供后续章节的 recent_context 使用）
            if progress_callback:
                progress_callback(f"📝 生成章节摘要...")
            try:
                summary_text, key_events = self.writer_agent.summarize_chapter(
                    chapter_number, chapter.title or "", final_content
                )
                if summary_text:
                    self.memory.chapter_mem.save_chapter_summary(
                        chapter_number, summary_text, key_events
                    )
            except Exception:
                pass  # 摘要生成失败不影响主流程

            # Step 6: 自动大纲 / 设定同步检测
            sync_checks = {}
            if progress_callback:
                progress_callback(f"🔄 检测大纲/设定同步...")
            try:
                sync_checks = self.outline_agent.analyze_chapter_consistency(
                    chapter_number, final_content
                )
            except Exception:
                pass  # 同步检测失败不影响主流程

            if progress_callback:
                progress_callback(f"✅ 第{chapter_number}章完成！字数：{len(final_content)}")

            return WorkflowResult(
                success=True,
                message=f"第{chapter_number}章写作完成",
                data={
                    "content": final_content,
                    "word_count": len(final_content),
                    "review_report": report.to_dict() if report else {},
                    "review_passed": report.passed if report else True,
                    "overall_score": report.overall_score if report else 0.0,
                    "sync_checks": sync_checks,
                }
            )

        except Exception as e:
            return WorkflowResult(
                success=False,
                message=f"第{chapter_number}章写作失败：{e}"
            )

    # ======================================
    # 阶段 4: 修改与更新
    # ======================================

    def update_chapter_content(self, chapter_number: int,
                                 new_content: str,
                                 change_summary: str = "用户修改") -> WorkflowResult:
        """
        用户修改章节内容后的处理
        - 保存修改
        - 记录版本历史
        - 同步向量数据库
        - 触发冲突检测
        """
        try:
            chapter = self.memory.global_mem.get_chapter_outline(chapter_number)
            if not chapter:
                return WorkflowResult(success=False, message="章节不存在")

            # 保存版本历史
            self.memory.chapter_mem.save_version(
                chapter.id, new_content, "user_edit", change_summary
            )

            # 更新内容
            self.memory.save_new_chapter(chapter_number, new_content, "content")

            # 重置审批状态
            chapter.approval_status = "pending"
            self.db.commit()

            return WorkflowResult(success=True, message="修改已保存")

        except Exception as e:
            return WorkflowResult(success=False, message=f"保存失败：{e}")

    def update_character(self, character_name: str,
                           updates: dict) -> WorkflowResult:
        """
        更新人物设定并检测影响
        """
        try:
            char = self.memory.global_mem.get_character(character_name)
            if not char:
                return WorkflowResult(success=False, message=f"找不到人物：{character_name}")

            # 保存更新
            for k, v in updates.items():
                if hasattr(char, k):
                    setattr(char, k, v)
            self.db.commit()

            # 检测设定冲突
            detector = ConflictDetector(self.novel_id, self.model_reviewer)
            conflicts = detector.detect_setting_conflict(updates, f"人物【{character_name}】")
            detector.close()

            msg = "人物设定已更新"
            if conflicts:
                msg += f"，发现{len(conflicts)}个潜在冲突，请查看冲突报告"

            return WorkflowResult(
                success=True,
                message=msg,
                data={"conflicts": [c.__dict__ for c in conflicts]}
            )

        except Exception as e:
            return WorkflowResult(success=False, message=f"更新失败：{e}")

    def update_world_setting(self, new_setting: dict) -> WorkflowResult:
        """
        更新世界观设定，并分析对已写完章节的影响

        变更检测流程：
        1. 比较新旧设定，提取实际变更项
        2. 保存新设定到数据库
        3. 调用 LLM 分析哪些已完成章节可能受影响
        4. 返回受影响章节列表供用户决策

        Returns:
            WorkflowResult.data 包含:
              - "changes": {字段: 新值} 实际变更的字段
              - "impact": {affected_chapters, unaffected_count, summary}
        """
        try:
            novel = self.memory.global_mem.get_novel()
            if not novel:
                return WorkflowResult(success=False, message="小说项目不存在")

            # 计算实际变更（过滤掉未修改的字段）
            old_setting = novel.get_world_setting()
            changes = {k: v for k, v in new_setting.items()
                       if v.strip() and old_setting.get(k, "").strip() != v.strip()}

            # 保存新设定
            self.memory.global_mem.save_world_setting(new_setting)

            # 无实质变更时直接返回
            if not changes:
                return WorkflowResult(success=True, message="世界观设定已保存（内容无变更）",
                                      data={"changes": {}, "impact": None})

            # 分析对已写章节的影响
            detector = ConflictDetector(self.novel_id, self.model_reviewer)
            impact = detector.analyze_setting_impact(changes, "世界观设定")
            detector.close()

            affected = impact.get("affected_chapters", [])
            msg = "世界观设定已更新"
            if affected:
                msg += f"，{len(affected)} 个已完成章节可能受影响，请查看影响报告"

            return WorkflowResult(
                success=True,
                message=msg,
                data={"changes": changes, "impact": impact}
            )

        except Exception as e:
            return WorkflowResult(success=False, message=f"设定更新失败：{e}")

    def update_chapter_outline(self, chapter_number: int,
                                updates: dict,
                                reason: str = "") -> WorkflowResult:
        """
        修改章节章纲，处理以下情况：
        - 若该章尚未写作：直接更新章纲
        - 若该章已写完：降级状态要求重审，并分析对后续已写章节的影响

        Args:
            chapter_number: 要修改的章节号
            updates: 要更新的章纲字段 dict，如 {"outline_core_event": "...", "title": "..."}
            reason: 用户填写的修改原因（供追溯）

        Returns:
            WorkflowResult.data 包含:
              - "was_published": bool，该章是否已写完（决定是否需要重写）
              - "affected_chapters": [{"chapter_number": N, "reason": "...", "severity": "..."}]
        """
        try:
            chapter = self.memory.global_mem.get_chapter_outline(chapter_number)
            if not chapter:
                return WorkflowResult(success=False, message=f"第{chapter_number}章章纲不存在")

            old_outline_text = chapter.to_outline_text()
            was_published = (chapter.status == "published")

            # 保存章纲更新
            allowed_fields = {
                "title", "outline_core_event", "outline_conflict",
                "outline_characters", "outline_scene", "outline_foreshadowing_set",
                "outline_foreshadowing_collect", "outline_emotion", "outline_ending"
            }
            for k, v in updates.items():
                if k in allowed_fields and hasattr(chapter, k):
                    setattr(chapter, k, v)

            # 若章节已写完，需要降级为待重审
            if was_published:
                chapter.status = "reviewed"  # 已写但需重审

            self.db.commit()

            # 分析对后续已写章节的影响（仅当核心内容发生变化时）
            core_change_keys = {"outline_core_event", "outline_conflict",
                                "outline_characters", "outline_ending"}
            affected_chapters = []
            if core_change_keys & set(updates.keys()):
                detector = ConflictDetector(self.novel_id, self.model_reviewer)
                affected_chapters = detector.analyze_outline_change_impact(
                    chapter_number, old_outline_text, updates
                )
                detector.close()

            msg = f"第{chapter_number}章章纲已更新"
            if was_published:
                msg += "，原章节内容需要重新审核（已标记）"
            if affected_chapters:
                msg += f"，另有 {len(affected_chapters)} 个后续章节可能受影响"

            return WorkflowResult(
                success=True,
                message=msg,
                data={
                    "was_published": was_published,
                    "affected_chapters": affected_chapters,
                    "reason": reason
                }
            )

        except Exception as e:
            return WorkflowResult(success=False, message=f"章纲更新失败：{e}")

    def batch_update_chapter_outlines(self, outlines: list[dict]) -> WorkflowResult:
        """
        批量保存 AI 生成的章节大纲。

        - 章节已存在：更新 title / outline_* 字段，outline_pending 升级为 outlined
        - 章节不存在：自动创建新章节槽，状态设为 outlined
        - 已写完（published）的章节：跳过，不覆盖正文
        """
        try:
            outline_fields = {
                "title", "outline_core_event", "outline_conflict",
                "outline_scene", "outline_emotion", "outline_ending",
            }
            updated = created = 0
            for data in outlines:
                ch_num = data.get("chapter_number")
                if not ch_num:
                    continue
                ch_num = int(ch_num)
                chapter = self.memory.global_mem.get_chapter_outline(ch_num)
                if chapter:
                    if chapter.status == "published":
                        continue  # 已写完，不覆盖
                    for field in outline_fields:
                        val = data.get(field)
                        if val:
                            setattr(chapter, field, val)
                    if chapter.status == "outline_pending":
                        chapter.status = "outlined"
                    updated += 1
                else:
                    # 章节不存在，新建章节槽
                    new_data = {k: v for k, v in data.items() if k in outline_fields and v}
                    new_data["chapter_number"] = ch_num
                    new_data["status"] = "outlined"
                    if "title" not in new_data:
                        new_data["title"] = f"第{ch_num}章"
                    self.memory.global_mem.save_chapter_outline(new_data)
                    created += 1
            self.db.commit()
            parts = []
            if updated:
                parts.append(f"更新 {updated} 章")
            if created:
                parts.append(f"新建 {created} 章")
            msg = "已" + "、".join(parts) + "的大纲"
            return WorkflowResult(success=True, message=msg, data={"updated": updated})
        except Exception as e:
            return WorkflowResult(success=False, message=f"批量更新失败：{e}")

    # ======================================
    # 风格迁移
    # ======================================

    def analyze_writing_style(self, reference_text: str,
                               progress_callback: Callable = None) -> WorkflowResult:
        """
        分析参考文本的写作风格，提取结构化特征，保存为风格档案。

        Args:
            reference_text: 喜欢的作家作品片段（建议500-3000字）
            progress_callback: 进度回调

        Returns:
            WorkflowResult.data["profile"] 包含10个风格维度
        """
        if not reference_text or not reference_text.strip():
            return WorkflowResult(success=False, message="参考文本不能为空")

        if progress_callback:
            progress_callback("🎨 正在分析写作风格特征，约需 20-40 秒...")

        try:
            profile = self.polisher_agent.analyze_style(reference_text.strip())

            # 保存风格档案和参考文本节选
            db = get_db()
            novel = db.query(Novel).filter(Novel.id == self.novel_id).first()
            if not novel:
                db.close()
                return WorkflowResult(success=False, message="小说项目不存在")
            novel.style_reference_text = reference_text.strip()[:3000]
            novel.set_style_profile(profile)
            db.commit()
            db.close()

            if progress_callback:
                progress_callback("✅ 风格分析完成！")

            return WorkflowResult(
                success=True,
                message="风格分析完成，已生成风格档案",
                data={"profile": profile}
            )

        except Exception as e:
            return WorkflowResult(success=False, message=f"风格分析失败：{e}")

    def auto_learn_style_from_chapters(self, sample_chapters: int = 5,
                                        progress_callback: Callable = None) -> WorkflowResult:
        """
        从已完成章节中自动学习写作风格。
        取最近 sample_chapters 篇已审核章节的正文，合并后调用 analyze_writing_style。
        """
        from core.models import Chapter
        db = get_db()
        chapters = (
            db.query(Chapter)
            .filter(
                Chapter.novel_id == self.novel_id,
                Chapter.approval_status == "approved",
                Chapter.content.isnot(None),
                Chapter.content != "",
            )
            .order_by(Chapter.chapter_number.desc())
            .limit(sample_chapters)
            .all()
        )
        db.close()

        if not chapters:
            return WorkflowResult(success=False, message="暂无已审核的章节，请先完成并审核至少一章。")

        # 按章节顺序拼接
        chapters_sorted = sorted(chapters, key=lambda c: c.chapter_number)
        combined = "\n\n".join(
            f"【第{c.chapter_number}章 {c.title or ''}】\n{c.content}" for c in chapters_sorted
        )
        count = len(chapters_sorted)

        if progress_callback:
            progress_callback(f"📖 已收集 {count} 章内容，正在分析风格特征…")

        result = self.analyze_writing_style(combined, progress_callback=progress_callback)
        if result.success:
            result.message = f"已从 {count} 章内容中学习风格，档案已保存。"
        return result

    def update_style_profile(self, profile: dict) -> WorkflowResult:
        """
        手动更新风格档案（用户编辑后保存）
        """
        try:
            db = get_db()
            novel = db.query(Novel).filter(Novel.id == self.novel_id).first()
            if not novel:
                db.close()
                return WorkflowResult(success=False, message="小说项目不存在")
            # 过滤空值字段
            cleaned = {k: v for k, v in profile.items() if v and str(v).strip()}
            novel.set_style_profile(cleaned)
            db.commit()
            db.close()
            return WorkflowResult(success=True, message="风格档案已保存")
        except Exception as e:
            return WorkflowResult(success=False, message=f"保存失败：{e}")

    def update_agent_models(self, agent_models: dict) -> WorkflowResult:
        """
        保存各 Agent 的模型分工配置。
        agent_models: {"outline": "model_id_or_empty", ...}
        空字符串 / None → 跟随项目默认模型（存 NULL）
        """
        allowed = {"outline", "character", "writer", "reviewer", "polisher", "reader"}
        try:
            db = get_db()
            novel_obj = db.query(Novel).filter(Novel.id == self.novel_id).first()
            if not novel_obj:
                db.close()
                return WorkflowResult(success=False, message="小说项目不存在")
            for key, model_id in agent_models.items():
                if key in allowed:
                    setattr(novel_obj, f"model_{key}", model_id or None)
            db.commit()
            db.close()
            return WorkflowResult(success=True, message="各 Agent 模型分工已保存")
        except Exception as e:
            return WorkflowResult(success=False, message=f"保存失败：{e}")

    # ======================================
    # 读者模拟
    # ======================================

    def reader_test_chapter(self, chapter_number: int,
                             reader_types: list[str] = None,
                             progress_callback: Callable = None) -> WorkflowResult:
        """
        对指定章节运行读者模拟测试，保存反馈到 Chapter。
        reader_types: 默认全部三种 ["power_fantasy", "literary", "light_novel"]
        """
        chapter = self.memory.global_mem.get_chapter_outline(chapter_number)
        if not chapter or not chapter.content:
            return WorkflowResult(success=False, message="章节不存在或尚未写作")

        if progress_callback:
            progress_callback(f"📖 正在模拟读者阅读第{chapter_number}章...")

        try:
            feedback = self.reader_agent.evaluate_chapter(
                chapter_number, chapter.content, reader_types
            )
            # 保存到 DB
            chapter.reader_feedback = json.dumps(feedback, ensure_ascii=False)
            chapter.reader_score = feedback.get("overall_score", 0)
            self.db.commit()

            return WorkflowResult(
                success=True,
                message=f"读者模拟完成，综合评分：{feedback.get('overall_score', 0):.1f}/10",
                data={"feedback": feedback}
            )
        except Exception as e:
            return WorkflowResult(success=False, message=f"读者模拟失败：{e}")

    # ======================================
    # 批量写作
    # ======================================

    def batch_write_chapters(
        self,
        chapter_numbers: list[int],
        word_target: int = 3000,
        auto_polish: bool = True,
        progress_callback: Callable = None,
        chapter_callback: Callable = None,
    ) -> WorkflowResult:
        """
        批量写作多个章节。按顺序逐章调用写作流水线。

        Args:
            chapter_numbers: 要写作的章节号列表（已排序）
            word_target: 每章目标字数
            auto_polish: 是否自动润色
            progress_callback: 整体进度回调 (message: str)
            chapter_callback: 单章完成回调 (chapter_number: int, result: WorkflowResult)

        Returns:
            WorkflowResult，data 包含 results 列表和统计
        """
        total = len(chapter_numbers)
        results = []

        for i, ch_num in enumerate(chapter_numbers, 1):
            if progress_callback:
                progress_callback(f"[{i}/{total}] 正在写作第{ch_num}章...")

            result = self.write_and_review_chapter(
                chapter_number=ch_num,
                word_target=word_target,
                auto_polish=auto_polish,
                progress_callback=progress_callback,
            )

            results.append({
                "chapter_number": ch_num,
                "success": result.success,
                "message": result.message,
                "word_count": result.data.get("word_count", 0) if result.success else 0,
                "score": result.data.get("overall_score", 0) if result.success else 0,
            })

            if chapter_callback:
                chapter_callback(ch_num, result)

        # 汇总统计
        success_count = sum(1 for r in results if r["success"])
        total_words = sum(r["word_count"] for r in results)
        avg_score = (sum(r["score"] for r in results if r["success"]) / success_count
                     if success_count else 0)

        return WorkflowResult(
            success=success_count > 0,
            message=f"批量写作完成：{success_count}/{total} 章成功，共 {total_words:,} 字",
            data={
                "results": results,
                "success_count": success_count,
                "fail_count": total - success_count,
                "total_words": total_words,
                "avg_score": avg_score,
            }
        )

    def clear_writing_style(self) -> WorkflowResult:
        """
        清除风格档案和参考文本
        """
        try:
            db = get_db()
            novel = db.query(Novel).filter(Novel.id == self.novel_id).first()
            if not novel:
                db.close()
                return WorkflowResult(success=False, message="小说项目不存在")
            novel.style_profile = None
            novel.style_reference_text = None
            db.commit()
            db.close()
            return WorkflowResult(success=True, message="风格档案已清除，后续润色将恢复默认风格")
        except Exception as e:
            return WorkflowResult(success=False, message=f"清除失败：{e}")

    # ======================================
    # 伏笔调度
    # ======================================

    def sync_outline_foreshadowings(self) -> WorkflowResult:
        """
        手动触发：将章纲 outline_foreshadowing_set 字符串同步到 Foreshadowing 表。
        只新增不存在的行，不覆盖已有记录。
        """
        try:
            created = self.memory.global_mem.sync_foreshadowings_from_outlines()
            return WorkflowResult(
                success=True,
                message=f"同步完成，新增 {created} 条伏笔记录" if created else "无需同步（无新伏笔）",
                data={"created": created}
            )
        except Exception as e:
            return WorkflowResult(success=False, message=f"同步失败：{e}")

    def get_foreshadowing_schedule_report(self, current_chapter: int) -> dict:
        """
        返回当前伏笔调度状态报告：过期/即将到期/正常/无截止。
        """
        gm = self.memory.global_mem
        overdue = gm.get_overdue_foreshadowings(current_chapter)
        due_soon = gm.get_due_soon_foreshadowings(current_chapter, window=10)
        due_soon_ids = {f.id for f in due_soon}
        overdue_ids = {f.id for f in overdue}

        all_active = gm.get_active_foreshadowings()
        on_track = [f for f in all_active if f.collect_by_chapter and f.id not in overdue_ids and f.id not in due_soon_ids]
        no_deadline = [f for f in all_active if not f.collect_by_chapter]

        def _serialize(fs_list):
            return [
                {
                    "id": f.id,
                    "name": f.name,
                    "set_chapter": f.set_chapter,
                    "collect_by_chapter": f.collect_by_chapter,
                    "importance": f.importance,
                    "description": f.description or "",
                }
                for f in fs_list
            ]

        return {
            "overdue": _serialize(overdue),
            "due_soon": _serialize(due_soon),
            "on_track": _serialize(on_track),
            "no_deadline": _serialize(no_deadline),
            "total_active": len(all_active),
        }

    def assign_foreshadowing_deadlines(self,
                                        progress_callback: Callable = None) -> WorkflowResult:
        """
        使用 LLM 批量为所有活跃且尚无截止章节的伏笔分配合理截止章节。
        """
        from core.models import Foreshadowing as ForeshadowingModel

        gm = self.memory.global_mem
        active_fs = [f for f in gm.get_active_foreshadowings() if not f.collect_by_chapter]
        if not active_fs:
            return WorkflowResult(success=True, message="所有活跃伏笔已设有截止章节，无需分配", data={"assignments": []})

        outline = gm.get_outline()
        total_chapters = outline.total_chapters if outline else 100
        story_structure = ""
        if outline and outline.story_structure:
            try:
                struct = json.loads(outline.story_structure)
                story_structure = "\n".join(f"{k}：{v}" for k, v in struct.items())
            except Exception:
                story_structure = outline.story_structure or ""

        if progress_callback:
            progress_callback(f"🤖 正在为 {len(active_fs)} 个伏笔分配截止章节...")

        fs_list_text = "\n".join(
            f"- id={f.id} 《{f.name}》（第{f.set_chapter}章埋下，{f.importance}重要度）：{f.description or '无描述'}"
            for f in active_fs
        )

        system_prompt = "你是一位小说结构分析师，擅长合理安排故事节奏和伏笔回收时机。输出合法JSON，不含其他文字。"
        user_prompt = f"""根据以下小说结构和伏笔信息，为每个伏笔分配合理的最晚回收章节（collect_by_chapter）。

小说共 {total_chapters} 章，故事结构：
{story_structure or '（未提供三幕结构）'}

待分配伏笔（共{len(active_fs)}条）：
{fs_list_text}

分配原则：
- 高重要度伏笔：最晚在全书后30%（第{int(total_chapters * 0.7)}章）前回收
- 中重要度伏笔：最晚在第二幕结束前（第{int(total_chapters * 0.8)}章）回收
- 低重要度伏笔：最晚在结局前5章（第{total_chapters - 5}章）前回收
- 埋下越早的伏笔，一般应越早回收
- 不要将截止章节设在伏笔埋下章节之前

输出JSON格式：
{{"assignments": [{{"id": 1, "collect_by_chapter": 45, "reason": "高重要度主线伏笔，应在高潮前揭示"}}, ...]}}"""

        try:
            from core.llm import NovelLLM
            llm = NovelLLM(self.model_outline)
            response = llm.generate(system_prompt, user_prompt, max_tokens=2048, cache_system=False)

            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start < 0:
                return WorkflowResult(success=False, message="LLM 返回格式异常，无法解析")

            try:
                result = json.loads(response[json_start:json_end])
            except json.JSONDecodeError:
                from json_repair import repair_json
                result = json.loads(repair_json(response[json_start:json_end]))
            assignments = result.get("assignments", [])

            # 批量更新（使用 gm.db，与 active_fs 对象所在的 session 一致）
            id_map = {f.id: f for f in active_fs}
            updated = 0
            for item in assignments:
                fs_id = item.get("id")
                collect_by = item.get("collect_by_chapter")
                if fs_id in id_map and collect_by:
                    id_map[fs_id].collect_by_chapter = int(collect_by)
                    updated += 1
            gm.db.commit()

            if progress_callback:
                progress_callback(f"✅ 已为 {updated} 个伏笔分配截止章节！")

            return WorkflowResult(
                success=True,
                message=f"已为 {updated} 个伏笔分配截止章节",
                data={"assignments": assignments}
            )

        except Exception as e:
            return WorkflowResult(success=False, message=f"AI分配失败：{e}")

    # ======================================
    # 状态报告
    # ======================================

    def get_status_report(self) -> dict:
        """生成当前小说完整状态报告"""
        return self.memory.get_status_report()

    def get_novel_info(self) -> Optional[Novel]:
        """获取小说基本信息"""
        return self.memory.global_mem.get_novel()

    # ======================================
    # 资源清理
    # ======================================

    def close(self):
        """清理所有资源"""
        self.memory.close()
        self.db.close()
        if self._outline_agent:
            self._outline_agent.close()
        if self._character_agent:
            self._character_agent.close()
        if self._writer_agent:
            self._writer_agent.close()
        if self._reviewer_agent:
            self._reviewer_agent.close()
        if self._polisher_agent:
            self._polisher_agent.close()
        if self._reader_agent:
            self._reader_agent.close()


# ======================================
# 便捷工厂函数
# ======================================

def create_new_novel(title: str, logline: str, genre: str = "",
                      writing_style: str = "",
                      llm_model: str = None) -> "NovelWorkflow":
    """
    创建新小说项目并返回工作流对象
    """
    novel_id = NovelWorkflow.create_novel(title, logline, genre, writing_style, llm_model)
    return NovelWorkflow(novel_id)


def load_novel(novel_id: int) -> "NovelWorkflow":
    """
    加载已有小说项目
    """
    return NovelWorkflow(novel_id)


def delete_novel(novel_id: int):
    """
    删除小说项目及所有关联数据
    - 手动删无 cascade 的 NovelOutline / Comment
    - ORM delete Novel 触发 SQLAlchemy cascade（chapters/characters/volumes/foreshadowings 等）
    - 清理 LanceDB 向量片段
    """
    from core.models import Comment
    from core.memory import _get_lancedb, TABLE_NAME

    db = get_db()
    try:
        db.query(NovelOutline).filter(NovelOutline.novel_id == novel_id).delete()
        db.query(Comment).filter(Comment.novel_id == novel_id).delete()
        novel = db.query(Novel).filter(Novel.id == novel_id).first()
        if novel:
            db.delete(novel)
        db.commit()
    finally:
        db.close()

    # 清理 LanceDB 向量
    try:
        ldb = _get_lancedb()
        if TABLE_NAME in ldb.table_names():
            table = ldb.open_table(TABLE_NAME)
            table.delete(f"novel_id = {novel_id}")
    except Exception:
        pass  # 向量清理失败不影响主流程


class NovelWorkflowImportMixin:
    """文档导入功能（混入 NovelWorkflow）"""

    def import_document_data(self, parsed: dict) -> WorkflowResult:
        """
        将 OutlineAgent.parse_document() 返回的结构化数据写入数据库

        Args:
            parsed: {total_outline, world_setting, characters, chapters}

        Returns:
            WorkflowResult with summary of what was written
        """
        summary = []
        mem = self.memory.global_mem

        # 整体大纲（只覆盖非空字段）
        total_outline = parsed.get("total_outline", {})
        if any(v for v in total_outline.values() if v):
            existing = mem.get_outline()
            outline_data = {}
            fields = ["premise", "theme", "main_conflict", "protagonist_arc",
                      "ending_summary", "story_structure"]
            for f in fields:
                val = total_outline.get(f)
                if val:
                    outline_data[f] = json.dumps(val, ensure_ascii=False) if isinstance(val, dict) else val
            if outline_data:
                if existing:
                    outline_data["novel_id"] = self.novel_id
                mem.save_outline(outline_data)
                summary.append(f"整体大纲：更新了 {len(outline_data)} 个字段")

        # 世界观设定（与已有内容合并，不覆盖已填字段）
        world_setting = parsed.get("world_setting", {})
        if world_setting:
            existing_ws = mem.get_world_setting() or {}
            merged = {**world_setting, **existing_ws}  # 已有的优先
            mem.save_world_setting(merged)
            new_keys = [k for k in world_setting if k not in existing_ws]
            summary.append(f"世界观：新增 {len(new_keys)} 个条目")

        # 人物档案（跳过已存在同名角色）
        characters = parsed.get("characters", [])
        added_chars = 0
        for char_data in characters:
            name = char_data.get("name", "").strip()
            if not name:
                continue
            existing_char = mem.get_character(name)
            if existing_char:
                continue  # 不覆盖已有角色
            # 过滤掉空字段
            clean = {k: v for k, v in char_data.items() if v and k != "novel_id"}
            clean["novel_id"] = self.novel_id
            mem.save_character(clean)
            added_chars += 1
        if added_chars:
            summary.append(f"人物：新增 {added_chars} 个")

        # 章节大纲（更新或新建）
        chapters = parsed.get("chapters", [])
        updated_chapters = 0
        for ch_data in chapters:
            ch_num = ch_data.get("chapter_number")
            if not ch_num:
                continue
            clean = {k: v for k, v in ch_data.items() if v and k != "novel_id"}
            clean["novel_id"] = self.novel_id
            # list 类型字段转 JSON string
            for list_field in ["outline_characters", "outline_foreshadowing_set", "outline_foreshadowing_collect"]:
                if isinstance(clean.get(list_field), list):
                    clean[list_field] = json.dumps(clean[list_field], ensure_ascii=False)
            existing_ch = mem.get_chapter_outline(ch_num)
            if not existing_ch:
                clean.setdefault("status", "outlined")
            mem.save_chapter_outline(clean)
            updated_chapters += 1
        if updated_chapters:
            summary.append(f"章节大纲：写入 {updated_chapters} 章")

        if not summary:
            return WorkflowResult(success=False, message="未从文档中提取到任何有效信息")
        return WorkflowResult(success=True, message="，".join(summary))


# 将 import_document_data 混入 NovelWorkflow
NovelWorkflow.import_document_data = NovelWorkflowImportMixin.import_document_data
