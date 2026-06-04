"""
写作页面 — Canvas 布局
左：章节控制 + AI 写作助手  |  右：章节正文 + 审核 / 读者 / 历史
"""

import json
import re

import streamlit as st

from core.models import get_db, Chapter, ContentVersion
from core.workflow import load_novel
from core.permissions import can_edit, can_approve
from core.agents import CanvasAgent
from ui.helpers import format_chapter_status, format_approval_badge
from ui.components.collaboration import render_chapter_comments, render_approval_status


def _extract_suggestion(text: str) -> str | None:
    matches = re.findall(r'```(?:markdown|text)?\n(.*?)```', text, re.DOTALL)
    return matches[-1].strip() if matches else None


def page_writing():
    novel_id = st.session_state.novel_id

    db = get_db()
    chapters = db.query(Chapter).filter(Chapter.novel_id == novel_id).order_by(Chapter.chapter_number).all()
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

        selected_label = st.selectbox("章节", list(chapter_options.keys()),
                                       index=default_idx, label_visibility="collapsed")
        selected_ch_num = chapter_options[selected_label]
        st.session_state.writing_chapter = selected_ch_num

        db = get_db()
        selected_ch = db.query(Chapter).filter(
            Chapter.novel_id == novel_id,
            Chapter.chapter_number == selected_ch_num
        ).first()
        db.close()

        # 章节信息
        if selected_ch:
            with st.container(border=True):
                st.markdown(f"**第{selected_ch_num}章《{selected_ch.title or '未命名'}》**")
                if selected_ch.outline_core_event:
                    st.caption(f"核心事件：{selected_ch.outline_core_event[:80]}")
                meta = []
                if selected_ch.word_count:
                    meta.append(f"{selected_ch.word_count:,}字")
                if selected_ch.review_score:
                    meta.append(f"评分 {selected_ch.review_score:.1f}/10")
                if meta:
                    st.caption(" · ".join(meta))

        st.divider()
        st.markdown("#### 写作参数")
        word_target  = st.slider("目标字数", 1000, 6000, 3000, step=500)
        auto_polish  = st.toggle("自动润色", value=True)
        save_edit_mode = st.toggle("编辑模式", value=False, disabled=not can_edit(novel_id))

        wc1, wc2 = st.columns(2)
        write_btn = wc1.button(
            "🚀 生成本章", use_container_width=True, type="primary",
            disabled=(st.session_state.is_writing or st.session_state.batch_writing or not can_edit(novel_id))
        )
        is_published = selected_ch and selected_ch.status == "published"
        rewrite_btn = wc2.button(
            "🔄 重新生成", use_container_width=True,
            disabled=(not is_published or st.session_state.is_writing or st.session_state.batch_writing or not can_edit(novel_id))
        )

        st.divider()

        # AI 写作助手聊天
        st.markdown("#### 🤖 AI 写作助手")
        chat_key    = f"writing_chat_{novel_id}_{selected_ch_num}"
        pending_key = f"writing_pending_{novel_id}_{selected_ch_num}"
        if chat_key not in st.session_state:
            st.session_state[chat_key] = []
        if pending_key not in st.session_state:
            st.session_state[pending_key] = None

        with st.container(height=340, border=True):
            history = st.session_state[chat_key]
            if not history:
                st.caption("💡 可以讨论章节思路，或让 AI 建议修改。\n\nAI 提供新版文本时，会出现「应用」按钮。")
            for idx, msg in enumerate(history):
                with st.chat_message(msg["role"]):
                    text = msg["content"]
                    suggestion = _extract_suggestion(text) if msg["role"] == "assistant" else None
                    if suggestion:
                        display = re.sub(r'```(?:markdown|text)?\n.*?```', '', text, flags=re.DOTALL).strip()
                        if display:
                            st.markdown(display)
                        with st.container(border=True):
                            st.caption("📄 AI 建议的新版文本")
                            st.markdown(suggestion[:200] + ("…" if len(suggestion) > 200 else ""))
                            if st.button("📋 应用到编辑器", key=f"apply_writing_{novel_id}_{selected_ch_num}_{idx}",
                                         use_container_width=True, type="primary"):
                                st.session_state[pending_key] = suggestion
                                st.rerun()
                    else:
                        st.markdown(text)

        if user_input := st.chat_input("和 AI 讨论本章…", key=f"writing_chat_input_{selected_ch_num}"):
            history = st.session_state[chat_key]
            history.append({"role": "user", "content": user_input})
            # 当前章节内容作上下文
            ch_ctx = selected_ch.content or selected_ch.outline_core_event or ""
            with st.spinner("AI 思考中…"):
                try:
                    agent = CanvasAgent(novel_id=novel_id, role="writer")
                    reply = agent.chat(history, document_content=ch_ctx)
                    agent.close()
                except Exception as e:
                    history.pop()
                    st.error(f"AI 出错：{e}")
                    st.stop()
            history.append({"role": "assistant", "content": reply})
            st.session_state[chat_key] = history
            st.rerun()

        if st.session_state[chat_key]:
            if st.button("🗑️ 清空对话", use_container_width=True, key=f"clear_writing_chat_{selected_ch_num}"):
                st.session_state[chat_key] = []
                st.rerun()

        # 批量写作（折叠）
        st.divider()
        with st.expander("📦 批量写作", expanded=False):
            writable = [c for c in chapters if c.status in ("outlined", "writing", "review_pending", "reviewed")]
            if not writable:
                st.info("没有待写作的章节")
            else:
                st.caption(f"共 {len(writable)} 个待写章节")
                bb1, bb2 = st.columns(2)
                with bb1:
                    batch_start = st.selectbox("起始章节", [c.chapter_number for c in writable],
                                                format_func=lambda n: f"第{n}章", key="batch_start")
                with bb2:
                    end_opts = [c.chapter_number for c in writable if c.chapter_number >= batch_start]
                    batch_end = st.selectbox("结束章节", end_opts,
                                              index=min(len(end_opts) - 1, 4),
                                              format_func=lambda n: f"第{n}章", key="batch_end")
                selected_range = [c.chapter_number for c in writable if batch_start <= c.chapter_number <= batch_end]
                if selected_range:
                    st.caption(f"将写作 **{len(selected_range)}** 章（第{selected_range[0]}~{selected_range[-1]}章）")
                    bp1, bp2 = st.columns(2)
                    with bp1:
                        batch_words  = st.slider("每章目标字数", 1000, 6000, 3000, step=500, key="batch_words")
                    with bp2:
                        batch_polish = st.toggle("自动润色", value=True, key="batch_polish")
                    if st.button("🚀 开始批量写作", use_container_width=True, type="primary",
                                 disabled=(st.session_state.is_writing or st.session_state.batch_writing or not can_edit(novel_id))):
                        st.session_state.batch_writing = True
                        workflow = load_novel(novel_id)
                        with st.status(f"批量写作中 (0/{len(selected_range)})…", expanded=True) as batch_status:
                            for idx, ch_num in enumerate(selected_range):
                                batch_status.update(label=f"批量写作中 ({idx}/{len(selected_range)})…")
                                st.write(f"**[{idx+1}/{len(selected_range)}] 正在生成第{ch_num}章…**")
                                ph = st.empty()
                                result = workflow.write_and_review_chapter(
                                    chapter_number=ch_num, word_target=batch_words,
                                    auto_polish=batch_polish, progress_callback=lambda m, _p=ph: _p.caption(m)
                                )
                                ph.empty()
                                if result.success:
                                    st.write(f"✅ 第{ch_num}章完成 · {result.data.get('word_count',0):,}字 · 评分 {result.data.get('overall_score',0):.1f}/10")
                                else:
                                    st.write(f"❌ 第{ch_num}章失败：{result.message}")
                            batch_status.update(label="批量写作完成！", state="complete")
                        workflow.close()
                        st.session_state.batch_writing = False
                        st.rerun()

    # ─── 右栏：章节内容 ──────────────────────────────────
    with col_content:
        # 执行写作
        output_area  = st.empty()
        status_area  = st.empty()

        if write_btn or rewrite_btn:
            st.session_state.is_writing = True

            with st.spinner(f"正在生成第{selected_ch_num}章…"):
                output_area.text_area("生成内容（实时显示）", value="", height=500, key="stream_display")

                def progress_cb(msg: str):
                    status_area.info(msg)

                try:
                    workflow = load_novel(novel_id)
                    result = workflow.write_and_review_chapter(
                        chapter_number=selected_ch_num,
                        word_target=word_target,
                        auto_polish=auto_polish,
                        progress_callback=progress_cb,
                    )
                    workflow.close()
                    st.session_state.is_writing = False

                    if result.success:
                        status_area.empty()
                        score  = result.data.get("overall_score", 0)
                        passed = result.data.get("review_passed", True)
                        if passed:
                            st.success(f"✅ 第{selected_ch_num}章生成完成！评分：{score:.1f}/10")
                        else:
                            st.warning(f"⚠️ 章节生成完成，存在问题。评分：{score:.1f}/10")

                        report = result.data.get("review_report", {})
                        if report.get("conflicts"):
                            with st.expander(f"📋 审核报告（{len(report['conflicts'])}个问题）"):
                                st.markdown(f"**摘要：** {report.get('summary', '')}")
                                for c in report["conflicts"]:
                                    sev  = c.get("severity", 0)
                                    icon = "🔴" if sev >= 7 else ("🟡" if sev >= 4 else "🟢")
                                    st.markdown(f"{icon} **[{c.get('type')}] 严重度{sev}**")
                                    st.markdown(f"- {c.get('description', '')}")
                        st.rerun()
                    else:
                        st.session_state.is_writing = False
                        st.error(f"生成失败：{result.message}")
                except Exception as e:
                    st.session_state.is_writing = False
                    st.error(f"生成出错：{e}")

        # 章节内容区（tabs）
        if selected_ch and selected_ch.content:
            # 待应用的 AI 建议
            pending = st.session_state.get(pending_key)

            tab_text, tab_review, tab_reader, tab_history, tab_comment = st.tabs([
                "📝 正文", "📋 审核", "📖 读者模拟", "📜 版本历史", "💬 评论"
            ])

            with tab_text:
                text_key = f"edit_content_{novel_id}_{selected_ch_num}"

                if pending:
                    st.info("💡 AI 建议的新版文本已就绪，编辑后点「保存」写入")
                    col_discard, _ = st.columns([1, 3])
                    with col_discard:
                        if st.button("❌ 放弃建议", key="discard_writing_pending"):
                            st.session_state[pending_key] = None
                            st.rerun()
                    # Pre-fill text area with AI suggestion
                    if st.session_state.get(text_key) != pending:
                        st.session_state[text_key] = pending

                if pending or save_edit_mode:
                    if text_key not in st.session_state:
                        st.session_state[text_key] = selected_ch.content

                    st.text_area(
                        f"第{selected_ch_num}章正文（编辑模式）",
                        key=text_key, height=560
                    )
                    change_summary = st.text_input("修改说明（可选）", placeholder="例如：修改了结尾段落")
                    if st.button("💾 保存修改", type="primary", disabled=not can_edit(novel_id)):
                        with st.spinner("保存中…"):
                            try:
                                workflow = load_novel(novel_id)
                                save_result = workflow.update_chapter_content(
                                    selected_ch_num,
                                    st.session_state[text_key],
                                    change_summary or "用户手动编辑"
                                )
                                workflow.close()
                                if save_result.success:
                                    st.session_state[pending_key] = None
                                    st.success("✅ 修改已保存")
                                    if save_result.data.get("review_report", {}).get("conflicts"):
                                        st.warning("检测到潜在冲突，请检查审核 tab")
                                    st.rerun()
                                else:
                                    st.error(save_result.message)
                            except Exception as e:
                                st.error(f"保存失败：{e}")
                else:
                    st.text_area(
                        f"第{selected_ch_num}章正文",
                        value=selected_ch.content,
                        height=560,
                        key=f"view_content_{selected_ch_num}",
                        disabled=True
                    )

                st.divider()
                render_approval_status(novel_id, selected_ch)

            with tab_review:
                report = selected_ch.get_review_report() if selected_ch.review_report else {}
                score  = selected_ch.review_score or 0
                if report:
                    st.metric("审核评分", f"{score:.1f}/10")
                    st.markdown(f"**摘要：** {report.get('summary', '')}")
                    conflicts = report.get("conflicts", [])
                    if conflicts:
                        for c in conflicts:
                            sev  = c.get("severity", 0)
                            icon = "🔴" if sev >= 7 else ("🟡" if sev >= 4 else "🟢")
                            with st.container(border=True):
                                st.markdown(f"{icon} **[{c.get('type', '')}] 严重度 {sev}**")
                                st.markdown(f"- 位置：`{c.get('location', '')[:100]}`")
                                st.markdown(f"- 描述：{c.get('description', '')}")
                                if c.get("solutions"):
                                    st.markdown("- 建议：" + " / ".join(c["solutions"][:2]))
                    else:
                        st.success("没有发现明显冲突")
                else:
                    st.info("暂无审核报告，生成章节后自动审核")

            with tab_reader:
                st.markdown("### 📖 读者模拟测试")
                st.caption("模拟爽文读者、文学爱好者、轻小说读者三种视角")

                if st.button("🎭 运行读者模拟", use_container_width=True, help="约需 30-60 秒"):
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

                        readers      = feedback.get("readers", {})
                        reader_keys  = ["power_fantasy", "literary", "light_novel"]
                        reader_icons = {"power_fantasy": "⚡", "literary": "📚", "light_novel": "🎮"}
                        reader_cols  = st.columns(3)

                        for col, rkey in zip(reader_cols, reader_keys):
                            rdata = readers.get(rkey, {})
                            if not rdata: continue
                            with col:
                                with st.container(border=True):
                                    st.markdown(f"#### {reader_icons.get(rkey, '📖')} {rdata.get('label', rkey)}")
                                    st.metric("平均分", f"{rdata.get('average', 0):.1f}")
                                    for dim, sc in rdata.get("scores", {}).items():
                                        st.progress(max(0.0, min(1.0, sc / 10)), text=f"{dim}: {sc}/10")
                                    if rdata.get("comment"):
                                        st.caption(rdata["comment"][:200])

                        highlights   = feedback.get("highlights", [])
                        suggestions  = feedback.get("improvement_suggestions", [])
                        if highlights or suggestions:
                            with st.expander("💡 亮点与改进建议"):
                                if highlights:
                                    st.markdown("**亮点：**")
                                    for h in highlights: st.markdown(f"- {h}")
                                if suggestions:
                                    st.markdown("**改进建议：**")
                                    for s in suggestions: st.markdown(f"- {s}")

            with tab_history:
                db = get_db()
                versions = db.query(ContentVersion).filter(
                    ContentVersion.chapter_id == selected_ch.id
                ).order_by(ContentVersion.version_number.desc()).limit(5).all()
                db.close()

                if versions:
                    for v in versions:
                        type_label = {"draft": "初稿", "polished": "润色版",
                                      "user_edit": "用户编辑", "reviewed": "审核版"}.get(v.version_type, v.version_type)
                        st.markdown(f"**v{v.version_number}** · {type_label} · {v.change_summary or ''}")
                        if v.content:
                            st.text(v.content[:150] + "…")
                        st.divider()
                else:
                    st.info("暂无版本历史")

            with tab_comment:
                render_chapter_comments(novel_id, selected_ch.id)

        elif not st.session_state.is_writing:
            if selected_ch and not selected_ch.content:
                with st.container(border=True):
                    st.info(f"第{selected_ch_num}章尚未生成内容，点击左侧「🚀 生成本章」开始创作")
                    if selected_ch.outline_core_event:
                        st.markdown(f"**章纲 - 核心事件：** {selected_ch.outline_core_event}")
                    if selected_ch.outline_conflict:
                        st.markdown(f"**主要冲突：** {selected_ch.outline_conflict}")
