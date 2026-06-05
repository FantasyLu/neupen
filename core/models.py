"""
数据库模型模块
使用 SQLAlchemy 定义所有 SQLite 数据模型
存储小说的结构化数据：项目、人物、大纲、章节、伏笔、历史版本
"""

import json
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime,
    ForeignKey, Boolean, Float, UniqueConstraint
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

from core.config import DB_PATH

# ======================================
# 数据库初始化
# ======================================
DATABASE_URL = f"sqlite:///{DB_PATH}"
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """获取数据库会话（上下文管理器）"""
    db = SessionLocal()
    try:
        return db
    except Exception:
        db.close()
        raise


# ======================================
# 数据模型定义
# ======================================

class Novel(Base):
    """小说项目表"""
    __tablename__ = "novels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False, comment="小说标题")
    logline = Column(Text, comment="一句话灵感/简介")
    genre = Column(String(50), comment="题材类型（玄幻/都市/言情等）")
    world_setting = Column(Text, comment="世界观设定（JSON格式）")
    writing_style = Column(Text, comment="写作风格要求")
    target_words = Column(Integer, default=0, comment="目标总字数")
    current_words = Column(Integer, default=0, comment="已写字数")
    status = Column(String(20), default="planning", comment="状态：planning/outlining/writing/completed")
    llm_model = Column(String(100), nullable=True, comment="本项目使用的 LLM 模型 ID（覆盖全局默认）")
    model_outline   = Column(String(100), nullable=True, comment="大纲师模型（为空跟随 llm_model）")
    model_character = Column(String(100), nullable=True, comment="人设师模型（为空跟随 llm_model）")
    model_writer    = Column(String(100), nullable=True, comment="写手部模型（为空跟随 llm_model）")
    model_reviewer  = Column(String(100), nullable=True, comment="审核师模型（为空跟随 llm_model）")
    model_polisher  = Column(String(100), nullable=True, comment="润色师模型（为空跟随 llm_model）")
    model_reader    = Column(String(100), nullable=True, comment="读者模拟模型（为空跟随 llm_model）")
    invite_code = Column(String(20), nullable=True, unique=True, comment="邀请码（协作者通过此码加入项目）")
    style_profile = Column(Text, nullable=True, comment="风格档案（JSON格式，10个维度）")
    style_reference_text = Column(Text, nullable=True, comment="上传的参考文本节选（前3000字）")
    quality_config = Column(Text, nullable=True, comment="写作质量参数（JSON格式）")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关联关系
    characters = relationship("Character", back_populates="novel", cascade="all, delete-orphan")
    volumes = relationship("Volume", back_populates="novel", cascade="all, delete-orphan")
    chapters = relationship("Chapter", back_populates="novel", cascade="all, delete-orphan")
    foreshadowings = relationship("Foreshadowing", back_populates="novel", cascade="all, delete-orphan")
    timeline_events = relationship("TimelineEvent", back_populates="novel", cascade="all, delete-orphan")
    collaborators = relationship("Collaborator", back_populates="novel", cascade="all, delete-orphan")
    documents = relationship("NovelDocument", back_populates="novel", cascade="all, delete-orphan")

    def get_world_setting(self) -> dict:
        """获取世界观设定（反序列化）"""
        try:
            return json.loads(self.world_setting) if self.world_setting else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_world_setting(self, data: dict):
        """设置世界观设定（序列化）"""
        self.world_setting = json.dumps(data, ensure_ascii=False)

    def get_style_profile(self) -> dict:
        """获取风格档案（反序列化）"""
        try:
            return json.loads(self.style_profile) if self.style_profile else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_style_profile(self, data: dict):
        """设置风格档案（序列化）"""
        self.style_profile = json.dumps(data, ensure_ascii=False)

    def get_quality_config(self) -> dict:
        """获取写作质量参数（反序列化）"""
        try:
            return json.loads(self.quality_config) if self.quality_config else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_quality_config(self, data: dict):
        """设置写作质量参数（序列化）"""
        self.quality_config = json.dumps(data, ensure_ascii=False)

    def get_agent_model(self, agent_key: str) -> Optional[str]:
        """
        获取指定 Agent 的有效模型 ID。
        回退链：per-agent 字段 → llm_model（项目默认）→ None（由 NovelLLM 使用全局 .env 默认）
        agent_key: "outline" | "character" | "writer" | "reviewer" | "polisher"
        """
        return getattr(self, f"model_{agent_key}", None) or self.llm_model or None

    def __repr__(self):
        return f"<Novel id={self.id} title={self.title}>"


