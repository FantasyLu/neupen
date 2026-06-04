"""
常驻全局 AI 创作助手
解析 outline / settings / chapter 类型代码块并提供一键应用。
"""
import re

import streamlit as st

from core.agents import CanvasAgent


# ──────────────────────────────────────────────────────────────
# 类型化代码块解析
# ──────────────────────────────────────────────────────────────

_TYPED_BLOCK_RE = re.compile(
    r'```(outline|settings|chapter)\n(.*?)```',
    re.DOTALL
)

_APPLY_LABELS = {
    "outline":  ("📖 应用到大纲",   "大纲管理"),
    "settings": ("⚙️ 应用到设定",   "设定管理"),
    "chapter":  ("✍️ 应用到当前章节", "写作"),
}


def _parse_response(text: str):
    """将 AI 回复拆成文本片段 + 类型化代码块的列表"""
    parts = []
    last_end = 0
    for m in _TYPED_BLOCK_RE.finditer(text):
        if m.start() > last_end:
            seg = text[last_end:m.start()].strip()
            if seg:
                parts.append({"type": "text", "content": seg})
        parts.append({"type": m.group(1), "content": m.group(2).strip()})
        last_end = m.end()
    tail = text[last_end:].strip()
    if tail:
        parts.append({"type": "text", "content": tail})
    return parts


def _apply_content(block_type: str, content: str, novel_id: int):
    """将内容写入对应 session state 并跳转到目标页面"""
    if block_type == "outline":
        st.session_state[f"outline_textarea_{novel_id}"] = content
        st.session_state.page = "大纲管理"
    elif block_type == "settings":
        # 写入背景设定文档的 pending
        pending = st.session_state.get(f"settings_pending_{novel_id}") or {}
        pending["background"] = content
        st.session_state[f"settings_pending_{novel_id}"] = pending
        st.session_state.page = "设定管理"
    elif block_type == "chapter":
        ch_num = st.session_state.get("writing_chapter") or 1
        st.session_state[f"writing_pending_{novel_id}_{ch_num}"] = content
        st.session_state.page = "写作"
    st.rerun()


# ──────────────────────────────────────────────────────────────
# 主渲染函数
# ──────────────────────────────────────────────────────────────

def render_global_chat(novel_id: int):
    """在调用处渲染常驻 AI 创作助手（设计为嵌入 sidebar）"""
    chat_key = f"global_chat_{novel_id}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []

    history: list = st.session_state[chat_key]

    st.markdown("#### 🤖 AI 创作助手")

    # 聊天历史（固定高度可滚动）
    with st.container(height=260, border=True):
        if not history:
            st.caption(
                "💡 随时告诉我你的想法——创作思路、人物设定、章节安排都可以讨论。\n\n"
                "我提供的大纲/设定/正文内容可一键写入对应位置。"
            )
        for idx, msg in enumerate(history):
            with st.chat_message(msg["role"]):
                if msg["role"] == "assistant":
                    parts = _parse_response(msg["content"])
                    for p_idx, part in enumerate(parts):
                        if part["type"] == "text":
                            st.markdown(part["content"])
                        else:
                            block_type = part["type"]
                            with st.container(border=True):
                                preview = part["content"][:150]
                                st.markdown(preview + ("…" if len(part["content"]) > 150 else ""))
                                if block_type == "chapter":
                                    # 章节内容已自动写入编辑器，只显示说明
                                    st.caption("✅ 已自动写入编辑器")
                                    if st.button("↩️ 重新写入编辑器",
                                                 key=f"global_reapply_{novel_id}_{idx}_{p_idx}",
                                                 use_container_width=True):
                                        _apply_content("chapter", part["content"], novel_id)
                                else:
                                    label, _ = _APPLY_LABELS[block_type]
                                    if st.button(label,
                                                 key=f"global_apply_{novel_id}_{idx}_{p_idx}_{block_type}",
                                                 use_container_width=True, type="primary"):
                                        _apply_content(block_type, part["content"], novel_id)
                else:
                    st.markdown(msg["content"])

    if user_input := st.chat_input("和 AI 讨论…", key="global_chat_input"):
        history.append({"role": "user", "content": user_input})
        # 把当前页面内容作上下文提示
        page = st.session_state.get("page", "")
        doc_ctx = ""
        if page == "大纲管理":
            doc_ctx = st.session_state.get(f"outline_textarea_{novel_id}", "")
        elif page == "写作":
            ch_num = st.session_state.get("writing_chapter") or 1
            doc_ctx = st.session_state.get(f"edit_content_{novel_id}_{ch_num}", "")

        with st.spinner("思考中…"):
            try:
                agent = CanvasAgent(novel_id=novel_id, role="global")
                reply = agent.chat(history, document_content=doc_ctx)
                agent.close()
            except Exception as e:
                history.pop()
                st.session_state[chat_key] = history
                st.error(f"AI 出错：{e}")
                return

        history.append({"role": "assistant", "content": reply})
        st.session_state[chat_key] = history

        # chapter 块自动写入编辑器，无需用户点击
        for part in _parse_response(reply):
            if part["type"] == "chapter":
                ch_num = st.session_state.get("writing_chapter") or 1
                st.session_state[f"writing_pending_{novel_id}_{ch_num}"] = part["content"]

        st.rerun()

    if history:
        if st.button("🗑️ 清空", use_container_width=True, key="clear_global_chat"):
            st.session_state[chat_key] = []
            st.rerun()
