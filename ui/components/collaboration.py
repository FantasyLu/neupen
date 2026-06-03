"""
协作组件
身份对话框、章节评论区、审批状态按钮
"""

import streamlit as st

from core.models import get_db, Chapter, Comment
from core.permissions import can_comment, can_approve
from ui.helpers import format_approval_badge


def render_identity_dialog():
    """首次访问时显示身份输入对话框"""
    st.markdown("---")
    col_l, col_c, col_r = st.columns([1, 2, 1])
    with col_c:
        st.markdown("## ✒️ Neupen")
        st.markdown("请输入你的显示名称以开始使用")
        with st.form("identity_form"):
            display_name = st.text_input("显示名称", placeholder="例如：小明")
            if st.form_submit_button("进入系统", use_container_width=True, type="primary"):
                if display_name.strip():
                    st.session_state["collab_display_name"] = display_name.strip()
                    st.rerun()
                else:
                    st.warning("请输入显示名称")


def render_chapter_comments(novel_id: int, chapter_id: int):
    """渲染章节评论区"""
    db = get_db()
    comments = db.query(Comment).filter(
        Comment.chapter_id == chapter_id,
        Comment.novel_id == novel_id
    ).order_by(Comment.created_at.desc()).all()
    db.close()

    st.markdown("### 💬 章节评论")

    if comments:
        for c in comments:
            with st.container(border=True):
                header_col, time_col = st.columns([3, 1])
                with header_col:
                    st.markdown(f"**{c.author_name}**")
                with time_col:
                    st.caption(c.created_at.strftime("%m-%d %H:%M") if c.created_at else "")
                st.markdown(c.content)
    else:
        st.caption("暂无评论")

    # 新评论表单
    if can_comment(novel_id):
        with st.form("add_comment_form", clear_on_submit=True):
            comment_text = st.text_area("写评论", placeholder="对本章的意见或建议...", height=80)
            if st.form_submit_button("发表评论", use_container_width=True):
                if comment_text.strip():
                    db = get_db()
                    new_comment = Comment(
                        chapter_id=chapter_id,
                        novel_id=novel_id,
                        author_name=st.session_state.get("collab_display_name", "匿名"),
                        content=comment_text.strip(),
                    )
                    db.add(new_comment)
                    db.commit()
                    db.close()
                    st.success("评论已发表")
                    st.rerun()
                else:
                    st.warning("请输入评论内容")


def render_approval_status(novel_id: int, chapter):
    """渲染章节审批状态和操作按钮"""
    st.markdown("### ✅ 审阅状态")
    current_status = chapter.approval_status or "pending"
    st.markdown(f"当前状态：**{format_approval_badge(current_status)}**")

    if can_approve(novel_id):
        btn_cols = st.columns(3)
        with btn_cols[0]:
            if st.button("✅ 通过", key=f"approve_{chapter.id}", use_container_width=True,
                         disabled=(current_status == "approved")):
                db = get_db()
                ch = db.query(Chapter).filter(Chapter.id == chapter.id).first()
                ch.approval_status = "approved"
                db.commit()
                db.close()
                st.rerun()
        with btn_cols[1]:
            if st.button("🟡 需修改", key=f"needs_rev_{chapter.id}", use_container_width=True,
                         disabled=(current_status == "needs_revision")):
                db = get_db()
                ch = db.query(Chapter).filter(Chapter.id == chapter.id).first()
                ch.approval_status = "needs_revision"
                db.commit()
                db.close()
                st.rerun()
        with btn_cols[2]:
            if st.button("❌ 驳回", key=f"reject_{chapter.id}", use_container_width=True,
                         disabled=(current_status == "rejected")):
                db = get_db()
                ch = db.query(Chapter).filter(Chapter.id == chapter.id).first()
                ch.approval_status = "rejected"
                db.commit()
                db.close()
                st.rerun()
