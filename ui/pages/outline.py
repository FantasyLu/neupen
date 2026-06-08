"""
大纲管理页面 — Canvas 布局
左：AI 协作聊天  |  右：大纲文档编辑器（整体大纲 / 章节大纲 / 导入文档）
"""
import json
import re

import streamlit as st

from core.models import get_db, Novel, Chapter, Volume, NovelOutline
from core.workflow import load_novel
from core.permissions import can_edit
from core.agents import OutlineAgent, CanvasAgent
from core.llm import DEFAULT_MODEL_ID
from ui.helpers import format_chapter_status, format_approval_badge
from ui.components.alerts import show_foreshadowing_alerts, show_outline_impact
from ui.components.model_selector import build_model_options


# ─────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────

def _outline_to_markdown(novel_outline: NovelOutline) -> str:
    """将 NovelOutline 结构化字段转为 Markdown 文档供编辑"""
    if not novel_outline:
        return ""
    parts = []
    if novel_outline.premise:
        parts.append(f"## 前提设定\n\n{novel_outline.premise}")
    if novel_outline.theme:
        parts.append(f"## 核心主题\n\n{novel_outline.theme}")
    if novel_outline.main_conflict:
        parts.append(f"## 主要矛盾\n\n{novel_outline.main_conflict}")
    if novel_outline.protagonist_arc:
        parts.append(f"## 主角弧光\n\n{novel_outline.protagonist_arc}")
    if novel_outline.ending_summary:
        parts.append(f"## 结局概要\n\n{novel_outline.ending_summary}")
    if novel_outline.story_structure:
        try:
            s = json.loads(novel_outline.story_structure)
            acts = "\n\n".join([
                f"**第一幕**：{s.get('act1', '')}",
                f"**第二幕**：{s.get('act2', '')}",
                f"**第三幕**：{s.get('act3', '')}",
            ])
            parts.append(f"## 三幕结构\n\n{acts}")
        except Exception:
            pass
    return "\n\n---\n\n".join(parts)


def _markdown_to_outline_fields(md: str) -> dict:
    """从 Markdown 文档提取 NovelOutline 字段（按 ## 标题映射，无需 AI）"""
    _HEADER_MAP = {
        "前提设定": "premise",
        "核心主题": "theme",
        "主要矛盾": "main_conflict",
        "主角弧光": "protagonist_arc",
        "结局概要": "ending_summary",
        "三幕结构": "__story_structure__",
    }
    sections: dict[str, list[str]] = {}
    current_key = None

    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            header = stripped[3:].strip()
            current_key = _HEADER_MAP.get(header)
        elif stripped == "---":
            current_key = None
        elif current_key is not None:
            sections.setdefault(current_key, []).append(line)

    result = {}
    for key, lines in sections.items():
        text = "\n".join(lines).strip()
        if not text:
            continue
        if key == "__story_structure__":
            act1 = act2 = act3 = ""
            for line in text.splitlines():
                if "**第一幕**：" in line:
                    act1 = line.split("**第一幕**：", 1)[1].strip()
                elif "**第二幕**：" in line:
                    act2 = line.split("**第二幕**：", 1)[1].strip()
                elif "**第三幕**：" in line:
                    act3 = line.split("**第三幕**：", 1)[1].strip()
            result["story_structure"] = json.dumps(
                {"act1": act1, "act2": act2, "act3": act3}, ensure_ascii=False
            )
        else:
            result[key] = text

    # 如果没有识别到任何结构化标题，把整个内容存为 premise
    if not result and md.strip():
        result["premise"] = md.strip()

    return result


def _save_outline_direct(novel_id: int, fields: dict):
    """直接将字段写入 NovelOutline，不经过 AI 解析"""
    db = get_db()
    outline = db.query(NovelOutline).filter(NovelOutline.novel_id == novel_id).first()
    if outline:
        for k, v in fields.items():
            if hasattr(outline, k):
                setattr(outline, k, v)
    else:
        outline = NovelOutline(novel_id=novel_id, **fields)
        db.add(outline)
    db.commit()
    db.close()