class Character(Base):
    """人物档案表"""
    __tablename__ = "characters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id"), nullable=False)
    name = Column(String(100), nullable=False, comment="姓名")
    aliases = Column(Text, comment="别名/绰号（JSON数组）")
    role = Column(String(50), comment="角色定位（主角/配角/反派等）")

    # 基本信息
    age = Column(String(50), comment="年龄（可以是范围）")
    gender = Column(String(20), comment="性别")
    appearance = Column(Text, comment="外貌描述")

    # 深度设定
    personality = Column(Text, comment="性格特征")
    background = Column(Text, comment="背景故事")
    abilities = Column(Text, comment="能力/技能（JSON格式）")
    relationships = Column(Text, comment="人际关系（JSON格式）")
    growth_arc = Column(Text, comment="成长弧光")
    current_state = Column(Text, comment="当前状态（随剧情更新）")
    motivations = Column(Text, comment="动机/目标")
    secrets = Column(Text, comment="秘密/隐藏信息")

    # 写作参考
    speech_patterns = Column(Text, comment="说话风格/口头禅")
    behavioral_patterns = Column(Text, comment="行为习惯")

    is_main = Column(Boolean, default=False, comment="是否主要人物")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关联
    novel = relationship("Novel", back_populates="characters")

    def get_abilities(self) -> list:
        try:
            return json.loads(self.abilities) if self.abilities else []
        except (json.JSONDecodeError, TypeError):
            return []

    def get_relationships(self) -> dict:
        try:
            return json.loads(self.relationships) if self.relationships else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def to_profile_text(self) -> str:
        """转为人设文字摘要（注入提示词用）"""
        parts = [f"【{self.name}】({self.role})"]
        if self.age: parts.append(f"年龄：{self.age}")
        if self.personality: parts.append(f"性格：{self.personality}")
        if self.background: parts.append(f"背景：{self.background}")
        if self.current_state: parts.append(f"当前状态：{self.current_state}")
        if self.motivations: parts.append(f"动机：{self.motivations}")
        if self.speech_patterns: parts.append(f"说话风格：{self.speech_patterns}")
        return "\n".join(parts)

    def __repr__(self):
        return f"<Character id={self.id} name={self.name}>"


class Volume(Base):
    """卷大纲表"""
    __tablename__ = "volumes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id"), nullable=False)
    volume_number = Column(Integer, nullable=False, comment="卷序号")
    title = Column(String(200), nullable=False, comment="卷标题")
    summary = Column(Text, comment="卷简介")
    main_conflict = Column(Text, comment="主要矛盾")
    arc_goal = Column(Text, comment="本卷目标/主题")
    start_chapter = Column(Integer, comment="开始章节号")
    end_chapter = Column(Integer, comment="结束章节号")
    created_at = Column(DateTime, default=datetime.utcnow)

    # 关联
    novel = relationship("Novel", back_populates="volumes")
    chapters = relationship("Chapter", back_populates="volume")

    __table_args__ = (UniqueConstraint("novel_id", "volume_number"),)

    def __repr__(self):
        return f"<Volume id={self.id} title={self.title}>"


