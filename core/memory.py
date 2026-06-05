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

from core.config import LANCEDB_DIR, RECENT_CHAPTERS_COUNT, VECTOR_TOP_K, CHUNK_SIZE, EMBEDDING_MODEL
from core.models import (
    get_db, Novel, Character, Volume, Chapter, Foreshadowing,
    TimelineEvent, NovelOutline
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
        _embedding_model = get_registry().get("sentence-transformers").create(
            name=EMBEDDING_MODEL, device="cpu"
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
        vector: Vector(model.ndims()) = model.VectorField()
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
        """保存世界观设定"""
        novel = self.get_novel()
        if novel:
            novel.set_world_setting(setting)
            self.db.commit()

    def get_all_characters(self) -> list[Character]:
        """获取所有人物档案"""
        return self.db.query(Character).filter(
            Character.novel_id == self.novel_id
        ).order_by(Character.is_main.desc(), Character.id).all()

    def get_character(self, name: str) -> Optional[Character]:
        """按名称查找人物"""
        return self.db.query(Character).filter(
            Character.novel_id == self.novel_id,
            Character.name == name
        ).first()

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
            char = Character(novel_id=self.novel_id, **{k: v for k, v in data.items() if k != "novel_id"})
            self.db.add(char)
            self.db.commit()
            self.db.refresh(char)
            return char

    def get_outline(self) -> Optional[NovelOutline]:
        """获取总大纲"""
        return self.db.query(NovelOutline).filter(
            NovelOutline.novel_id == self.novel_id
        ).first()

    def save_outline(self, data: dict) -> NovelOutline:
        """保存总大纲"""
        existing = self.get_outline()
        if existing:
            for k, v in data.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
            self.db.commit()
            return existing
        else:
            outline = NovelOutline(novel_id=self.novel_id, **{k: v for k, v in data.items() if k != "novel_id"})
            self.db.add(outline)
            self.db.commit()
            self.db.refresh(outline)
            return outline

    def get_volumes(self) -> list[Volume]:
        """获取所有卷大纲"""
        return self.db.query(Volume).filter(
            Volume.novel_id == self.novel_id
        ).order_by(Volume.volume_number).all()

    def save_volume(self, data: dict) -> Volume:
        """保存卷大纲"""
        existing = self.db.query(Volume).filter(
            Volume.novel_id == self.novel_id,
            Volume.volume_number == data.get("volume_number")
        ).first()
        if existing:
            for k, v in data.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
            self.db.commit()
            return existing
        else:
            vol = Volume(novel_id=self.novel_id, **{k: v for k, v in data.items() if k != "novel_id"})
            self.db.add(vol)
            self.db.commit()
            self.db.refresh(vol)
            return vol

    def get_chapter_outlines(self) -> list[Chapter]:
        """获取所有章纲"""
        return self.db.query(Chapter).filter(
            Chapter.novel_id == self.novel_id
        ).order_by(Chapter.chapter_number).all()

    def get_chapter_outline(self, chapter_number: int) -> Optional[Chapter]:
        """获取指定章纲"""
        return self.db.query(Chapter).filter(
            Chapter.novel_id == self.novel_id,
            Chapter.chapter_number == chapter_number
        ).first()

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
            chap = Chapter(novel_id=self.novel_id, **{k: v for k, v in data.items() if k != "novel_id"})
            self.db.add(chap)
            self.db.commit()
            self.db.refresh(chap)
            return chap

    def get_active_foreshadowings(self) -> list[Foreshadowing]:
        """获取所有未回收的伏笔"""
        return self.db.query(Foreshadowing).filter(
            Foreshadowing.novel_id == self.novel_id,
            Foreshadowing.status == "active"
        ).order_by(Foreshadowing.importance.desc()).all()

    def get_all_foreshadowings(self) -> list[Foreshadowing]:
        """获取所有伏笔"""
        return self.db.query(Foreshadowing).filter(
            Foreshadowing.novel_id == self.novel_id
        ).all()

    def save_foreshadowing(self, data: dict) -> Foreshadowing:
        """保存伏笔"""
        f = Foreshadowing(novel_id=self.novel_id, **{k: v for k, v in data.items() if k != "novel_id"})
        self.db.add(f)
        self.db.commit()
        self.db.refresh(f)
        return f

    def collect_foreshadowing(self, foreshadowing_id: int, chapter_number: int, content: str):
        """标记伏笔为已回收"""
        f = self.db.query(Foreshadowing).filter(Foreshadowing.id == foreshadowing_id).first()
        if f:
            f.status = "collected"
            f.collect_chapter = chapter_number
            f.collect_content = content
            self.db.commit()

    def get_overdue_foreshadowings(self, current_chapter: int) -> list[Foreshadowing]:
        """获取已过截止章节但尚未回收的伏笔"""
        return self.db.query(Foreshadowing).filter(
            Foreshadowing.novel_id == self.novel_id,
            Foreshadowing.status == "active",
            Foreshadowing.collect_by_chapter.isnot(None),
            Foreshadowing.collect_by_chapter < current_chapter
        ).order_by(Foreshadowing.collect_by_chapter).all()

    def get_due_soon_foreshadowings(self, current_chapter: int, window: int = 10) -> list[Foreshadowing]:
        """获取即将到期（截止章节在未来 window 章内）的伏笔"""
        return self.db.query(Foreshadowing).filter(
            Foreshadowing.novel_id == self.novel_id,
            Foreshadowing.status == "active",
            Foreshadowing.collect_by_chapter.isnot(None),
            Foreshadowing.collect_by_chapter >= current_chapter,
            Foreshadowing.collect_by_chapter <= current_chapter + window
        ).order_by(Foreshadowing.collect_by_chapter).all()

    def sync_foreshadowings_from_outlines(self) -> int:
        """
        将章纲中 outline_foreshadowing_set 字符串同步到 Foreshadowing 表。
        只创建不存在的记录（按名称去重）。返回新增数量。
        """
        chapters = self.db.query(Chapter).filter(
            Chapter.novel_id == self.novel_id
        ).order_by(Chapter.chapter_number).all()

        existing_names = {
            f.name for f in self.db.query(Foreshadowing).filter(
                Foreshadowing.novel_id == self.novel_id
            ).all()
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
                        status="active"
                    )
                    self.db.add(new_fs)
                    existing_names.add(name.strip())
                    created += 1
        if created:
            self.db.commit()
        return created

    def get_timeline(self) -> list[TimelineEvent]:
        """获取时间线"""
        return self.db.query(TimelineEvent).filter(
            TimelineEvent.novel_id == self.novel_id
        ).order_by(TimelineEvent.chapter_number).all()

    def add_timeline_event(self, data: dict) -> TimelineEvent:
        """添加时间线事件"""
        event = TimelineEvent(novel_id=self.novel_id, **{k: v for k, v in data.items() if k != "novel_id"})
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def build_global_context(self, include_chapters: bool = False) -> str:
        """
        构建全局上下文字符串，注入到 Agent 提示词中
        包含：世界观设定、主要人物、总大纲概要、未回收伏笔
        """
        parts = []
        novel = self.get_novel()
        if not novel:
            return ""

        # 小说基本信息
        parts.append(f"=== 小说基本信息 ===\n标题：{novel.title}\n题材：{novel.genre or '未设定'}")
        if novel.logline:
            parts.append(f"简介：{novel.logline}")

        # 世界观设定
        world = self.get_world_setting()
        if world:
            parts.append("\n=== 世界观设定 ===")
            for k, v in world.items():
                parts.append(f"{k}：{v}")

        # 总大纲概要
        outline = self.get_outline()
        if outline:
            parts.append("\n=== 总大纲 ===")
            if outline.theme: parts.append(f"核心主题：{outline.theme}")
            if outline.main_conflict: parts.append(f"主要矛盾：{outline.main_conflict}")
            if outline.protagonist_arc: parts.append(f"主角弧光：{outline.protagonist_arc}")
            if outline.ending_summary: parts.append(f"结局概要：{outline.ending_summary}")

        # 主要人物档案
        chars = self.get_all_characters()
        if chars:
            parts.append("\n=== 人物档案 ===")
            for char in chars:
                parts.append(char.to_profile_text())
                parts.append("")  # 空行分隔

        # 未回收伏笔
        foreshadowings = self.get_active_foreshadowings()
        if foreshadowings:
            parts.append("\n=== 待回收伏笔 ===")
            for f in foreshadowings:
                deadline_str = f"  ⚠️ 最晚第{f.collect_by_chapter}章回收" if f.collect_by_chapter else ""
                parts.append(f"[{f.importance}] 第{f.set_chapter}章埋下《{f.name}》：{f.description}{deadline_str}")

        # 可选：章节大纲列表
        if include_chapters:
            chapters = self.get_chapter_outlines()
            if chapters:
                parts.append("\n=== 章节大纲总览 ===")
                for ch in chapters:
                    parts.append(ch.to_outline_text())

        return "\n".join(parts)

    def close(self):
        self.db.close()


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

    def get_recent_chapters(self, before_chapter: int, count: int = RECENT_CHAPTERS_COUNT) -> list[Chapter]:
        """获取指定章节之前的最近N章"""
        return self.db.query(Chapter).filter(
            Chapter.novel_id == self.novel_id,
            Chapter.chapter_number < before_chapter,
            Chapter.content.isnot(None)
        ).order_by(Chapter.chapter_number.desc()).limit(count).all()[::-1]

    def get_chapter(self, chapter_number: int) -> Optional[Chapter]:
        """获取指定章节"""
        return self.db.query(Chapter).filter(
            Chapter.novel_id == self.novel_id,
            Chapter.chapter_number == chapter_number
        ).first()

    def save_chapter_content(self, chapter_number: int, content: str,
                              content_type: str = "content"):
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

    def save_chapter_summary(self, chapter_number: int, summary: str,
                               key_events: list[str]):
        """保存章节摘要和关键事件"""
        chapter = self.get_chapter(chapter_number)
        if chapter:
            chapter.summary = summary
            chapter.key_events = json.dumps(key_events, ensure_ascii=False)
            self.db.commit()

    def build_recent_context(self, current_chapter: int) -> str:
        """
        构建最近N章的上下文字符串
        包含完整内容 + 摘要，注入到写手Agent提示词
        """
        recent = self.get_recent_chapters(current_chapter)
        if not recent:
            return "（这是第一章，没有前情）"

        parts = [f"=== 最近{len(recent)}章内容（前情提要）==="]
        for ch in recent:
            parts.append(f"\n--- 第{ch.chapter_number}章《{ch.title or ''}》---")
            if ch.summary:
                parts.append(f"[摘要] {ch.summary}")
            if ch.content:
                # 取正文前1500字作为上下文（节省token）
                preview = ch.content[:1500]
                if len(ch.content) > 1500:
                    preview += "...（正文已截取）"
                parts.append(preview)

        return "\n".join(parts)

    def save_version(self, chapter_id: int, content: str,
                      version_type: str, change_summary: str = ""):
        """保存版本历史"""
        from core.models import ContentVersion
        from core.config import MAX_VERSIONS

        from sqlalchemy import func

        # 获取当前版本数量（用于判断是否需要淘汰旧版本）
        version_count = self.db.query(ContentVersion).filter(
            ContentVersion.chapter_id == chapter_id
        ).count()

        # 如果超出最大版本数，删除最旧的版本
        if version_count >= MAX_VERSIONS:
            oldest = self.db.query(ContentVersion).filter(
                ContentVersion.chapter_id == chapter_id
            ).order_by(ContentVersion.version_number).first()
            if oldest:
                self.db.delete(oldest)
                self.db.flush()

        # 用 MAX+1 而非 COUNT+1，避免删除旧版本后版本号与已有记录重复
        max_num = self.db.query(func.max(ContentVersion.version_number)).filter(
            ContentVersion.chapter_id == chapter_id
        ).scalar() or 0

        version = ContentVersion(
            chapter_id=chapter_id,
            version_number=max_num + 1,
            content=content,
            version_type=version_type,
            change_summary=change_summary
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

    def search_relevant(self, query: str, n_results: int = VECTOR_TOP_K,
                         exclude_chapter: Optional[int] = None) -> list[dict]:
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
                fragments.append({
                    "content": row["text"],
                    "chapter_number": row["chapter_number"],
                    "title": row.get("title", ""),
                    "relevance": relevance,
                })

            return fragments
        except Exception as e:
            print(f"⚠️ 向量检索出错：{e}")
            return []

    def build_relevant_context(self, query: str, current_chapter: int) -> str:
        """
        构建相关历史片段的上下文字符串
        """
        fragments = self.search_relevant(query, exclude_chapter=current_chapter)
        if not fragments:
            return ""

        parts = ["=== 相关历史细节（语义检索）==="]
        for f in fragments[:5]:  # 最多展示5个片段
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

    def search_cross_novel(self, query: str, n_results: int = VECTOR_TOP_K,
                            exclude_novel_id: Optional[int] = None) -> list[dict]:
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

    def build_writing_context(self, chapter_number: int,
                               chapter_outline: "Chapter") -> str:
        """
        为写手Agent构建完整的写作上下文
        整合三层记忆：全局设定 + 最近章节 + 相关细节
        """
        parts = []

        # Layer 1: 全局记忆
        global_ctx = self.global_mem.build_global_context()
        if global_ctx:
            parts.append(global_ctx)

        # Layer 2: 章节记忆（最近N章）
        recent_ctx = self.chapter_mem.build_recent_context(chapter_number)
        if recent_ctx:
            parts.append(recent_ctx)

        # Layer 3: 碎片化记忆（语义检索）
        search_query = (
            f"{chapter_outline.outline_core_event or ''} "
            f"{', '.join(chapter_outline.get_outline_characters())} "
            f"{chapter_outline.outline_scene or ''}"
        )
        fragment_ctx = self.fragment_mem.build_relevant_context(
            search_query, chapter_number
        )
        if fragment_ctx:
            parts.append(fragment_ctx)

        # 当前章纲
        parts.append(f"\n=== 本章章纲 ===\n{chapter_outline.to_outline_text()}")

        return "\n\n".join(parts)

    def build_review_context(self, chapter: "Chapter",
                              content: str) -> str:
        """
        为审核师Agent构建完整的审核上下文
        """
        parts = []

        # 全局设定（审核必须参照完整设定）
        global_ctx = self.global_mem.build_global_context()
        if global_ctx:
            parts.append(global_ctx)

        # 前情章节摘要
        recent = self.chapter_mem.get_recent_chapters(chapter.chapter_number)
        if recent:
            parts.append("=== 前情摘要 ===")
            for ch in recent[-3:]:  # 最近3章的摘要
                if ch.summary:
                    parts.append(f"第{ch.chapter_number}章《{ch.title}》：{ch.summary}")

        # 当前章纲
        parts.append(f"\n=== 本章应有内容（章纲）===\n{chapter.to_outline_text()}")

        # 待审核的正文
        parts.append(f"\n=== 待审核正文 ===\n{content}")

        return "\n\n".join(parts)

    def save_new_chapter(self, chapter_number: int, content: str,
                          content_type: str = "content"):
        """保存新章节内容并同步到向量数据库"""
        # 保存到SQLite
        chapter = self.chapter_mem.save_chapter_content(
            chapter_number, content, content_type
        )

        # 同步到ChromaDB（仅保存正式内容，草稿不索引）
        if content_type == "content":
            self.fragment_mem.add_chapter(
                chapter_number,
                chapter.title or "",
                content
            )

        return chapter

    def get_status_report(self) -> dict:
        """生成当前小说状态报告"""
        novel = self.global_mem.get_novel()
        if not novel:
            return {"error": "小说不存在"}

        chars = self.global_mem.get_all_characters()
        chapters = self.chapter_mem.db.query(Chapter).filter(
            Chapter.novel_id == self.novel_id
        ).order_by(Chapter.chapter_number).all()
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
                "current_state": char.current_state or "未更新"
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
                {"name": f.name, "set_chapter": f.set_chapter, "importance": f.importance}
                for f in foreshadowings_active
            ],
            "total_foreshadowings": len(foreshadowings_all),
            "collected_foreshadowings": len([f for f in foreshadowings_all if f.status == "collected"]),
            "next_chapters": [
                ch.to_outline_text() for ch in chapters
                if ch.status in ("outline_pending", "outlined")
            ][:3]
        }

    def close(self):
        """关闭所有数据库连接"""
        self.global_mem.close()
        self.chapter_mem.close()
