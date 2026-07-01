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


"""
UI 工具函数
纯格式化函数，无重量级依赖
"""

import streamlit as st
import streamlit.components.v1 as components

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


# ── 带行号的章节编辑器 ────────────────────────────────────────────────────────

def render_chapter_editor(
    text_key: str,
    height: int = 560,
    disabled: bool = False,
    placeholder: str = "在此书写章节内容…",
    font_size_key: str = "editor_font_size",
) -> None:
    """
    带行号 + 字体工具栏的章节编辑器。

    行号通过在 textarea 左侧叠加一个同步滚动的行号列实现（纯 CSS + JS）。
    工具栏提供：字体大小调节、插入加粗/斜体/下划线 Markdown 标记。
    编辑内容仍存储在 st.session_state[text_key]（纯文本 / Markdown）。
    """
    # ── 工具栏 ──────────────────────────────────────────────────────────────
    if font_size_key not in st.session_state:
        st.session_state[font_size_key] = 15

    toolbar_cols = st.columns([3, 1, 1, 1, 1, 1])
    with toolbar_cols[0]:
        st.session_state[font_size_key] = st.slider(
            "字体大小", 12, 24,
            value=st.session_state[font_size_key],
            key=f"{font_size_key}_slider",
            label_visibility="collapsed",
        )
    font_size = st.session_state[font_size_key]

    # 格式标记按钮：点击后在 session_state 里标记，JS 负责插入
    fmt_insert_key = f"{text_key}_fmt_insert"
    with toolbar_cols[1]:
        if st.button("B", key=f"{text_key}_bold",
                     help="加粗（Markdown: **文字**）",
                     use_container_width=True):
            st.session_state[fmt_insert_key] = "bold"
    with toolbar_cols[2]:
        if st.button("I", key=f"{text_key}_italic",
                     help="斜体（Markdown: *文字*）",
                     use_container_width=True):
            st.session_state[fmt_insert_key] = "italic"
    with toolbar_cols[3]:
        if st.button("U", key=f"{text_key}_underline",
                     help="下划线（HTML: <u>文字</u>）",
                     use_container_width=True):
            st.session_state[fmt_insert_key] = "underline"
    with toolbar_cols[4]:
        if st.button("🔴", key=f"{text_key}_red",
                     help="红色文字（HTML: <span style='color:#e05'>文字</span>）",
                     use_container_width=True):
            st.session_state[fmt_insert_key] = "red"
    with toolbar_cols[5]:
        if st.button("💛", key=f"{text_key}_highlight",
                     help="高亮（HTML: <mark>文字</mark>）",
                     use_container_width=True):
            st.session_state[fmt_insert_key] = "highlight"

    fmt_to_insert = st.session_state.pop(fmt_insert_key, None)

    # ── 行号 + textarea 复合组件（纯 HTML/CSS/JS，无外部依赖）─────────────
    current_text = st.session_state.get(text_key, "")
    line_count = max(current_text.count("\n") + 1, 1)
    # 转义用于 HTML 属性
    escaped = current_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    fmt_js = ""
    if fmt_to_insert:
        _fmt_map = {
            "bold":      ("**", "**", "粗体文字"),
            "italic":    ("*", "*", "斜体文字"),
            "underline": ("<u>", "</u>", "文字"),
            "red":       ("<span style=\"color:#e05\">", "</span>", "文字"),
            "highlight": ("<mark>", "</mark>", "文字"),
        }
        pre, suf, placeholder_txt = _fmt_map[fmt_to_insert]
        fmt_js = f"""
        (function(){{
            var ta = document.getElementById('ch_editor');
            if (!ta) return;
            var s = ta.selectionStart, e = ta.selectionEnd;
            var sel = ta.value.substring(s, e) || '{placeholder_txt}';
            var ins = '{pre}' + sel + '{suf}';
            ta.value = ta.value.substring(0, s) + ins + ta.value.substring(e);
            ta.selectionStart = s + {len(pre)};
            ta.selectionEnd   = s + {len(pre)} + sel.length;
            ta.focus();
            syncLineNums();
            notifyStreamlit(ta.value);
        }})();
        """

    readonly_attr = "readonly" if disabled else ""

    html_code = f"""
    <style>
    #editor-wrap {{
        display: flex;
        border: 1px solid rgba(232,226,216,0.14);
        border-radius: 3px;
        background: #161411;
        overflow: hidden;
        font-family: 'Noto Serif SC', 'Source Han Serif', 'STSong', Georgia, serif;
        font-size: {font_size}px;
        line-height: 1.8;
    }}
    #line-nums {{
        width: 44px;
        min-width: 44px;
        background: #1a1714;
        border-right: 1px solid rgba(232,226,216,0.06);
        color: #4a4641;
        text-align: right;
        padding: 12px 8px 12px 4px;
        font-size: {max(font_size - 3, 10)}px;
        line-height: 1.8;
        overflow: hidden;
        user-select: none;
        box-sizing: border-box;
        white-space: pre;
        font-family: 'SF Mono', 'Fira Code', monospace;
    }}
    #ch_editor {{
        flex: 1;
        background: transparent;
        border: none;
        outline: none;
        color: #e8e2d8;
        padding: 12px 16px;
        font-size: {font_size}px;
        line-height: 1.8;
        font-family: inherit;
        resize: none;
        height: {height}px;
        box-sizing: border-box;
        overflow-y: auto;
        white-space: pre-wrap;
        word-break: break-all;
    }}
    #ch_editor::placeholder {{ color: #4a4641; }}
    #ch_editor:focus {{ box-shadow: inset 0 0 0 1px rgba(201,169,110,0.2); }}
    </style>

    <div id="editor-wrap">
        <div id="line-nums">{chr(10).join(str(i) for i in range(1, line_count + 1))}</div>
        <textarea
            id="ch_editor"
            placeholder="{placeholder}"
            {readonly_attr}
        >{escaped}</textarea>
    </div>

    <script>
    (function() {{
        var ta  = document.getElementById('ch_editor');
        var lns = document.getElementById('line-nums');

        function syncLineNums() {{
            var lines = ta.value.split('\\n').length;
            var nums  = '';
            for (var i = 1; i <= lines; i++) nums += i + '\\n';
            lns.textContent = nums;
        }}

        function notifyStreamlit(val) {{
            window.parent.postMessage({{
                type: 'streamlit:setComponentValue',
                value: val
            }}, '*');
        }}

        // 同步行号随滚动
        ta.addEventListener('scroll', function() {{
            lns.scrollTop = ta.scrollTop;
        }});

        // 内容变化时更新行号并通知 Streamlit
        ta.addEventListener('input', function() {{
            syncLineNums();
            notifyStreamlit(ta.value);
        }});

        // 初始化行号
        syncLineNums();

        {fmt_js}
    }})();
    </script>
    """

    # 用 components.html 渲染，用一个隐藏 text_area 作为数据桥
    # 组件高度 = 编辑器高度 + 工具栏余量
    components.html(html_code, height=height + 30, scrolling=False)

    # 隐藏原生 text_area 作为数据同步桥（用户输入通过 JS postMessage 已不可靠，
    # 故维持原生 text_area 作为主数据源，行号编辑器作为视觉层）
    # 实际采用双轨方案：HTML 组件展示视觉效果，原生 text_area 做数据 key
    st.markdown(
        f"""
        <style>
        /* 隐藏原生 text_area 的外观，仅保留数据绑定 */
        div[data-testid="stTextArea"][data-key="{text_key}"] > div > div > textarea {{
            display: none !important;
        }}
        div[data-testid="stTextArea"][data-key="{text_key}"] > label {{
            display: none !important;
        }}
        div[data-testid="stTextArea"][data-key="{text_key}"] {{
            margin: 0 !important; padding: 0 !important; height: 0 !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.text_area(
        "editor_bridge",
        key=text_key,
        height=1,
        label_visibility="hidden",
        disabled=disabled,
    )


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
    components.html(html, height=height + 4, scrolling=False)