class Chapter(Base):
    """章节大纲和正文表"""
    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id"), nullable=False)
    volume_id = Column(Integer, ForeignKey("volumes.id"), nullable=True)
    chapter_number = Column(Integer, nullable=False, comment="章节序号")
    title = Column(String(200), comment="章节标题")

    # 章纲（结构化）
    outline_core_event = Column(Text, comment="核心事件")
    outline_conflict = Column(Text, comment="主要冲突")
    outline_characters = Column(Text, comment="出场人物（JSON数组）")
    outline_scene = Column(Text, comment="场景设定")
    outline_foreshadowing_set = Column(Text, comment="本章埋下的伏笔（JSON数组）")
    outline_foreshadowing_collect = Column(Text, comment="本章回收的伏笔（JSON数组）")
    outline_emotion = Column(Text, comment="情感基调")
    outline_ending = Column(Text, comment="章节结尾方式")

    # 正文
    content = Column(Text, comment="章节正文（已审核润色版）")
    content_draft = Column(Text, comment="章节草稿（初步生成版）")
    word_count = Column(Integer, default=0, comment="字数统计")

    # 摘要（用于后续章节的上下文注入）
    summary = Column(Text, comment="章节内容摘要")
    key_events = Column(Text, comment="关键事件（JSON数组）")

    # 状态
    status = Column(
        String(30), default="outline_pending",
        comment="状态：outline_pending/outlined/writing/review_pending/reviewed/polished/published"
    )
    review_report = Column(Text, comment="审核报告（JSON格式）")
    review_score = Column(Float, default=0.0, comment="审核评分(0-10)")
    reader_feedback = Column(Text, comment="读者模拟反馈（JSON格式，含三种读者类型的评分和评语）")
    reader_score = Column(Float, default=0.0, comment="读者模拟综合评分(0-10)")
    approval_status = Column(String(20), default="pending", comment="审阅状态：pending/approved/needs_revision/rejected")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关联
    novel = relationship("Novel", back_populates="chapters")
    volume = relationship("Volume", back_populates="chapters")
    versions = relationship("ContentVersion", back_populates="chapter", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("novel_id", "chapter_number"),)

    def get_outline_characters(self) -> list:
        try:
            return json.loads(self.outline_characters) if self.outline_characters else []
        except (json.JSONDecodeError, TypeError):
            return []

    def get_review_report(self) -> dict:
        try:
            return json.loads(self.review_report) if self.review_report else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def to_outline_text(self) -> str:
        """转为章纲文字摘要"""
        parts = [f"第{self.chapter_number}章《{self.title or '未命名'}》"]
        if self.outline_core_event: parts.append(f"核心事件：{self.outline_core_event}")
        if self.outline_conflict: parts.append(f"主要冲突：{self.outline_conflict}")
        if self.outline_characters: parts.append(f"出场人物：{', '.join(self.get_outline_characters())}")
        if self.outline_scene: parts.append(f"场景：{self.outline_scene}")
        if self.outline_foreshadowing_set:
            try:
                items = json.loads(self.outline_foreshadowing_set)
                if items: parts.append(f"埋下伏笔：{', '.join(items)}")
            except (json.JSONDecodeError, TypeError):
                pass
        if self.outline_foreshadowing_collect:
            try:
                items = json.loads(self.outline_foreshadowing_collect)
                if items: parts.append(f"回收伏笔：{', '.join(items)}")
            except (json.JSONDecodeError, TypeError):
                pass
        if self.outline_emotion: parts.append(f"情感基调：{self.outline_emotion}")
        return "\n".join(parts)

    def __repr__(self):
        return f"<Chapter id={self.id} chapter={self.chapter_number} title={self.title}>"


class Foreshadowing(Base):
    """伏笔库表"""
    __tablename__ = "foreshadowings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id"), nullable=False)
    name = Column(String(200), nullable=False, comment="伏笔名称/简称")
    description = Column(Text, comment="伏笔详细描述")
    set_chapter = Column(Integer, comment="埋下的章节号")
    set_content = Column(Text, comment="埋下时的具体内容")
    collect_chapter = Column(Integer, comment="回收的章节号（未回收为null）")
    collect_content = Column(Text, comment="回收时的具体内容")
    importance = Column(String(10), default="medium", comment="重要程度：high/medium/low")
    status = Column(String(20), default="active", comment="状态：active/collected/abandoned")
    collect_by_chapter = Column(Integer, nullable=True, comment="最晚回收章节（截止章节，为空表示不限）")
    notes = Column(Text, nullable=True, comment="备注（回收建议、关联线索等）")
    created_at = Column(DateTime, default=datetime.utcnow)

    # 关联
    novel = relationship("Novel", back_populates="foreshadowings")

    def __repr__(self):
        return f"<Foreshadowing id={self.id} name={self.name} status={self.status}>"


class TimelineEvent(Base):
    """全局时间线表"""
    __tablename__ = "timeline_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id"), nullable=False)
    chapter_number = Column(Integer, comment="发生的章节")
    in_story_time = Column(String(100), comment="故事内时间（如：第3年春天）")
    event_name = Column(String(200), nullable=False, comment="事件名称")
    event_description = Column(Text, comment="事件详细描述")
    characters_involved = Column(Text, comment="涉及人物（JSON数组）")
    impact = Column(Text, comment="事件影响")
    created_at = Column(DateTime, default=datetime.utcnow)

    # 关联
    novel = relationship("Novel", back_populates="timeline_events")

    def __repr__(self):
        return f"<TimelineEvent id={self.id} name={self.event_name}>"


class ContentVersion(Base):
    """内容历史版本表（版本控制）"""
    __tablename__ = "content_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id"), nullable=False)
    version_number = Column(Integer, nullable=False, comment="版本号")
    content = Column(Text, comment="版本内容")
    version_type = Column(String(30), comment="版本类型：draft/reviewed/polished/user_edit")
    change_summary = Column(Text, comment="改动摘要")
    created_at = Column(DateTime, default=datetime.utcnow)

    # 关联
    chapter = relationship("Chapter", back_populates="versions")

    __table_args__ = (UniqueConstraint("chapter_id", "version_number"),)

    def __repr__(self):
        return f"<ContentVersion chapter={self.chapter_id} v{self.version_number}>"


