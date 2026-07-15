"""
Neupen — 应用入口
st.set_page_config 必须是第一个 Streamlit 调用，因此放在此处
"""

import streamlit as st

st.set_page_config(
    page_title="Neupen",
    page_icon="✒️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 全局样式注入 ─────────────────────────────────────────────────────────────
# 设计语言：暗黑杂志风（warm / restrained / editorial）
# 色板：暖黑 #0e0d0b · 暖米 #e8e2d8 · 暖金 #c9a96e · 隔离线 rgba(232,226,216,0.08)
# 字体：标题用 Cormorant Garamond（细衬线）· 正文 / 标签沿用系统无衬线
st.markdown(
    """
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,500;1,300;1,400&family=Cormorant:wght@300;400&display=swap" rel="stylesheet">

    <style>
    /* ── 色彩变量 ── */
    :root {
        --bg:         #0e0d0b;
        --bg2:        #161411;
        --bg3:        #1e1b17;
        --text:       #e8e2d8;
        --text-muted: #8a8278;
        --gold:       #c9a96e;
        --gold-dim:   rgba(201,169,110,0.15);
        --border:     rgba(232,226,216,0.08);
        --border-mid: rgba(232,226,216,0.14);
    }

    /* ── 全局背景 & 文字 ── */
    .stApp { background-color: var(--bg); }
    .stApp > header { background-color: transparent !important; }

    /* ── 标题字体：Cormorant Garamond ── */
    h1, h2, h3 {
        font-family: 'Cormorant Garamond', 'Cormorant', Georgia, serif !important;
        font-weight: 300 !important;
        letter-spacing: 0.04em;
        color: var(--text) !important;
    }
    h1 { font-size: 2.6rem !important; line-height: 1.2; }
    h2 { font-size: 1.9rem !important; }
    h3 { font-size: 1.4rem !important; }

    /* ── 页面标题：st.title() ── */
    .stMarkdown h1:first-child,
    [data-testid="stHeading"] h1 {
        font-family: 'Cormorant Garamond', serif !important;
        font-weight: 300 !important;
        font-size: 2.4rem !important;
        letter-spacing: 0.06em;
        border-bottom: 1px solid var(--border-mid);
        padding-bottom: 0.6rem;
        margin-bottom: 2rem;
    }

    /* ── 正文 & caption ── */
    p, li, .stMarkdown p { color: var(--text); line-height: 1.75; }
    .stCaption, [data-testid="stCaptionContainer"] {
        color: var(--text-muted) !important;
        font-size: 0.78rem;
        letter-spacing: 0.04em;
    }

    /* ── 按钮：极简 outline 风格 ── */
    .stButton > button {
        background: transparent !important;
        border: 1px solid var(--border-mid) !important;
        color: var(--text) !important;
        border-radius: 2px !important;
        font-size: 0.82rem !important;
        letter-spacing: 0.08em !important;
        padding: 0.45rem 1.1rem !important;
        transition: border-color 0.2s, color 0.2s, background 0.2s !important;
    }
    .stButton > button:hover {
        border-color: var(--gold) !important;
        color: var(--gold) !important;
        background: var(--gold-dim) !important;
    }
    /* primary 按钮：暖金实心 */
    .stButton > button[kind="primary"] {
        background: var(--gold) !important;
        border-color: var(--gold) !important;
        color: var(--bg) !important;
        font-weight: 500 !important;
    }
    .stButton > button[kind="primary"]:hover {
        background: #d4b47a !important;
        border-color: #d4b47a !important;
        color: var(--bg) !important;
    }

    /* ── Tabs ── */
    .stTabs [data-baseweb="tab-list"] {
        background: transparent !important;
        border-bottom: 1px solid var(--border) !important;
        gap: 0.1rem;
    }
    .stTabs [data-baseweb="tab"] {
        background: transparent !important;
        color: var(--text-muted) !important;
        border: none !important;
        font-size: 0.8rem !important;
        letter-spacing: 0.1em !important;
        text-transform: uppercase !important;
        padding: 0.6rem 1.2rem !important;
        border-radius: 0 !important;
    }
    .stTabs [aria-selected="true"] {
        color: var(--gold) !important;
        border-bottom: 1px solid var(--gold) !important;
    }

    /* ── 输入框 & 文本域 ── */
    .stTextInput input,
    .stTextArea textarea,
    .stSelectbox select,
    [data-baseweb="input"] input,
    [data-baseweb="textarea"] textarea {
        background-color: var(--bg2) !important;
        border: 1px solid var(--border-mid) !important;
        border-radius: 2px !important;
        color: var(--text) !important;
    }
    .stTextInput input:focus,
    .stTextArea textarea:focus {
        border-color: var(--gold) !important;
        box-shadow: 0 0 0 1px var(--gold-dim) !important;
    }

    /* ── Metric ── */
    [data-testid="stMetricValue"] {
        font-family: 'Cormorant Garamond', serif !important;
        font-size: 2rem !important;
        font-weight: 300 !important;
        color: var(--text) !important;
    }
    [data-testid="stMetricLabel"] {
        color: var(--text-muted) !important;
        font-size: 0.72rem !important;
        letter-spacing: 0.1em !important;
        text-transform: uppercase !important;
    }

    /* ── Container / 卡片 ── */
    [data-testid="stVerticalBlock"] > [data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlockBorderWrapper"] {
        background-color: var(--bg2) !important;
        border: 1px solid var(--border) !important;
        border-radius: 3px !important;
    }

    /* ── Expander ── */
    .streamlit-expanderHeader {
        background-color: var(--bg2) !important;
        border: 1px solid var(--border) !important;
        border-radius: 2px !important;
        color: var(--text) !important;
        font-size: 0.82rem !important;
        letter-spacing: 0.06em !important;
    }

    /* ── Divider ── */
    hr { border-color: var(--border) !important; margin: 1.6rem 0 !important; }

    /* ── Code / st.code ── */
    code, pre, .stCodeBlock {
        background-color: var(--bg3) !important;
        border: 1px solid var(--border) !important;
        border-radius: 2px !important;
        color: var(--gold) !important;
        font-size: 0.8rem !important;
    }

    /* ── Alert / info / warning ── */
    [data-testid="stAlert"] {
        border-radius: 2px !important;
        border-left-width: 2px !important;
    }

    /* ── Progress bar ── */
    [data-testid="stProgressBar"] > div { background-color: var(--gold) !important; }

    /* ── Slider ── */
    [data-testid="stSlider"] [data-baseweb="slider"] [role="slider"] {
        background-color: var(--gold) !important;
    }

    /* ── Scrollbar ── */
    ::-webkit-scrollbar { width: 4px; height: 4px; }
    ::-webkit-scrollbar-track { background: var(--bg); }
    ::-webkit-scrollbar-thumb { background: var(--border-mid); border-radius: 2px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

    /* ── Sidebar ── */
    [data-testid="stSidebar"] {
        background-color: var(--bg2) !important;
        border-right: 1px solid var(--border) !important;
    }
    [data-testid="stSidebar"] .stButton > button {
        font-size: 0.78rem !important;
        letter-spacing: 0.1em !important;
        text-transform: uppercase !important;
        border-color: transparent !important;
        padding: 0.4rem 0.8rem !important;
    }
    [data-testid="stSidebar"] .stButton > button:hover {
        border-color: var(--gold) !important;
        color: var(--gold) !important;
        background: var(--gold-dim) !important;
    }
    [data-testid="stSidebar"] .stButton > button[kind="primary"] {
        background: transparent !important;
        border-color: var(--gold) !important;
        color: var(--gold) !important;
    }
    [data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {
        background: var(--gold-dim) !important;
    }

    /* ── Spinner ── */
    [data-testid="stSpinner"] { color: var(--gold) !important; }

    /* ── Select / Multiselect ── */
    [data-baseweb="select"] [data-baseweb="popover"] {
        background: var(--bg2) !important;
        border: 1px solid var(--border-mid) !important;
    }

    /* ── Checkbox & Radio ── */
    [data-testid="stCheckbox"] label,
    [data-testid="stRadio"] label { color: var(--text) !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

@st.cache_resource
def _init_tracing():
    from core.tracing import init_tracing
    return init_tracing()

_init_tracing()

from ui.app import main

main()
