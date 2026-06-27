"""
常驻全局 AI 创作助手
解析 outline / settings / world / characters / chapter / volume / foreshadowing 类型代码块并提供一键应用。
AI 未使用类型化代码块时，提供「应用到…」下拉选择器兜底。
"""
import json
import re

import streamlit as st

from core.agents import CanvasAgent
from core.workflow import load_novel
from core.models import get_db, Novel, Chapter, Character, Foreshadowing, Volume


# ──────────────────────────────────────────────────────────────
# 类型化代码块解析
# ──────────────────────────────────────────────────────────────

_TYPED_BLOCK_RE = re.compile(
    r'```\s*(outline|settings|world|characters|chapter|volume|foreshadowing|style)\s*\r?\n(.*?)```',
    re.DOTALL
)

_APPLY_LABELS = {
    "outline":       ("📖 应用到大纲",   "大纲管理"),
    "settings":      ("⚙️ 应用到设定文档", "设定管理"),
    "world":         ("🌍 应用到世界观",   "设定管理"),
    "characters":    ("👤 应用到人物档案", "设定管理"),
    "chapter":       ("✍️ 应用到章节",    "写作"),
    "volume":        ("📋 应用到卷大纲",   "大纲管理"),
    "foreshadowing": ("📌 应用到伏笔库",   "设定管理"),
    "style":         ("🎨 应用到写作风格", "设定管理"),
}

# 兜底应用目标列表（用于未使用类型化代码块时）
_FALLBACK_TARGETS = [
    ("大纲管理", "outline"),
    ("设定文档", "settings"),
    ("世界观",   "world"),
    ("人物档案", "characters"),
    ("章节正文", "chapter"),
]


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


def _has_typed_blocks(text: str) -> bool:
    """检查 AI 回复是否包含类型化代码块"""
    return bool(_TYPED_BLOCK_RE.search(text))


def _extract_substantive_content(text: str) -> str:
    """从 AI 回复中提取实质性内容，剥离对话前缀和废话。

    优先级：
    1. 有类型化代码块 → 合并所有代码块内容（不含对话前缀）
    2. 有普通 markdown 代码块 → 合并块内内容
    3. 有 --- 分隔线 → 取最后一段之后的内容
    4. 否则 → 尝试去掉对话前缀
    """
    # 1. 提取类型化代码块
    typed_blocks = _TYPED_BLOCK_RE.findall(text)
    if typed_blocks:
        return "\n\n".join(block[1].strip() for block in typed_blocks)

    # 2. 提取普通 markdown 代码块（``` 或 ```markdown）
    md_blocks = re.findall(r'```(?:markdown|text)?\s*\r?\n(.*?)```', text, re.DOTALL)
    if md_blocks:
        return "\n\n".join(b.strip() for b in md_blocks)

    # 3. 取最后一个 --- 分隔线之后的内容
    parts = text.split("\n---\n")
    if len(parts) > 1:
        candidate = parts[-1].strip()
        if len(candidate) > 40:
            return candidate

    # 4. 去掉 AI 对话前缀（常见开头模式）
    _CONVERSATION_PREFIXES = [
        r'^好的[，,]\s*',
        r'^收到[，,]\s*',
        r'^了解[，,]\s*',
        r'^明白了[，,]\s*',
        r'^根据您[^。\n]*[。\n]',
        r'^以下[是为][^。\n]*[：:]\s*',
        r'^现在[，,]?\s*我[^。\n]*[：:]\s*',
        r'^已[经收][^。\n]*[。\n]',
    ]
    for pattern in _CONVERSATION_PREFIXES:
        cleaned = re.sub(pattern, '', text.strip(), count=1, flags=re.DOTALL)
        if cleaned != text.strip():
            return cleaned.strip()

    return text.strip()


# ──────────────────────────────────────────────────────────────
# 应用逻辑
# ──────────────────────────────────────────────────────────────