class NovelOutline(Base):
    """总大纲表（独立存储，支持多层结构）"""
    __tablename__ = "novel_outlines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id"), nullable=False, unique=True)
    premise = Column(Text, comment="前提设定")
    theme = Column(Text, comment="核心主题")
    main_conflict = Column(Text, comment="主要矛盾（全书）")
    story_structure = Column(Text, comment="故事结构（JSON：开端/发展/高潮/结局）")
    protagonist_arc = Column(Text, comment="主角成长弧光（全书）")
    ending_summary = Column(Text, comment="结局概要")
    total_chapters = Column(Integer, default=0, comment="预计总章节数")
    full_outline_text = Column(Text, comment="原始大纲文本")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<NovelOutline novel_id={self.novel_id}>"


class NovelDocument(Base):
    """小说设定文档表（自由格式 Markdown，与结构化字段并存）"""
    __tablename__ = "novel_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id"), nullable=False)
    doc_type = Column(String(50), nullable=False,
                      comment="文档类型：background / system / characters / custom")
    title = Column(String(200), nullable=False, comment="文档标题")
    content = Column(Text, default="", comment="Markdown 格式内容")
    sort_order = Column(Integer, default=0, comment="排序序号")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    novel = relationship("Novel", back_populates="documents")

    __table_args__ = (UniqueConstraint("novel_id", "doc_type", "title"),)

    def __repr__(self):
        return f"<NovelDocument novel_id={self.novel_id} type={self.doc_type} title={self.title}>"


class Collaborator(Base):
    """协作者表"""
    __tablename__ = "collaborators"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id"), nullable=False)
    display_name = Column(String(100), nullable=False, comment="显示名称")
    role = Column(String(20), nullable=False, default="reviewer",
                  comment="角色：owner / reviewer")
    last_seen_at = Column(DateTime, default=datetime.utcnow, comment="最后活跃时间")
    current_page = Column(String(50), nullable=True, comment="当前所在页面")
    created_at = Column(DateTime, default=datetime.utcnow)

    novel = relationship("Novel", back_populates="collaborators")

    __table_args__ = (UniqueConstraint("novel_id", "display_name"),)

    def __repr__(self):
        return f"<Collaborator id={self.id} name={self.display_name} role={self.role}>"


class Comment(Base):
    """章节评论表"""
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id"), nullable=False)
    novel_id = Column(Integer, ForeignKey("novels.id"), nullable=False)
    author_name = Column(String(100), nullable=False, comment="评论者显示名")
    content = Column(Text, nullable=False, comment="评论内容")
    created_at = Column(DateTime, default=datetime.utcnow)

    chapter = relationship("Chapter", backref="comments")
    novel = relationship("Novel")

    def __repr__(self):
        return f"<Comment id={self.id} author={self.author_name}>"


# ======================================
# 数据库初始化函数
# ======================================
def init_db():
    """创建所有数据表"""
    Base.metadata.create_all(bind=engine)


def drop_db():
    """删除所有数据表（谨慎使用！）"""
    Base.metadata.drop_all(bind=engine)


def _migrate_add_columns():
    """
    对已有数据库补齐新增字段（ALTER TABLE）
    SQLAlchemy create_all 只建表不加列，通过此函数处理增量迁移
    """
    from sqlalchemy import inspect as sa_inspect, text
    insp = sa_inspect(engine)

    migrations = [
        # (table_name, column_name, column_def)
        ("novels", "llm_model",                    "VARCHAR(100)"),
        ("novels", "model_outline",                "VARCHAR(100)"),
        ("novels", "model_character",              "VARCHAR(100)"),
        ("novels", "model_writer",                 "VARCHAR(100)"),
        ("novels", "model_reviewer",               "VARCHAR(100)"),
        ("novels", "model_polisher",               "VARCHAR(100)"),
        ("novels", "style_profile",                "TEXT"),
        ("novels", "style_reference_text",         "TEXT"),
        ("novels",         "model_reader",          "VARCHAR(100)"),
        ("chapters",       "reader_feedback",       "TEXT"),
        ("chapters",       "reader_score",          "REAL DEFAULT 0.0"),
        ("foreshadowings", "collect_by_chapter",   "INTEGER"),
        ("foreshadowings", "notes",                "TEXT"),
        ("novels",         "invite_code",          "VARCHAR(20)"),
        ("chapters",       "approval_status",      "VARCHAR(20) DEFAULT 'pending'"),
        ("novels",         "quality_config",       "TEXT"),
    ]

    with engine.connect() as conn:
        for table, col, col_def in migrations:
            try:
                existing = [c["name"] for c in insp.get_columns(table)]
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}"))
                    conn.commit()
            except Exception:
                pass  # 表不存在等情况由 create_all 处理


# 启动时自动初始化并迁移
init_db()
_migrate_add_columns()
