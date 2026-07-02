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
        # ── 品牌标题：杂志风，细衬线 + 装饰线 ──
        st.markdown(
            """
            <div style="padding: 1.4rem 0 1rem 0;">
                <div style="
                    width: 24px; height: 1px;
                    background: #c9a96e;
                    margin-bottom: 0.75rem;
                "></div>
                <div style="
                    font-family: 'Cormorant Garamond', 'Cormorant', Georgia, serif;
                    font-size: 1.45rem;
                    font-weight: 300;
                    letter-spacing: 0.2em;
                    color: #e8e2d8;
                    text-transform: uppercase;
                    line-height: 1;
                ">Neupen</div>
                <div style="
                    font-size: 0.6rem;
                    letter-spacing: 0.24em;
                    color: #8a8278;
                    text-transform: uppercase;
                    margin-top: 0.3rem;
                ">AI Novel Studio</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.divider()

        # ── 当前项目显示 ──
        if st.session_state.novel_id:
            db = get_db()
            novel = db.query(Novel).filter(Novel.id == st.session_state.novel_id).first()
            db.close()
            if novel:
                # 项目信息块
                st.markdown(
                    f"""
                    <div style="margin-bottom: 0.8rem;">
                        <div style="
                            font-size: 0.6rem;
                            letter-spacing: 0.18em;
                            color: #8a8278;
                            text-transform: uppercase;
                            margin-bottom: 0.3rem;
                        ">当前项目</div>
                        <div style="
                            font-family: 'Cormorant Garamond', serif;
                            font-size: 1.1rem;
                            font-weight: 300;
                            color: #e8e2d8;
                            letter-spacing: 0.04em;
                            line-height: 1.3;
                        ">{novel.title}</div>
                        <div style="
                            font-size: 0.68rem;
                            color: #8a8278;
                            margin-top: 0.2rem;
                            letter-spacing: 0.06em;
                        ">{format_status(novel.status)}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                # 身份显示
                identity = get_current_identity()
                if identity and identity.get("novel_id") == novel.id:
                    role_label = "主笔" if identity["role"] == "owner" else "审阅者"
                    st.markdown(
                        f"""
                        <div style="
                            font-size: 0.68rem;
                            letter-spacing: 0.08em;
                            color: #c9a96e;
                            margin-bottom: 0.6rem;
                        ">{role_label} · {identity['display_name']}</div>
                        """,
                        unsafe_allow_html=True,
                    )

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
                            with st.expander(f"在线协作者 ({len(online)})"):
                                for c in online:
                                    role_tag = "主笔" if c["role"] == "owner" else "审阅"
                                    page_info = f" · {c['current_page']}" if c.get("current_page") else ""
                                    st.caption(f"{role_tag} {c['display_name']}{page_info}")
                    except Exception:
                        pass

                if st.button("切换项目", width="stretch"):
                    st.session_state.novel_id = None
                    st.session_state.collab_identity = None
                    st.session_state.page = "项目管理"
                    st.rerun()
                st.divider()

        # ── 页面导航 ──
        pages = ["项目管理"]
        if st.session_state.novel_id:
            pages += ["设定管理", "大纲管理", "写作", "可视化", "导出"]

        for page in pages:
            is_current = st.session_state.page == page
            btn_type = "primary" if is_current else "secondary"
            if st.button(page, width="stretch", type=btn_type, key=f"nav_{page}"):
                st.session_state.page = page
                st.rerun()

        # 平台风格（全局配置）
        st.divider()
        is_ps = st.session_state.page == "平台风格"
        if st.button("平台风格", width="stretch",
                     type="primary" if is_ps else "secondary",
                     key="nav_platform"):
            st.session_state.page = "平台风格"
            st.rerun()

        if st.session_state.novel_id:
            st.divider()
            render_global_chat(st.session_state.novel_id)

        st.divider()
        st.markdown(
            """
            <div style="
                font-size: 0.58rem;
                letter-spacing: 0.14em;
                color: #4a4641;
                text-transform: uppercase;
                text-align: center;
                padding-bottom: 0.4rem;
            ">Powered by Claude</div>
            """,
            unsafe_allow_html=True,
        )


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
