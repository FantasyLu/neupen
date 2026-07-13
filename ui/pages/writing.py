"""
写作页面 — Canvas 布局
左：章节控制 + AI 写作助手  |  右：章节正文 + 审核 / 读者 / 历史
"""

import json
import re

import streamlit as st

from core.models import get_db, Chapter, ContentVersion, Novel
from core.workflow import load_novel
from core.permissions import can_edit, can_approve
from core.agents import CanvasAgent, ReviewerAgent, OutlineAgent, WriterAgent, PolisherAgent
from ui.helpers import (
    format_chapter_status,
    format_approval_badge,
    render_chapter_editor,
    render_stream_preview,
)
from ui.components.collaboration import render_chapter_comments, render_approval_status


def _extract_suggestion(text: str) -> str | None:
    # 匹配 chapter / markdown / text / 无语言标识 四种代码块
    matches = re.findall(r"```(?:chapter|markdown|text)?\n(.*?)```", text, re.DOTALL)
    return matches[-1].strip() if matches else None


def _auto_save(
    novel_id: int,
    ch_num: int,
    content: str,
    pending_key: str,
    text_key: str,
    label: str = "AI 审核建议自动保存",
) -> bool:
    """保存章节内容并清除 pending 状态，返回是否成功。"""
    try:
        wf = load_novel(novel_id)
        wf.update_chapter_content(ch_num, content, label)
        wf.close()
        st.session_state[pending_key] = None
        st.session_state[text_key] = content
        return True
    except Exception as e:
        st.warning(f"自动保存失败：{e}")
        return False


def _auto_execute_sync(novel_id: int, sync_checks: dict, chapter_number: int = None) -> int:
    """自动执行所有同步操作（无需用户逐条确认），返回成功执行的条目数。"""
    count = 0
    try:
        wf = load_novel(novel_id)
        for char in sync_checks.get("new_characters", []):
            try:
                char_data = {
                    "name": char.get("name", ""),
                    "role": char.get("role", ""),
                    "personality": char.get("personality", ""),
                    "background": char.get("background", ""),
                }
                if char.get("relationships"):
                    rels = char["relationships"]
                    char_data["relationships"] = (
                        json.dumps(rels, ensure_ascii=False)
                        if not isinstance(rels, str)
                        else rels
                    )
                wf.memory.global_mem.save_character(char_data)
                count += 1
            except Exception:
                pass
        for cu in sync_checks.get("character_updates", []):
            try:
                field = cu.get("field", "current_state")
                value = cu.get("new_value", "")
                if (
                    field in ("relationships", "abilities")
                    and value
                    and not isinstance(value, str)
                ):
                    value = json.dumps(value, ensure_ascii=False)
                wf.memory.global_mem.save_character(
                    {
                        "name": cu.get("name", ""),
                        field: value,
                    }
                )
                count += 1
            except Exception:
                pass
        # timeline_events → TimelineEvent 表
        for te in sync_checks.get("timeline_events", []):
            try:
                involved = te.get("characters_involved", [])
                event_data = {
                    "event_name": te.get("event_name", ""),
                    "event_description": te.get("event_description", ""),
                    "in_story_time": te.get("in_story_time", ""),
                    "characters_involved": (
                        json.dumps(involved, ensure_ascii=False)
                        if isinstance(involved, list)
                        else involved
                    ),
                    "impact": te.get("impact", ""),
                }
                wf.memory.global_mem.add_timeline_event(event_data)
                count += 1
            except Exception:
                pass
        # foreshadowing_updates → Foreshadowing 表（只新增，不修改已有）
        for fs in sync_checks.get("foreshadowing_updates", []):
            try:
                fs_data = {
                    "name": fs.get("name", ""),
                    "description": fs.get("description", ""),
                    "importance": fs.get("importance", "medium"),
                    "collect_by_chapter": fs.get("collect_by_chapter") or None,
                    "notes": fs.get("notes", "") or None,
                    "status": "active",
                    "set_chapter": chapter_number,
                }
                wf.memory.global_mem.save_foreshadowing(fs_data)
                count += 1
            except Exception:
                pass
        # outline_updates → 纯替换（不追加），仅处理 merged_content
        for upd in sync_checks.get("outline_updates", []):
            try:
                field = upd.get("field", "")
                merged = upd.get("merged_content", "")
                if not field or not merged:
                    continue
                wf.memory.global_mem.save_outline({field: merged.strip()})
                count += 1
            except Exception:
                pass
        wf.close()
    except Exception:
        pass
    for ws in sync_checks.get("world_setting_updates", []):
        try:
            db = get_db()
            novel_obj = db.query(Novel).filter(Novel.id == novel_id).first()
            world = novel_obj.get_world_setting()
            world[ws.get("key", "")] = ws.get("value", "")
            novel_obj.set_world_setting(world)
            db.commit()
            db.close()
            count += 1
        except Exception:
            pass
    return count