def _extract_suggestion(text: str) -> str | None:
    """从 AI 回复中提取最后一个 markdown/text 代码块作为建议内容"""
    matches = re.findall(r'```(?:markdown|text)?\n(.*?)```', text, re.DOTALL)
    return matches[-1].strip() if matches else None


# ─────────────────────────────────────────────────
# 主页面
# ─────────────────────────────────────────────────

def page_outline():
    novel_id = st.session_state.novel_id

    db = get_db()
    novel = db.query(Novel).filter(Novel.id == novel_id).first()
    chapters = db.query(Chapter).filter(Chapter.novel_id == novel_id).order_by(Chapter.chapter_number).all()
    volumes = db.query(Volume).filter(Volume.novel_id == novel_id).order_by(Volume.volume_number).all()
    novel_outline = db.query(NovelOutline).filter(NovelOutline.novel_id == novel_id).first()
    db.close()

    if not novel:
        st.error("项目不存在")
        return

    # 状态总览
    total = len(chapters)
    published = sum(1 for c in chapters if c.status == "published")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("总章数", total)
    c2.metric("已完成", published)
    c3.metric("总字数", f"{sum(c.word_count or 0 for c in chapters):,}")
    c4.metric("进度", f"{published/total*100:.0f}%" if total else "0%")

    st.divider()

    # Session state 键（按 novel_id 隔离）
    chat_key   = f"outline_chat_{novel_id}"
    pending_key = f"outline_pending_{novel_id}"
    textarea_key = f"outline_textarea_{novel_id}"

    if chat_key not in st.session_state:
        st.session_state[chat_key] = []
    if pending_key not in st.session_state:
        st.session_state[pending_key] = None
    # 初始化文本框内容（首次从 DB 加载）
    if textarea_key not in st.session_state:
        st.session_state[textarea_key] = _outline_to_markdown(novel_outline)

    # 有新的 AI 建议时刷新文本框
    if st.session_state[pending_key] is not None:
        st.session_state[textarea_key] = st.session_state[pending_key]
        st.session_state[pending_key] = None

    # Canvas 两栏布局
    col_chat, col_doc = st.columns([1, 2], gap="medium")

    # ─── 左栏：AI 聊天 ────────────────────────────────────
    with col_chat:
        st.markdown("#### 🤖 AI 大纲助手")

        # 聊天历史（固定高度可滚动）
        with st.container(height=520, border=True):
            history = st.session_state[chat_key]
            if not history:
                st.caption("💡 告诉 AI 你的故事方向，或让它帮你完善某个部分。\n\n"
                           "AI 提供新版大纲时，会出现「应用到编辑器」按钮。")
            for idx, msg in enumerate(history):
                with st.chat_message(msg["role"]):
                    text = msg["content"]
                    suggestion = _extract_suggestion(text) if msg["role"] == "assistant" else None
                    if suggestion:
                        display = re.sub(r'```(?:markdown|text)?\n.*?```', '', text, flags=re.DOTALL).strip()
                        if display:
                            st.markdown(display)
                        with st.container(border=True):
                            st.caption("📄 AI 生成了新版大纲")
                            st.markdown(suggestion[:300] + ("…" if len(suggestion) > 300 else ""))
                            if st.button("📋 应用到编辑器", key=f"apply_outline_{novel_id}_{idx}",
                                         use_container_width=True, type="primary"):
                                st.session_state[pending_key] = suggestion
                                st.rerun()
                    else:
                        st.markdown(text)

        if user_input := st.chat_input("和 AI 讨论大纲…", key="outline_chat_input"):
            history = st.session_state[chat_key]
            history.append({"role": "user", "content": user_input})
            current_doc = st.session_state.get(textarea_key, "")
            with st.spinner("AI 思考中…"):
                try:
                    agent = CanvasAgent(novel_id=novel_id, role="outline")
                    reply = agent.chat(history, document_content=current_doc)
                    agent.close()
                except Exception as e:
                    history.pop()
                    st.error(f"AI 出错：{e}")
                    st.stop()
            history.append({"role": "assistant", "content": reply})
            st.session_state[chat_key] = history
            st.rerun()

        if st.session_state[chat_key]:
            if st.button("🗑️ 清空对话", use_container_width=True, key="clear_outline_chat"):
                st.session_state[chat_key] = []
                st.rerun()

    # ─── 右栏：大纲文档 ──────────────────────────────────
    with col_doc:
        tab1, tab2, tab3 = st.tabs(["📖 整体大纲", "📋 章节大纲", "📄 导入文档"])

        # ── Tab1: 整体大纲（Markdown 编辑器）────────────────
        with tab1:
            btn_c1, btn_c2, btn_c3 = st.columns([2, 2, 1])
            with btn_c1:
                if st.button("💾 保存", type="primary", use_container_width=True,
                             disabled=not can_edit(novel_id)):
                    md = st.session_state.get(textarea_key, "").strip()
                    if md:
                        try:
                            fields = _markdown_to_outline_fields(md)
                            _save_outline_direct(novel_id, fields)
                            st.toast("✅ 大纲已保存", icon="💾")
                        except Exception as e:
                            st.error(f"保存失败：{e}")
            with btn_c2:
                if can_edit(novel_id) and st.button("🤖 重新生成大纲", use_container_width=True):
                    st.session_state["confirm_regen_outline"] = True
            with btn_c3:
                if st.button("↺ 从DB刷新", use_container_width=True, help="丢弃当前编辑，从数据库重新加载"):
                    db = get_db()
                    fresh = db.query(NovelOutline).filter(NovelOutline.novel_id == novel_id).first()
                    db.close()
                    st.session_state[textarea_key] = _outline_to_markdown(fresh)
                    st.rerun()

            st.text_area(
                "整体大纲（Markdown 格式，保存时 AI 会解析各字段）",
                key=textarea_key,
                height=480,
                placeholder="在此编写整体大纲，或通过左侧 AI 聊天生成。\n\n"
                            "示例格式：\n## 前提设定\n...\n\n## 核心主题\n...",
            )

            # 重新生成确认
            if st.session_state.get("confirm_regen_outline"):
                with st.container(border=True):
                    st.warning("⚠️ 重新生成大纲将覆盖所有现有章纲，是否继续？")
                    new_ch_count = st.number_input("总章节数", min_value=10, max_value=500,
                                                    value=max(total or 100, 10), step=10, key="regen_ch_count")
                    r1, r2 = st.columns(2)
                    if r1.button("确认重新生成", type="primary", key="confirm_regen_btn"):
                        progress_ph = st.empty()
                        workflow = load_novel(novel_id)
                        result = workflow.generate_outline(
                            total_chapters=new_ch_count,
                            progress_callback=lambda m: progress_ph.info(m)
                        )
                        workflow.close()
                        progress_ph.empty()
                        st.session_state["confirm_regen_outline"] = False
                        if result.success:
                            st.session_state.pop(textarea_key, None)
                            st.success(result.message)
                            st.rerun()
                        else:
                            st.error(result.message)
                    if r2.button("取消", key="cancel_regen_btn"):
                        st.session_state["confirm_regen_outline"] = False
                        st.rerun()

            # 卷大纲
            st.divider()
            vol_hdr1, vol_hdr2 = st.columns([3, 1])
            with vol_hdr1:
                st.markdown(f"**卷大纲（共 {len(volumes)} 卷）**")
            with vol_hdr2:
                if st.button("➕ 新建卷", use_container_width=True, disabled=not can_edit(novel_id)):
                    next_num = max((v.volume_number for v in volumes), default=0) + 1
                    last_end = max((v.end_chapter or 0 for v in volumes), default=0)
                    workflow = load_novel(novel_id)
                    workflow.memory.global_mem.save_volume({
                        "volume_number": next_num,
                        "title": f"第{next_num}卷",
                        "start_chapter": last_end + 1,
                        "end_chapter": last_end + 10,
                    })
                    workflow.close()
                    st.success(f"✅ 已新建第{next_num}卷")
                    st.rerun()

            if volumes:
                for vol in volumes:
                    with st.expander(
                        f"第{vol.volume_number}卷《{vol.title}》  第{vol.start_chapter}~{vol.end_chapter}章",
                        expanded=False
                    ):
                        with st.form(f"vol_edit_{vol.id}"):
                            ve_title = st.text_input("卷标题", value=vol.title or "")
                            ve_summary = st.text_area("简介", value=vol.summary or "", height=80)
                            ve_conflict = st.text_area("主要矛盾", value=vol.main_conflict or "", height=60)
                            ve_goal = st.text_area("本卷目标/主题", value=vol.arc_goal or "", height=60)
                            ve_c1, ve_c2 = st.columns(2)
                            with ve_c1:
                                ve_start = st.number_input("起始章节", min_value=1,
                                                            value=vol.start_chapter or 1, key=f"ve_start_{vol.id}")
                            with ve_c2:
                                ve_end = st.number_input("结束章节", min_value=1,
                                                          value=vol.end_chapter or 1, key=f"ve_end_{vol.id}")
                            ve_btn1, ve_btn2 = st.columns([3, 1])
                            with ve_btn1:
                                _save_vol = st.form_submit_button("💾 保存卷大纲", disabled=not can_edit(novel_id))
                            with ve_btn2:
                                _del_vol = st.form_submit_button("🗑️ 删除此卷", disabled=not can_edit(novel_id))
                            if _save_vol:
                                workflow = load_novel(novel_id)
                                workflow.memory.global_mem.save_volume({
                                    "volume_number": vol.volume_number,
                                    "title": ve_title.strip() or vol.title,
                                    "summary": ve_summary.strip(),
                                    "main_conflict": ve_conflict.strip(),
                                    "arc_goal": ve_goal.strip(),
                                    "start_chapter": ve_start,
                                    "end_chapter": ve_end,
                                })
                                workflow.close()
                                st.success("✅ 卷大纲已保存")
                                st.rerun()
                            if _del_vol:
                                db = get_db()
                                db.query(Volume).filter_by(id=vol.id).delete()
                                db.commit()
                                db.close()
                                st.success(f"✅ 第{vol.volume_number}卷已删除")
                                st.rerun()
            else:
                st.info("暂无卷大纲，点击「新建卷」手动创建，或通过 AI 生成大纲时自动创建")

        # ── Tab2: 章节大纲 ────────────────────────────────
        with tab2:
            # ── 批量生成章纲 ──────────────────────────────
            batch_result_key = f"batch_outline_result_{novel_id}"

            with st.expander("🤖 AI 批量生成章纲", expanded=False):
                if not can_edit(novel_id):
                    st.warning("仅主笔可以生成章纲")
                else:
                    ch_min = chapters[0].chapter_number if chapters else 1
                    total_ch = novel_outline.total_chapters if novel_outline and novel_outline.total_chapters else 0

                    rc1, rc2 = st.columns(2)
                    with rc1:
                        range_start = st.number_input(
                            "起始章节", min_value=1, value=ch_min,
                            step=1, key="batch_range_start"
                        )
                    with rc2:
                        default_end = min(ch_min + 9, total_ch) if total_ch else ch_min + 9
                        range_end = st.number_input(
                            "结束章节", min_value=range_start,
                            value=max(range_start, default_end), step=1, key="batch_range_end"
                        )

                    ch_count_label = range_end - range_start + 1
                    st.caption(f"将为第 {range_start}～{range_end} 章（共 {ch_count_label} 章）生成章纲。不存在的章节会自动创建。")

                    range_desc = st.text_area(
                        "描述这段剧情的内容和进展",
                        height=100,
                        placeholder="例如：这10章完成主角进入魔法学院后的适应期，经历入学考核、结交同伴、遭遇第一个强敌，最终在一次危机中展现出潜力，引起教授关注。",
                        key="batch_range_desc"
                    )

                    # ── 卷纲关联 ──
                    assign_vol = st.checkbox("📖 同时归入卷纲", key="batch_assign_vol")
                    _batch_vol_data = None
                    if assign_vol:
                        vol_options = [f"第{v.volume_number}卷《{v.title}》" for v in volumes]
                        vol_options.append("➕ 新建卷")
                        vol_choice = st.selectbox("选择卷", vol_options, key="batch_vol_choice")
                        if vol_choice == "➕ 新建卷":
                            bv_c1, bv_c2 = st.columns(2)
                            with bv_c1:
                                bv_title = st.text_input("新卷标题", key="batch_new_vol_title",
                                                          placeholder="如：学院篇")
                            with bv_c2:
                                next_vol_num = max((v.volume_number for v in volumes), default=0) + 1
                                bv_num = st.number_input("卷号", min_value=1, value=next_vol_num,
                                                          key="batch_new_vol_num")
                            bv_summary = st.text_area("卷简介（可选）", height=60, key="batch_new_vol_summary",
                                                       placeholder="概述本卷的主要内容和目标")
                            bv_conflict = st.text_input("本卷核心矛盾（可选）", key="batch_new_vol_conflict")
                            _batch_vol_data = {
                                "mode": "create",
                                "volume_number": int(bv_num),
                                "title": bv_title.strip() or f"第{bv_num}卷",
                                "summary": bv_summary.strip(),
                                "main_conflict": bv_conflict.strip(),
                            }
                        else:
                            sel_idx = vol_options.index(vol_choice)
                            _batch_vol_data = {"mode": "existing", "volume": volumes[sel_idx]}

                    gen_btn = st.button(
                        "✨ AI 生成章纲", type="primary", use_container_width=True,
                        disabled=not range_desc.strip()
                    )
                    if gen_btn and range_desc.strip():
                        # 先处理卷纲
                        if _batch_vol_data:
                            workflow = load_novel(novel_id)
                            if _batch_vol_data["mode"] == "create":
                                workflow.memory.global_mem.save_volume({
                                    "volume_number": _batch_vol_data["volume_number"],
                                    "title": _batch_vol_data["title"],
                                    "summary": _batch_vol_data["summary"],
                                    "main_conflict": _batch_vol_data["main_conflict"],
                                    "start_chapter": int(range_start),
                                    "end_chapter": int(range_end),
                                })
                                st.toast(f"✅ 第{_batch_vol_data['volume_number']}卷已创建")
                            else:
                                vol_obj = _batch_vol_data["volume"]
                                new_start = min(vol_obj.start_chapter or range_start, int(range_start))
                                new_end = max(vol_obj.end_chapter or range_end, int(range_end))
                                workflow.memory.global_mem.save_volume({
                                    "volume_number": vol_obj.volume_number,
                                    "title": vol_obj.title,
                                    "summary": vol_obj.summary or "",
                                    "main_conflict": vol_obj.main_conflict or "",
                                    "arc_goal": vol_obj.arc_goal or "",
                                    "start_chapter": new_start,
                                    "end_chapter": new_end,
                                })
                                st.toast(f"✅ 第{vol_obj.volume_number}卷章节范围已更新为 {new_start}~{new_end}")
                            workflow.close()

                        with st.spinner(f"AI 正在为第{range_start}~{range_end}章设计章纲…"):
                            try:
                                agent = OutlineAgent(novel_id)
                                result_list = agent.generate_chapter_range_outlines(
                                    int(range_start), int(range_end), range_desc.strip()
                                )
                                agent.close()
                                st.session_state[batch_result_key] = result_list
                            except Exception as e:
                                st.error(f"生成失败：{e}")

            # 批量生成结果预览 & 确认保存
            batch_result = st.session_state.get(batch_result_key)
            if batch_result:
                st.markdown("#### 📋 生成结果预览")
                st.caption(f"共 {len(batch_result)} 章，确认后将写入数据库")
                for item in batch_result:
                    ch_num = item.get("chapter_number", "?")
                    title  = item.get("title", "")
                    core   = item.get("outline_core_event", "")
                    st.markdown(f"**第{ch_num}章《{title}》** — {core[:80]}{'…' if len(core) > 80 else ''}")

                bc1, bc2 = st.columns(2)
                with bc1:
                    if st.button("✅ 确认保存", type="primary", use_container_width=True):
                        with st.spinner("保存中…"):
                            workflow = load_novel(novel_id)
                            save_result = workflow.batch_update_chapter_outlines(batch_result)
                            workflow.close()
                        if save_result.success:
                            st.success(f"✅ {save_result.message}")
                            st.session_state[batch_result_key] = None
                            st.rerun()
                        else:
                            st.error(save_result.message)
                with bc2:
                    if st.button("❌ 放弃", use_container_width=True):
                        st.session_state[batch_result_key] = None
                        st.rerun()

                st.divider()

            max_published = max((c.chapter_number for c in chapters if c.status == "published"), default=0)
            show_foreshadowing_alerts(novel_id, max_published)

            fc1, fc2 = st.columns([1, 2])
            with fc1:
                status_filter = st.selectbox(
                    "状态筛选", ["全部", "未完成", "已完成", "有章纲"], key="outline_filter"
                )
            with fc2:
                search_ch = st.text_input("搜索章节", placeholder="章节标题或关键词", key="outline_search")

            filtered = chapters
            if status_filter == "未完成":
                filtered = [c for c in chapters if c.status != "published"]
            elif status_filter == "已完成":
                filtered = [c for c in chapters if c.status == "published"]
            elif status_filter == "有章纲":
                filtered = [c for c in chapters if c.status != "outline_pending"]
            if search_ch:
                filtered = [c for c in filtered
                            if search_ch in (c.title or "") or search_ch in (c.outline_core_event or "")]

            if not filtered:
                st.info("没有匹配的章节")
            else:
                for chapter in filtered:
                    status_badge = format_chapter_status(chapter.status)
                    approval = format_approval_badge(chapter.approval_status) if chapter.status == "published" else ""
                    with st.expander(
                        f"第{chapter.chapter_number}章《{chapter.title or '未命名'}》 {status_badge} {approval}",
                        expanded=False
                    ):
                        tv, te = st.tabs(["查看", "编辑章纲"])

                        with tv:
                            vc1, vc2 = st.columns(2)
                            with vc1:
                                if chapter.outline_core_event:
                                    st.markdown(f"**核心事件：** {chapter.outline_core_event}")
                                if chapter.outline_conflict:
                                    st.markdown(f"**主要冲突：** {chapter.outline_conflict}")
                                if chapter.outline_scene:
                                    st.markdown(f"**场景：** {chapter.outline_scene}")
                                if chapter.outline_emotion:
                                    st.markdown(f"**情感基调：** {chapter.outline_emotion}")
                            with vc2:
                                chars_list = chapter.get_outline_characters()
                                if chars_list:
                                    st.markdown(f"**出场人物：** {', '.join(chars_list)}")
                                if chapter.outline_foreshadowing_set:
                                    try:
                                        fs = json.loads(chapter.outline_foreshadowing_set)
                                        if fs: st.markdown(f"**埋下伏笔：** {', '.join(fs)}")
                                    except Exception:
                                        pass
                                if chapter.outline_foreshadowing_collect:
                                    try:
                                        fc = json.loads(chapter.outline_foreshadowing_collect)
                                        if fc: st.markdown(f"**回收伏笔：** {', '.join(fc)}")
                                    except Exception:
                                        pass
                                if chapter.outline_ending:
                                    st.markdown(f"**结尾：** {chapter.outline_ending}")
                                if chapter.word_count:
                                    st.markdown(f"**字数：** {chapter.word_count:,}")

                            if chapter.status in ("outlined", "writing", "review_pending"):
                                if st.button(f"✍️ 写作第{chapter.chapter_number}章",
                                             key=f"goto_write_{chapter.chapter_number}"):
                                    st.session_state.writing_chapter = chapter.chapter_number
                                    st.session_state.page = "写作"
                                    st.rerun()

                        with te:
                            if chapter.status == "published":
                                st.warning("⚠️ 该章节已写完，修改章纲后将标记为「需重审」。")

                            with st.form(f"outline_edit_{chapter.chapter_number}"):
                                new_title   = st.text_input("章节标题", value=chapter.title or "")
                                new_core    = st.text_area("核心事件 ⚡️", value=chapter.outline_core_event or "", height=60)
                                new_conflict = st.text_area("主要冲突", value=chapter.outline_conflict or "", height=60)
                                new_scene   = st.text_area("场景设定", value=chapter.outline_scene or "", height=60)
                                new_emotion = st.text_input("情感基调", value=chapter.outline_emotion or "")
                                new_ending  = st.text_input("结尾方式 ⚡️", value=chapter.outline_ending or "")

                                def _fs_str(v):
                                    if not v: return ""
                                    try:
                                        return ", ".join(json.loads(v))
                                    except Exception:
                                        return v or ""

                                new_fs_set     = st.text_input("埋下伏笔（逗号分隔）", value=_fs_str(chapter.outline_foreshadowing_set))
                                new_fs_collect = st.text_input("回收伏笔（逗号分隔）", value=_fs_str(chapter.outline_foreshadowing_collect))
                                edit_reason    = st.text_input("修改原因（可选）")

                                if st.form_submit_button("💾 保存章纲并分析影响",
                                                         disabled=not can_edit(novel_id)):
                                    updates = {}
                                    if new_title.strip() != (chapter.title or ""):
                                        updates["title"] = new_title.strip()
                                    if new_core.strip() != (chapter.outline_core_event or ""):
                                        updates["outline_core_event"] = new_core.strip()
                                    if new_conflict.strip() != (chapter.outline_conflict or ""):
                                        updates["outline_conflict"] = new_conflict.strip()
                                    if new_scene.strip() != (chapter.outline_scene or ""):
                                        updates["outline_scene"] = new_scene.strip()
                                    if new_emotion.strip() != (chapter.outline_emotion or ""):
                                        updates["outline_emotion"] = new_emotion.strip()
                                    if new_ending.strip() != (chapter.outline_ending or ""):
                                        updates["outline_ending"] = new_ending.strip()

                                    def _to_list(s):
                                        return [x.strip() for x in s.split(",") if x.strip()]

                                    fss = _to_list(new_fs_set)
                                    if fss != json.loads(chapter.outline_foreshadowing_set or "[]"):
                                        updates["outline_foreshadowing_set"] = json.dumps(fss, ensure_ascii=False)
                                    fsc = _to_list(new_fs_collect)
                                    if fsc != json.loads(chapter.outline_foreshadowing_collect or "[]"):
                                        updates["outline_foreshadowing_collect"] = json.dumps(fsc, ensure_ascii=False)

                                    if not updates:
                                        st.info("没有检测到修改")
                                    else:
                                        with st.spinner("保存并分析影响…"):
                                            workflow = load_novel(novel_id)
                                            result = workflow.update_chapter_outline(
                                                chapter.chapter_number, updates, edit_reason
                                            )
                                            workflow.close()
                                        if result.success:
                                            st.success(f"✅ {result.message}")
                                            affected = result.data.get("affected_chapters", [])
                                            if affected:
                                                show_outline_impact(chapter.chapter_number, affected)
                                            st.rerun()
                                        else:
                                            st.error(result.message)

        # ── Tab3: 导入文档 ────────────────────────────────
        with tab3:
            st.markdown("### 📄 导入大纲/设定文档")
            st.caption("粘贴任意格式的大纲或设定文档，AI 识别内容并填入对应字段，已有数据不会被覆盖。")

            if not can_edit(novel_id):
                st.warning("仅主笔可以导入文档")
            else:
                options, label_map = build_model_options()
                default_idx = next((i for i, lbl in enumerate(options) if label_map[lbl] == DEFAULT_MODEL_ID), 0)
                parse_model_label = st.selectbox("解析模型", options, index=default_idx, key="parse_model_select")
                parse_model_id = label_map.get(parse_model_label, DEFAULT_MODEL_ID)

                doc_text = st.text_area(
                    "粘贴文档内容",
                    height=250,
                    placeholder="可以是任意格式：大纲、世界观、人物档案、章节列表……AI 自动识别。",
                    key="import_doc_text"
                )

                pc1, pc2 = st.columns([2, 1])
                with pc1:
                    parse_btn = st.button("🔍 AI 解析", type="primary",
                                          disabled=not doc_text.strip(), use_container_width=True)
                with pc2:
                    if st.button("清空", use_container_width=True):
                        st.session_state["import_doc_text"] = ""
                        st.session_state["import_parsed_result"] = None
                        st.rerun()

                if parse_btn and doc_text.strip():
                    with st.spinner("AI 正在解析…"):
                        try:
                            agent = OutlineAgent(novel_id, parse_model_id)
                            parsed = agent.parse_document(doc_text.strip())
                            agent.close()
                            st.session_state["import_parsed_result"] = parsed
                        except Exception as e:
                            st.error(f"解析失败：{e}")

                parsed_result = st.session_state.get("import_parsed_result")
                if parsed_result:
                    st.divider()
                    st.markdown("#### 解析结果预览")
                    total_outline = parsed_result.get("total_outline", {})
                    world_setting = parsed_result.get("world_setting", {})
                    characters    = parsed_result.get("characters", [])
                    ch_parsed     = parsed_result.get("chapters", [])
                    has_content   = False

                    if any(v for v in total_outline.values() if v):
                        has_content = True
                        with st.expander(f"📖 整体大纲（{sum(1 for v in total_outline.values() if v)} 字段）", expanded=True):
                            labels = {"premise": "前提设定", "theme": "核心主题", "main_conflict": "主要矛盾",
                                      "protagonist_arc": "主角弧光", "ending_summary": "结局概要", "story_structure": "三幕结构"}
                            for k, v in total_outline.items():
                                if v:
                                    st.markdown(f"**{labels.get(k, k)}：** {json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v}")

                    if world_setting:
                        has_content = True
                        with st.expander(f"🌍 世界观设定（{len(world_setting)} 条）", expanded=True):
                            for k, v in world_setting.items():
                                if v: st.markdown(f"**{k}：** {v}")

                    if characters:
                        has_content = True
                        with st.expander(f"👤 人物档案（{len(characters)} 个）", expanded=True):
                            for ch in characters:
                                desc = ch.get("personality") or ch.get("background", "")
                                st.markdown(f"- **{ch.get('name', '未命名')}**（{ch.get('role', '')}）：{desc[:60] if desc else ''}")

                    if ch_parsed:
                        has_content = True
                        with st.expander(f"📋 章节大纲（{len(ch_parsed)} 章）", expanded=False):
                            for ch in ch_parsed[:10]:
                                st.markdown(f"- 第{ch.get('chapter_number')}章《{ch.get('title', '')}》：{ch.get('outline_core_event', '')[:60]}")
                            if len(ch_parsed) > 10:
                                st.caption(f"…共 {len(ch_parsed)} 章，仅展示前 10")

                    if not has_content:
                        st.warning("未识别到有效内容，请检查文档或换用其他模型。")
                    else:
                        st.divider()
                        if st.button("✅ 确认导入", type="primary", use_container_width=True):
                            with st.spinner("写入数据库…"):
                                workflow = load_novel(novel_id)
                                result = workflow.import_document_data(parsed_result)
                                workflow.close()
                            if result.success:
                                st.success(f"✅ 导入完成：{result.message}")
                                st.session_state["import_parsed_result"] = None
                                st.session_state.pop(textarea_key, None)
                                st.rerun()
                            else:
                                st.error(result.message)