def _apply_content(block_type: str, content: str, novel_id: int):
    """将内容写入对应目标（session state / 数据库），并跳转到目标页面"""
    if block_type == "outline":
        st.session_state[f"outline_textarea_{novel_id}"] = content
        # 自动持久化到大纲数据库
        try:
            from core.models import NovelOutline
            from ui.pages.outline import _markdown_to_outline_fields
            fields = _markdown_to_outline_fields(content)
            db_w = get_db()
            outline = db_w.query(NovelOutline).filter(NovelOutline.novel_id == novel_id).first()
            if outline:
                for k, v in fields.items():
                    if hasattr(outline, k):
                        setattr(outline, k, v)
            else:
                db_w.add(NovelOutline(novel_id=novel_id, **fields))
            db_w.commit()
            db_w.close()
            st.toast("✅ 大纲已保存", icon="📖")
        except Exception:
            pass
        st.session_state.page = "大纲管理"

    elif block_type == "settings":
        pending = st.session_state.get(f"settings_pending_{novel_id}") or {}
        pending["background"] = content
        st.session_state[f"settings_pending_{novel_id}"] = pending
        # 自动持久化到设定文档数据库
        try:
            db_s = get_db()
            existing = db_s.query(NovelDocument).filter_by(
                novel_id=novel_id, doc_type="background"
            ).first()
            if existing:
                existing.content = content
            else:
                db_s.add(NovelDocument(
                    novel_id=novel_id, doc_type="background",
                    title="背景设定", content=content, sort_order=0
                ))
            db_s.commit()
            db_s.close()
            st.toast("✅ 设定已保存", icon="⚙️")
        except Exception:
            pass
        st.session_state.page = "设定管理"

    elif block_type == "world":
        try:
            data = json.loads(content)
            db = get_db()
            novel_obj = db.query(Novel).filter(Novel.id == novel_id).first()
            if novel_obj:
                world = novel_obj.get_world_setting()
                world.update(data)
                novel_obj.set_world_setting(world)
                db.commit()
                st.toast(f"✅ 已更新 {len(data)} 条世界观设定", icon="🌍")
            db.close()
        except json.JSONDecodeError:
            st.toast("⚠️ 世界观数据格式错误，需要 JSON 格式", icon="⚠️")

    elif block_type == "characters":
        wf = None
        saved = 0
        deleted = 0
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                data = [data]
            wf = load_novel(novel_id)
            for char in data:
                name = char.get("name", "").strip()
                if not name:
                    continue
                action = char.get("action", "create")  # create / update / delete
                if action == "delete":
                    if wf.memory.global_mem.delete_character(name):
                        deleted += 1
                    continue
                char_data = {
                    "name": name,
                    "role": char.get("role", ""),
                    "age": char.get("age", ""),
                    "gender": char.get("gender", ""),
                    "personality": char.get("personality", ""),
                    "background": char.get("background", ""),
                    "appearance": char.get("appearance", ""),
                    "growth_arc": char.get("growth_arc", ""),
                    "current_state": char.get("current_state", ""),
                    "motivations": char.get("motivations", ""),
                    "speech_patterns": char.get("speech_patterns", ""),
                    "secrets": char.get("secrets", ""),
                    "is_main": char.get("is_main", False),
                }
                # 可选字段：别名、人际关系、能力
                if char.get("aliases"):
                    char_data["aliases"] = json.dumps(
                        char["aliases"], ensure_ascii=False
                    ) if not isinstance(char["aliases"], str) else char["aliases"]
                if char.get("abilities"):
                    char_data["abilities"] = json.dumps(
                        char["abilities"], ensure_ascii=False
                    ) if not isinstance(char["abilities"], str) else char["abilities"]
                if char.get("relationships"):
                    char_data["relationships"] = json.dumps(
                        char["relationships"], ensure_ascii=False
                    ) if not isinstance(char["relationships"], str) else char["relationships"]
                wf.memory.global_mem.save_character(char_data)
                saved += 1
            parts = []
            if saved:
                parts.append(f"保存 {saved} 人")
            if deleted:
                parts.append(f"删除 {deleted} 人")
            if parts:
                st.toast(f"✅ 已{'，'.join(parts)}", icon="👤")
            else:
                st.toast("⚠️ 未找到有效人物数据（缺少 name 字段）", icon="⚠️")
        except json.JSONDecodeError:
            st.toast("⚠️ 人物数据格式错误，需要 JSON 数组格式", icon="⚠️")
        except Exception as e:
            st.toast(f"⚠️ 人物操作失败：{e}", icon="⚠️")
        finally:
            if wf is not None:
                try:
                    wf.close()
                except Exception:
                    pass

    elif block_type == "chapter":
        ch_num = st.session_state.get("writing_chapter") or 1
        st.session_state[f"writing_pending_{novel_id}_{ch_num}"] = content
        try:
            wf = load_novel(novel_id)
            wf.update_chapter_content(ch_num, content, "AI 全局助手自动保存")
            wf.close()
            st.session_state[f"edit_content_{novel_id}_{ch_num}"] = content
        except Exception:
            pass
        st.session_state.page = "写作"

    elif block_type == "volume":
        wf = None
        saved = 0
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                data = [data]
            wf = load_novel(novel_id)
            for vol in data:
                if not vol.get("volume_number"):
                    continue
                wf.memory.global_mem.save_volume(vol)
                saved += 1
            if saved:
                st.toast(f"✅ 已保存 {saved} 卷", icon="📋")
                st.session_state.page = "大纲管理"
            else:
                st.toast("⚠️ 未找到有效卷数据（缺少 volume_number）", icon="⚠️")
        except json.JSONDecodeError:
            st.toast("⚠️ 卷大纲数据格式错误，需要 JSON 格式", icon="⚠️")
        except Exception as e:
            st.toast(f"⚠️ 卷大纲保存失败：{e}", icon="⚠️")
        finally:
            if wf is not None:
                try:
                    wf.close()
                except Exception:
                    pass

    elif block_type == "foreshadowing":
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                data = [data]
            db = get_db()
            saved = 0
            for fs in data:
                name = fs.get("name", "").strip()
                if not name:
                    continue
                f = Foreshadowing(
                    novel_id=novel_id,
                    name=name,
                    description=fs.get("description", ""),
                    importance=fs.get("importance", "medium"),
                    set_chapter=fs.get("set_chapter", 1),
                    collect_by_chapter=fs.get("collect_by_chapter"),
                    status="active",
                )
                db.add(f)
                saved += 1
            db.commit()
            db.close()
            if saved:
                st.toast(f"✅ 已添加 {saved} 条伏笔", icon="📌")
        except (json.JSONDecodeError, Exception):
            st.toast("⚠️ 伏笔数据格式错误", icon="⚠️")

    st.rerun()


