"""
大纲管理页面
整体大纲 / 章节大纲 / 导入文档
"""

import json

import streamlit as st

from core.models import get_db, Novel, Chapter, Volume, NovelOutline
from core.workflow import load_novel
from core.permissions import can_edit
from core.agents import OutlineAgent
from core.llm import DEFAULT_MODEL_ID
from ui.helpers import format_chapter_status, format_approval_badge
from ui.components.alerts import show_foreshadowing_alerts, show_outline_impact
from ui.components.model_selector import build_model_options


def page_outline():
    st.title("🗂 大纲管理")
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
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总章数", total)
    col2.metric("已完成", published)
    col3.metric("总字数", f"{sum(c.word_count or 0 for c in chapters):,}")
    col4.metric("进度", f"{published/total*100:.0f}%" if total else "0%")

    tab1, tab2, tab3 = st.tabs(["📖 整体大纲", "📋 章节大纲", "📄 导入文档"])

    # ─────────────────────────────────────────────────
    # Tab 1: 整体大纲
    # ─────────────────────────────────────────────────
    with tab1:
        # 重新生成大纲
        if can_edit(novel_id):
            if st.button("🤖 重新生成大纲", help="会覆盖现有章纲！"):
                st.session_state["confirm_regen"] = True

            if st.session_state.get("confirm_regen"):
                with st.container(border=True):
                    st.warning("⚠️ 重新生成大纲将覆盖所有现有章纲，是否继续？")
                    new_ch_count = st.number_input("总章节数", min_value=10, max_value=500,
                                                    value=total or 100, step=10)
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

        st.divider()

        # 整体大纲展示与编辑
        if novel_outline:
            st.markdown("### 整体大纲")
            with st.form("total_outline_form"):
                premise = st.text_area("前提设定", value=novel_outline.premise or "", height=80)
                theme = st.text_area("核心主题", value=novel_outline.theme or "", height=60)
                main_conflict = st.text_area("全书主要矛盾", value=novel_outline.main_conflict or "", height=80)
                protagonist_arc = st.text_area("主角成长弧光", value=novel_outline.protagonist_arc or "", height=80)
                ending_summary = st.text_area("结局概要", value=novel_outline.ending_summary or "", height=80)

                st.markdown("**三幕结构**")
                try:
                    story_struct = json.loads(novel_outline.story_structure) if novel_outline.story_structure else {}
                except Exception:
                    story_struct = {}
                act1 = st.text_area("第一幕", value=story_struct.get("act1", ""), height=60)
                act2 = st.text_area("第二幕", value=story_struct.get("act2", ""), height=60)
                act3 = st.text_area("第三幕", value=story_struct.get("act3", ""), height=60)

                if st.form_submit_button("💾 保存整体大纲", disabled=not can_edit(novel_id)):
                    from core.memory import MemoryManager
                    mem = MemoryManager(novel_id)
                    mem.global_mem.save_outline({
                        "novel_id": novel_id,
                        "premise": premise.strip(),
                        "theme": theme.strip(),
                        "main_conflict": main_conflict.strip(),
                        "protagonist_arc": protagonist_arc.strip(),
                        "ending_summary": ending_summary.strip(),
                        "story_structure": json.dumps(
                            {"act1": act1.strip(), "act2": act2.strip(), "act3": act3.strip()},
                            ensure_ascii=False
                        ),
                    })
                    mem.close()
                    st.success("✅ 整体大纲已保存")
                    st.rerun()
        else:
            st.info("尚未生成整体大纲，点击「重新生成大纲」按钮开始。")

        # 卷大纲
        if volumes:
            st.divider()
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

    # ─────────────────────────────────────────────────
    # Tab 2: 章节大纲
    # ─────────────────────────────────────────────────
    with tab2:
        max_published = max((c.chapter_number for c in chapters if c.status == "published"), default=0)
        show_foreshadowing_alerts(novel_id, max_published)

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

                        if chapter.status in ("outlined", "writing", "review_pending"):
                            if st.button(f"✍️ 写作第{chapter.chapter_number}章",
                                         key=f"write_from_outline_{chapter.chapter_number}"):
                                st.session_state.writing_chapter = chapter.chapter_number
                                st.session_state.page = "写作"
                                st.rerun()

                    with tab_edit:
                        if chapter.status == "published":
                            st.warning("⚠️ 该章节已写完，修改章纲后本章将被标记为「需重审」。")

                        with st.form(f"outline_edit_{chapter.chapter_number}"):
                            new_title = st.text_input("章节标题", value=chapter.title or "")
                            new_core = st.text_area("核心事件 ⚡️", value=chapter.outline_core_event or "", height=60,
                                                    help="变更核心事件会触发后续章节影响分析")
                            new_conflict = st.text_area("主要冲突", value=chapter.outline_conflict or "", height=60)
                            new_scene = st.text_area("场景设定", value=chapter.outline_scene or "", height=60)
                            new_emotion = st.text_input("情感基调", value=chapter.outline_emotion or "")
                            new_ending = st.text_input("结尾方式 ⚡️", value=chapter.outline_ending or "",
                                                       help="变更结尾方式会触发后续章节影响分析")

                            def _fs_to_str(json_field_val):
                                if not json_field_val:
                                    return ""
                                try:
                                    items = json.loads(json_field_val)
                                    return ", ".join(items) if items else ""
                                except Exception:
                                    return json_field_val

                            new_fs_set = st.text_input("埋下的伏笔（逗号分隔）",
                                                       value=_fs_to_str(chapter.outline_foreshadowing_set))
                            new_fs_collect = st.text_input("回收的伏笔（逗号分隔）",
                                                           value=_fs_to_str(chapter.outline_foreshadowing_collect))
                            edit_reason = st.text_input("修改原因（可选）",
                                                        placeholder="例如：调整节奏，把高潮提前一章")

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
                                new_fs_set_list = [s.strip() for s in new_fs_set.split(",") if s.strip()]
                                old_fs_set_list = json.loads(chapter.outline_foreshadowing_set or "[]")
                                if new_fs_set_list != old_fs_set_list:
                                    updates["outline_foreshadowing_set"] = json.dumps(
                                        new_fs_set_list, ensure_ascii=False)
                                new_fs_collect_list = [s.strip() for s in new_fs_collect.split(",") if s.strip()]
                                old_fs_collect_list = json.loads(chapter.outline_foreshadowing_collect or "[]")
                                if new_fs_collect_list != old_fs_collect_list:
                                    updates["outline_foreshadowing_collect"] = json.dumps(
                                        new_fs_collect_list, ensure_ascii=False)

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
                                            show_outline_impact(chapter.chapter_number, affected)
                                        st.rerun()
                                    else:
                                        st.error(result.message)

    # ─────────────────────────────────────────────────
    # Tab 3: 导入文档
    # ─────────────────────────────────────────────────
    with tab3:
        st.markdown("### 📄 导入大纲/设定文档")
        st.caption("粘贴你自己编写的大纲、世界观、人物设定等内容，AI 会识别并分别填入对应字段。已存在的数据不会被覆盖。")

        if not can_edit(novel_id):
            st.warning("仅主笔可以导入文档")
        else:
            # 选择解析模型
            options, label_map = build_model_options()
            default_idx = next((i for i, lbl in enumerate(options) if label_map[lbl] == DEFAULT_MODEL_ID), 0)
            parse_model_label = st.selectbox("解析模型", options, index=default_idx, key="parse_model_select")
            parse_model_id = label_map.get(parse_model_label, DEFAULT_MODEL_ID)

            doc_text = st.text_area(
                "粘贴文档内容",
                height=300,
                placeholder="可以是任意格式：大纲、世界观设定、人物档案、章节列表……AI 会自动识别。",
                key="import_doc_text"
            )

            col_parse, col_clear = st.columns([2, 1])
            with col_parse:
                parse_btn = st.button("🔍 AI 解析", type="primary", disabled=not doc_text.strip(),
                                      use_container_width=True)
            with col_clear:
                if st.button("清空", use_container_width=True):
                    st.session_state["import_doc_text"] = ""
                    st.session_state["import_parsed_result"] = None
                    st.rerun()

            if parse_btn and doc_text.strip():
                with st.spinner("AI 正在解析文档…"):
                    try:
                        agent = OutlineAgent(novel_id, parse_model_id)
                        parsed = agent.parse_document(doc_text.strip())
                        agent.close()
                        st.session_state["import_parsed_result"] = parsed
                    except Exception as e:
                        st.error(f"解析失败：{e}")

            # 展示解析结果预览
            parsed_result = st.session_state.get("import_parsed_result")
            if parsed_result:
                st.divider()
                st.markdown("#### 解析结果预览")

                total_outline = parsed_result.get("total_outline", {})
                world_setting = parsed_result.get("world_setting", {})
                characters = parsed_result.get("characters", [])
                chapters_parsed = parsed_result.get("chapters", [])

                has_content = False

                if any(v for v in total_outline.values() if v):
                    has_content = True
                    with st.expander(f"📖 整体大纲（{sum(1 for v in total_outline.values() if v)} 个字段）",
                                     expanded=True):
                        for k, v in total_outline.items():
                            if v:
                                label = {"premise": "前提设定", "theme": "核心主题",
                                         "main_conflict": "主要矛盾", "protagonist_arc": "主角弧光",
                                         "ending_summary": "结局概要", "story_structure": "三幕结构"}.get(k, k)
                                st.markdown(f"**{label}：** {json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v}")

                if world_setting:
                    has_content = True
                    with st.expander(f"🌍 世界观设定（{len(world_setting)} 条）", expanded=True):
                        for k, v in world_setting.items():
                            if v:
                                st.markdown(f"**{k}：** {v}")

                if characters:
                    has_content = True
                    with st.expander(f"👤 人物档案（{len(characters)} 个）", expanded=True):
                        for ch in characters:
                            name = ch.get("name", "未命名")
                            role = ch.get("role", "")
                            st.markdown(f"- **{name}**（{role}）：{ch.get('personality', '') or ch.get('background', '')[:60] if ch.get('personality') or ch.get('background') else ''}...")

                if chapters_parsed:
                    has_content = True
                    with st.expander(f"📋 章节大纲（{len(chapters_parsed)} 章）", expanded=False):
                        for ch in chapters_parsed[:10]:
                            st.markdown(f"- 第{ch.get('chapter_number')}章《{ch.get('title', '')}》：{ch.get('outline_core_event', '')[:60]}…")
                        if len(chapters_parsed) > 10:
                            st.caption(f"…共 {len(chapters_parsed)} 章，仅展示前 10 章")

                if not has_content:
                    st.warning("未从文档中识别到有效内容，请检查文档格式或换用其他模型重试。")
                else:
                    st.divider()
                    if st.button("✅ 确认导入", type="primary", use_container_width=True):
                        with st.spinner("正在写入数据库…"):
                            workflow = load_novel(novel_id)
                            result = workflow.import_document_data(parsed_result)
                            workflow.close()
                        if result.success:
                            st.success(f"✅ 导入完成：{result.message}")
                            st.session_state["import_parsed_result"] = None
                        else:
                            st.error(result.message)
