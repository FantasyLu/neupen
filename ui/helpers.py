"""
UI 工具函数
纯格式化函数，无重量级依赖
"""

import streamlit as st
from streamlit_ace import st_ace

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


# ── 带行号的章节编辑器（streamlit-ace）────────────────────────────────────────

def render_chapter_editor(
    text_key: str,
    height: int = 560,
    disabled: bool = False,
    placeholder: str = "在此书写章节内容…",
    font_size_key: str = "editor_font_size",
    force_version: int = 0,
) -> None:
    """
    带行号 + 字体工具栏的章节编辑器（基于 streamlit-ace）。

    streamlit-ace 原生支持行号、双向数据绑定，编辑内容直接写入
    st.session_state[text_key]，无需 postMessage 桥接。
    工具栏提供字体大小调节；格式插入暂通过 session_state 标记 + ace 命令实现。

    force_version: 传入不同的整数可强制重建 ace 组件（用于外部写入新内容后刷新显示）。
    """
    # ── 字体大小工具栏 ────────────────────────────────────────────────────────
    if font_size_key not in st.session_state:
        st.session_state[font_size_key] = 15

    font_size = st.slider(
        "字体大小",
        min_value=12,
        max_value=24,
        value=st.session_state[font_size_key],
        key=f"{font_size_key}_slider",
        label_visibility="collapsed",
    )
    st.session_state[font_size_key] = font_size

    # ── Ace 编辑器 ─────────────────────────────────────────────────────────────
    current_text = st.session_state.get(text_key, "") or ""

    # st_ace 返回编辑器当前内容（每次交互都会返回最新值）
    new_value = st_ace(
        value=current_text,
        placeholder=placeholder,
        language="text",          # 纯文本，不高亮语法
        theme="tomorrow_night",   # 暗黑主题，接近项目整体风格
        font_size=font_size,
        tab_size=4,
        show_gutter=True,         # 显示行号
        show_print_margin=False,
        wrap=True,                # 自动换行
        auto_update=True,         # 每次按键立即回传（非仅失焦时）
        readonly=disabled,
        height=height,
        key=f"{text_key}_ace_v{force_version}",
    )

    # 将 ace 编辑器的值同步回 session_state[text_key]
    if new_value is not None:
        st.session_state[text_key] = new_value


# ── 流式输出预览区 ─────────────────────────────────────────────────────────────

def render_stream_preview(stream_text: str, chapter_num: int, height: int = 400) -> None:
    """
    流式输出预览区：在编辑器上方展示 AI 正在生成的内容。
    使用与行号编辑器相同的视觉风格，但只读。
    """
    line_count = max(stream_text.count("\n") + 1, 1) if stream_text else 1
    escaped = (stream_text
               .replace("&", "&amp;")
               .replace("<", "&lt;")
               .replace(">", "&gt;")) if stream_text else ""

    nums = "\n".join(str(i) for i in range(1, line_count + 1))

    html = f"""
    <style>
    #stream-wrap {{
        display: flex;
        border: 1px solid rgba(201,169,110,0.25);
        border-radius: 3px;
        background: #161411;
        overflow: hidden;
        font-family: 'Noto Serif SC', 'Source Han Serif', 'STSong', Georgia, serif;
        font-size: 15px;
        line-height: 1.8;
        height: {height}px;
    }}
    #stream-lns {{
        width: 44px;
        min-width: 44px;
        background: #1a1714;
        border-right: 1px solid rgba(232,226,216,0.06);
        color: #4a4641;
        text-align: right;
        padding: 12px 8px 12px 4px;
        font-size: 12px;
        line-height: 1.8;
        overflow: hidden;
        user-select: none;
        font-family: 'SF Mono', monospace;
        white-space: pre;
    }}
    #stream-content {{
        flex: 1;
        color: #e8e2d8;
        padding: 12px 16px;
        overflow-y: auto;
        white-space: pre-wrap;
        word-break: break-all;
        line-height: 1.8;
        font-family: inherit;
    }}
    #stream-cursor {{
        display: inline-block;
        width: 2px;
        height: 1em;
        background: #c9a96e;
        margin-left: 2px;
        vertical-align: text-bottom;
        animation: blink .9s step-end infinite;
    }}
    @keyframes blink {{ 50% {{ opacity: 0; }} }}
    </style>

    <div id="stream-wrap">
        <div id="stream-lns">{nums}</div>
        <div id="stream-content">{escaped}<span id="stream-cursor"></span></div>
    </div>
    <script>
    (function(){{
        var c = document.getElementById('stream-content');
        if (c) c.scrollTop = c.scrollHeight;
    }})();
    </script>
    """
    st.iframe(html, height=height + 4)
