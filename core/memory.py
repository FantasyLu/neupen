"""
三层结构化记忆体系

Layer 1 - 全局记忆（永久存储，SQLite）
    世界观设定、所有人物档案、总大纲、卷大纲、章大纲、伏笔库、全局时间线

Layer 2 - 章节记忆（中期存储，SQLite）
    每章的完整内容、核心事件摘要、出场人物列表、本章埋下和回收的伏笔
    生成新章节时，自动检索最近N章的完整内容

Layer 3 - 碎片化记忆（向量存储，LanceDB + Qwen3-Embedding）
    所有历史内容分块向量化存储（单表多小说，支持跨小说检索）
    生成内容时，根据当前章纲的主题，自动检索相关的历史细节
    Lance 格式原生支持版本快照和 time-travel
"""

import json
from typing import Optional

import lancedb
from lancedb.pydantic import LanceModel, Vector
from lancedb.embeddings import get_registry

from core.config import (
    LANCEDB_DIR,
    RECENT_CHAPTERS_COUNT,
    VECTOR_TOP_K,
    CHUNK_SIZE,
    EMBEDDING_MODEL,
)
from core.models import (
    get_db,
    Novel,
    Character,
    Volume,
    Chapter,
    Foreshadowing,
    TimelineEvent,
    NovelOutline,
)


# ======================================
# LanceDB 初始化
# ======================================

_embedding_model = None
_lancedb_conn = None

TABLE_NAME = "chapter_chunks"


def _get_embedding_model():
    """获取 Embedding 模型（单例缓存，首次调用时下载模型）"""
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = (
            get_registry()
            .get("sentence-transformers")
            .create(name=EMBEDDING_MODEL, device="cpu")
        )
    return _embedding_model


def _get_lancedb():
    """获取 LanceDB 连接（单例缓存）"""
    global _lancedb_conn
    if _lancedb_conn is None:
        _lancedb_conn = lancedb.connect(str(LANCEDB_DIR))
    return _lancedb_conn


def _get_chunk_schema():
    """构建 ChapterChunk schema（需要在 embedding 模型加载后调用）"""
    model = _get_embedding_model()

    class ChapterChunk(LanceModel):
        text: str = model.SourceField()
        vector: Vector(model.ndims()) = model.VectorField()  # type: ignore[valid-type]
        novel_id: int
        chapter_number: int
        title: str
        chunk_index: int

    return ChapterChunk


def _get_chunks_table():
    """获取或创建全局 chapter_chunks 表"""
    db = _get_lancedb()
    if TABLE_NAME in db.table_names():
        return db.open_table(TABLE_NAME)
    schema = _get_chunk_schema()
    return db.create_table(TABLE_NAME, schema=schema)


# ======================================
# Layer 1: 全局记忆操作
# ======================================