# ──────────────────────────────────────────────────────────────
# 构建上下文辅助
# ──────────────────────────────────────────────────────────────

def _build_doc_context(novel_id: int) -> str:
    """根据当前页面和选中的内容，构建给 AI 的上下文文档"""
    parts = []
    page = st.session_state.get("page", "")

    # 大纲内容
    outline_text = st.session_state.get(f"outline_textarea_{novel_id}", "")
    if outline_text:
        parts.append(f"【当前大纲】\n```markdown\n{outline_text[:2000]}\n```")

    # 当前章节内容
    ch_num = st.session_state.get("writing_chapter")
    if ch_num:
        ch_content = st.session_state.get(f"edit_content_{novel_id}_{ch_num}", "")
        if ch_content:
            parts.append(f"【第{ch_num}章正文】\n```\n{ch_content[:2000]}\n```")

    # 设定文档
    settings_pending = st.session_state.get(f"settings_pending_{novel_id}") or {}
    for doc_type, content in settings_pending.items():
        if content:
            parts.append(f"【设定文档-{doc_type}】\n```markdown\n{content[:1500]}\n```")

    # 人物列表（供 AI 了解现有谁）
    try:
        db = get_db()
        chars = db.query(Character).filter(Character.novel_id == novel_id).all()
        db.close()
        if chars:
            names = [c.name for c in chars]
            parts.append(f"【已有人物】{', '.join(names[:20])}")
    except Exception:
        pass

    return "\n\n".join(parts)


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
    with st.container(height=280, border=True):
        if not history:
            st.caption(
                "💡 随时告诉我你的想法——大纲调整、人物设计、世界观完善、章节修改都可以。\n\n"
                "我会根据讨论内容，自动生成可直接写入系统的内容块；你也可以手动选择目标一键写入。"
            )
        for idx, msg in enumerate(history):
            with st.chat_message(msg["role"]):
                if msg["role"] == "assistant":
                    parts = _parse_response(msg["content"])
                    has_typed = _has_typed_blocks(msg["content"])

                    for p_idx, part in enumerate(parts):
                        if part["type"] == "text":
                            st.markdown(part["content"])
                        else:
                            block_type = part["type"]
                            label, _ = _APPLY_LABELS.get(block_type, ("应用", ""))
                            with st.container(border=True):
                                preview = part["content"][:150]
                                st.markdown(preview + ("…" if len(part["content"]) > 150 else ""))
                                if block_type == "chapter":
                                    st.caption("✅ 已自动写入编辑器")
                                    if st.button("↩️ 重新写入",
                                                 key=f"global_reapply_{novel_id}_{idx}_{p_idx}",
                                                 use_container_width=True):
                                        _apply_content("chapter", part["content"], novel_id)
                                else:
                                    if st.button(label,
                                                 key=f"global_apply_{novel_id}_{idx}_{p_idx}_{block_type}",
                                                 use_container_width=True, type="primary"):
                                        _apply_content(block_type, part["content"], novel_id)

                    # ── 兜底：AI 未使用类型化代码块时，显示「应用到…」选择器 ──
                    if not has_typed and len(msg["content"].strip()) > 80:
                        with st.container(border=True):
                            st.caption("📋 AI 未使用代码块格式，你可以手动选择写入目标")
                            fallback_targets = [("自动检测", "auto")] + _FALLBACK_TARGETS
                            fallback_labels = [t[0] for t in fallback_targets]
                            fb_sel = st.selectbox(
                                "选择写入目标",
                                fallback_labels,
                                key=f"fb_target_{novel_id}_{idx}",
                                label_visibility="collapsed"
                            )
                            if st.button("✅ 确认写入", key=f"fb_apply_{novel_id}_{idx}",
                                         use_container_width=True, type="primary"):
                                sel_idx = fallback_labels.index(fb_sel)
                                target = fallback_targets[sel_idx][1]
                                if target == "auto":
                                    # 自动检测：根据当前页面推断目标
                                    page = st.session_state.get("page", "")
                                    if page == "写作":
                                        target = "chapter"
                                    elif page == "大纲管理":
                                        target = "outline"
                                    elif page == "设定管理":
                                        target = "settings"
                                    else:
                                        target = "outline"
                                content_to_apply = _extract_substantive_content(msg["content"])
                                _apply_content(target, content_to_apply, novel_id)

                else:
                    st.markdown(msg["content"])

    if user_input := st.chat_input("和 AI 讨论…", key="global_chat_input"):
        history.append({"role": "user", "content": user_input})

        # 构建当前上下文 + 页面感知信息
        doc_ctx = _build_doc_context(novel_id)
        page = st.session_state.get("page", "")
        ch_num = st.session_state.get("writing_chapter") or 1
        if not doc_ctx or not doc_ctx.strip():
            if page == "大纲管理":
                doc_ctx = st.session_state.get(f"outline_textarea_{novel_id}", "")
            elif page == "写作":
                doc_ctx = st.session_state.get(f"edit_content_{novel_id}_{ch_num}", "")

        with st.spinner("思考中…"):
            try:
                # 读取项目级 Canvas 温度
                canvas_temp = None
                try:
                    db = get_db()
                    n = db.query(Novel).filter(Novel.id == novel_id).first()
                    db.close()
                    if n:
                        canvas_temp = n.temp_canvas
                except Exception:
                    pass
                from core.config import TEMPERATURE_CANVAS as _DEF_CANVAS_TEMP
                agent = CanvasAgent(novel_id=novel_id, role="global",
                                     temperature=canvas_temp if canvas_temp is not None else _DEF_CANVAS_TEMP)
                reply = agent.chat(history, document_content=doc_ctx,
                                   page=page, chapter_number=ch_num if page == "写作" else None)
                agent.close()
            except Exception as e:
                history.pop()
                st.session_state[chat_key] = history
                st.error(f"AI 出错：{e}")
                return

        history.append({"role": "assistant", "content": reply})
        st.session_state[chat_key] = history

        # chapter 块自动写入编辑器并保存
        for part in _parse_response(reply):
            if part["type"] == "chapter":
                ch_num = st.session_state.get("writing_chapter") or 1
                st.session_state[f"writing_pending_{novel_id}_{ch_num}"] = part["content"]
                try:
                    wf = load_novel(novel_id)
                    wf.update_chapter_content(ch_num, part["content"], "AI 全局助手自动保存")
                    wf.close()
                    st.session_state[f"edit_content_{novel_id}_{ch_num}"] = part["content"]
                except Exception:
                    pass
            elif part["type"] == "style":
                # 自动合并 style 偏好到小说风格档案
                try:
                    style_update = json.loads(part["content"])
                    if isinstance(style_update, dict) and style_update:
                        db = get_db()
                        novel = db.query(Novel).filter(Novel.id == novel_id).first()
                        if novel:
                            current = novel.get_style_profile()
                            for k, v in style_update.items():
                                if v and isinstance(v, str) and v.strip():
                                    current[k] = v.strip()
                            novel.set_style_profile(current)
                            db.commit()
                        db.close()
                except Exception:
                    pass

        st.rerun()

    if history:
        if st.button("🗑️ 清空", use_container_width=True, key="clear_global_chat"):
            st.session_state[chat_key] = []
            st.rerun()