def page_writing():
    novel_id = st.session_state.novel_id

    db = get_db()
    chapters = (
        db.query(Chapter)
        .filter(Chapter.novel_id == novel_id)
        .order_by(Chapter.chapter_number)
        .all()
    )
    db.close()

    if not chapters:
        st.warning("请先在「大纲管理」页面生成章纲")
        return

    # Canvas 两栏布局
    col_ctrl, col_content = st.columns([1, 2], gap="medium")

    # ─── 左栏：控制面板 + AI 聊天 ────────────────────────
    with col_ctrl:
        st.markdown("#### 选择章节")
        chapter_options = {
            f"第{c.chapter_number}章 《{c.title or '未命名'}》 {format_chapter_status(c.status)}": c.chapter_number
            for c in chapters
        }
        default_idx = 0
        if st.session_state.writing_chapter:
            for i, num in enumerate(chapter_options.values()):
                if num == st.session_state.writing_chapter:
                    default_idx = i
                    break

        selected_label = st.selectbox(
            "章节",
            list(chapter_options.keys()),
            index=default_idx,
            label_visibility="collapsed",
        )
        selected_ch_num = chapter_options[selected_label]
        st.session_state.writing_chapter = selected_ch_num

        db = get_db()
        selected_ch = (
            db.query(Chapter)
            .filter(
                Chapter.novel_id == novel_id, Chapter.chapter_number == selected_ch_num
            )
            .first()
        )
        db.close()

        # 全局助手使用的 session 键
        pending_key = f"writing_pending_{novel_id}_{selected_ch_num}"
        text_key = f"edit_content_{novel_id}_{selected_ch_num}"

        # 章节信息
        if selected_ch:
            with st.container(border=True):
                st.markdown(
                    f"**第{selected_ch_num}章《{selected_ch.title or '未命名'}》**"
                )
                if selected_ch.outline_core_event:
                    st.caption(f"核心事件：{selected_ch.outline_core_event[:80]}")
                meta = []
                if selected_ch.word_count:
                    meta.append(f"{selected_ch.word_count:,}字")
                if selected_ch.review_score:
                    meta.append(f"评分 {selected_ch.review_score:.1f}/10")
                if meta:
                    st.caption(" · ".join(meta))

        # Token 消耗统计
        db = get_db()
        novel_obj = db.query(Novel).filter(Novel.id == novel_id).first()
        db.close()
        if novel_obj and (
            novel_obj.total_input_tokens or novel_obj.total_output_tokens
        ):
            input_t = novel_obj.total_input_tokens or 0
            output_t = novel_obj.total_output_tokens or 0
            total_t = input_t + output_t
            with st.container(border=True):
                st.caption("📊 Token 消耗统计")
                st.caption(f"输入 {input_t:,} · 输出 {output_t:,} · 合计 {total_t:,}")
                st.caption("⚠️ 流式生成部分为估算值，统计仅供参考，非精确计费数据")

        st.divider()
        st.markdown("#### 写作参数")
        word_target = st.slider("目标字数", 1000, 6000, 3000, step=500)
        word_tolerance = (
            st.slider(
                "字数容差",
                10,
                50,
                30,
                step=5,
                format="%d%%",
                help="允许实际字数偏离目标字数的比例。设为30%时，目标3000字则允许2100~3900字。",
            )
            / 100
        )
        auto_polish = st.toggle("自动润色", value=True)
        agentic_write = st.toggle(
            "🧠 Agentic 写作",
            value=True,
            help="启用后 Agent 会自主决定需要查询哪些角色档案、伏笔、历史片段，再开始写作。质量更高但速度较慢。",
        )

        wc1, wc2 = st.columns(2)
        write_btn = wc1.button(
            "🚀 生成本章",
            width="stretch",
            type="primary",
            disabled=(
                st.session_state.is_writing
                or st.session_state.batch_writing
                or not can_edit(novel_id)
            ),
        )
        is_published = selected_ch and selected_ch.status == "published"
        rewrite_btn = wc2.button(
            "🔄 重新生成",
            width="stretch",
            disabled=(
                not is_published
                or st.session_state.is_writing
                or st.session_state.batch_writing
                or not can_edit(novel_id)
            ),
        )

        st.divider()

        # 批量写作（折叠）
        st.divider()
        with st.expander("📦 批量写作", expanded=False):
            writable = [
                c
                for c in chapters
                if c.status in ("outlined", "writing", "review_pending", "reviewed")
            ]
            if not writable:
                st.info("没有待写作的章节")
            else:
                st.caption(f"共 {len(writable)} 个待写章节")
                bb1, bb2 = st.columns(2)
                with bb1:
                    batch_start = st.selectbox(
                        "起始章节",
                        [c.chapter_number for c in writable],
                        format_func=lambda n: f"第{n}章",
                        key="batch_start",
                    )
                with bb2:
                    end_opts = [
                        c.chapter_number
                        for c in writable
                        if c.chapter_number >= batch_start
                    ]
                    batch_end = st.selectbox(
                        "结束章节",
                        end_opts,
                        index=min(len(end_opts) - 1, 4),
                        format_func=lambda n: f"第{n}章",
                        key="batch_end",
                    )
                selected_range = [
                    c.chapter_number
                    for c in writable
                    if batch_start <= c.chapter_number <= batch_end
                ]
                if selected_range:
                    st.caption(
                        f"将写作 **{len(selected_range)}** 章（第{selected_range[0]}~{selected_range[-1]}章）"
                    )
                    bp1, bp2 = st.columns(2)
                    with bp1:
                        batch_words = st.slider(
                            "每章目标字数",
                            1000,
                            6000,
                            3000,
                            step=500,
                            key="batch_words",
                        )
                    with bp2:
                        batch_tolerance = (
                            st.slider(
                                "字数容差",
                                10,
                                50,
                                30,
                                step=5,
                                format="%d%%",
                                key="batch_tolerance",
                                help="允许实际字数偏离目标字数的比例。",
                            )
                            / 100
                        )
                    bp3, bp4 = st.columns(2)
                    with bp3:
                        batch_polish = st.toggle(
                            "自动润色", value=True, key="batch_polish"
                        )
                    with bp4:
                        batch_auto_sync = st.toggle(
                            "自动同步大纲/人物",
                            value=True,
                            key="batch_auto_sync",
                            help="写完每章后自动将 AI 检测到的人物状态变化、大纲更新等同步入库，无需手动逐条确认",
                        )
                    batch_agentic = st.toggle(
                        "🧠 Agentic 写作",
                        value=True,
                        key="batch_agentic",
                        help="启用后每章均走 Agentic 模式：Agent 自主查询所需信息后写作，写完后自动进行 Agentic 审核（含重试修正）。质量更高但耗时更长。",
                    )
                    btn_col1, btn_col2 = st.columns([3, 1])
                    with btn_col1:
                        start_batch = st.button(
                            "🚀 开始批量写作",
                            width="stretch",
                            type="primary",
                            disabled=(
                                st.session_state.is_writing
                                or st.session_state.batch_writing
                                or not can_edit(novel_id)
                            ),
                        )
                    with btn_col2:
                        if st.button(
                            "⏹ 停止",
                            width="stretch",
                            disabled=not st.session_state.batch_writing,
                        ):
                            st.session_state.batch_stop_requested = True
                    if start_batch:
                        st.session_state.batch_writing = True
                        st.session_state.batch_stop_requested = False
                        workflow = load_novel(novel_id)
                        with st.status(
                            f"批量写作中 (0/{len(selected_range)})…", expanded=True
                        ) as batch_status:
                            for idx, ch_num in enumerate(selected_range):
                                if st.session_state.get("batch_stop_requested"):
                                    batch_status.update(
                                        label="已停止批量写作", state="error"
                                    )
                                    st.write("⏹ 用户已停止批量写作")
                                    break
                                batch_status.update(
                                    label=f"批量写作中 ({idx}/{len(selected_range)})…"
                                )
                                st.write(
                                    f"**[{idx + 1}/{len(selected_range)}] 正在生成第{ch_num}章…**"
                                )

                                ph = st.empty()
                                if batch_agentic:
                                    # ── Agentic 批量写作路径 ──────────────────
                                    try:
                                        _writer = WriterAgent(novel_id)
                                        _batch_content = _writer.write_chapter_agentic(
                                            chapter_number=ch_num,
                                            word_target=batch_words,
                                            word_count_tolerance=batch_tolerance,
                                        )
                                        ph.caption(
                                            f"✍️ 正文生成完成（{len(_batch_content)} 字），进入审核…"
                                        )

                                        _reviewer = ReviewerAgent(novel_id)
                                        _review_res = _reviewer.review_chapter_agentic(
                                            chapter_number=ch_num,
                                            content=_batch_content,
                                            writer_agent=_writer,
                                        )
                                        _reviewer.close()
                                        _final_content = _review_res.get(
                                            "content", _batch_content
                                        )
                                        _writer.close()

                                        ph.caption("💾 保存并润色…")
                                        result = workflow.write_and_review_chapter(
                                            chapter_number=ch_num,
                                            word_target=batch_words,
                                            word_count_tolerance=batch_tolerance,
                                            auto_polish=batch_polish,
                                            progress_callback=lambda m, _p=ph: (
                                                _p.caption(m)
                                            ),
                                            prefilled_content=_final_content,
                                            skip_review=True,  # Agentic 已完成审核，跳过标准审核
                                        )
                                    except Exception as _e:
                                        ph.empty()
                                        st.write(
                                            f"❌ 第{ch_num}章 Agentic 写作失败：{_e}"
                                        )
                                        continue
                                else:
                                    # ── 标准批量写作路径 ──────────────────────
                                    result = workflow.write_and_review_chapter(
                                        chapter_number=ch_num,
                                        word_target=batch_words,
                                        word_count_tolerance=batch_tolerance,
                                        auto_polish=batch_polish,
                                        progress_callback=lambda m, _p=ph: _p.caption(
                                            m
                                        ),
                                    )
                                ph.empty()
                                if result.success:
                                    sync_checks = result.data.get("sync_checks", {})
                                    sync_count = (
                                        (
                                            len(sync_checks.get("new_characters", []))
                                            + len(
                                                sync_checks.get("character_updates", [])
                                            )
                                            + len(
                                                sync_checks.get("timeline_events", [])
                                            )
                                            + len(
                                                sync_checks.get(
                                                    "foreshadowing_updates", []
                                                )
                                            )
                                            + len(
                                                sync_checks.get("outline_updates", [])
                                            )
                                            + len(
                                                sync_checks.get(
                                                    "world_setting_updates", []
                                                )
                                            )
                                        )
                                        if sync_checks
                                        else 0
                                    )
                                    if sync_checks and sync_count:
                                        if batch_auto_sync:
                                            done = _auto_execute_sync(
                                                novel_id, sync_checks, chapter_number=ch_num
                                            )
                                            st.session_state[
                                                f"writing_sync_{novel_id}_{ch_num}"
                                            ] = {"_done": True}
                                            sync_hint = f" · ✅已自动同步{done}条"
                                        else:
                                            st.session_state[
                                                f"writing_sync_{novel_id}_{ch_num}"
                                            ] = sync_checks
                                            sync_hint = (
                                                f" · 🔄{sync_count}条同步建议待确认"
                                            )
                                    else:
                                        sync_hint = ""
                                    st.write(
                                        f"✅ 第{ch_num}章完成 · {result.data.get('word_count', 0):,}字 · 评分 {result.data.get('overall_score', 0):.1f}/10{sync_hint}"
                                    )
                                else:
                                    st.write(f"❌ 第{ch_num}章失败：{result.message}")
                            batch_status.update(
                                label="批量写作完成！", state="complete"
                            )
                        workflow.close()
                        st.session_state.batch_writing = False
                        st.session_state.batch_stop_requested = False
                        st.rerun()

        with st.expander("🔙 批量回退状态", expanded=False):
            published_chs = [c for c in chapters if c.status == "published"]
            if not published_chs:
                st.info("没有已完成的章节")
            else:
                st.caption(f"共 {len(published_chs)} 个已完成章节")
                rv1, rv2 = st.columns(2)
                with rv1:
                    rv_start = st.selectbox(
                        "起始章节",
                        [c.chapter_number for c in published_chs],
                        format_func=lambda n: f"第{n}章",
                        key="revert_start",
                    )
                with rv2:
                    rv_end_opts = [
                        c.chapter_number
                        for c in published_chs
                        if c.chapter_number >= rv_start
                    ]
                    rv_end = st.selectbox(
                        "结束章节",
                        rv_end_opts,
                        index=len(rv_end_opts) - 1,
                        format_func=lambda n: f"第{n}章",
                        key="revert_end",
                    )
                rv_status_options = {
                    "outlined": "已有章纲（可重新生成）",
                    "writing": "写作中",
                    "review_pending": "待审核",
                    "reviewed": "已审核（可重新润色）",
                }
                rv_target = st.selectbox(
                    "回退到",
                    list(rv_status_options.keys()),
                    format_func=lambda x: rv_status_options[x],
                    key="batch_revert_target",
                )
                rv_range = [
                    c for c in published_chs if rv_start <= c.chapter_number <= rv_end
                ]
                st.caption(
                    f"将回退 **{len(rv_range)}** 章（第{rv_start}~{rv_end}章）→ {rv_status_options[rv_target]}"
                )
                if st.button(
                    f"确认批量回退（{len(rv_range)}章）",
                    width="stretch",
                    disabled=not can_edit(novel_id),
                ):
                    db = get_db()
                    for c in rv_range:
                        ch_obj = db.query(Chapter).filter_by(id=c.id).first()
                        if ch_obj:
                            ch_obj.status = rv_target
                    db.commit()
                    db.close()
                    st.success(f"✅ 已回退 {len(rv_range)} 章")
                    st.rerun()

        with st.expander("📝 重新生成章节摘要", expanded=False):
            st.caption(
                "为已有正文的章节重新生成详细摘要（300-800字）。"
                "新摘要将用于后续章节写作时的前情参考，替代原始正文注入，显著节省 token 消耗。"
            )
            st.warning(
                "⚠️ **请谨慎使用**\n\n"
                "此操作会为每章调用一次 LLM 生成摘要，**消耗 API 额度**。"
                "如果你的小说已完成很多章（如 50+ 章），总 token 消耗将相当可观。\n\n"
                "**建议**：仅在首次升级时执行一次，或当你发现特定章节摘要质量不佳时选择性地重新生成。"
                "正常情况下，每章写完时已自动生成详细摘要，无需频繁手动操作。"
            )
            # 可选章节列表
            eligible = [c for c in chapters if c.content]
            if not eligible:
                st.info("没有需要生成摘要的章节")
            else:
                need_summary = [
                    c for c in eligible if not c.summary or len(c.summary) < 150
                ]
                has_summary = [
                    c for c in eligible if c.summary and len(c.summary) >= 150
                ]
                st.caption(
                    f"共 **{len(eligible)}** 个已完成章节"
                    + (
                        f"，其中 **{len(need_summary)}** 个缺少/偏短摘要"
                        if need_summary
                        else ""
                    )
                )
                # 多选：默认勾选缺少/偏短摘要的章节
                ch_options = {
                    f"第{c.chapter_number}章《{c.title or '未命名'}》"
                    + (
                        " ⚠️缺摘要" if not c.summary or len(c.summary) < 150 else ""
                    ): c.chapter_number
                    for c in eligible
                }
                default_selected = [
                    f"第{c.chapter_number}章《{c.title or '未命名'}》"
                    + (" ⚠️缺摘要" if not c.summary or len(c.summary) < 150 else "")
                    for c in (need_summary or eligible)  # 如果没有缺摘要的，默认全选
                ]
                selected_labels = st.multiselect(
                    "选择要重新生成摘要的章节",
                    options=list(ch_options.keys()),
                    default=default_selected,
                    label_visibility="collapsed",
                )
                selected_nums = [ch_options[l] for l in selected_labels]

                is_busy = (
                    st.session_state.is_writing
                    or st.session_state.batch_writing
                    or not can_edit(novel_id)
                )

                c1, c2 = st.columns(2)
                with c1:
                    if st.button(
                        "🔄 重新生成选中",
                        width="stretch",
                        type="secondary",
                        disabled=(is_busy or not selected_nums),
                    ):
                        workflow = load_novel(novel_id)
                        try:
                            with st.status(
                                f"正在生成 {len(selected_nums)} 章摘要…", expanded=True
                            ) as ss:

                                def sp(msg):
                                    st.write(msg)

                                result = workflow.writer_agent.regenerate_all_summaries(
                                    progress_callback=sp, chapter_numbers=selected_nums
                                )
                                ss.update(
                                    label=f"完成：成功 {result['success']}，失败 {result['failed']}，跳过 {result['skipped']}",
                                    state="complete"
                                    if result["failed"] == 0
                                    else "error",
                                )
                        finally:
                            workflow.close()
                        st.rerun()
                with c2:
                    if st.button(
                        "🔄 全部重新生成",
                        width="stretch",
                        type="secondary",
                        disabled=is_busy,
                    ):
                        workflow = load_novel(novel_id)
                        try:
                            with st.status(
                                "正在生成全部章节摘要…", expanded=True
                            ) as ss:

                                def sp(msg):
                                    st.write(msg)

                                result = workflow.writer_agent.regenerate_all_summaries(
                                    progress_callback=sp
                                )
                                ss.update(
                                    label=f"完成：成功 {result['success']}，失败 {result['failed']}，跳过 {result['skipped']}",
                                    state="complete"
                                    if result["failed"] == 0
                                    else "error",
                                )
                        finally:
                            workflow.close()
                        st.rerun()

    # ─── 右栏：章节内容 ──────────────────────────────────
    with col_content:
        # ── 流式预览区（写作时显示，写完后消失）──────────────────────────
        stream_preview_area = st.empty()
        status_area = st.empty()

        if write_btn or rewrite_btn:
            st.session_state.is_writing = True

            def stream_cb(chunk: str):
                nonlocal streaming_text
                streaming_text += chunk
                with stream_preview_area.container():
                    st.markdown(
                        f"""<div style="font-size:0.68rem;letter-spacing:0.14em;color:#c9a96e;
                        text-transform:uppercase;margin-bottom:0.4rem;">
                        正在生成 · 第{selected_ch_num}章</div>""",
                        unsafe_allow_html=True,
                    )
                    render_stream_preview(streaming_text, selected_ch_num, height=420)

            def progress_cb(msg: str):
                status_area.info(msg)

            streaming_text = ""
            try:
                workflow = load_novel(novel_id)

                if agentic_write:
                    # ── Agentic 写作路径 ──────────────────────────────────
                    status_area.info(f"🧠 Agentic 模式：Agent 正在思考需要哪些信息…")

                    _gen_log: list[str] = []  # 收集本次生成过程日志，完成后存入 session_state

                    def _handle_event(event_type: str, data: dict, agentic_status, stage: str = "write"):
                        """处理单个 agentic 事件，更新 st.status 内容并收集日志。
                        stage: "write" 写作阶段 / "review" 审核阶段，用于区分 reasoning 存储 key。
                        """
                        from core.agentic_loop import StepEvent
                        if event_type == StepEvent.THINKING:
                            thinking_text = data.get("thinking_text", "").strip()
                            reasoning = data.get("reasoning", "").strip()
                            tool_count = data.get("tool_count", 0)
                            header = f"💭 **思考**（准备调用 {tool_count} 个工具）" if tool_count else "💭 **思考**"
                            agentic_status.write(header)
                            # reasoning 直接写入 status 主体，截取前 300 字避免过长
                            if reasoning:
                                preview = reasoning[:300] + ("…" if len(reasoning) > 300 else "")
                                agentic_status.write(preview)
                                _gen_log.append(f"{header}\n{preview}")
                            elif thinking_text:
                                agentic_status.write(thinking_text)
                                _gen_log.append(f"{header}\n{thinking_text}")
                            else:
                                _gen_log.append(header)
                        elif event_type == StepEvent.TOOL_CALL:
                            tool = data.get("tool", "")
                            args = data.get("args", {})
                            idx = data.get("call_index", "?")
                            limit = data.get("max_calls", 15)
                            args_str = "、".join(f"{k}={v}" for k, v in args.items())
                            msg = f"**[{idx}/{limit}]** 查询 `{tool}` — {args_str}"
                            agentic_status.write(msg)
                            status_area.info(f"🔍 正在查询：{tool}（{idx}/{limit}）")
                            _gen_log.append(msg)
                        elif event_type == StepEvent.TOOL_RESULT:
                            result_len = data.get("result_length", 0)
                            msg = f"→ 获取 {result_len} 字"
                            agentic_status.write(msg)
                            _gen_log.append(msg)
                        elif event_type == StepEvent.DUPLICATE_SKIP:
                            tool = data.get("tool", "")
                            msg = f"*(重复查询 `{tool}`，使用缓存)*"
                            agentic_status.write(msg)
                            _gen_log.append(msg)
                        elif event_type == StepEvent.STALL_DETECTED:
                            agentic_status.write("⚠️ 检测到重复查询，开始生成正文…")
                            status_area.info("✍️ 信息收集完毕，开始生成正文…")
                            _gen_log.append("⚠️ 检测到重复查询，开始生成正文…")
                        elif event_type == StepEvent.MAX_CALLS_REACHED:
                            agentic_status.write("📊 已达查询上限，开始生成正文…")
                            status_area.info("✍️ 查询完成，开始生成正文…")
                            _gen_log.append("📊 已达查询上限，开始生成正文…")
                        elif event_type == StepEvent.BUDGET_EXCEEDED:
                            agentic_status.write("⚠️ Token 预算接近上限，开始生成正文…")
                            status_area.info("✍️ 开始生成正文…")
                            _gen_log.append("⚠️ Token 预算接近上限，开始生成正文…")
                        elif event_type == StepEvent.FINAL_OUTPUT:
                            word_count = len(data.get("text", ""))
                            msg = f"✍️ 正文生成完成（{word_count} 字），进入字数校验…"
                            agentic_status.write(msg)
                            status_area.info(msg)
                            _gen_log.append(msg)
                            all_reasoning = data.get("all_reasoning", "").strip()
                            if all_reasoning:
                                if stage == "write":
                                    st.session_state[f"write_reasoning_{novel_id}_{selected_ch_num}"] = all_reasoning
                                else:
                                    st.session_state[f"review_reasoning_agentic_{novel_id}_{selected_ch_num}"] = all_reasoning
                        elif event_type == StepEvent.STATUS_MSG:
                            msg = data.get("msg", "")
                            agentic_status.write(msg)
                            status_area.info(msg)
                            _gen_log.append(msg)

                    with st.status("🤔 Agent 思考过程", expanded=True) as agentic_status:
                        agentic_status.write("**写作阶段**")
                        writer = WriterAgent(novel_id)

                        # ── generator 迭代：每个事件立即刷新 UI ──────────────
                        content = ""
                        for event_type, data in writer.write_chapter_agentic_gen(
                            chapter_number=selected_ch_num,
                            word_target=word_target,
                            word_count_tolerance=word_tolerance,
                        ):
                            _handle_event(event_type, data, agentic_status, stage="write")
                            if event_type == "content_ready":
                                content = data.get("content", "")

                        status_area.info(
                            f"✅ 写作完成（{len(content)} 字），进入 Agentic 审核…"
                        )

                        # 写作阶段结束后立即保存 last_reasoning（兜底：FINAL_OUTPUT 未携带 reasoning 时）
                        _wr_key = f"write_reasoning_{novel_id}_{selected_ch_num}"
                        if not st.session_state.get(_wr_key) and writer.llm.last_reasoning:
                            st.session_state[_wr_key] = writer.llm.last_reasoning

                        agentic_status.write("---")
                        agentic_status.write("**审核阶段**")

                        # ── Agentic 审核 generator ────────────────────────────
                        reviewer = ReviewerAgent(novel_id)
                        review_result = {}
                        for event_type, data in reviewer.review_chapter_agentic_gen(
                            chapter_number=selected_ch_num,
                            content=content,
                            writer_agent=writer,
                        ):
                            _handle_event(event_type, data, agentic_status, stage="review")
                            if event_type == "content_ready":
                                review_result = data.get("result", {})
                        reviewer.close()

                        final_content = review_result.get("content", content)
                        review_rounds = review_result.get("rounds", 1)
                        review_passed = review_result.get("passed", False)
                        review_score = review_result.get("final_score", 0.0)

                        result_label = "✅ PASS" if review_passed else "❌ REJECT（已用完重试次数）"
                        agentic_status.write(
                            f"**审核完成**：{result_label} | 得分 {review_score}/10 | 共 {review_rounds} 轮"
                        )
                        _gen_log.append(f"**审核完成**：{result_label} | 得分 {review_score}/10 | 共 {review_rounds} 轮")
                        agentic_status.update(
                            label=f"🤔 Agent 思考过程（{'通过' if review_passed else '未通过'}，{review_score}/10）",
                            state="complete" if review_passed else "error",
                            expanded=False,
                        )

                    writer.close()

                    # 保存 Agentic 生成过程日志到 session_state（rerun 后仍可展示）
                    st.session_state[f"gen_log_{novel_id}_{selected_ch_num}"] = _gen_log
                    st.session_state[f"gen_review_agentic_{novel_id}_{selected_ch_num}"] = {
                        "passed": review_passed,
                        "score": review_score,
                        "rounds": review_rounds,
                    }

                    # 将最终内容交给 workflow 保存（跳过写作步骤+标准审核，直接保存+润色）
                    status_area.info("💾 正在保存并润色…")
                    result = workflow.write_and_review_chapter(
                        chapter_number=selected_ch_num,
                        word_target=word_target,
                        word_count_tolerance=word_tolerance,
                        auto_polish=auto_polish,
                        progress_callback=progress_cb,
                        prefilled_content=final_content,  # 传入已审核/修正的内容
                        skip_review=True,  # Agentic 已完成审核，跳过标准审核避免双重执行
                    )

                else:
                    # ── 标准流式写作路径 ──────────────────────────────────
                    status_area.info(f"✍️ 正在生成第{selected_ch_num}章…")
                    result = workflow.write_and_review_chapter(
                        chapter_number=selected_ch_num,
                        word_target=word_target,
                        word_count_tolerance=word_tolerance,
                        auto_polish=auto_polish,
                        progress_callback=progress_cb,
                        stream_callback=stream_cb,
                    )
                    # 标准路径：流式时 last_reasoning 为空，非流式时有值
                    wr = workflow.writer_agent.llm.last_reasoning
                    if wr:
                        st.session_state[f"write_reasoning_{novel_id}_{selected_ch_num}"] = wr

                workflow.close()
                st.session_state.is_writing = False

                if result.success:
                    status_area.empty()
                    stream_preview_area.empty()
                    score = result.data.get("overall_score", 0)
                    passed = result.data.get("review_passed", True)
                    report = result.data.get("review_report", {})

                    # 保存生成结果到 session_state，rerun 后在固定区域持续展示
                    st.session_state[f"gen_result_{novel_id}_{selected_ch_num}"] = {
                        "score": score,
                        "passed": passed,
                        "report": report,
                    }

                    # 润色说明和思维链（workflow 返回）
                    polish_report = result.data.get("polish_report", "")
                    polish_reasoning = result.data.get("polish_reasoning", "")
                    if polish_report:
                        st.session_state[f"polish_report_{novel_id}_{selected_ch_num}"] = polish_report
                    if polish_reasoning:
                        st.session_state[f"polish_reasoning_{novel_id}_{selected_ch_num}"] = polish_reasoning

                    # 自动同步检测结果写入 session state → 用户无需手动触发
                    sync_checks = result.data.get("sync_checks", {})
                    if sync_checks:
                        sync_key = f"writing_sync_{novel_id}_{selected_ch_num}"
                        st.session_state[sync_key] = sync_checks

                    # 清除编辑区缓存，确保显示新生成的内容
                    st.session_state.pop(
                        f"edit_content_{novel_id}_{selected_ch_num}", None
                    )
                    st.rerun()
                else:
                    stream_preview_area.empty()
                    st.session_state.is_writing = False
                    st.error(f"生成失败：{result.message}")
            except Exception as e:
                stream_preview_area.empty()
                st.session_state.is_writing = False
                st.error(f"生成出错：{e}")

        # ── 生成结果固定展示区（rerun 后持续保留）────────────────────────────
        gen_result = st.session_state.get(f"gen_result_{novel_id}_{selected_ch_num}")
        if gen_result:
            score = gen_result["score"]
            passed = gen_result["passed"]
            report = gen_result.get("report", {})
            sync_checks = st.session_state.get(f"writing_sync_{novel_id}_{selected_ch_num}", {})

            if passed:
                st.success(f"✅ 第{selected_ch_num}章生成完成！评分：{score:.1f}/10")
            else:
                st.warning(f"⚠️ 章节生成完成，存在问题。评分：{score:.1f}/10")

            # 同步建议提示
            if sync_checks:
                total = (
                    len(sync_checks.get("new_characters", []))
                    + len(sync_checks.get("character_updates", []))
                    + len(sync_checks.get("timeline_events", []))
                    + len(sync_checks.get("foreshadowing_updates", []))
                    + len(sync_checks.get("outline_updates", []))
                    + len(sync_checks.get("world_setting_updates", []))
                )
                if total:
                    st.info(f"🔄 发现 {total} 条同步建议（含人物状态、时间线、伏笔），已在「审核」标签页等待确认")

            # ── 写作思考过程（Agentic 写作阶段 reasoning）
            write_reasoning = st.session_state.get(f"write_reasoning_{novel_id}_{selected_ch_num}", "")
            if write_reasoning:
                with st.expander("💭 写作思考过程", expanded=False):
                    st.markdown(write_reasoning)

            # ── Agentic 审核结果（含工具调用日志 + 审核 reasoning）
            agentic_review = st.session_state.get(f"gen_review_agentic_{novel_id}_{selected_ch_num}")
            if agentic_review:
                r_label = "✅ PASS" if agentic_review["passed"] else "❌ REJECT"
                with st.expander(
                    f"🔍 Agentic 审核：{r_label} | 得分 {agentic_review['score']}/10 | 共 {agentic_review['rounds']} 轮",
                    expanded=False,
                ):
                    # 审核 reasoning
                    review_reasoning_agentic = st.session_state.get(
                        f"review_reasoning_agentic_{novel_id}_{selected_ch_num}", ""
                    )
                    if review_reasoning_agentic:
                        st.markdown("**审核思考过程**")
                        st.markdown(review_reasoning_agentic)
                        st.divider()
                    # 工具调用日志
                    gen_log = st.session_state.get(f"gen_log_{novel_id}_{selected_ch_num}", [])
                    if gen_log:
                        st.markdown("**Agent 工具调用过程**")
                        for line in gen_log:
                            st.markdown(line)

            # ── 标准审核冲突报告（非 Agentic 路径）
            elif report.get("conflicts"):
                with st.expander(f"📋 审核报告（{len(report['conflicts'])}个问题）", expanded=False):
                    st.markdown(f"**摘要：** {report.get('summary', '')}")
                    for c in report["conflicts"]:
                        sev = c.get("severity", 0)
                        icon = "🔴" if sev >= 7 else ("🟡" if sev >= 4 else "🟢")
                        st.markdown(f"{icon} **[{c.get('type')}] 严重度{sev}**")
                        st.markdown(f"- {c.get('description', '')}")

        # 章节内容区（tabs）
        pending = st.session_state.get(pending_key)
        if selected_ch:
            tab_text, tab_summary, tab_review, tab_reader, tab_history, tab_comment = (
                st.tabs(["正文", "摘要", "审核", "读者模拟", "版本历史", "评论"])
            )

            with tab_text:
                review_key = f"writing_manual_review_{novel_id}_{selected_ch_num}"
                editor_version_key = f"editor_version_{novel_id}_{selected_ch_num}"
                if editor_version_key not in st.session_state:
                    st.session_state[editor_version_key] = 0

                # 章纲参考（折叠展示，默认展开）
                if selected_ch and selected_ch.outline_core_event:
                    with st.expander("📋 本章章纲", expanded=True):
                        st.markdown(selected_ch.to_outline_markdown())

                if pending:
                    st.info("💡 AI 已将建议写入编辑器，确认无误后点「保存」写入")
                    col_discard, _ = st.columns([1, 3])
                    with col_discard:
                        if st.button("❌ 放弃建议", key="discard_writing_pending"):
                            st.session_state[pending_key] = None
                            st.rerun()
                    if st.session_state.get(text_key) != pending:
                        st.session_state[text_key] = pending

                # 始终可编辑，首次加载从数据库初始化
                if text_key not in st.session_state:
                    st.session_state[text_key] = selected_ch.content or ""

                render_chapter_editor(
                    text_key=text_key,
                    height=520,
                    disabled=not can_edit(novel_id),
                    placeholder="在此直接书写章节内容，或通过左侧 AI 生成后应用…",
                    font_size_key=f"editor_font_size_{novel_id}",
                    force_version=st.session_state.get(editor_version_key, 0),
                )

                if can_edit(novel_id):
                    change_summary = st.text_input(
                        "修改说明（可选）", placeholder="例如：修改了结尾段落"
                    )
                    btn1, btn2, btn3, btn4 = st.columns([2, 1, 1, 1])
                    with btn1:
                        save_clicked = st.button(
                            "💾 保存", type="primary", width="stretch"
                        )
                    with btn2:
                        review_clicked = st.button(
                            "🔍 AI 审核",
                            width="stretch",
                            help="对当前编辑区内容发起审核，无需先保存",
                        )
                        _rv_score = (st.session_state.get(review_key) or {}).get(
                            "overall_score"
                        ) or selected_ch.review_score
                        if _rv_score:
                            st.caption(f"✅ 已审核 {_rv_score:.1f}/10")
                    with btn3:
                        suggest_clicked = st.button(
                            "✨ AI 建议",
                            width="stretch",
                            help="让 AI 提出改进建议，结果将显示在左侧聊天中",
                        )
                    with btn4:
                        polish_clicked = st.button(
                            "💅 润色",
                            width="stretch",
                            help="对当前正文做文字润色（含去AI味后处理），结果写入编辑器等待确认",
                        )

                    # 章节状态回退
                    if selected_ch.status == "published":
                        with st.expander("🔙 回退章节状态", expanded=False):
                            _status_options = {
                                "outlined": "已有章纲（可重新生成）",
                                "writing": "写作中",
                                "review_pending": "待审核",
                                "reviewed": "已审核（可重新润色）",
                            }
                            _target_status = st.selectbox(
                                "回退到",
                                list(_status_options.keys()),
                                format_func=lambda x: _status_options[x],
                                key=f"revert_status_{selected_ch.id}",
                            )
                            if st.button(
                                "确认回退", key=f"revert_btn_{selected_ch.id}"
                            ):
                                db = get_db()
                                ch_obj = (
                                    db.query(Chapter)
                                    .filter_by(id=selected_ch.id)
                                    .first()
                                )
                                ch_obj.status = _target_status
                                db.commit()
                                db.close()
                                st.success(
                                    f"✅ 章节状态已回退为「{_status_options[_target_status]}」"
                                )
                                st.rerun()

                    if save_clicked:
                        with st.spinner("保存中…"):
                            try:
                                workflow = load_novel(novel_id)
                                save_result = workflow.update_chapter_content(
                                    selected_ch_num,
                                    st.session_state[text_key],
                                    change_summary or "用户手动编辑",
                                )
                                workflow.close()
                                if save_result.success:
                                    st.session_state[pending_key] = None
                                    st.success("✅ 已保存")
                                    st.rerun()
                                else:
                                    st.error(save_result.message)
                            except Exception as e:
                                st.error(f"保存失败：{e}")

                    if review_clicked:
                        current_text = st.session_state.get(text_key, "").strip()
                        if not current_text:
                            st.warning("编辑区没有内容，请先写一些内容再审核")
                        else:
                            with st.spinner("AI 审核中…"):
                                try:
                                    agent = ReviewerAgent(novel_id)
                                    report = agent.review_chapter(
                                        selected_ch_num, current_text
                                    )
                                    review_reasoning_key = f"review_reasoning_{novel_id}_{selected_ch_num}"
                                    st.session_state[review_reasoning_key] = agent.detector.llm.last_reasoning
                                    agent.close()
                                    st.session_state[review_key] = report.to_dict()
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"审核失败：{e}")

                    if suggest_clicked:
                        current_text = st.session_state.get(text_key, "").strip()
                        if not current_text:
                            st.warning("编辑区没有内容，请先写一些内容再请求建议")
                        else:
                            auto_msg = (
                                "请阅读以下正文内容，从**故事节奏、人物表现、场景描写**三个维度指出具体不足，"
                                "并给出修改后的完整版本（用 ```chapter 代码块包裹，以便一键写入编辑器）。"
                            )
                            history = st.session_state[chat_key]
                            history.append({"role": "user", "content": auto_msg})
                            with st.spinner("AI 分析中…"):
                                try:
                                    agent = CanvasAgent(
                                        novel_id=novel_id, role="writer"
                                    )
                                    reply = agent.chat(
                                        history, document_content=current_text
                                    )
                                    agent.close()
                                    history.append(
                                        {"role": "assistant", "content": reply}
                                    )
                                    st.session_state[chat_key] = history
                                    # 自动写入编辑器并保存
                                    sug = _extract_suggestion(reply)
                                    if sug:
                                        _auto_save(
                                            novel_id,
                                            selected_ch_num,
                                            sug,
                                            pending_key,
                                            text_key,
                                            "AI 建议自动保存",
                                        )
                                        st.toast("✅ 已自动保存")
                                    st.rerun()
                                except Exception as e:
                                    history.pop()
                                    st.session_state[chat_key] = history
                                    st.error(f"AI 出错：{e}")

                    if polish_clicked:
                        current_text = st.session_state.get(text_key, "").strip()
                        if not current_text:
                            st.warning("编辑区没有内容，请先写一些内容再润色")
                        else:
                            with st.spinner("润色中（含去AI味后处理，可能需要一点时间）…"):
                                try:
                                    agent = PolisherAgent(novel_id)
                                    polished, reasoning, polish_report = agent.polish_chapter(current_text)
                                    agent.close()
                                    st.session_state[pending_key] = polished
                                    st.session_state[text_key] = polished
                                    polish_reasoning_key = f"polish_reasoning_{novel_id}_{selected_ch_num}"
                                    st.session_state[polish_reasoning_key] = reasoning
                                    polish_report_key = f"polish_report_{novel_id}_{selected_ch_num}"
                                    st.session_state[polish_report_key] = polish_report
                                    # 递增版本号，强制 ace 编辑器重建以显示新内容
                                    st.session_state[editor_version_key] = (
                                        st.session_state.get(editor_version_key, 0) + 1
                                    )
                                    st.toast("✅ 润色完成，请确认后保存")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"润色失败：{e}")

                # 润色说明展示（润色思路 + 改动列表）
                polish_report_key = f"polish_report_{novel_id}_{selected_ch_num}"
                polish_report = st.session_state.get(polish_report_key, "")
                if polish_report:
                    with st.expander("📝 润色说明", expanded=True):
                        st.markdown(polish_report)

                # 润色思维链展示
                polish_reasoning_key = f"polish_reasoning_{novel_id}_{selected_ch_num}"
                polish_reasoning = st.session_state.get(polish_reasoning_key, "")
                if polish_reasoning:
                    with st.expander("💭 润色思考过程", expanded=False):
                        st.markdown(polish_reasoning)

                # 审核结果展示
                manual_review = st.session_state.get(review_key)
                if manual_review:
                    review_reasoning_key = f"review_reasoning_{novel_id}_{selected_ch_num}"
                    review_reasoning = st.session_state.get(review_reasoning_key, "")
                    if review_reasoning:
                        with st.expander("💭 AI 审核思考过程", expanded=False):
                            st.markdown(review_reasoning)
                    st.divider()
                    score = manual_review.get("overall_score", 0)
                    passed = manual_review.get("passed", True)
                    mc1, mc2 = st.columns([1, 3])
                    with mc1:
                        st.metric("审核评分", f"{score:.1f}/10")
                    with mc2:
                        st.markdown("✅ 通过" if passed else "❌ 发现问题")
                        st.caption(manual_review.get("summary", ""))

                    conflicts = manual_review.get("conflicts", [])
                    if conflicts:
                        full_text = st.session_state.get(text_key, "")
                        for i, c in enumerate(conflicts):
                            sev = c.get("severity", 0)
                            icon = "🔴" if sev >= 7 else ("🟡" if sev >= 4 else "🟢")
                            loc = (c.get("location") or "").strip()
                            sols = c.get("solutions", [])

                            # ── 状态追踪（提前加载，供标题使用）
                            done_key = f"sol_done_{novel_id}_{selected_ch_num}_{i}"
                            done_set = st.session_state.get(done_key, set())
                            issue_key = f"review_issue_{novel_id}_{selected_ch_num}_{i}"
                            if issue_key not in st.session_state:
                                st.session_state[issue_key] = []
                            issue_hist = st.session_state[issue_key]
                            has_ai_reply = any(
                                m["role"] == "assistant" for m in issue_hist
                            )
                            is_handled = bool(done_set) or has_ai_reply

                            with st.container(border=True):
                                # ── 标题 + 描述（含已处理徽章）
                                badge = "  ✅ 已处理" if is_handled else "  ⏳ 待处理"
                                st.markdown(
                                    f"{icon} **[{c.get('type', '未知')}] 严重度 {sev}**{badge}"
                                )
                                st.markdown(c.get("description", ""))

                                # ── 位置定位
                                if loc:
                                    pos = full_text.find(loc[:60]) if full_text else -1
                                    if pos >= 0:
                                        line_no = full_text[:pos].count("\n") + 1
                                        st.caption(f"📍 约第 {line_no} 行")
                                    else:
                                        st.caption("📍 原文位置：")
                                    st.code(loc, language=None)

                                # ── 快捷方案按钮（已应用的显示标签）
                                if sols and can_edit(novel_id):
                                    st.caption("快捷方案（AI 直接修改）：")
                                    for j, sol in enumerate(sols[:3]):
                                        if j in done_set:
                                            st.success(f"✅ {sol}（已应用）")
                                        else:
                                            if st.button(
                                                f"✏️ {sol}",
                                                width="stretch",
                                                key=f"qapply_{novel_id}_{selected_ch_num}_{i}_{j}",
                                            ):
                                                cur = st.session_state.get(
                                                    text_key, ""
                                                ).strip()
                                                if cur:
                                                    with st.spinner("AI 修改中…"):
                                                        try:
                                                            _agent = CanvasAgent(
                                                                novel_id=novel_id,
                                                                role="writer",
                                                            )
                                                            _msg = (
                                                                f"请修改以下章节正文，针对问题：\n"
                                                                f"「{c.get('description', '')}」\n"
                                                                f"位置参考：{loc[:120]}\n"
                                                                f"按方案「{sol}」修改，"
                                                                f"直接输出完整修改后正文（用 ```chapter 代码块包裹）。"
                                                            )
                                                            _reply = _agent.chat(
                                                                [
                                                                    {
                                                                        "role": "user",
                                                                        "content": _msg,
                                                                    }
                                                                ],
                                                                document_content=cur,
                                                            )
                                                            _agent.close()
                                                            _sug = _extract_suggestion(
                                                                _reply
                                                            )
                                                            if _sug:
                                                                _auto_save(
                                                                    novel_id,
                                                                    selected_ch_num,
                                                                    _sug,
                                                                    pending_key,
                                                                    text_key,
                                                                    "AI 快捷方案自动保存",
                                                                )
                                                                done_set.add(j)
                                                                st.session_state[
                                                                    done_key
                                                                ] = done_set
                                                                st.toast(
                                                                    "✅ 已自动保存"
                                                                )
                                                                st.rerun()
                                                        except Exception as _e:
                                                            st.error(f"修改失败：{_e}")

                                # ── 对话式修改（已讨论时标签变色）
                                expander_label = (
                                    "💬 与 AI 讨论如何修改  ✅ 已讨论"
                                    if has_ai_reply
                                    else "💬 与 AI 讨论如何修改"
                                )
                                with st.expander(
                                    expander_label, expanded=bool(issue_hist)
                                ):
                                    for h_idx, hmsg in enumerate(issue_hist):
                                        with st.chat_message(hmsg["role"]):
                                            if hmsg["role"] == "assistant":
                                                _sug2 = _extract_suggestion(
                                                    hmsg["content"]
                                                )
                                                _disp = re.sub(
                                                    r"```(?:markdown|text)?\n.*?```",
                                                    "",
                                                    hmsg["content"],
                                                    flags=re.DOTALL,
                                                ).strip()
                                                if _disp:
                                                    st.markdown(_disp)
                                                if _sug2:
                                                    with st.container(border=True):
                                                        st.caption("✅ 已写入编辑器")
                                                        with st.expander(
                                                            "查看修改内容",
                                                            expanded=False,
                                                        ):
                                                            st.text(
                                                                _sug2[:800]
                                                                + (
                                                                    "…"
                                                                    if len(_sug2) > 800
                                                                    else ""
                                                                )
                                                            )
                                                        if st.button(
                                                            "↩️ 重新写入",
                                                            key=f"ri_{novel_id}_{selected_ch_num}_{i}_{h_idx}",
                                                            width="stretch",
                                                        ):
                                                            _auto_save(
                                                                novel_id,
                                                                selected_ch_num,
                                                                _sug2,
                                                                pending_key,
                                                                text_key,
                                                                "AI 对话建议自动保存",
                                                            )
                                                            st.toast("✅ 已自动保存")
                                                            st.rerun()
                                            else:
                                                st.markdown(hmsg["content"])

                                    # 用计数器改变 key 来清空输入框（Streamlit 不允许直接修改 widget 的 state）
                                    _clr_key = (
                                        f"ii_clr_{novel_id}_{selected_ch_num}_{i}"
                                    )
                                    _clr_cnt = st.session_state.get(_clr_key, 0)
                                    _input_key = f"ii_{novel_id}_{selected_ch_num}_{i}_{_clr_cnt}"
                                    user_idea = st.text_input(
                                        "告诉 AI 你的修改思路…",
                                        key=_input_key,
                                        placeholder="例如：改得更含蓄，不要直接说明动机",
                                    )
                                    if (
                                        st.button(
                                            "发送",
                                            width="stretch",
                                            key=f"isend_{novel_id}_{selected_ch_num}_{i}",
                                        )
                                        and user_idea.strip()
                                    ):
                                        cur2 = st.session_state.get(
                                            text_key, ""
                                        ).strip()
                                        if not issue_hist:
                                            send_content = (
                                                f"我在审核章节时发现了一个问题：\n\n"
                                                f"**类型：** {c.get('type', '')}\n"
                                                f"**描述：** {c.get('description', '')}\n"
                                                f"**位置：** {loc[:150] if loc else '见正文'}\n\n"
                                                f"我希望：{user_idea.strip()}\n\n"
                                                f"请给出修改后的完整正文（用 ```chapter 代码块包裹）。"
                                            )
                                        else:
                                            send_content = user_idea.strip()

                                        issue_hist.append(
                                            {
                                                "role": "user",
                                                "content": user_idea.strip(),
                                            }
                                        )
                                        send_hist = issue_hist[:-1] + [
                                            {"role": "user", "content": send_content}
                                        ]

                                        with st.spinner("AI 思考中…"):
                                            try:
                                                _a = CanvasAgent(
                                                    novel_id=novel_id, role="writer"
                                                )
                                                _r = _a.chat(
                                                    send_hist, document_content=cur2
                                                )
                                                _a.close()
                                            except Exception as _e2:
                                                issue_hist.pop()
                                                st.session_state[issue_key] = issue_hist
                                                st.error(f"AI 出错：{_e2}")
                                                st.stop()

                                        issue_hist.append(
                                            {"role": "assistant", "content": _r}
                                        )
                                        st.session_state[issue_key] = issue_hist
                                        _sug3 = _extract_suggestion(_r)
                                        if _sug3:
                                            _auto_save(
                                                novel_id,
                                                selected_ch_num,
                                                _sug3,
                                                pending_key,
                                                text_key,
                                                "AI 对话建议自动保存",
                                            )
                                            st.toast("✅ 已自动保存")
                                        # 递增计数器 → 下次渲染创建新 key 的空输入框
                                        st.session_state[_clr_key] = _clr_cnt + 1
                                        st.rerun()
                    else:
                        st.success("未发现明显问题")

                    if st.button(
                        "清除审核结果",
                        key="clear_manual_review",
                        width="stretch",
                    ):
                        st.session_state[review_key] = None
                        # 清除各问题的操作状态
                        for _ci in range(len(conflicts)):
                            st.session_state.pop(
                                f"sol_done_{novel_id}_{selected_ch_num}_{_ci}", None
                            )
                            st.session_state.pop(
                                f"review_issue_{novel_id}_{selected_ch_num}_{_ci}", None
                            )
                            st.session_state.pop(
                                f"ii_clr_{novel_id}_{selected_ch_num}_{_ci}", None
                            )
                        st.rerun()

                # ── 大纲/设定同步检测 ─────────────────────────────
                sync_key = f"writing_sync_{novel_id}_{selected_ch_num}"
                sync_result = st.session_state.get(sync_key)
                current_text_for_sync = st.session_state.get(text_key, "").strip()

                _sync_is_done = isinstance(sync_result, dict) and sync_result.get(
                    "_done"
                )

                if current_text_for_sync and can_edit(novel_id):
                    st.divider()
                    if st.button(
                        "🔄 大纲 / 设定同步检测",
                        width="stretch",
                        help="分析本章内容，检测是否有新角色或情节变化需要同步到大纲 / 设定",
                    ):
                        with st.spinner("AI 分析章节内容…"):
                            try:
                                agent = OutlineAgent(novel_id)
                                result = agent.analyze_chapter_consistency(
                                    selected_ch_num, current_text_for_sync
                                )
                                agent.close()
                                st.session_state[sync_key] = result
                                st.rerun()
                            except Exception as e:
                                st.error(f"检测失败：{e}")
                    if _sync_is_done:
                        st.caption("✅ 上次同步检测：无需更新  · 可随时重新检测")

                if sync_result and not _sync_is_done:
                    new_chars = list(sync_result.get("new_characters", []))
                    char_upds = list(sync_result.get("character_updates", []))
                    timeline_evts = list(sync_result.get("timeline_events", []))
                    foreshadowing_upds = list(
                        sync_result.get("foreshadowing_updates", [])
                    )
                    outline_upds = list(sync_result.get("outline_updates", []))
                    ws_upds = list(sync_result.get("world_setting_updates", []))
                    total = (
                        len(new_chars)
                        + len(char_upds)
                        + len(timeline_evts)
                        + len(foreshadowing_upds)
                        + len(outline_upds)
                        + len(ws_upds)
                    )

                    if total == 0:
                        st.success("✅ 大纲和设定与本章内容一致，无需更新")
                        if st.button("完成", key="clear_sync_empty"):
                            st.session_state[sync_key] = {"_done": True}
                            st.rerun()
                    else:
                        st.markdown(f"##### 🔄 发现 {total} 条更新建议，请逐一确认")

                        # ── 一键全部通过 ─────────────────────────────────────
                        _qa1, _qa2 = st.columns([2, 1])
                        with _qa1:
                            if st.button(
                                f"⚡ 一键全部通过（{total} 条）",
                                key=f"sync_approve_all_{novel_id}_{selected_ch_num}",
                                width="stretch",
                                type="primary",
                                help="自动执行所有建议操作：添加新角色、更新人物状态、追加大纲、写入世界观设定",
                            ):
                                with st.spinner("正在批量同步…"):
                                    try:
                                        done = _auto_execute_sync(novel_id, sync_result, chapter_number=selected_ch_num)
                                        st.session_state[sync_key] = {"_done": True}
                                        st.success(f"✅ 已成功同步 {done} 条")
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"批量同步失败：{e}")
                        with _qa2:
                            if st.button(
                                "❌ 全部跳过",
                                key=f"sync_skip_all_{novel_id}_{selected_ch_num}",
                                width="stretch",
                                help="忽略所有建议，标记为已完成",
                            ):
                                st.session_state[sync_key] = {"_done": True}
                                st.rerun()

                        st.divider()

                        # —— 新增人物 ——
                        for i, char in enumerate(new_chars):
                            with st.container(border=True):
                                st.markdown(
                                    f"👤 **新增人物：{char.get('name')}**（{char.get('role', '')}）"
                                )
                                if char.get("personality"):
                                    st.caption(f"性格：{char['personality']}")
                                if char.get("background"):
                                    st.caption(f"背景：{char['background']}")
                                st.caption(f"原因：{char.get('reason', '')}")
                                ca1, ca2 = st.columns(2)
                                with ca1:
                                    if st.button(
                                        "✅ 添加到人物档案",
                                        key=f"sync_char_add_{novel_id}_{selected_ch_num}_{i}",
                                        width="stretch",
                                        type="primary",
                                    ):
                                        try:
                                            wf = load_novel(novel_id)
                                            _nc_data = {
                                                "name": char.get("name", ""),
                                                "role": char.get("role", ""),
                                                "personality": char.get(
                                                    "personality", ""
                                                ),
                                                "background": char.get(
                                                    "background", ""
                                                ),
                                            }
                                            if char.get("relationships"):
                                                _nc_rels = char["relationships"]
                                                _nc_data["relationships"] = (
                                                    json.dumps(
                                                        _nc_rels, ensure_ascii=False
                                                    )
                                                    if not isinstance(_nc_rels, str)
                                                    else _nc_rels
                                                )
                                            wf.memory.global_mem.save_character(
                                                _nc_data
                                            )
                                            wf.close()
                                            new_chars.pop(i)
                                            sync_result["new_characters"] = new_chars
                                            st.session_state[sync_key] = sync_result
                                            st.success(f"已添加 {char.get('name')}")
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"添加失败：{e}")
                                with ca2:
                                    if st.button(
                                        "❌ 跳过",
                                        key=f"sync_char_skip_{novel_id}_{selected_ch_num}_{i}",
                                        width="stretch",
                                    ):
                                        new_chars.pop(i)
                                        sync_result["new_characters"] = new_chars
                                        st.session_state[sync_key] = sync_result
                                        st.rerun()

                        # —— 人物状态更新 ——
                        _field_labels = {
                            "current_state": "当前状态",
                            "growth_arc": "成长弧光",
                            "abilities": "能力设定",
                            "relationships": "人际关系",
                        }
                        for i, cu in enumerate(char_upds):
                            with st.container(border=True):
                                field_name = _field_labels.get(
                                    cu.get("field", ""), cu.get("field", "")
                                )
                                st.markdown(
                                    f"🧑 **人物更新：{cu.get('name')} — {field_name}**"
                                )
                                _new_val = cu.get('new_value', '')
                                if not isinstance(_new_val, str):
                                    import json as _json
                                    _new_val = _json.dumps(_new_val, ensure_ascii=False)
                                st.caption(f"新内容：{_new_val[:200]}")
                                st.caption(f"原因：{cu.get('reason', '')}")
                                cu1, cu2 = st.columns(2)
                                with cu1:
                                    if st.button(
                                        "✅ 更新人物档案",
                                        key=f"sync_cu_add_{novel_id}_{selected_ch_num}_{i}",
                                        width="stretch",
                                        type="primary",
                                    ):
                                        try:
                                            wf = load_novel(novel_id)
                                            _sync_field = cu.get(
                                                "field", "current_state"
                                            )
                                            _sync_value = cu.get("new_value", "")
                                            if (
                                                _sync_field
                                                in ("relationships", "abilities")
                                                and _sync_value
                                                and not isinstance(_sync_value, str)
                                            ):
                                                _sync_value = json.dumps(
                                                    _sync_value, ensure_ascii=False
                                                )
                                            wf.memory.global_mem.save_character(
                                                {
                                                    "name": cu.get("name", ""),
                                                    _sync_field: _sync_value,
                                                }
                                            )
                                            wf.close()
                                            char_upds.pop(i)
                                            sync_result["character_updates"] = char_upds
                                            st.session_state[sync_key] = sync_result
                                            st.success(
                                                f"已更新 {cu.get('name')} 的{field_name}"
                                            )
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"更新失败：{e}")
                                with cu2:
                                    if st.button(
                                        "❌ 跳过",
                                        key=f"sync_cu_skip_{novel_id}_{selected_ch_num}_{i}",
                                        width="stretch",
                                    ):
                                        char_upds.pop(i)
                                        sync_result["character_updates"] = char_upds
                                        st.session_state[sync_key] = sync_result
                                        st.rerun()

                        # —— 大纲字段更新（仅全书级结构性变化，替换写入）——
                        for i, upd in enumerate(outline_upds):
                            with st.container(border=True):
                                field_label = {
                                    "premise": "前提设定",
                                    "theme": "核心主题",
                                    "main_conflict": "主要矛盾",
                                    "protagonist_arc": "主角弧光",
                                    "ending_summary": "结局概要",
                                    "story_structure": "三幕结构",
                                }.get(upd.get("field", ""), upd.get("field", ""))
                                st.markdown(f"📖 **大纲更新：{field_label}**")
                                merged = upd.get("merged_content", "")
                                st.caption(
                                    f"新内容：{merged[:200]}{'…' if len(merged) > 200 else ''}"
                                )
                                st.caption(f"原因：{upd.get('reason', '')}")
                                if upd.get("_truncated"):
                                    st.warning("⚠️ 内容超出 300 字已自动截断")
                                ob1, ob2 = st.columns(2)
                                with ob1:
                                    if st.button(
                                        "✅ 更新大纲",
                                        key=f"sync_outline_add_{novel_id}_{selected_ch_num}_{i}",
                                        width="stretch",
                                        type="primary",
                                    ):
                                        try:
                                            wf = load_novel(novel_id)
                                            field = upd.get("field", "")
                                            wf.memory.global_mem.save_outline(
                                                {field: merged.strip()}
                                            )
                                            wf.close()
                                            outline_upds.pop(i)
                                            sync_result["outline_updates"] = (
                                                outline_upds
                                            )
                                            st.session_state[sync_key] = sync_result
                                            st.success("已更新大纲（替换原内容）")
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"更新失败：{e}")
                                with ob2:
                                    if st.button(
                                        "❌ 跳过",
                                        key=f"sync_outline_skip_{novel_id}_{selected_ch_num}_{i}",
                                        width="stretch",
                                    ):
                                        outline_upds.pop(i)
                                        sync_result["outline_updates"] = outline_upds
                                        st.session_state[sync_key] = sync_result
                                        st.rerun()

                        # —— 时间线事件 ——
                        for i, te in enumerate(timeline_evts):
                            with st.container(border=True):
                                st.markdown(
                                    f"⏱️ **时间线事件：{te.get('event_name', '')}**"
                                )
                                if te.get("in_story_time"):
                                    st.caption(f"时间：{te['in_story_time']}")
                                st.caption(
                                    f"描述：{te.get('event_description', '')[:200]}"
                                )
                                if te.get("characters_involved"):
                                    involved = te["characters_involved"]
                                    if isinstance(involved, list):
                                        involved = "、".join(involved)
                                    st.caption(f"涉及人物：{involved}")
                                if te.get("impact"):
                                    st.caption(f"影响：{te['impact']}")
                                te1, te2 = st.columns(2)
                                with te1:
                                    if st.button(
                                        "✅ 写入时间线",
                                        key=f"sync_te_add_{novel_id}_{selected_ch_num}_{i}",
                                        width="stretch",
                                        type="primary",
                                    ):
                                        try:
                                            wf = load_novel(novel_id)
                                            involved = te.get("characters_involved", [])
                                            event_data = {
                                                "event_name": te.get("event_name", ""),
                                                "event_description": te.get(
                                                    "event_description", ""
                                                ),
                                                "in_story_time": te.get(
                                                    "in_story_time", ""
                                                ),
                                                "characters_involved": (
                                                    json.dumps(
                                                        involved, ensure_ascii=False
                                                    )
                                                    if isinstance(involved, list)
                                                    else involved
                                                ),
                                                "impact": te.get("impact", ""),
                                            }
                                            wf.memory.global_mem.add_timeline_event(
                                                event_data
                                            )
                                            wf.close()
                                            timeline_evts.pop(i)
                                            sync_result["timeline_events"] = (
                                                timeline_evts
                                            )
                                            st.session_state[sync_key] = sync_result
                                            st.success("已写入时间线")
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"写入失败：{e}")
                                with te2:
                                    if st.button(
                                        "❌ 跳过",
                                        key=f"sync_te_skip_{novel_id}_{selected_ch_num}_{i}",
                                        width="stretch",
                                    ):
                                        timeline_evts.pop(i)
                                        sync_result["timeline_events"] = timeline_evts
                                        st.session_state[sync_key] = sync_result
                                        st.rerun()

                        # —— 新增伏笔 ——
                        _importance_labels = {
                            "high": "重要",
                            "medium": "一般",
                            "low": "次要",
                        }
                        for i, fs in enumerate(foreshadowing_upds):
                            with st.container(border=True):
                                imp_label = _importance_labels.get(
                                    fs.get("importance", "medium"),
                                    fs.get("importance", ""),
                                )
                                st.markdown(
                                    f"🎭 **新增伏笔：{fs.get('name', '')}**（{imp_label}）"
                                )
                                st.caption(f"描述：{fs.get('description', '')[:200]}")
                                if fs.get("collect_by_chapter"):
                                    st.caption(
                                        f"最晚回收：第{fs['collect_by_chapter']}章"
                                    )
                                if fs.get("notes"):
                                    st.caption(f"备注：{fs['notes']}")
                                fs1, fs2 = st.columns(2)
                                with fs1:
                                    if st.button(
                                        "✅ 写入伏笔库",
                                        key=f"sync_fs_add_{novel_id}_{selected_ch_num}_{i}",
                                        width="stretch",
                                        type="primary",
                                    ):
                                        try:
                                            wf = load_novel(novel_id)
                                            fs_data = {
                                                "name": fs.get("name", ""),
                                                "description": fs.get(
                                                    "description", ""
                                                ),
                                                "importance": fs.get(
                                                    "importance", "medium"
                                                ),
                                                "collect_by_chapter": fs.get(
                                                    "collect_by_chapter"
                                                )
                                                or None,
                                                "notes": fs.get("notes", "") or None,
                                                "status": "active",
                                                "set_chapter": selected_ch_num,
                                            }
                                            wf.memory.global_mem.save_foreshadowing(
                                                fs_data
                                            )
                                            wf.close()
                                            foreshadowing_upds.pop(i)
                                            sync_result["foreshadowing_updates"] = (
                                                foreshadowing_upds
                                            )
                                            st.session_state[sync_key] = sync_result
                                            st.success(
                                                f"已写入伏笔《{fs.get('name')}》"
                                            )
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"写入失败：{e}")
                                with fs2:
                                    if st.button(
                                        "❌ 跳过",
                                        key=f"sync_fs_skip_{novel_id}_{selected_ch_num}_{i}",
                                        width="stretch",
                                    ):
                                        foreshadowing_upds.pop(i)
                                        sync_result["foreshadowing_updates"] = (
                                            foreshadowing_upds
                                        )
                                        st.session_state[sync_key] = sync_result
                                        st.rerun()

                        # —— 世界观设定更新 ——
                        for i, ws in enumerate(ws_upds):
                            with st.container(border=True):
                                st.markdown(f"🌍 **设定新增：{ws.get('key', '')}**")
                                st.caption(f"内容：{ws.get('value', '')[:120]}")
                                st.caption(f"原因：{ws.get('reason', '')}")
                                wb1, wb2 = st.columns(2)
                                with wb1:
                                    if st.button(
                                        "✅ 写入世界观设定",
                                        key=f"sync_ws_add_{novel_id}_{selected_ch_num}_{i}",
                                        width="stretch",
                                        type="primary",
                                    ):
                                        try:
                                            db = get_db()
                                            novel_obj = (
                                                db.query(Novel)
                                                .filter(Novel.id == novel_id)
                                                .first()
                                            )
                                            world = novel_obj.get_world_setting()
                                            world[ws.get("key", "")] = ws.get(
                                                "value", ""
                                            )
                                            novel_obj.set_world_setting(world)
                                            db.commit()
                                            db.close()
                                            ws_upds.pop(i)
                                            sync_result["world_setting_updates"] = (
                                                ws_upds
                                            )
                                            st.session_state[sync_key] = sync_result
                                            st.success("已写入世界观设定")
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"更新失败：{e}")
                                with wb2:
                                    if st.button(
                                        "❌ 跳过",
                                        key=f"sync_ws_skip_{novel_id}_{selected_ch_num}_{i}",
                                        width="stretch",
                                    ):
                                        ws_upds.pop(i)
                                        sync_result["world_setting_updates"] = ws_upds
                                        st.session_state[sync_key] = sync_result
                                        st.rerun()

                        # 全部条目处理完后自动标记完成
                        remaining = (
                            len(new_chars)
                            + len(char_upds)
                            + len(timeline_evts)
                            + len(foreshadowing_upds)
                            + len(outline_upds)
                            + len(ws_upds)
                        )
                        if remaining == 0:
                            st.session_state[sync_key] = {"_done": True}
                            st.rerun()

                st.divider()
                render_approval_status(novel_id, selected_ch)

            with tab_summary:
                st.markdown("### 📄 章节摘要")
                st.caption("摘要和关键事件会作为后续章节的上下文，影响 AI 续写的连贯性")
                _sum_key = f"ch_summary_{novel_id}_{selected_ch_num}"
                _evt_key = f"ch_events_{novel_id}_{selected_ch_num}"
                try:
                    _existing_events = (
                        json.loads(selected_ch.key_events)
                        if selected_ch.key_events
                        else []
                    )
                except (json.JSONDecodeError, TypeError):
                    _existing_events = []
                with st.form(f"edit_summary_{selected_ch.id}"):
                    sum_text = st.text_area(
                        "章节摘要",
                        value=selected_ch.summary or "",
                        height=150,
                        placeholder="概括本章主要内容…",
                    )
                    events_text = st.text_area(
                        "关键事件（每行一个）",
                        value="\n".join(_existing_events),
                        height=100,
                        placeholder="事件1\n事件2\n…",
                    )
                    if st.form_submit_button(
                        "💾 保存摘要", disabled=not can_edit(novel_id)
                    ):
                        new_events = [
                            e.strip()
                            for e in events_text.strip().split("\n")
                            if e.strip()
                        ]
                        workflow = load_novel(novel_id)
                        workflow.memory.chapter_mem.save_chapter_summary(
                            selected_ch_num, sum_text.strip(), new_events
                        )
                        workflow.close()
                        st.success("✅ 章节摘要已保存")
                        st.rerun()

            with tab_review:
                report = (
                    selected_ch.get_review_report() if selected_ch.review_report else {}
                )
                score = selected_ch.review_score or 0
                if report:
                    st.metric("审核评分", f"{score:.1f}/10")
                    st.markdown(f"**摘要：** {report.get('summary', '')}")
                    conflicts = report.get("conflicts", [])
                    if conflicts:
                        for c in conflicts:
                            sev = c.get("severity", 0)
                            icon = "🔴" if sev >= 7 else ("🟡" if sev >= 4 else "🟢")
                            with st.container(border=True):
                                st.markdown(
                                    f"{icon} **[{c.get('type', '')}] 严重度 {sev}**"
                                )
                                st.markdown(f"- 描述：{c.get('description', '')}")
                                if c.get("location"):
                                    st.code(c["location"], language=None)
                                if c.get("solutions"):
                                    for _s in c["solutions"]:
                                        st.markdown(f"  - {_s}")
                    else:
                        st.success("没有发现明显冲突")
                else:
                    st.info("暂无审核报告，生成章节后自动审核")

                st.divider()
                with st.expander("✏️ 手动编辑审核评分", expanded=False):
                    with st.form(f"edit_review_{selected_ch.id}"):
                        _rv_summary = report.get("summary", "") if report else ""
                        rv_score_edit = st.number_input(
                            "审核评分",
                            min_value=0.0,
                            max_value=10.0,
                            value=float(score),
                            step=0.5,
                        )
                        rv_summary_edit = st.text_area(
                            "审核摘要", value=_rv_summary, height=80
                        )
                        if st.form_submit_button(
                            "💾 保存评分", disabled=not can_edit(novel_id)
                        ):
                            db = get_db()
                            ch_obj = (
                                db.query(Chapter).filter_by(id=selected_ch.id).first()
                            )
                            ch_obj.review_score = rv_score_edit
                            if report:
                                report["summary"] = rv_summary_edit.strip()
                                ch_obj.review_report = json.dumps(
                                    report, ensure_ascii=False
                                )
                            elif rv_summary_edit.strip():
                                ch_obj.review_report = json.dumps(
                                    {
                                        "summary": rv_summary_edit.strip(),
                                        "conflicts": [],
                                    },
                                    ensure_ascii=False,
                                )
                            db.commit()
                            db.close()
                            st.success("✅ 审核评分已更新")
                            st.rerun()

            with tab_reader:
                st.markdown("### 📖 读者模拟测试")
                st.caption("模拟爽文读者、文学爱好者、轻小说读者三种视角")

                if st.button(
                    "🎭 运行读者模拟", width="stretch", help="约需 30-60 秒"
                ):
                    with st.spinner("模拟读者阅读体验…"):
                        workflow = load_novel(novel_id)
                        result = workflow.reader_test_chapter(
                            selected_ch_num, progress_callback=lambda m: st.toast(m)
                        )
                        workflow.close()
                    if result.success:
                        st.success(result.message)
                        st.rerun()
                    else:
                        st.error(result.message)

                if selected_ch.reader_feedback:
                    try:
                        feedback = json.loads(selected_ch.reader_feedback)
                    except Exception:
                        feedback = {}

                    if feedback:
                        if selected_ch.reader_score:
                            st.metric("综合评分", f"{selected_ch.reader_score:.1f}/10")

                        readers = feedback.get("readers", {})
                        reader_keys = ["power_fantasy", "literary", "light_novel"]
                        reader_icons = {
                            "power_fantasy": "⚡",
                            "literary": "📚",
                            "light_novel": "🎮",
                        }
                        reader_cols = st.columns(3)

                        for col, rkey in zip(reader_cols, reader_keys):
                            rdata = readers.get(rkey, {})
                            if not rdata:
                                continue
                            with col:
                                with st.container(border=True):
                                    st.markdown(
                                        f"#### {reader_icons.get(rkey, '📖')} {rdata.get('label', rkey)}"
                                    )
                                    st.metric(
                                        "平均分", f"{rdata.get('average', 0):.1f}"
                                    )
                                    for dim, sc in rdata.get("scores", {}).items():
                                        st.progress(
                                            max(0.0, min(1.0, sc / 10)),
                                            text=f"{dim}: {sc}/10",
                                        )
                                    if rdata.get("comment"):
                                        st.caption(rdata["comment"][:200])

                        highlights = feedback.get("highlights", [])
                        suggestions = feedback.get("improvement_suggestions", [])
                        if highlights or suggestions:
                            with st.expander("💡 亮点与改进建议"):
                                if highlights:
                                    st.markdown("**亮点：**")
                                    for h in highlights:
                                        st.markdown(f"- {h}")
                                if suggestions:
                                    st.markdown("**改进建议：**")
                                    for s in suggestions:
                                        st.markdown(f"- {s}")

                st.divider()
                with st.expander("✏️ 手动编辑读者评分", expanded=False):
                    with st.form(f"edit_reader_{selected_ch.id}"):
                        rd_score_edit = st.number_input(
                            "综合评分",
                            min_value=0.0,
                            max_value=10.0,
                            value=float(selected_ch.reader_score or 0),
                            step=0.5,
                        )
                        if st.form_submit_button(
                            "💾 保存评分", disabled=not can_edit(novel_id)
                        ):
                            db = get_db()
                            ch_obj = (
                                db.query(Chapter).filter_by(id=selected_ch.id).first()
                            )
                            ch_obj.reader_score = rd_score_edit
                            db.commit()
                            db.close()
                            st.success("✅ 读者评分已更新")
                            st.rerun()

            with tab_history:
                db = get_db()
                versions = (
                    db.query(ContentVersion)
                    .filter(ContentVersion.chapter_id == selected_ch.id)
                    .order_by(ContentVersion.version_number.desc())
                    .limit(5)
                    .all()
                )
                db.close()

                if versions:
                    for v in versions:
                        type_label = {
                            "draft": "初稿",
                            "polished": "润色版",
                            "user_edit": "用户编辑",
                            "reviewed": "审核版",
                        }.get(v.version_type, v.version_type)
                        st.markdown(
                            f"**v{v.version_number}** · {type_label} · {v.change_summary or ''}"
                        )
                        if v.content:
                            st.text(v.content[:150] + "…")
                        st.divider()
                else:
                    st.info("暂无版本历史")

            with tab_comment:
                render_chapter_comments(novel_id, selected_ch.id)