class GlobalMemory:
    """
    全局记忆管理器
    负责读写世界观设定、人物档案、大纲、伏笔库等永久性数据
    """

    def __init__(self, novel_id: int):
        self.novel_id = novel_id
        self.db = get_db()

    def get_novel(self) -> Optional[Novel]:
        """获取小说基础信息"""
        return self.db.query(Novel).filter(Novel.id == self.novel_id).first()

    def get_world_setting(self) -> dict:
        """获取世界观设定"""
        novel = self.get_novel()
        if novel:
            return novel.get_world_setting()
        return {}

    def save_world_setting(self, setting: dict):
        """保存世界观设定，清空旧压缩缓存（压缩在 build_global_context 惰性触发）"""
        novel = self.get_novel()
        if novel:
            novel.set_world_setting(setting)
            # 内容变更，清空旧压缩缓存；下次 build_global_context 时惰性重建
            novel.world_setting_compressed = None
            self.db.commit()

    def get_all_characters(self) -> list[Character]:
        """获取所有人物档案"""
        return (
            self.db.query(Character)
            .filter(Character.novel_id == self.novel_id)
            .order_by(Character.is_main.desc(), Character.id)
            .all()
        )

    def get_character(self, name: str) -> Optional[Character]:
        """按名称查找人物"""
        return (
            self.db.query(Character)
            .filter(Character.novel_id == self.novel_id, Character.name == name)
            .first()
        )

    def save_character(self, data: dict) -> Character:
        """保存或更新人物档案"""
        existing = self.get_character(data.get("name", ""))
        if existing:
            for k, v in data.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
            self.db.commit()
            return existing
        else:
            char = Character(
                novel_id=self.novel_id,
                **{k: v for k, v in data.items() if k != "novel_id"},
            )
            self.db.add(char)
            self.db.commit()
            self.db.refresh(char)
            return char

    def delete_character(self, name: str) -> bool:
        """按名称删除人物档案。返回是否成功删除。"""
        existing = self.get_character(name)
        if existing:
            self.db.delete(existing)
            self.db.commit()
            return True
        return False

    def get_outline(self) -> Optional[NovelOutline]:
        """获取总大纲"""
        return (
            self.db.query(NovelOutline)
            .filter(NovelOutline.novel_id == self.novel_id)
            .first()
        )

    def save_outline(self, data: dict) -> NovelOutline:
        """保存总大纲，清空旧压缩缓存（压缩在 build_global_context 惰性触发）"""
        # 将 dict/list 字段序列化为 JSON 字符串，避免 SQLite 类型错误
        serialized = {
            k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
            for k, v in data.items()
        }
        existing = self.get_outline()
        if existing:
            for k, v in serialized.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
            # 清空旧压缩缓存
            for f in (
                "theme_compressed",
                "main_conflict_compressed",
                "protagonist_arc_compressed",
                "ending_summary_compressed",
            ):
                setattr(existing, f, None)
            self.db.commit()
            outline = existing
        else:
            outline = NovelOutline(
                novel_id=self.novel_id,
                **{k: v for k, v in serialized.items() if k != "novel_id"},
            )
            self.db.add(outline)
            self.db.commit()
            self.db.refresh(outline)
        return outline

    def get_volumes(self) -> list[Volume]:
        """获取所有卷大纲"""
        return (
            self.db.query(Volume)
            .filter(Volume.novel_id == self.novel_id)
            .order_by(Volume.volume_number)
            .all()
        )

    def save_volume(self, data: dict) -> Volume:
        """保存卷大纲"""
        existing = (
            self.db.query(Volume)
            .filter(
                Volume.novel_id == self.novel_id,
                Volume.volume_number == data.get("volume_number"),
            )
            .first()
        )
        if existing:
            for k, v in data.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
            self.db.commit()
            return existing
        else:
            vol = Volume(
                novel_id=self.novel_id,
                **{k: v for k, v in data.items() if k != "novel_id"},
            )
            self.db.add(vol)
            self.db.commit()
            self.db.refresh(vol)
            return vol

    def get_chapter_outlines(self) -> list[Chapter]:
        """获取所有章纲"""
        return (
            self.db.query(Chapter)
            .filter(Chapter.novel_id == self.novel_id)
            .order_by(Chapter.chapter_number)
            .all()
        )

    def get_chapter_outline(self, chapter_number: int) -> Optional[Chapter]:
        """获取指定章纲"""
        return (
            self.db.query(Chapter)
            .filter(
                Chapter.novel_id == self.novel_id,
                Chapter.chapter_number == chapter_number,
            )
            .first()
        )

    def save_chapter_outline(self, data: dict) -> Chapter:
        """保存章纲"""
        existing = self.get_chapter_outline(data.get("chapter_number", -1))
        if existing:
            for k, v in data.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
            self.db.commit()
            return existing
        else:
            clean = {
                k: v for k, v in data.items() if k != "novel_id" and hasattr(Chapter, k)
            }
            chap = Chapter(novel_id=self.novel_id, **clean)
            self.db.add(chap)
            self.db.commit()
            self.db.refresh(chap)
            return chap

    def get_active_foreshadowings(self) -> list[Foreshadowing]:
        """获取所有未回收的伏笔"""
        return (
            self.db.query(Foreshadowing)
            .filter(
                Foreshadowing.novel_id == self.novel_id,
                Foreshadowing.status == "active",
            )
            .order_by(Foreshadowing.importance.desc())
            .all()
        )

    def get_all_foreshadowings(self) -> list[Foreshadowing]:
        """获取所有伏笔"""
        return (
            self.db.query(Foreshadowing)
            .filter(Foreshadowing.novel_id == self.novel_id)
            .all()
        )

    def save_foreshadowing(self, data: dict) -> Foreshadowing:
        """保存伏笔"""
        f = Foreshadowing(
            novel_id=self.novel_id, **{k: v for k, v in data.items() if k != "novel_id"}
        )
        self.db.add(f)
        self.db.commit()
        self.db.refresh(f)
        return f

    def collect_foreshadowing(
        self, foreshadowing_id: int, chapter_number: int, content: str
    ):
        """标记伏笔为已回收"""
        f = (
            self.db.query(Foreshadowing)
            .filter(Foreshadowing.id == foreshadowing_id)
            .first()
        )
        if f:
            f.status = "collected"
            f.collect_chapter = chapter_number
            f.collect_content = content
            self.db.commit()

    def get_overdue_foreshadowings(self, current_chapter: int) -> list[Foreshadowing]:
        """获取已过截止章节但尚未回收的伏笔"""
        return (
            self.db.query(Foreshadowing)
            .filter(
                Foreshadowing.novel_id == self.novel_id,
                Foreshadowing.status == "active",
                Foreshadowing.collect_by_chapter.isnot(None),
                Foreshadowing.collect_by_chapter < current_chapter,
            )
            .order_by(Foreshadowing.collect_by_chapter)
            .all()
        )

    def get_due_soon_foreshadowings(
        self, current_chapter: int, window: int = 10
    ) -> list[Foreshadowing]:
        """获取即将到期（截止章节在未来 window 章内）的伏笔"""
        return (
            self.db.query(Foreshadowing)
            .filter(
                Foreshadowing.novel_id == self.novel_id,
                Foreshadowing.status == "active",
                Foreshadowing.collect_by_chapter.isnot(None),
                Foreshadowing.collect_by_chapter >= current_chapter,
                Foreshadowing.collect_by_chapter <= current_chapter + window,
            )
            .order_by(Foreshadowing.collect_by_chapter)
            .all()
        )

    def sync_foreshadowings_from_outlines(self) -> int:
        """
        将章纲中 outline_foreshadowing_set 字符串同步到 Foreshadowing 表。
        只创建不存在的记录（按名称去重）。返回新增数量。
        """
        chapters = (
            self.db.query(Chapter)
            .filter(Chapter.novel_id == self.novel_id)
            .order_by(Chapter.chapter_number)
            .all()
        )

        existing_names = {
            f.name
            for f in self.db.query(Foreshadowing)
            .filter(Foreshadowing.novel_id == self.novel_id)
            .all()
        }
        created = 0
        for ch in chapters:
            if not ch.outline_foreshadowing_set:
                continue
            try:
                names = json.loads(ch.outline_foreshadowing_set)
            except Exception:
                continue
            for name in names:
                if name and name.strip() and name.strip() not in existing_names:
                    new_fs = Foreshadowing(
                        novel_id=self.novel_id,
                        name=name.strip(),
                        set_chapter=ch.chapter_number,
                        importance="medium",
                        status="active",
                    )
                    self.db.add(new_fs)
                    existing_names.add(name.strip())
                    created += 1
        if created:
            self.db.commit()
        return created

    def get_timeline(self) -> list[TimelineEvent]:
        """获取时间线"""
        return (
            self.db.query(TimelineEvent)
            .filter(TimelineEvent.novel_id == self.novel_id)
            .order_by(TimelineEvent.chapter_number)
            .all()
        )

    def add_timeline_event(self, data: dict) -> TimelineEvent:
        """添加时间线事件"""
        event = TimelineEvent(
            novel_id=self.novel_id, **{k: v for k, v in data.items() if k != "novel_id"}
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def build_global_context(
        self,
        include_chapters: bool = False,
        active_chars: list[str] | None = None,
        chapter_keywords: set[str] | None = None,
        current_chapter: int | None = None,
    ) -> str:
        """
        构建全局上下文字符串，注入到 Agent 提示词中
        包含：世界观设定、主要人物、总大纲概要、未回收伏笔

        Args:
            include_chapters: 是否附加章节大纲列表
            active_chars: 本章出场人物姓名列表。
                若提供，出场人物调用 to_chapter_relevant_profile() 按相关性过滤字段，
                其余人物只给单行简介；
                若为 None（默认），保持原有全量注入行为（to_profile_text）。
            chapter_keywords: 本章关键词集合（来自章纲字段）。
                若提供，世界观条目只注入「key 或 value 中包含任一关键词」的条目，
                同时作为人物档案字段和伏笔相关性过滤依据；
                若为 None（默认），保持原有全量注入行为。
            current_chapter: 当前章节号。
                若提供，用于判断伏笔是否即将到期（到期迫近的伏笔强制完整注入）；
                若为 None，退化为仅按关键词过滤。
        """
        parts = []
        novel = self.get_novel()
        if not novel:
            return ""

        # 小说基本信息
        parts.append(
            f"=== 小说基本信息 ===\n标题：{novel.title}\n题材：{novel.genre or '未设定'}"
        )
        if novel.logline:
            parts.append(f"简介：{novel.logline}")

        # 世界观设定：优先使用压缩版；未压缩时惰性触发同步压缩并缓存
        world_raw = self.get_world_setting()
        if world_raw:
            novel_obj = novel  # 复用已查询的 novel 对象，避免多次 DB 查询
            if novel_obj and novel_obj.world_setting_compressed:
                try:
                    world = json.loads(novel_obj.world_setting_compressed)
                except Exception:
                    world = world_raw
            else:
                # 压缩缓存缺失，惰性同步压缩后写回 DB
                world = (
                    _compress_world_setting_sync(
                        novel_obj=novel_obj,
                        world_raw=world_raw,
                        db=self.db,
                    )
                    if novel_obj
                    else world_raw
                )

            if chapter_keywords:
                matched = {
                    k: v
                    for k, v in world.items()
                    if any(kw in k or kw in str(v) for kw in chapter_keywords)
                }
                skipped = len(world) - len(matched)
                if matched:
                    parts.append("\n=== 世界观设定（与本章相关）===")
                    for k, v in matched.items():
                        parts.append(f"{k}：{v}")
                    if skipped > 0:
                        parts.append(f"（另有 {skipped} 条与本章无关的设定已省略）")
            else:
                parts.append("\n=== 世界观设定 ===")
                for k, v in world.items():
                    parts.append(f"{k}：{v}")

        # 总大纲概要：优先使用各字段的压缩版；未压缩时惰性触发同步压缩并缓存
        outline = self.get_outline()
        if outline:
            # 若任意字段压缩版缺失，惰性触发同步压缩
            needs_compress = any(
                getattr(outline, f"{f}_compressed", None) is None
                for f in ("theme", "main_conflict", "protagonist_arc", "ending_summary")
                if getattr(outline, f, None)
            )
            if needs_compress:
                _compress_outline_sync(outline=outline, db=self.db, novel=novel)

            parts.append("\n=== 总大纲 ===")

            # 对每个字段：有压缩版用压缩版，否则用原文
            def _outline_val(field: str) -> str:
                compressed = getattr(outline, f"{field}_compressed", None)
                original = getattr(outline, field, None) or ""
                return (compressed or original).strip()

            theme_val = _outline_val("theme")
            conflict_val = _outline_val("main_conflict")
            arc_val = _outline_val("protagonist_arc")
            ending_val = _outline_val("ending_summary")
            if theme_val:
                parts.append(f"核心主题：{theme_val}")
            if conflict_val:
                parts.append(f"主要矛盾：{conflict_val}")
            if arc_val:
                parts.append(f"主角弧光：{arc_val}")
            if ending_val:
                parts.append(f"结局概要：{ending_val}")

        # 人物档案：按出场过滤
        chars = self.get_all_characters()
        if chars:
            if active_chars is not None:
                # 规范化出场人物集合
                active_set = {n.strip() for n in active_chars if n.strip()}
                appearing = [c for c in chars if c.name in active_set]
                others = [c for c in chars if c.name not in active_set]

                if appearing:
                    parts.append("\n=== 本章出场人物档案 ===")
                    # 同台人物名集合，用于关系字段的相关性判断
                    co_chars = {c.name for c in appearing}
                    for char in appearing:
                        if chapter_keywords:
                            # 按本章关键词做字段级相关性过滤（不截断，只取相关字段）
                            parts.append(
                                char.to_chapter_relevant_profile(
                                    chapter_keywords=chapter_keywords,
                                    co_appearing_chars=co_chars,
                                )
                            )
                        else:
                            # 无关键词时退化为全字段输出
                            parts.append(char.to_profile_text())
                        parts.append("")
                if others:
                    parts.append("=== 其他人物（本章未出场，仅供参考）===")
                    parts.append("  ".join(char.to_brief_text() for char in others))
            else:
                # 默认行为：全量注入（无章节上下文时保持原行为）
                parts.append("\n=== 人物档案 ===")
                for char in chars:
                    parts.append(char.to_profile_text())
                    parts.append("")

        # 未回收伏笔：按相关性分三级注入
        foreshadowings = self.get_active_foreshadowings()
        if foreshadowings:
            if chapter_keywords:
                # ── 有章节上下文时做相关性过滤 ──────────────────────
                urgent = []  # 级别1：到期迫近（≤5章），强制完整注入
                relevant = []  # 级别2：关键词命中，完整注入
                others_fs = []  # 级别3：其余，仅单行汇总

                _URGENT_WINDOW = 5
                for f in foreshadowings:
                    # 判断是否到期迫近
                    is_urgent = (
                        current_chapter is not None
                        and f.collect_by_chapter is not None
                        and f.collect_by_chapter <= current_chapter + _URGENT_WINDOW
                    )
                    if is_urgent:
                        urgent.append(f)
                        continue

                    # 关键词命中：伏笔名 / 描述 / notes 中有章节关键词
                    searchable = " ".join(
                        filter(None, [f.name, f.description, f.notes])
                    )
                    if any(kw in searchable for kw in chapter_keywords):
                        relevant.append(f)
                    else:
                        others_fs.append(f)

                lines = []
                if urgent:
                    lines.append("【即将到期，必须处理】")
                    for f in urgent:
                        lines.append(f.to_full_text())
                if relevant:
                    lines.append("【与本章相关】")
                    for f in relevant:
                        lines.append(f.to_full_text())
                if others_fs:
                    brief_list = "、".join(f.to_brief_text() for f in others_fs)
                    lines.append(f"【其余 {len(others_fs)} 条未列出】{brief_list}")

                if lines:
                    parts.append("\n=== 待回收伏笔 ===\n" + "\n".join(lines))
            else:
                # ── 无章节关键词：仅按到期迫近过滤，其余给单行汇总 ──────
                # 避免在没有章节上下文时把全部伏笔塞进 prompt
                _URGENT_WINDOW = 5
                urgent_fs = []
                summary_fs = []
                for f in foreshadowings:
                    is_urgent = (
                        current_chapter is not None
                        and f.collect_by_chapter is not None
                        and f.collect_by_chapter <= current_chapter + _URGENT_WINDOW
                    )
                    if is_urgent:
                        urgent_fs.append(f)
                    else:
                        summary_fs.append(f)

                lines = []
                if urgent_fs:
                    lines.append("【即将到期，必须处理】")
                    for f in urgent_fs:
                        lines.append(f.to_full_text())
                if summary_fs:
                    brief_list = "、".join(f.to_brief_text() for f in summary_fs)
                    lines.append(f"【其余 {len(summary_fs)} 条待回收伏笔】{brief_list}")
                if lines:
                    parts.append("\n=== 待回收伏笔 ===\n" + "\n".join(lines))

        # 可选：章节大纲列表
        if include_chapters:
            chapters = self.get_chapter_outlines()
            if chapters:
                parts.append("\n=== 章节大纲总览 ===")
                for ch in chapters:
                    parts.append(ch.to_outline_text())

        return "\n".join(parts)

    def search_characters_by_trait(self, trait: str) -> list[Character]:
        """
        按特征关键词模糊搜索人物档案。
        在 name / aliases / role / personality / background 字段中匹配关键词。
        支持空格分隔的多关键词（OR 逻辑）。
        供 Agentic 工具调用使用。
        """
        import re as _re

        keywords = [k.strip() for k in _re.split(r"[\s，,]+", trait) if k.strip()]
        if not keywords:
            return []

        all_chars = self.get_all_characters()
        results = []
        for char in all_chars:
            searchable = " ".join(
                filter(
                    None,
                    [
                        char.name or "",
                        char.aliases or "",
                        char.role or "",
                        char.personality or "",
                        char.background or "",
                        char.current_state or "",
                        char.motivations or "",
                    ],
                )
            )
            if any(kw in searchable for kw in keywords):
                results.append(char)
        return results

    def search_foreshadowings_by_keyword(self, keyword: str) -> list[Foreshadowing]:
        """
        在伏笔的 name / description / notes 字段中搜索关键词。
        返回所有状态的伏笔（active / collected / abandoned）。
        支持空格分隔的多关键词（OR 逻辑）。
        供 Agentic 工具调用使用。
        """
        import re as _re

        keywords = [k.strip() for k in _re.split(r"[\s，,]+", keyword) if k.strip()]
        if not keywords:
            return self.get_all_foreshadowings()

        all_fs = self.get_all_foreshadowings()
        results = []
        for f in all_fs:
            searchable = " ".join(
                filter(
                    None,
                    [
                        f.name or "",
                        f.description or "",
                        f.notes or "",
                        f.set_content or "",
                    ],
                )
            )
            if any(kw in searchable for kw in keywords):
                results.append(f)
        return results

    def query_world_setting_section(self, section: str) -> dict:
        """
        从世界观 JSON 中提取与 section 关键词匹配的条目。
        支持模糊匹配（key 中包含关键词）。
        若无匹配则返回完整世界观（兜底）。
        供 Agentic 工具调用使用。
        """
        world = self.get_world_setting()
        if not world:
            return {}
        if not section or section.strip() == "":
            return world

        keywords = [
            k.strip().lower()
            for k in section.replace("，", ",").split(",")
            if k.strip()
        ]
        matched = {
            k: v
            for k, v in world.items()
            if any(kw in k.lower() or kw in str(v).lower() for kw in keywords)
        }
        # 若无精确匹配，降级返回完整世界观
        return matched if matched else world

    def search_timeline_by_keyword(self, keyword: str) -> list[TimelineEvent]:
        """
        在时间线事件的 event_name / event_description / characters_involved 中搜索关键词。
        按章节号升序返回。
        供 Agentic 工具调用使用。
        """
        import re as _re

        keywords = [k.strip() for k in _re.split(r"[\s，,]+", keyword) if k.strip()]
        all_events = self.get_timeline()
        if not keywords:
            return all_events

        results = []
        for ev in all_events:
            searchable = " ".join(
                filter(
                    None,
                    [
                        ev.event_name or "",
                        ev.event_description or "",
                        ev.characters_involved or "",
                        ev.impact or "",
                    ],
                )
            )
            if any(kw in searchable for kw in keywords):
                results.append(ev)
        return results

    def close(self):
        self.db.close()


# ── 惰性同步压缩辅助函数（模块级，供 build_global_context 调用）─────────────────


def _compress_world_setting_sync(novel_obj, world_raw: dict, db) -> dict:
    """
    同步压缩世界观设定，将结果写回 DB 并返回压缩后的 dict。
    任何异常均打印警告后回退原文，不抛出。
    """
    import sys

    try:
        from core.config import COMPRESS_WORLD_THRESHOLD, COMPRESS_WORLD_TARGET_MAX
        from core.agents import FieldCompressor

        needs_compress = any(
            len(str(v)) > COMPRESS_WORLD_THRESHOLD for v in world_raw.values()
        )
        if not needs_compress:
            # 所有条目都在阈值内，直接把原文当作压缩版缓存
            novel_obj.world_setting_compressed = novel_obj.world_setting
            db.commit()
            return world_raw

        compressor = FieldCompressor(
            novel_id=novel_obj.id, model_id=novel_obj.llm_model or None
        )
        compressed = compressor.compress_world_setting(
            world=world_raw,
            threshold=COMPRESS_WORLD_THRESHOLD,
            target_max=COMPRESS_WORLD_TARGET_MAX,
        )
        novel_obj.world_setting_compressed = json.dumps(compressed, ensure_ascii=False)
        db.commit()
        return compressed
    except Exception as e:
        print(f"[压缩警告] 世界观压缩失败，回退原文：{e}", file=sys.stderr)
        return world_raw


def _compress_outline_sync(outline, db, novel) -> None:
    """
    同步压缩大纲字段，将结果写回 outline.*_compressed 并 commit。
    任何异常均打印警告，不抛出。
    """
    import sys

    try:
        from core.config import COMPRESS_OUTLINE_THRESHOLD, COMPRESS_OUTLINE_TARGETS
        from core.agents import FieldCompressor

        model_id = novel.llm_model if novel else None
        compressor = FieldCompressor(novel_id=outline.novel_id, model_id=model_id)
        compressed = compressor.compress_outline_fields(
            outline=outline,
            field_targets=COMPRESS_OUTLINE_TARGETS,
            threshold=COMPRESS_OUTLINE_THRESHOLD,
        )
        for field, value in compressed.items():
            setattr(outline, f"{field}_compressed", value)
        # 对没有超过阈值、未被压缩器写入的字段，把原文当压缩版存入
        for field in COMPRESS_OUTLINE_TARGETS:
            if getattr(outline, f"{field}_compressed", None) is None:
                orig = getattr(outline, field, None)
                if orig:
                    setattr(outline, f"{field}_compressed", orig)
        db.commit()
    except Exception as e:
        print(f"[压缩警告] 大纲压缩失败，回退原文：{e}", file=sys.stderr)


# ======================================
# Layer 2: 章节记忆操作
# ======================================


class ChapterMemory:
    """
    章节记忆管理器
    负责读写章节内容、摘要、事件等
    """

    def __init__(self, novel_id: int):
        self.novel_id = novel_id
        self.db = get_db()

    def get_recent_chapters(
        self, before_chapter: int, count: int = RECENT_CHAPTERS_COUNT
    ) -> list[Chapter]:
        """获取指定章节之前的最近N章"""
        return (
            self.db.query(Chapter)
            .filter(
                Chapter.novel_id == self.novel_id,
                Chapter.chapter_number < before_chapter,
                Chapter.content.isnot(None),
            )
            .order_by(Chapter.chapter_number.desc())
            .limit(count)
            .all()[::-1]
        )

    def get_chapter(self, chapter_number: int) -> Optional[Chapter]:
        """获取指定章节"""
        return (
            self.db.query(Chapter)
            .filter(
                Chapter.novel_id == self.novel_id,
                Chapter.chapter_number == chapter_number,
            )
            .first()
        )

    def get_chapters_by_range(self, start: int, end: int) -> list[Chapter]:
        """
        获取指定章节范围内的所有章节（含 start 和 end）。
        按章节序号升序排列，只返回已有正文或摘要的章节。
        """
        return (
            self.db.query(Chapter)
            .filter(
                Chapter.novel_id == self.novel_id,
                Chapter.chapter_number >= start,
                Chapter.chapter_number <= end,
            )
            .order_by(Chapter.chapter_number)
            .all()
        )

    def save_chapter_content(
        self, chapter_number: int, content: str, content_type: str = "content"
    ):
        """保存章节内容，自动更新字数统计"""
        chapter = self.get_chapter(chapter_number)
        if not chapter:
            raise ValueError(f"章节 {chapter_number} 不存在，请先创建章纲")

        # 字数统计
        word_count = len(content)

        if content_type == "draft":
            chapter.content_draft = content
        else:
            chapter.content = content
            chapter.word_count = word_count

        self.db.commit()
        return chapter

    def save_chapter_summary(
        self, chapter_number: int, summary: str, key_events: list[str]
    ):
        """保存章节摘要和关键事件"""
        chapter = self.get_chapter(chapter_number)
        if chapter:
            chapter.summary = summary
            chapter.key_events = json.dumps(key_events, ensure_ascii=False)
            self.db.commit()

    def build_recent_context(self, current_chapter: int, adaptive: bool = False) -> str:
        """
        构建最近N章的上下文摘要，注入到写手Agent提示词。

        只使用每章的详细摘要和关键事件列表，不再注入原始正文。
        摘要需要在章节保存时由 WriterAgent.summarize_chapter() 生成。
        如果某章缺失摘要，会使用正文前300字作为临时标记并提示用户回填。

        Args:
            adaptive: 是否启用章数自适应。启用时前几章少注入，
                避免把有限的上下文窗口浪费在不存在的历史章节上。
                第1章=0章，第2-3章=1章，第4-6章=3章，第7章起=全量(RECENT_CHAPTERS_COUNT)。
        """
        if adaptive:
            if current_chapter <= 1:
                count = 0
            elif current_chapter <= 3:
                count = 1
            elif current_chapter <= 6:
                count = 3
            else:
                count = RECENT_CHAPTERS_COUNT
        else:
            count = RECENT_CHAPTERS_COUNT

        if count == 0:
            return "（这是第一章，没有前情）"

        recent = self.get_recent_chapters(current_chapter, count=count)
        if not recent:
            return "（这是第一章，没有前情）"

        parts = [f"=== 前{len(recent)}章情节摘要（写手请仔细阅读，确保剧情连贯）==="]
        missing_summary = False
        # 单章摘要字数上限：避免某章摘要过长撑爆 token 窗口
        _SUMMARY_MAX_CHARS = 400
        for ch in recent:
            parts.append(f"\n--- 第{ch.chapter_number}章《{ch.title or ''}》---")
            if ch.summary:
                summary_text = ch.summary
                if len(summary_text) > _SUMMARY_MAX_CHARS:
                    summary_text = summary_text[:_SUMMARY_MAX_CHARS] + "…（摘要已截断）"
                parts.append(f"📖 {summary_text}")
                if ch.key_events:
                    try:
                        events = json.loads(ch.key_events)
                        if events:
                            parts.append(f"🔑 关键事件：")
                            for ev in events:
                                parts.append(f"  • {ev}")
                    except (json.JSONDecodeError, TypeError):
                        pass
            elif ch.content:
                # 无摘要时用正文前300字作为临时标记
                preview = ch.content[:300]
                if len(ch.content) > 300:
                    preview += "..."
                parts.append(f"⚠️ [本章缺失详细摘要，以下为正文片段仅供参考] {preview}")
                missing_summary = True

        if missing_summary:
            parts.append(
                "\n⚠️ 以上标记的章节缺少详细摘要。"
                "建议在写作前先为这些章节生成摘要，以获得更好的上下文质量。"
            )
        return "\n".join(parts)

    def save_version(
        self, chapter_id: int, content: str, version_type: str, change_summary: str = ""
    ):
        """保存版本历史"""
        from core.models import ContentVersion
        from core.config import MAX_VERSIONS

        from sqlalchemy import func

        # 获取当前版本数量（用于判断是否需要淘汰旧版本）
        version_count = (
            self.db.query(ContentVersion)
            .filter(ContentVersion.chapter_id == chapter_id)
            .count()
        )

        # 如果超出最大版本数，删除最旧的版本
        if version_count >= MAX_VERSIONS:
            oldest = (
                self.db.query(ContentVersion)
                .filter(ContentVersion.chapter_id == chapter_id)
                .order_by(ContentVersion.version_number)
                .first()
            )
            if oldest:
                self.db.delete(oldest)
                self.db.flush()

        # 用 MAX+1 而非 COUNT+1，避免删除旧版本后版本号与已有记录重复
        max_num = (
            self.db.query(func.max(ContentVersion.version_number))
            .filter(ContentVersion.chapter_id == chapter_id)
            .scalar()
            or 0
        )

        version = ContentVersion(
            chapter_id=chapter_id,
            version_number=max_num + 1,
            content=content,
            version_type=version_type,
            change_summary=change_summary,
        )
        self.db.add(version)
        self.db.commit()

    def close(self):
        self.db.close()


# ======================================
# Layer 3: 碎片化向量记忆操作
# ======================================


class FragmentMemory:
    """
    碎片化向量记忆管理器
    将历史内容分块向量化，支持语义相似度检索
    使用 LanceDB 存储，单表多小说，支持版本快照和跨小说检索
    """

    def __init__(self, novel_id: int):
        self.novel_id = novel_id
        self._table = None

    @property
    def table(self):
        """懒加载 LanceDB 表"""
        if self._table is None:
            self._table = _get_chunks_table()
        return self._table

    def _chunk_text(self, text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
        """将文本分块（按段落优先，不够再按字符数）"""
        if not text:
            return []

        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        chunks = []
        current_chunk = []
        current_size = 0

        for para in paragraphs:
            if current_size + len(para) > chunk_size and current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = [para]
                current_size = len(para)
            else:
                current_chunk.append(para)
                current_size += len(para)

        if current_chunk:
            chunks.append("\n".join(current_chunk))

        return chunks

    def add_chapter(self, chapter_number: int, title: str, content: str):
        """将章节内容分块向量化存储"""
        chunks = self._chunk_text(content)
        if not chunks:
            return

        # 先删除该章的旧内容（如果存在）
        try:
            self.table.delete(
                f"novel_id = {self.novel_id} AND chapter_number = {chapter_number}"
            )
        except Exception:
            pass

        # 构建数据行（LanceDB 自动调用 embedding 模型向量化 text 字段）
        rows = [
            {
                "text": chunk,
                "novel_id": self.novel_id,
                "chapter_number": chapter_number,
                "title": title or "",
                "chunk_index": i,
            }
            for i, chunk in enumerate(chunks)
        ]

        self.table.add(rows)

    def search_relevant(
        self,
        query: str,
        n_results: int = VECTOR_TOP_K,
        exclude_chapter: Optional[int] = None,
    ) -> list[dict]:
        """
        根据查询语义检索最相关的历史内容片段
        返回按相关度排序的片段列表
        """
        try:
            where_clause = f"novel_id = {self.novel_id}"
            if exclude_chapter is not None:
                where_clause += f" AND chapter_number != {exclude_chapter}"

            results = (
                self.table.search(query)
                .metric("cosine")
                .where(where_clause)
                .limit(n_results)
                .to_list()
            )

            fragments = []
            for row in results:
                # cosine distance 范围 [0, 2]，转换为相关度 [0, 1]
                cosine_dist = row.get("_distance", 0)
                relevance = 1 - cosine_dist / 2
                fragments.append(
                    {
                        "content": row["text"],
                        "chapter_number": row["chapter_number"],
                        "title": row.get("title", ""),
                        "relevance": relevance,
                    }
                )

            return fragments
        except Exception as e:
            print(f"⚠️ 向量检索出错：{e}")
            return []

    def build_relevant_context(
        self,
        query: str,
        current_chapter: int,
        n_results: int = 5,
        min_relevance: float = 0.0,
    ) -> str:
        """
        构建相关历史片段的上下文字符串

        Args:
            n_results: 最多检索并展示的片段数（默认5，写作场景建议传3）
            min_relevance: 相关度门槛 [0, 1]，低于此值的片段丢弃（默认0不过滤）
        """
        fragments = self.search_relevant(
            query, n_results=n_results, exclude_chapter=current_chapter
        )
        if not fragments:
            return ""

        # 过滤低相关度片段
        if min_relevance > 0:
            fragments = [f for f in fragments if f["relevance"] >= min_relevance]
        if not fragments:
            return ""

        parts = ["=== 相关历史细节（语义检索）==="]
        for f in fragments:
            parts.append(
                f"\n[第{f['chapter_number']}章《{f['title']}》| 相关度{f['relevance']:.2f}]\n{f['content']}"
            )

        return "\n".join(parts)

    def delete_chapter(self, chapter_number: int):
        """删除章节的向量数据"""
        try:
            self.table.delete(
                f"novel_id = {self.novel_id} AND chapter_number = {chapter_number}"
            )
        except Exception as e:
            print(f"⚠️ 删除向量数据出错：{e}")

    # ── 跨小说检索（新增） ──────────────────────────────────────

    def search_cross_novel(
        self,
        query: str,
        n_results: int = VECTOR_TOP_K,
        exclude_novel_id: Optional[int] = None,
    ) -> list[dict]:
        """
        跨小说语义检索——在其他作品中查找相关片段，用于找灵感或复用素材
        """
        try:
            search = self.table.search(query).metric("cosine")
            if exclude_novel_id is not None:
                search = search.where(f"novel_id != {exclude_novel_id}")
            results = search.limit(n_results).to_list()

            return [
                {
                    "content": row["text"],
                    "novel_id": row["novel_id"],
                    "chapter_number": row["chapter_number"],
                    "title": row.get("title", ""),
                    "relevance": 1 - row.get("_distance", 0) / 2,
                }
                for row in results
            ]
        except Exception as e:
            print(f"⚠️ 跨小说检索出错：{e}")
            return []

    # ── 版本管理（新增） ────────────────────────────────────────

    def list_versions(self) -> list:
        """列出所有向量数据版本快照"""
        try:
            return self.table.list_versions()
        except Exception:
            return []

    def checkout_version(self, version: int):
        """切换到指定版本（time-travel 模式，只读）"""
        self.table.checkout(version)

    def checkout_latest(self):
        """回到最新版本"""
        self.table.checkout_latest()

    def restore_version(self, version: int):
        """将指定版本恢复为新的最新版本（非破坏性，创建新版本）"""
        self.table.restore(version)


# ======================================
# 统一记忆接口
# ======================================


def _extract_chapter_keywords(chapter_outline: "Chapter") -> set[str]:
    """
    从章纲各字段提取关键词集合，用于世界观的相关性过滤。
    提取字段：核心事件、主要冲突、场景、情感基调、出场人物、伏笔名。
    返回长度 >= 2 的词语集合（单字词噪音大，跳过）。
    """
    import json as _json

    tokens: set[str] = set()

    text_fields = [
        chapter_outline.outline_core_event or "",
        chapter_outline.outline_conflict or "",
        chapter_outline.outline_scene or "",
        chapter_outline.outline_emotion or "",
    ]
    for field in text_fields:
        # 按标点和空格切分，保留长度>=2的词
        import re as _re

        for tok in _re.split(
            "[，。！？、；：\u201c\u201d\u2018\u2019【】《》 \t\n\r]+", field
        ):
            tok = tok.strip()
            if len(tok) >= 2:
                tokens.add(tok)

    # 出场人物名直接加入
    try:
        chars = _json.loads(chapter_outline.outline_characters or "[]")
        for name in chars:
            if name and len(name.strip()) >= 1:
                tokens.add(name.strip())
    except Exception:
        pass

    # 伏笔名
    for fs_field in [
        chapter_outline.outline_foreshadowing_set or "",
        chapter_outline.outline_foreshadowing_collect or "",
    ]:
        try:
            names = _json.loads(fs_field)
            for n in names:
                if n and len(n.strip()) >= 2:
                    tokens.add(n.strip())
        except Exception:
            pass

    return tokens


class MemoryManager:
    """
    统一记忆接口
    整合三层记忆系统，为 Agent 提供统一的记忆存取接口
    """

    def __init__(self, novel_id: int):
        self.novel_id = novel_id
        self.global_mem = GlobalMemory(novel_id)
        self.chapter_mem = ChapterMemory(novel_id)
        self.fragment_mem = FragmentMemory(novel_id)

    def build_writing_context(
        self, chapter_number: int, chapter_outline: "Chapter"
    ) -> str:
        """
        为写手Agent构建完整的写作上下文
        整合三层记忆：全局设定 + 最近章节 + 相关细节

        优化策略：
        - 人物档案按本章出场人物过滤，非出场人物只给单行简介
          ⚠️ active_chars = 本章章纲出场人物 ∪ 上一章章纲出场人物
          （上一章人物可能因连续场景仍在本章，避免被降级为简介）
        - 世界观按章节关键词过滤，只注入相关条目
        - L3 向量检索上限收紧到 3 片段，且设相关度门槛 0.5
        - 摘要注入章数根据当前章节自适应（前期少、后期多）
        """
        parts = []

        # 提取本章出场人物（章纲已有）
        current_chars = set(chapter_outline.get_outline_characters())

        # 并入上一章出场人物（连续场景中上章人物可能仍在场）
        if chapter_number > 1:
            prev = self.chapter_mem.get_chapter(chapter_number - 1)
            if prev:
                prev_chars = prev.get_outline_characters()
                current_chars.update(prev_chars)

        active_chars = list(current_chars)

        # 构建章节关键词集（来自章纲各字段）用于世界观过滤
        chapter_keywords = _extract_chapter_keywords(chapter_outline)

        # Layer 1: 全局记忆（按出场人物过滤档案，按关键词过滤世界观和伏笔）
        global_ctx = self.global_mem.build_global_context(
            active_chars=active_chars or None,
            chapter_keywords=chapter_keywords or None,
            current_chapter=chapter_number,
        )
        if global_ctx:
            parts.append(global_ctx)

        # Layer 2: 章节记忆（自适应章数）
        recent_ctx = self.chapter_mem.build_recent_context(
            chapter_number, adaptive=True
        )
        if recent_ctx:
            parts.append(recent_ctx)

        # Layer 3: 碎片化记忆（上限3片段，相关度门槛0.5）
        # 核心事件重复一次，提升其在向量相似度计算中的权重；
        # 人物和场景作为补充维度，帮助检索出角色/场景延续性细节。
        _core_event = chapter_outline.outline_core_event or ""
        _chars_str = ", ".join(active_chars)
        _scene = chapter_outline.outline_scene or ""
        search_query = f"{_core_event} {_core_event} {_chars_str} {_scene}".strip()
        fragment_ctx = self.fragment_mem.build_relevant_context(
            search_query, chapter_number, n_results=3, min_relevance=0.5
        )
        if fragment_ctx:
            parts.append(fragment_ctx)

        # 当前章纲
        parts.append(f"\n=== 本章章纲 ===\n{chapter_outline.to_outline_text()}")

        return "\n\n".join(parts)

    def build_review_context(self, chapter: "Chapter", content: str) -> str:
        """
        为审核师Agent构建完整的审核上下文。
        - 世界观：按本章章纲关键词过滤，减少无关设定噪音
        - 人物档案：出场人物给完整档案，其余只给单行简介
        - 正文：截断至 6000 字
        """
        chapter_keywords = _extract_chapter_keywords(chapter)

        # 从章纲提取出场人物
        import json as _json

        try:
            active_chars = _json.loads(chapter.outline_characters or "[]")
        except Exception:
            active_chars = None

        parts = []

        # 全局设定（按关键词过滤世界观；人物按出场过滤；伏笔按相关性过滤）
        global_ctx = self.global_mem.build_global_context(
            active_chars=active_chars if active_chars else None,
            chapter_keywords=chapter_keywords if chapter_keywords else None,
            current_chapter=chapter.chapter_number,
        )
        if global_ctx:
            parts.append(global_ctx)

        # 前情章节摘要（最近3章）
        recent = self.chapter_mem.get_recent_chapters(chapter.chapter_number)
        if recent:
            parts.append("=== 前情摘要 ===")
            for ch in recent[-3:]:
                if ch.summary:
                    parts.append(f"第{ch.chapter_number}章《{ch.title}》：{ch.summary}")

        # 当前章纲
        parts.append(f"\n=== 本章应有内容（章纲）===\n{chapter.to_outline_text()}")

        # 待审核的正文（截断至6000字）
        content_truncated = content[:6000]
        content_note = "...(内容过长已截断)" if len(content) > 6000 else ""
        parts.append(f"\n=== 待审核正文 ===\n{content_truncated}{content_note}")

        return "\n\n".join(parts)

    def save_new_chapter(
        self, chapter_number: int, content: str, content_type: str = "content"
    ):
        """保存新章节内容并同步到向量数据库"""
        # 保存到SQLite
        chapter = self.chapter_mem.save_chapter_content(
            chapter_number, content, content_type
        )

        # 同步到ChromaDB（仅保存正式内容，草稿不索引）
        if content_type == "content":
            self.fragment_mem.add_chapter(chapter_number, chapter.title or "", content)

        return chapter

    def get_status_report(self) -> dict:
        """生成当前小说状态报告"""
        novel = self.global_mem.get_novel()
        if not novel:
            return {"error": "小说不存在"}

        chars = self.global_mem.get_all_characters()
        chapters = (
            self.chapter_mem.db.query(Chapter)
            .filter(Chapter.novel_id == self.novel_id)
            .order_by(Chapter.chapter_number)
            .all()
        )
        foreshadowings_active = self.global_mem.get_active_foreshadowings()
        foreshadowings_all = self.global_mem.get_all_foreshadowings()

        # 统计各状态章节
        chapter_stats = {}
        for ch in chapters:
            chapter_stats[ch.status] = chapter_stats.get(ch.status, 0) + 1

        # 获取最新章节状态
        char_states = []
        for char in chars[:5]:  # 主要人物
            state = {
                "name": char.name,
                "role": char.role,
                "current_state": char.current_state or "未更新",
            }
            char_states.append(state)

        return {
            "novel_title": novel.title,
            "total_words": sum(ch.word_count or 0 for ch in chapters),
            "total_chapters": len(chapters),
            "completed_chapters": chapter_stats.get("published", 0),
            "chapter_stats": chapter_stats,
            "main_characters_states": char_states,
            "active_foreshadowings": [
                {
                    "name": f.name,
                    "set_chapter": f.set_chapter,
                    "importance": f.importance,
                }
                for f in foreshadowings_active
            ],
            "total_foreshadowings": len(foreshadowings_all),
            "collected_foreshadowings": len(
                [f for f in foreshadowings_all if f.status == "collected"]
            ),
            "next_chapters": [
                ch.to_outline_text()
                for ch in chapters
                if ch.status in ("outline_pending", "outlined")
            ][:3],
        }

    def close(self):
        """关闭所有数据库连接"""
        self.global_mem.close()
        self.chapter_mem.close()
