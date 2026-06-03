"""
写作页面
章节生成、编辑、审核报告、读者模拟、批量写作、版本历史
"""

import json

import streamlit as st

from core.models import get_db, Chapter, ContentVersion
from core.workflow import load_novel
from core.permissions import can_edit, can_approve
from ui.helpers import format_chapter_status, format_approval_badge
from ui.components.collaboration import render_chapter_comments, render_approval_status


def page_writing():
    st.title("✍️ 写作")
    novel_id = st.session_state.novel_id

    db = get_db()
    chapters = db.query(Chapter).filter(Chapter.novel_id == novel_id).order_by(Chapter.chapter_number).all()
    db.close()

    if not chapters:
        st.warning("请先在「大纲管理」页面生成章纲")
        return

    # 章节选择
    col_sel, col_info = st.columns([2, 3])
    with col_sel:
        unwritten = [c for c in chapters if c.status in ("outlined", "writing", "review_pending", "reviewed")]
        published = [c for c in chapters if c.status == "published"]

        st.markdown("### 选择章节")
        chapter_options = {
            f"第{c.chapter_number}章 《{c.title or '未命名'}》 {format_chapter_status(c.status)}"
            f" {format_approval_badge(c.approval_status) if c.status == 'published' else ''}": c.chapter_number
            for c in chapters
        }

        # 默认选择上次写作的章节或第一个未完成章节
        default_idx = 0
        if st.session_state.writing_chapter:
            for i, (label, num) in enumerate(chapter_options.items()):
                if num == st.session_state.writing_chapter:
                    default_idx = i
                    break

        selected_label = st.selectbox("选择章节", list(chapter_options.keys()), index=default_idx)
        selected_ch_num = chapter_options[selected_label]
        st.session_state.writing_chapter = selected_ch_num

        # 写作参数
        st.divider()
        st.markdown("### 写作参数")
        word_target = st.slider("目标字数", 1000, 6000, 3000, step=500)
        auto_polish = st.toggle("自动润色", value=True)

        db = get_db()
        selected_ch = db.query(Chapter).filter(
            Chapter.novel_id == novel_id,
            Chapter.chapter_number == selected_ch_num
        ).first()
        db.close()

    with col_info:
        if selected_ch:
            st.markdown(f"### 第{selected_ch_num}章《{selected_ch.title or '未命名'}》")
            if selected_ch.outline_core_event:
                st.info(f"**核心事件：** {selected_ch.outline_core_event}")
            st.markdown(f"**状态：** {format_chapter_status(selected_ch.status)}")
            if selected_ch.word_count:
                st.markdown(f"**字数：** {selected_ch.word_count:,}")
            if selected_ch.review_score:
                st.markdown(f"**审核评分：** {selected_ch.review_score:.1f}/10")
            if selected_ch.status == "published":
                st.markdown(f"**审阅状态：** {format_approval_badge(selected_ch.approval_status)}")

    st.divider()

    # 写作按钮区域
    btn_col1, btn_col2, btn_col3 = st.columns(3)

    with btn_col1:
        write_btn = st.button("🚀 生成本章", use_container_width=True, type="primary",
                               disabled=(st.session_state.is_writing
                                         or st.session_state.batch_writing
                                         or not can_edit(novel_id)))
    with btn_col2:
        is_published = selected_ch and selected_ch.status == "published"
        rewrite_btn = st.button("🔄 重新生成", use_container_width=True,
                                 disabled=(not is_published or st.session_state.is_writing
                                           or st.session_state.batch_writing
                                           or not can_edit(novel_id)))
    with btn_col3:
        if can_edit(novel_id):
            save_edit_mode = st.toggle("进入编辑模式", value=False)
        else:
            save_edit_mode = False

    # 写作输出区域
    output_area = st.empty()
    status_area = st.empty()

    # 执行写作
    if write_btn or rewrite_btn:
        st.session_state.is_writing = True
        st.session_state.stream_content = ""

        with st.spinner(f"正在生成第{selected_ch_num}章..."):
            content_placeholder = output_area.text_area(
                "生成内容（实时显示）",
                value="",
                height=500,
                key="stream_display"
            )

            # 流式写作回调
            accumulated = []
            def stream_cb(chunk: str):
                accumulated.append(chunk)

            def progress_cb(msg: str):
                status_area.info(msg)

            try:
                workflow = load_novel(novel_id)
                result = workflow.write_and_review_chapter(
                    chapter_number=selected_ch_num,
                    word_target=word_target,
                    auto_polish=auto_polish,
                    progress_callback=progress_cb,
                    stream_callback=stream_cb
                )
                workflow.close()
                st.session_state.is_writing = False

                if result.success:
                    status_area.empty()
                    st.session_state.stream_content = result.data.get("content", "")

                    # 显示审核报告
                    report = result.data.get("review_report", {})
                    score = result.data.get("overall_score", 0)
                    passed = result.data.get("review_passed", True)

                    if passed:
                        st.success(f"✅ 第{selected_ch_num}章生成完成！评分：{score:.1f}/10")
                    else:
                        st.warning(f"⚠️ 章节生成完成，但存在问题。评分：{score:.1f}/10")

                    # 展示审核详情
                    if report.get("conflicts"):
                        with st.expander(f"📋 审核报告（{len(report['conflicts'])}个问题）"):
                            st.markdown(f"**摘要：** {report.get('summary', '')}")
                            for c in report["conflicts"]:
                                sev = c.get("severity", 0)
                                icon = "🔴" if sev >= 7 else ("🟡" if sev >= 4 else "🟢")
                                st.markdown(f"**{icon} [{c.get('type')}] 严重度{sev}**")
                                st.markdown(f"- 位置：`{c.get('location', '')[:100]}`")
                                st.markdown(f"- 描述：{c.get('description', '')}")
                                if c.get("solutions"):
                                    st.markdown("- 建议：" + " / ".join(c["solutions"][:2]))

                    st.rerun()
                else:
                    st.session_state.is_writing = False
                    st.error(f"生成失败：{result.message}")

            except Exception as e:
                st.session_state.is_writing = False
                st.error(f"生成出错：{e}")

    # 显示已有内容或编辑
    if selected_ch and selected_ch.content:
        if save_edit_mode:
            edited_content = output_area.text_area(
                f"第{selected_ch_num}章正文（编辑模式）",
                value=selected_ch.content,
                height=600,
                key=f"edit_content_{selected_ch_num}"
            )
            change_summary = st.text_input("修改说明（可选）", placeholder="例如：修改了结尾段落")
            if st.button("💾 保存修改内容", type="primary",
                         disabled=not can_edit(novel_id)):
                with st.spinner("保存中..."):
                    try:
                        workflow = load_novel(novel_id)
                        save_result = workflow.update_chapter_content(
                            selected_ch_num, edited_content,
                            change_summary or "用户手动编辑"
                        )
                        workflow.close()
                        if save_result.success:
                            st.success("✅ 修改已保存")
                            # 显示冲突检测结果
                            report = save_result.data.get("review_report", {})
                            if report.get("conflicts"):
                                st.warning(f"检测到 {len(report['conflicts'])} 个潜在冲突，请检查")
                            st.rerun()
                        else:
                            st.error(save_result.message)
                    except Exception as e:
                        st.error(f"保存失败：{e}")
        else:
            output_area.text_area(
                f"第{selected_ch_num}章正文",
                value=selected_ch.content,
                height=600,
                key=f"view_content_{selected_ch_num}",
                disabled=True
            )

        # 审批状态
        st.divider()
        render_approval_status(novel_id, selected_ch)

        # 章节评论
        st.divider()
        render_chapter_comments(novel_id, selected_ch.id)

        # 读者模拟测试
        st.divider()
        st.markdown("### 📖 读者模拟测试")
        st.caption("模拟爽文读者、文学爱好者、轻小说读者三种视角，给出阅读体验评分")

        test_col1, test_col2 = st.columns([1, 2])
        with test_col1:
            if st.button("🎭 运行读者模拟", use_container_width=True,
                         help="模拟三种读者类型阅读本章，给出体验评分和改进建议"):
                with st.spinner("正在模拟读者阅读体验..."):
                    workflow = load_novel(novel_id)
                    result = workflow.reader_test_chapter(
                        selected_ch_num,
                        progress_callback=lambda m: st.toast(m)
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
            except (json.JSONDecodeError, TypeError):
                feedback = {}

            if feedback:
                readers = feedback.get("readers", {})

                with test_col2:
                    if selected_ch.reader_score:
                        st.metric("综合评分", f"{selected_ch.reader_score:.1f}/10")

                # 三种读者的评分卡片
                reader_cols = st.columns(3)
                reader_keys = ["power_fantasy", "literary", "light_novel"]
                reader_icons = {"power_fantasy": "⚡", "literary": "📚", "light_novel": "🎮"}

                for col, rkey in zip(reader_cols, reader_keys):
                    rdata = readers.get(rkey, {})
                    if not rdata:
                        continue
                    with col:
                        with st.container(border=True):
                            icon = reader_icons.get(rkey, "📖")
                            st.markdown(f"#### {icon} {rdata.get('label', rkey)}")
                            st.metric("平均分", f"{rdata.get('average', 0):.1f}")
                            for dim, score in rdata.get("scores", {}).items():
                                bar_pct = max(0.0, min(1.0, score / 10))
                                st.progress(bar_pct, text=f"{dim}: {score}/10")
                            if rdata.get("comment"):
                                st.caption(rdata["comment"][:200])

                # 亮点和建议
                highlights = feedback.get("highlights", [])
                suggestions = feedback.get("improvement_suggestions", [])
                if highlights or suggestions:
                    with st.expander("💡 亮点与改进建议"):
                        if highlights:
                            st.markdown("**亮点摘录：**")
                            for h in highlights:
                                st.markdown(f"- {h}")
                        if suggestions:
                            st.markdown("**改进建议：**")
                            for s in suggestions:
                                st.markdown(f"- {s}")

        # 版本历史
        db = get_db()
        versions = db.query(ContentVersion).filter(
            ContentVersion.chapter_id == selected_ch.id
        ).order_by(ContentVersion.version_number.desc()).limit(5).all()
        db.close()

        if versions:
            with st.expander(f"📜 版本历史（最近{len(versions)}个版本）"):
                for v in versions:
                    type_label = {
                        "draft": "初稿",
                        "polished": "润色版",
                        "user_edit": "用户编辑",
                        "reviewed": "审核版"
                    }.get(v.version_type, v.version_type)
                    st.markdown(f"**v{v.version_number}** · {type_label} · {v.change_summary or ''}")
                    if v.content:
                        st.text(v.content[:150] + "...")
                    st.divider()
    elif not st.session_state.is_writing:
        output_area.info("点击「生成本章」开始创作，内容将实时显示在这里")

    # ---- 批量写作 ----
    st.divider()
    with st.expander("📦 批量写作", expanded=False):
        writable = [c for c in chapters
                    if c.status in ("outlined", "writing", "review_pending", "reviewed")]

        if not writable:
            st.info("没有待写作的章节，所有章节均已完成")
        else:
            st.caption(f"共 {len(writable)} 个待写作章节可供批量生成")

            batch_col1, batch_col2 = st.columns(2)
            with batch_col1:
                batch_start = st.selectbox(
                    "起始章节",
                    [c.chapter_number for c in writable],
                    format_func=lambda n: f"第{n}章",
                    key="batch_start",
                )
            with batch_col2:
                end_options = [c.chapter_number for c in writable
                               if c.chapter_number >= batch_start]
                batch_end = st.selectbox(
                    "结束章节",
                    end_options,
                    index=min(len(end_options) - 1, 4),
                    format_func=lambda n: f"第{n}章",
                    key="batch_end",
                )

            selected_range = [c.chapter_number for c in writable
                              if batch_start <= c.chapter_number <= batch_end]

            if selected_range:
                st.markdown(
                    f"将写作 **{len(selected_range)}** 章"
                    f"（第{selected_range[0]}章 ~ 第{selected_range[-1]}章）"
                )

                param_col1, param_col2 = st.columns(2)
                with param_col1:
                    batch_words = st.slider("每章目标字数", 1000, 6000, 3000, step=500,
                                            key="batch_words")
                with param_col2:
                    batch_polish = st.toggle("自动润色", value=True, key="batch_polish")

                batch_disabled = (st.session_state.is_writing
                                  or st.session_state.batch_writing
                                  or not can_edit(novel_id))
                if st.button("🚀 开始批量写作", use_container_width=True, type="primary",
                             disabled=batch_disabled,
                             help=f"将按顺序自动写完 {len(selected_range)} 章"):
                    st.session_state.batch_writing = True
                    workflow = load_novel(novel_id)

                    with st.status(
                        f"批量写作中 (0/{len(selected_range)})...",
                        expanded=True
                    ) as batch_status:
                        batch_results = []

                        for idx, ch_num in enumerate(selected_range):
                            batch_status.update(
                                label=f"批量写作中 ({idx}/{len(selected_range)})..."
                            )
                            st.write(f"**[{idx+1}/{len(selected_range)}] 正在生成第{ch_num}章...**")

                            progress_ph = st.empty()
                            def _batch_progress_cb(msg, _ph=progress_ph):
                                _ph.caption(msg)

                            result = workflow.write_and_review_chapter(
                                chapter_number=ch_num,
                                word_target=batch_words,
                                auto_polish=batch_polish,
                                progress_callback=_batch_progress_cb,
                            )
                            progress_ph.empty()

                            if result.success:
                                score = result.data.get("overall_score", 0)
                                wc = result.data.get("word_count", 0)
                                st.write(f"✅ 第{ch_num}章完成 · {wc:,}字 · 评分 {score:.1f}/10")
                            else:
                                st.write(f"❌ 第{ch_num}章失败：{result.message}")

                            batch_results.append({
                                "chapter_number": ch_num,
                                "success": result.success,
                                "word_count": result.data.get("word_count", 0) if result.success else 0,
                                "score": result.data.get("overall_score", 0) if result.success else 0,
                                "message": result.message,
                            })

                        batch_status.update(
                            label=f"批量写作完成！({len(selected_range)} 章)",
                            state="complete",
                            expanded=True,
                        )

                    workflow.close()
                    st.session_state.batch_writing = False

                    # 汇总报告
                    ok = [r for r in batch_results if r["success"]]
                    fail = [r for r in batch_results if not r["success"]]
                    total_words = sum(r["word_count"] for r in ok)
                    avg_score = sum(r["score"] for r in ok) / len(ok) if ok else 0

                    st.divider()
                    st.markdown("### 📊 批量写作报告")
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("成功", f"{len(ok)}/{len(batch_results)}")
                    m2.metric("总字数", f"{total_words:,}")
                    m3.metric("平均评分", f"{avg_score:.1f}/10")
                    m4.metric("失败", len(fail))

                    if fail:
                        with st.expander(f"❌ {len(fail)} 章写作失败"):
                            for r in fail:
                                st.markdown(f"- 第{r['chapter_number']}章：{r['message']}")

                    st.toast(f"批量写作完成：{len(ok)}/{len(batch_results)} 章成功！", icon="✅")
                    st.balloons()
