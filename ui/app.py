"""
主应用路由
会话状态初始化 + 身份入口 + API Key 检查 + 页面路由
"""

import streamlit as st

from core.models import get_db
from core.permissions import is_authenticated, update_presence
from ui.components.api_key import any_api_key_configured, render_api_key_setup
from ui.components.collaboration import render_identity_dialog
from ui.sidebar import render_sidebar, render_status_report
from ui.pages.project import page_project_management
from ui.pages.settings import page_settings
from ui.pages.outline import page_outline
from ui.pages.writing import page_writing
from ui.pages.visualization import page_visualization
from ui.pages.export import page_export


def init_session_state():
    defaults = {
        "novel_id": None,
        "page": "项目管理",
        "writing_chapter": None,
        "stream_content": "",
        "is_writing": False,
        "batch_writing": False,
        "collab_display_name": None,
        "collab_identity": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def main():
    init_session_state()

    # 身份入口：首次访问需输入显示名称
    if not st.session_state.get("collab_display_name"):
        render_identity_dialog()
        return

    # 首次使用引导：未配置任何 API Key 时展示配置页面
    if not any_api_key_configured():
        render_api_key_setup()
        return

    # 在线心跳
    if is_authenticated() and st.session_state.novel_id:
        try:
            db = get_db()
            update_presence(db, st.session_state.novel_id, st.session_state.page)
            db.close()
        except Exception:
            pass

    render_sidebar()

    page = st.session_state.page

    if page == "项目管理":
        page_project_management()
    elif page == "设定管理":
        if st.session_state.novel_id:
            page_settings()
        else:
            st.warning("请先选择或创建一个小说项目")
    elif page == "大纲管理":
        if st.session_state.novel_id:
            page_outline()
        else:
            st.warning("请先选择或创建一个小说项目")
    elif page == "写作":
        if st.session_state.novel_id:
            page_writing()
        else:
            st.warning("请先选择或创建一个小说项目")
    elif page == "可视化":
        if st.session_state.novel_id:
            page_visualization()
        else:
            st.warning("请先选择或创建一个小说项目")
    elif page == "导出":
        if st.session_state.novel_id:
            page_export()
        else:
            st.warning("请先选择或创建一个小说项目")

    render_status_report()
