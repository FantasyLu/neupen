"""
UI 工具函数
纯格式化函数，无重量级依赖
"""

import streamlit as st

from core.models import get_db, Novel


def get_all_novels() -> list[Novel]:
    """获取所有小说项目列表"""
    db = get_db()
    novels = db.query(Novel).order_by(Novel.created_at.desc()).all()
    db.close()
    return novels


def format_status(status: str) -> str:
    """状态标签汉化"""
    status_map = {
        "planning":  "📝 规划中",
        "outlining": "🗂 大纲阶段",
        "writing":   "✍️ 写作中",
        "completed": "✅ 已完成",
    }
    return status_map.get(status, status)


def format_chapter_status(status: str) -> str:
    status_map = {
        "outline_pending": "📋 待章纲",
        "outlined":        "📄 有章纲",
        "writing":         "✍️ 写作中",
        "review_pending":  "🔍 审核中",
        "reviewed":        "🔎 已审核",
        "polished":        "✨ 已润色",
        "published":       "✅ 已完成",
    }
    return status_map.get(status, status)


def format_approval_badge(status: str) -> str:
    """审批状态 badge"""
    badges = {
        "pending":        "🔘 待审阅",
        "approved":       "✅ 已通过",
        "needs_revision": "🟡 需修改",
        "rejected":       "❌ 已驳回",
    }
    return badges.get(status, status or "🔘 待审阅")


def show_success(msg: str):
    st.success(msg)


def show_error(msg: str):
    st.error(msg)
