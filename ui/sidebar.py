"""
侧边栏导航 + 项目状态报告
"""

import streamlit as st

from core.models import get_db, Novel
from core.permissions import get_current_identity, get_online_collaborators
from core.workflow import load_novel
from ui.helpers import format_status
from ui.components.global_chat import render_global_chat


def render_sidebar():
    with st.sidebar:
        st.markdown("# ✒️ Neupen")
        st.divider()

        # 当前项目显示
        if st.session_state.novel_id:
            db = get_db()
            novel = db.query(Novel).filter(Novel.id == st.session_state.novel_id).first()
            db.close()
            if novel:
                st.markdown(f"**当前项目**")
                st.markdown(f"📚 {novel.title}")
                st.caption(format_status(novel.status))

                # 身份显示
                identity = get_current_identity()
                if identity and identity.get("novel_id") == novel.id:
                    role_icon = "👑" if identity["role"] == "owner" else "👁"
                    role_label = "主笔" if identity["role"] == "owner" else "审阅者"
                    st.markdown(f"{role_icon} **{role_label}** {identity['display_name']}")

                    # Owner 显示邀请码
                    if identity["role"] == "owner" and novel.invite_code:
                        st.code(novel.invite_code, language=None)
                        st.caption("分享邀请码给协作者")

                    # 在线协作者
                    try:
                        db = get_db()
                        online = get_online_collaborators(db, novel.id)
                        db.close()
                        if online:
                            with st.expander(f"👥 在线协作者 ({len(online)})"):
                                for c in online:
                                    c_icon = "👑" if c["role"] == "owner" else "👁"
                                    page_info = f" · {c['current_page']}" if c.get("current_page") else ""
                                    st.markdown(f"{c_icon} {c['display_name']}{page_info}")
                    except Exception:
                        pass

                if st.button("切换项目", use_container_width=True):
                    st.session_state.novel_id = None
                    st.session_state.collab_identity = None
                    st.session_state.page = "项目管理"
                    st.rerun()
                st.divider()

        # 页面导航（仅在选择项目后显示完整菜单）
        pages = ["项目管理"]
        if st.session_state.novel_id:
            pages += ["设定管理", "大纲管理", "写作", "可视化", "导出"]

        for page in pages:
            icon = {
                "项目管理": "🏠",
                "设定管理": "⚙️",
                "大纲管理": "🗂",
                "写作":    "✍️",
                "可视化":  "📊",
                "导出":    "📤",
            }.get(page, "")
            is_current = st.session_state.page == page
            btn_type = "primary" if is_current else "secondary"
            if st.button(f"{icon} {page}", use_container_width=True, type=btn_type):
                st.session_state.page = page
                st.rerun()

        if st.session_state.novel_id:
            st.divider()
            render_global_chat(st.session_state.novel_id)

        st.divider()
        st.caption("Powered by Claude & Anthropic")


def render_status_report():
    """在侧边栏显示简要状态报告"""
    if not st.session_state.novel_id:
        return
    with st.sidebar:
        st.divider()
        with st.expander("📊 项目状态", expanded=False):
            try:
                workflow = load_novel(st.session_state.novel_id)
                report = workflow.get_status_report()
                workflow.close()

                if report:
                    completed = report.get("completed_chapters", 0)
                    total = report.get("total_chapters", 0)
                    words = report.get("total_words", 0)
                    pct = f"{completed/total*100:.0f}%" if total else "0%"
                    st.markdown(f"**进度：** {completed}/{total}章 ({pct})")
                    st.markdown(f"**字数：** {words:,}")
                    active_fs = report.get("active_foreshadowings", [])
                    if active_fs:
                        st.warning(f"⚠️ {len(active_fs)} 个未回收伏笔")
                    next_chs = report.get("next_chapters", [])
                    if next_chs:
                        st.info(f"待写：{next_chs[0][:30]}...")
            except Exception:
                pass
