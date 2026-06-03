"""
大纲管理页面
章纲展示、编辑、重新生成、影响分析
"""

import json

import streamlit as st

from core.models import get_db, Novel, Chapter, Volume
from core.workflow import load_novel
from core.permissions import can_edit
from ui.helpers import format_chapter_status, format_approval_badge
from ui.components.alerts import show_foreshadowing_alerts, show_outline_impact


def page_outline():
    st.title("🗂 大纲管理")
    novel_id = st.session_state.novel_id

    db = get_db()
    novel = db.query(Novel).filter(Novel.id == novel_id).first()
    chapters = db.query(Chapter).filter(Chapter.novel_id == novel_id).order_by(Chapter.chapter_number).all()
    volumes = db.query(Volume).filter(Volume.novel_id == novel_id).order_by(Volume.volume_number).all()
    db.close()

    if not novel:
        st.error("项目不存在")
        return

    # 状态总览
    total = len(chapters)
    published = sum(1 for c in chapters if c.status == "published")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总章数", total)
    col2.metric("已完成", published)
    col3.metric("总字数", f"{sum(c.word_count or 0 for c in chapters):,}")
    col4.metric("进度", f"{published/total*100:.0f}%" if total else "0%")

    # 伏笔调度警告（只在有过期/即将到期时展示）
    max_published = max((c.chapter_number for c in chapters if c.status == "published"), default=0)
    show_foreshadowing_alerts(novel_id, max_published)

    # 操作按钮
    st.divider()
    col_gen, col_ch = st.columns(2)
    with col_gen:
        if st.button("🤖 重新生成大纲", use_container_width=True, help="会覆盖现有章纲！",
                     disabled=not can_edit(novel_id)):
            st.session_state["confirm_regen"] = True

        if st.session_state.get("confirm_regen"):
            with st.container(border=True):
                st.warning("⚠️ 重新生成大纲将覆盖所有现有章纲，是否继续？")
                new_ch_count = st.number_input("总章节数", min_value=10, max_value=500, value=total or 100, step=10)
                c1, c2 = st.columns(2)
                if c1.button("确认重新生成", type="primary"):
                    progress_ph = st.empty()
                    workflow = load_novel(novel_id)
                    result = workflow.generate_outline(
                        total_chapters=new_ch_count,
                        progress_callback=lambda m: progress_ph.info(m)
                    )
                    workflow.close()
                    progress_ph.empty()
                    st.session_state["confirm_regen"] = False
                    if result.success:
                        st.success(result.message)
                        st.rerun()
                    else:
                        st.error(result.message)
                if c2.button("取消"):
                    st.session_state["confirm_regen"] = False
                    st.rerun()

    # 卷大纲展示
    if volumes:
        st.markdown("### 卷大纲")
        for vol in volumes:
            vol_chapters = [c for c in chapters
                            if vol.start_chapter and vol.end_chapter
                            and vol.start_chapter <= c.chapter_number <= vol.end_chapter]
            with st.expander(
                f"第{vol.volume_number}卷《{vol.title}》  第{vol.start_chapter}~{vol.end_chapter}章",
                expanded=False
            ):
                if vol.summary:
                    st.markdown(f"**简介：** {vol.summary}")
                if vol.main_conflict:
                    st.markdown(f"**主要矛盾：** {vol.main_conflict}")
                st.caption(f"共 {len(vol_chapters)} 章")

    # 章纲列表
    st.divider()
    st.markdown("### 章节大纲")

    # 过滤控件
    filter_col1, filter_col2 = st.columns([1, 2])
    with filter_col1:
        status_filter = st.selectbox(
            "状态筛选",
            ["全部", "未完成", "已完成", "有章纲"],
            key="outline_filter"
        )
    with filter_col2:
        search_ch = st.text_input("搜索章节", placeholder="章节标题或关键词", key="outline_search")

    # 过滤章节
    filtered_chapters = chapters
    if status_filter == "未完成":
        filtered_chapters = [c for c in chapters if c.status != "published"]
    elif status_filter == "已完成":
        filtered_chapters = [c for c in chapters if c.status == "published"]
    elif status_filter == "有章纲":
        filtered_chapters = [c for c in chapters if c.status != "outline_pending"]
    if search_ch:
        filtered_chapters = [c for c in filtered_chapters
                              if search_ch in (c.title or "") or search_ch in (c.outline_core_event or "")]

    if not filtered_chapters:
        st.info("没有匹配的章节")
    else:
        for chapter in filtered_chapters:
            status_badge = format_chapter_status(chapter.status)
            approval = format_approval_badge(chapter.approval_status) if chapter.status == "published" else ""
            with st.expander(
                f"第{chapter.chapter_number}章《{chapter.title or '未命名'}》 {status_badge} {approval}",
                expanded=False
            ):
                tab_view, tab_edit = st.tabs(["查看", "编辑章纲"])

                with tab_view:
                    c1, c2 = st.columns(2)
                    with c1:
                        if chapter.outline_core_event:
                            st.markdown(f"**核心事件：** {chapter.outline_core_event}")
                        if chapter.outline_conflict:
                            st.markdown(f"**主要冲突：** {chapter.outline_conflict}")
                        if chapter.outline_scene:
                            st.markdown(f"**场景：** {chapter.outline_scene}")
                        if chapter.outline_emotion:
                            st.markdown(f"**情感基调：** {chapter.outline_emotion}")
                    with c2:
                        chars_list = chapter.get_outline_characters()
                        if chars_list:
                            st.markdown(f"**出场人物：** {', '.join(chars_list)}")
                        if chapter.outline_foreshadowing_set:
                            try:
                                fs = json.loads(chapter.outline_foreshadowing_set)
                                if fs: st.markdown(f"**埋下伏笔：** {', '.join(fs)}")
                            except (json.JSONDecodeError, TypeError):
                                pass
                        if chapter.outline_foreshadowing_collect:
                            try:
                                fc = json.loads(chapter.outline_foreshadowing_collect)
                                if fc: st.markdown(f"**回收伏笔：** {', '.join(fc)}")
                            except (json.JSONDecodeError, TypeError):
                                pass
                        if chapter.outline_ending:
                            st.markdown(f"**结尾方式：** {chapter.outline_ending}")
                        if chapter.word_count:
                            st.markdown(f"**字数：** {chapter.word_count:,}")

                    if chapter.status == "published" and chapter.content:
                        st.divider()
                        st.markdown("**正文预览（前300字）**")
                        st.text(chapter.content[:300] + "...")

                    # 前往写作按钮
                    if chapter.status in ("outlined", "writing", "review_pending"):
                        if st.button(f"✍️ 写作第{chapter.chapter_number}章", key=f"write_from_outline_{chapter.chapter_number}"):
                            st.session_state.writing_chapter = chapter.chapter_number
                            st.session_state.page = "写作"
                            st.rerun()

                with tab_edit:
                    was_published = (chapter.status == "published")
                    if was_published:
                        st.warning(
                            "⚠️ 该章节已写完。修改章纲后，本章将被标记为「需重审」，"
                            "且可能影响后续已完成章节的连续性。"
                        )

                    with st.form(f"outline_edit_{chapter.chapter_number}"):
                        new_title = st.text_input("章节标题", value=chapter.title or "")
                        new_core = st.text_area(
                            "核心事件 ⚡️",
                            value=chapter.outline_core_event or "", height=60,
                            help="变更核心事件会触发后续章节影响分析"
                        )
                        new_conflict = st.text_area("主要冲突", value=chapter.outline_conflict or "", height=60)
                        new_scene = st.text_area("场景设定", value=chapter.outline_scene or "", height=60)
                        new_emotion = st.text_input("情感基调", value=chapter.outline_emotion or "")
                        new_ending = st.text_input(
                            "结尾方式 ⚡️",
                            value=chapter.outline_ending or "",
                            help="变更结尾方式会触发后续章节影响分析"
                        )

                        # 伏笔字段（逗号分隔字符串）
                        def _fs_to_str(json_field_val):
                            if not json_field_val:
                                return ""
                            try:
                                items = json.loads(json_field_val)
                                return ", ".join(items) if items else ""
                            except Exception:
                                return json_field_val

                        new_fs_set = st.text_input(
                            "埋下的伏笔（逗号分隔）",
                            value=_fs_to_str(chapter.outline_foreshadowing_set),
                            help="本章新埋下的伏笔名称，多个用逗号分隔"
                        )
                        new_fs_collect = st.text_input(
                            "回收的伏笔（逗号分隔）",
                            value=_fs_to_str(chapter.outline_foreshadowing_collect),
                            help="本章回收的伏笔名称，多个用逗号分隔"
                        )

                        edit_reason = st.text_input(
                            "修改原因（可选）",
                            placeholder="例如：调整节奏，把高潮提前一章"
                        )

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
                            # 伏笔字段：转回 JSON 数组格式
                            new_fs_set_list = [s.strip() for s in new_fs_set.split(",") if s.strip()]
                            old_fs_set_list = json.loads(chapter.outline_foreshadowing_set or "[]")
                            if new_fs_set_list != old_fs_set_list:
                                updates["outline_foreshadowing_set"] = json.dumps(new_fs_set_list, ensure_ascii=False)
                            new_fs_collect_list = [s.strip() for s in new_fs_collect.split(",") if s.strip()]
                            old_fs_collect_list = json.loads(chapter.outline_foreshadowing_collect or "[]")
                            if new_fs_collect_list != old_fs_collect_list:
                                updates["outline_foreshadowing_collect"] = json.dumps(new_fs_collect_list, ensure_ascii=False)

                            if not updates:
                                st.info("没有检测到修改")
                            else:
                                with st.spinner("保存章纲并分析影响..."):
                                    workflow = load_novel(novel_id)
                                    result = workflow.update_chapter_outline(
                                        chapter.chapter_number, updates, edit_reason
                                    )
                                    workflow.close()
                                if result.success:
                                    st.success(f"✅ {result.message}")
                                    affected = result.data.get("affected_chapters", [])
                                    if affected:
                                        show_outline_impact(
                                            chapter.chapter_number, affected
                                        )
                                    st.rerun()
                                else:
                                    st.error(result.message)
