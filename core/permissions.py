"""
权限管理模块
协同写作的身份验证、权限检查、邀请码生成、在线状态管理
"""

import random
import string
from datetime import datetime, timedelta

import streamlit as st
from sqlalchemy.orm import Session

from core.models import Collaborator, Novel


# 去除易混淆字符（O/0/I/1/l）
_CODE_CHARS = string.ascii_uppercase.replace("O", "").replace("I", "") + \
              string.digits.replace("0", "").replace("1", "")


def generate_invite_code(length: int = 8) -> str:
    """生成随机邀请码"""
    return "".join(random.choices(_CODE_CHARS, k=length))


def get_current_identity() -> dict | None:
    """读取 session 中的身份信息"""
    return st.session_state.get("collab_identity")


def is_authenticated() -> bool:
    """是否已设置身份"""
    return st.session_state.get("collab_display_name") is not None


def current_role(novel_id: int) -> str | None:
    """当前用户在指定项目中的角色"""
    identity = get_current_identity()
    if identity and identity.get("novel_id") == novel_id:
        return identity.get("role")
    return None


def is_owner(novel_id: int) -> bool:
    """是否为项目 owner"""
    return current_role(novel_id) == "owner"


def can_edit(novel_id: int) -> bool:
    """是否有编辑权限（仅 owner）"""
    return is_owner(novel_id)


def can_comment(novel_id: int) -> bool:
    """是否可评论（owner + reviewer）"""
    return current_role(novel_id) in ("owner", "reviewer")


def can_approve(novel_id: int) -> bool:
    """是否可审批（owner + reviewer）"""
    return current_role(novel_id) in ("owner", "reviewer")


def require_edit_permission(novel_id: int) -> bool:
    """
    权限门控：若无编辑权限则显示警告。
    返回 True 表示有权限，False 表示无权限。
    """
    if can_edit(novel_id):
        return True
    st.warning("你是审阅者，无法执行此操作。仅主笔可编辑内容。")
    return False


def update_presence(db: Session, novel_id: int, page: str):
    """更新在线状态心跳"""
    identity = get_current_identity()
    if not identity:
        return
    collab = db.query(Collaborator).filter_by(id=identity["collaborator_id"]).first()
    if collab:
        collab.last_seen_at = datetime.utcnow()
        collab.current_page = page
        db.commit()


def get_online_collaborators(db: Session, novel_id: int, timeout_minutes: int = 10) -> list:
    """获取在线协作者列表（timeout_minutes 内有心跳的）"""
    cutoff = datetime.utcnow() - timedelta(minutes=timeout_minutes)
    collabs = db.query(Collaborator).filter(
        Collaborator.novel_id == novel_id,
        Collaborator.last_seen_at >= cutoff
    ).all()
    return [
        {
            "id": c.id,
            "display_name": c.display_name,
            "role": c.role,
            "current_page": c.current_page,
        }
        for c in collabs
    ]
