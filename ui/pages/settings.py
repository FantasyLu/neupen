"""
设定管理页面
"""

import json

import streamlit as st

from core.models import get_db, Novel, Chapter, Character, Foreshadowing, NovelDocument
from core.workflow import load_novel
from core.llm import DEFAULT_MODEL_ID, check_api_key, get_model_info
from core.permissions import can_edit
from core.platform_styles import load_platform_styles
from ui.components.model_selector import (
    build_model_options, render_model_card, render_all_models_panel,
    FOLLOW_LABEL, build_agent_model_options,
)
from ui.components.api_key import render_api_key_form
from ui.components.alerts import show_impact_report


def page_settings():
    st.title("⚙️ 设定管理")
    novel_id = st.session_state.novel_id

    db = get_db()
    novel = db.query(Novel).filter(Novel.id == novel_id).first()
    db.close()

    if not novel:
        st.error("项目不存在")
        return

    # 全局助手需要的 session 键
    pending_key = f"settings_pending_{novel_id}"
    if pending_key not in st.session_state:
        st.session_state[pending_key] = {}  # {doc_type: content}

    # ─── 设定文档 + 管理 ───────────────────────────
    with st.container():
        tab_doc, tab_world, tab_chars, tab_fs, tab_model, tab_style, tab_platform, tab_quality, tab_api = st.tabs([
            "📄 文档设定", "🌍 世界观", "👤 人物档案", "📌 伏笔", "🤖 模型", "✍️ 风格", "📺 平台", "⚡ 写作质量", "🔑 API Key"
        ])

        # ──────────────────────────────────────────────────
        # Tab: 文档设定
        # ──────────────────────────────────────────────────
        with tab_doc:
            st.caption("以 Markdown 格式自由记录各类设定，与左侧 AI 协作完成内容")

            db = get_db()
            docs = db.query(NovelDocument).filter(
                NovelDocument.novel_id == novel_id
            ).order_by(NovelDocument.sort_order, NovelDocument.id).all()
            db.close()
            doc_map = {d.doc_type: d for d in docs}

            pending = st.session_state.get(pending_key, {})

            DEFAULT_DOC_TYPES = [
                ("background",  "🌍 背景设定",      0),
                ("system",      "⚡ 科技/魔法体系",  1),
                ("characters",  "👤 人物设定",       2),
            ]

            for doc_type, doc_label, sort_idx in DEFAULT_DOC_TYPES:
                doc = doc_map.get(doc_type)
                textarea_key = f"settings_doc_{novel_id}_{doc_type}"

                # 初始化文本框
                if textarea_key not in st.session_state:
                    st.session_state[textarea_key] = doc.content if doc else ""
                # 如果有 AI 建议，注入文本框
                if doc_type in pending:
                    st.session_state[textarea_key] = pending.pop(doc_type)
                    st.session_state[pending_key] = pending

                has_content = bool(st.session_state.get(textarea_key, "").strip())
                with st.expander(doc_label, expanded=not has_content):
                    view_mode = st.radio(
                        "模式", ["✏️ 编辑", "👁️ 预览"],
                        horizontal=True, key=f"docmode_{novel_id}_{doc_type}",
                        label_visibility="collapsed"
                    )
                    if view_mode == "✏️ 编辑":
                        st.text_area(
                            "内容", height=300,
                            placeholder=f"以 Markdown 格式记录{doc_label.split(' ', 1)[-1]}，或通过左侧 AI 生成…",
                            key=textarea_key,
                            label_visibility="collapsed"
                        )
                        sc1, sc2 = st.columns([3, 1])
                        if sc1.button("💾 保存", key=f"save_{novel_id}_{doc_type}",
                                      disabled=not can_edit(novel_id), use_container_width=True):
                            new_content = st.session_state.get(textarea_key, "")
                            db = get_db()
                            existing = db.query(NovelDocument).filter_by(
                                novel_id=novel_id, doc_type=doc_type
                            ).first()
                            if existing:
                                existing.content = new_content
                            else:
                                db.add(NovelDocument(
                                    novel_id=novel_id, doc_type=doc_type,
                                    title=doc_label.split(" ", 1)[-1],
                                    content=new_content, sort_order=sort_idx
                                ))
                            db.commit()
                            db.close()
                            st.success("✅ 已保存")
                            st.rerun()
                        if sc2.button("↺", key=f"refresh_{novel_id}_{doc_type}",
                                      help="从数据库重新加载", use_container_width=True):
                            db = get_db()
                            d = db.query(NovelDocument).filter_by(novel_id=novel_id, doc_type=doc_type).first()
                            db.close()
                            st.session_state[textarea_key] = d.content if d else ""
                            st.rerun()
                    else:
                        content_val = st.session_state.get(textarea_key, "")
                        if content_val:
                            st.markdown(content_val)
                        else:
                            st.info("暂无内容，切换到「编辑」模式开始记录")

            # 自定义文档
            custom_docs = [d for d in docs if d.doc_type == "custom"]
            if custom_docs:
                st.divider()
                st.markdown("**自定义文档**")
                for cdoc in custom_docs:
                    ctk = f"settings_doc_{novel_id}_custom_{cdoc.id}"
                    if ctk not in st.session_state:
                        st.session_state[ctk] = cdoc.content or ""
                    with st.expander(f"📝 {cdoc.title}", expanded=False):
                        vm = st.radio("模式", ["✏️ 编辑", "👁️ 预览"],
                                      horizontal=True, key=f"cdmode_{novel_id}_{cdoc.id}",
                                      label_visibility="collapsed")
                        if vm == "✏️ 编辑":
                            new_title   = st.text_input("标题", value=cdoc.title, key=f"cdtitle_{novel_id}_{cdoc.id}")
                            st.text_area("内容", height=250, key=ctk, label_visibility="collapsed")
                            c1, c2 = st.columns([3, 1])
                            if c1.button("💾 保存", key=f"cdsave_{novel_id}_{cdoc.id}",
                                         disabled=not can_edit(novel_id), use_container_width=True):
                                db = get_db()
                                obj = db.query(NovelDocument).filter_by(id=cdoc.id).first()
                                obj.title   = new_title.strip() or obj.title
                                obj.content = st.session_state.get(ctk, "")
                                db.commit()
                                db.close()
                                st.success("✅ 已保存")
                                st.rerun()
                            if c2.button("🗑️", key=f"cddel_{novel_id}_{cdoc.id}",
                                         disabled=not can_edit(novel_id), use_container_width=True):
                                db = get_db()
                                db.query(NovelDocument).filter_by(id=cdoc.id).delete()
                                db.commit()
                                db.close()
                                st.rerun()
                        else:
                            val = st.session_state.get(ctk, "")
                            if val:
                                st.markdown(val)
                            else:
                                st.info("暂无内容")

            st.divider()
            with st.form("new_doc_form"):
                st.markdown("**➕ 新增自定义文档**")
                new_doc_title = st.text_input("文档标题", placeholder="例如：门派体系、宗教设定…")
                if st.form_submit_button("新增", disabled=not can_edit(novel_id)):
                    if new_doc_title.strip():
                        db = get_db()
                        db.add(NovelDocument(
                            novel_id=novel_id, doc_type="custom",
                            title=new_doc_title.strip(), content="",
                            sort_order=len(custom_docs) + 10
                        ))
                        db.commit()
                        db.close()
                        st.rerun()
                    else:
                        st.warning("请输入文档标题")

        # ──────────────────────────────────────────────────
        # Tab: 世界观设定
        # ──────────────────────────────────────────────────
        with tab_world:
            st.markdown("### 世界观设定")
            world_setting = novel.get_world_setting() if novel.world_setting else {}

            with st.form("world_setting_form"):
                updated_world = {}
                default_keys = ["基本规则", "特殊体系", "社会结构", "地理环境"]
                all_keys = list(dict.fromkeys(list(world_setting.keys()) + default_keys))

                for key in all_keys:
                    updated_world[key] = st.text_area(
                        key, value=world_setting.get(key, ""), height=80, key=f"world_{key}"
                    )

                custom_key = st.text_input("新增设定项名称（可选）")
                custom_val = st.text_area("新增设定内容", height=60)
                save_btn = st.form_submit_button("💾 保存世界观设定", use_container_width=True,
                                                  disabled=not can_edit(novel_id))

            if save_btn:
                if custom_key.strip():
                    updated_world[custom_key.strip()] = custom_val
                updated_world = {k: v for k, v in updated_world.items() if v.strip()}
                with st.spinner("保存并分析影响…"):
                    workflow = load_novel(novel_id)
                    result = workflow.update_world_setting(updated_world)
                    workflow.close()
                if result.success:
                    st.success(f"✅ {result.message}")
                    impact = result.data.get("impact")
                    if impact and impact.get("affected_chapters"):
                        show_impact_report(impact, "世界观设定")
                    st.rerun()
                else:
                    st.error(result.message)

            st.divider()
            st.markdown("### 小说基本信息")
            with st.form("novel_info_form"):
                new_title  = st.text_input("标题", value=novel.title)
                new_author = st.text_input("作者/笔名", value=novel.author or "")
                new_logline = st.text_area("简介（一句话灵感）", value=novel.logline or "", height=80)
                new_genre  = st.text_input("题材", value=novel.genre or "")
                new_style  = st.text_area("写作风格要求", value=novel.writing_style or "", height=80)
                if st.form_submit_button("💾 保存基本信息", disabled=not can_edit(novel_id)):
                    db = get_db()
                    obj = db.query(Novel).filter(Novel.id == novel_id).first()
                    obj.title = new_title.strip()
                    obj.author = new_author.strip()
                    obj.logline = new_logline.strip()
                    obj.genre = new_genre.strip()
                    obj.writing_style = new_style.strip()
                    db.commit()
                    db.close()
                    st.success("✅ 基本信息已更新")
                    st.rerun()

        # ──────────────────────────────────────────────────
        # Tab: 人物档案
        # ──────────────────────────────────────────────────
        with tab_chars:
            db = get_db()
            chars = db.query(Character).filter(Character.novel_id == novel_id).order_by(
                Character.is_main.desc(), Character.name
            ).all()
            db.close()

            col_header, col_btn = st.columns([3, 1])
            with col_header:
                st.markdown(f"### 人物档案（共 {len(chars)} 位）")
            with col_btn:
                if st.button("🤖 AI生成人物", use_container_width=True, disabled=not can_edit(novel_id)):
                    with st.spinner("根据大纲生成人物档案…"):
                        workflow = load_novel(novel_id)
                        result = workflow.generate_characters_from_outline(
                            progress_callback=lambda m: st.toast(m)
                        )
                        workflow.close()
                        if result.success:
                            st.success(result.message)
                            st.rerun()
                        else:
                            st.error(result.message)

            # ── 手动新建人物 ──
            with st.expander("➕ 手动新建人物", expanded=False):
                with st.form("create_character_form"):
                    nc_c1, nc_c2 = st.columns(2)
                    with nc_c1:
                        nc_name = st.text_input("姓名 *")
                        nc_role = st.selectbox("角色定位", ["主角", "配角", "反派", "导师", "其他"], index=1)
                        nc_age = st.text_input("年龄", placeholder="如：18岁、20-30岁")
                        nc_gender = st.text_input("性别", placeholder="如：男、女")
                    with nc_c2:
                        nc_personality = st.text_area("性格特征", height=68)
                        nc_motivations = st.text_input("动机/目标")
                        nc_is_main = st.checkbox("主要人物")
                    nc_appearance = st.text_area("外貌描述", height=60)
                    nc_background = st.text_area("背景故事", height=60)
                    nc_growth = st.text_area("成长弧光", height=60)
                    if st.form_submit_button("✅ 创建人物", disabled=not can_edit(novel_id)):
                        if not nc_name.strip():
                            st.error("姓名为必填项")
                        else:
                            char_data = {"name": nc_name.strip(), "role": nc_role, "is_main": nc_is_main}
                            if nc_age.strip(): char_data["age"] = nc_age.strip()
                            if nc_gender.strip(): char_data["gender"] = nc_gender.strip()
                            if nc_personality.strip(): char_data["personality"] = nc_personality.strip()
                            if nc_motivations.strip(): char_data["motivations"] = nc_motivations.strip()
                            if nc_appearance.strip(): char_data["appearance"] = nc_appearance.strip()
                            if nc_background.strip(): char_data["background"] = nc_background.strip()
                            if nc_growth.strip(): char_data["growth_arc"] = nc_growth.strip()
                            workflow = load_novel(novel_id)
                            workflow.memory.global_mem.save_character(char_data)
                            workflow.close()
                            st.success(f"✅ 人物「{nc_name.strip()}」已创建")
                            st.rerun()

            if not chars:
                st.info("暂无人物档案，点击「AI生成人物」根据大纲自动生成，或手动新建")
            else:
                search = st.text_input("🔍 搜索人物", placeholder="姓名或角色")
                filtered = [c for c in chars if not search or search in c.name or search in (c.role or "")]

                for char in filtered:
                    with st.expander(
                        f"{'⭐ ' if char.is_main else ''}{char.name}  ·  {char.role or '未设定'}  ·  {char.age or ''}",
                        expanded=False
                    ):
                        c1, c2 = st.columns(2)
                        with c1:
                            st.markdown(f"**性格：** {char.personality or '未设定'}")
                            st.markdown(f"**动机：** {char.motivations or '未设定'}")
                            st.markdown(f"**当前状态：** {char.current_state or '未设定'}")
                        with c2:
                            st.markdown(f"**说话风格：** {char.speech_patterns or '未设定'}")
                            st.markdown(f"**行为习惯：** {char.behavioral_patterns or '未设定'}")
                        if char.background:
                            st.markdown(f"**背景故事：** {char.background}")
                        if char.growth_arc:
                            st.markdown(f"**成长弧光：** {char.growth_arc}")

                        with st.form(f"char_edit_{char.id}"):
                            st.markdown("**编辑人物档案**")
                            ec1, ec2 = st.columns(2)
                            with ec1:
                                e_name       = st.text_input("姓名", value=char.name or "")
                                e_role       = st.text_input("角色定位", value=char.role or "")
                                e_age        = st.text_input("年龄", value=char.age or "")
                                e_gender     = st.text_input("性别", value=char.gender or "")
                                e_is_main    = st.checkbox("主要人物", value=bool(char.is_main))
                            with ec2:
                                e_personality  = st.text_area("性格", value=char.personality or "", height=60)
                                e_motivations  = st.text_input("动机/目标", value=char.motivations or "")
                                e_state        = st.text_input("当前状态", value=char.current_state or "")
                                e_secrets      = st.text_area("隐藏秘密", value=char.secrets or "", height=60)
                            e_appearance   = st.text_area("外貌描述", value=char.appearance or "", height=60)
                            e_background   = st.text_area("背景故事", value=char.background or "", height=60)
                            e_growth       = st.text_area("成长弧光", value=char.growth_arc or "", height=60)
                            e_speech       = st.text_area("说话风格/口头禅", value=char.speech_patterns or "", height=60)
                            e_behavior     = st.text_area("行为习惯", value=char.behavioral_patterns or "", height=60)
                            try:
                                _aliases_list = json.loads(char.aliases) if char.aliases else []
                            except (json.JSONDecodeError, TypeError):
                                _aliases_list = []
                            e_aliases      = st.text_input("别名/绰号（逗号分隔）", value=", ".join(_aliases_list) if _aliases_list else "")
                            e_abilities    = st.text_area("能力/技能（每行一个）", value="\n".join(char.get_abilities()) if char.get_abilities() else "", height=60)
                            try:
                                _rels_dict = json.loads(char.relationships) if char.relationships else {}
                            except (json.JSONDecodeError, TypeError):
                                _rels_dict = {}
                            e_relationships = st.text_area("人际关系（JSON 格式）",
                                                           value=json.dumps(_rels_dict, ensure_ascii=False, indent=2) if _rels_dict else "",
                                                           height=80)
                            if st.form_submit_button("💾 保存人物档案", use_container_width=True,
                                                      disabled=not can_edit(novel_id)):
                                updates = {
                                    "name": e_name.strip(),
                                    "role": e_role.strip(),
                                    "age": e_age.strip(),
                                    "gender": e_gender.strip(),
                                    "is_main": e_is_main,
                                    "personality": e_personality.strip(),
                                    "motivations": e_motivations.strip(),
                                    "current_state": e_state.strip(),
                                    "secrets": e_secrets.strip(),
                                    "appearance": e_appearance.strip(),
                                    "background": e_background.strip(),
                                    "growth_arc": e_growth.strip(),
                                    "speech_patterns": e_speech.strip(),
                                    "behavioral_patterns": e_behavior.strip(),
                                    "aliases": json.dumps([a.strip() for a in e_aliases.split(",") if a.strip()], ensure_ascii=False),
                                    "abilities": json.dumps([a.strip() for a in e_abilities.split("\n") if a.strip()], ensure_ascii=False),
                                    "relationships": e_relationships.strip() if e_relationships.strip() else None,
                                }
                                workflow = load_novel(novel_id)
                                workflow.memory.global_mem.save_character(updates)
                                workflow.close()
                                st.success(f"✅ 人物「{e_name.strip()}」已更新")
                                st.rerun()

        # ──────────────────────────────────────────────────
        # Tab: 伏笔管理
        # ──────────────────────────────────────────────────
        with tab_fs:
            db = get_db()
            fs_list = db.query(Foreshadowing).filter(
                Foreshadowing.novel_id == novel_id
            ).order_by(Foreshadowing.importance.desc(), Foreshadowing.set_chapter).all()
            published_ch_obj = db.query(Chapter).filter(
                Chapter.novel_id == novel_id, Chapter.status == "published"
            ).order_by(Chapter.chapter_number.desc()).first()
            current_ch_num = published_ch_obj.chapter_number if published_ch_obj else 0
            db.close()

            active    = [f for f in fs_list if f.status == "active"]
            collected = [f for f in fs_list if f.status == "collected"]

            def _urgency_key(f):
                if f.collect_by_chapter:
                    if f.collect_by_chapter < current_ch_num:         return (0, f.collect_by_chapter)
                    elif f.collect_by_chapter <= current_ch_num + 10: return (1, f.collect_by_chapter)
                    else:                                               return (2, f.collect_by_chapter)
                return (3, 999)

            active_sorted = sorted(active, key=_urgency_key)

            col1, col2, col3 = st.columns(3)
            col1.metric("未回收", len(active))
            col2.metric("已回收", len(collected))
            col3.metric("总计",   len(fs_list))

            bc1, bc2 = st.columns(2)
            no_deadline_count = sum(1 for f in active if not f.collect_by_chapter)
            with bc1:
                if st.button(f"🤖 AI分配截止章节（{no_deadline_count}条待分配）",
                             use_container_width=True,
                             disabled=(no_deadline_count == 0 or not can_edit(novel_id))):
                    with st.spinner("AI 分配截止章节…"):
                        workflow = load_novel(novel_id)
                        result = workflow.assign_foreshadowing_deadlines(progress_callback=lambda m: st.toast(m))
                        workflow.close()
                    if result.success:
                        st.success(result.message)
                        st.rerun()
                    else:
                        st.error(result.message)
            with bc2:
                if st.button("🔄 同步大纲伏笔", use_container_width=True, disabled=not can_edit(novel_id)):
                    with st.spinner("同步中…"):
                        workflow = load_novel(novel_id)
                        result = workflow.sync_outline_foreshadowings()
                        workflow.close()
                    if result.success:
                        st.success(result.message)
                        st.rerun()
                    else:
                        st.error(result.message)

            if active_sorted:
                st.markdown("### ⏳ 待回收伏笔")
                for fs in active_sorted:
                    imp_color = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(fs.importance, "⚪")
                    deadline_badge = ""
                    if fs.collect_by_chapter:
                        if fs.collect_by_chapter < current_ch_num:
                            deadline_badge = f"  ❌ 已过期（应第{fs.collect_by_chapter}章前回收）"
                        elif fs.collect_by_chapter <= current_ch_num + 10:
                            deadline_badge = f"  ⏰ 还有{fs.collect_by_chapter - current_ch_num}章到期"
                        else:
                            deadline_badge = f"  📅 第{fs.collect_by_chapter}章前"

                    with st.container(border=True):
                        fc1, fc2, fc3, fc4 = st.columns([3, 1, 1, 1])
                        with fc1:
                            st.markdown(f"{imp_color} **{fs.name}**（第{fs.set_chapter}章）{deadline_badge}")
                            if fs.description: st.caption(fs.description)
                            if fs.notes:       st.caption(f"备注：{fs.notes}")
                        with fc2:
                            collect_ch = st.number_input("回收章节", min_value=1, value=fs.set_chapter + 1,
                                                          key=f"collect_{fs.id}", label_visibility="collapsed")
                        with fc3:
                            if st.button("标记回收", key=f"btn_collect_{fs.id}", disabled=not can_edit(novel_id)):
                                db = get_db()
                                obj = db.query(Foreshadowing).filter_by(id=fs.id).first()
                                obj.status = "collected"
                                obj.collect_chapter = collect_ch
                                db.commit()
                                db.close()
                                st.rerun()
                        with fc4:
                            _edit_key = f"fs_editing_{fs.id}"
                            if st.button("✏️ 编辑", key=f"btn_edit_fs_{fs.id}", disabled=not can_edit(novel_id)):
                                st.session_state[_edit_key] = not st.session_state.get(_edit_key, False)
                                st.rerun()
                        if st.session_state.get(f"fs_editing_{fs.id}", False):
                            with st.form(f"fs_edit_form_{fs.id}"):
                                fe_name = st.text_input("伏笔名称", value=fs.name or "")
                                fe_desc = st.text_area("详细描述", value=fs.description or "", height=60)
                                fe_c1, fe_c2 = st.columns(2)
                                with fe_c1:
                                    fe_set_ch = st.number_input("埋下章节", min_value=1, value=fs.set_chapter or 1, key=f"fe_set_{fs.id}")
                                    fe_imp = st.selectbox("重要程度", ["high", "medium", "low"],
                                                          index=["high", "medium", "low"].index(fs.importance or "medium"),
                                                          format_func=lambda x: {"high": "高", "medium": "中", "low": "低"}[x],
                                                          key=f"fe_imp_{fs.id}")
                                with fe_c2:
                                    fe_deadline = st.number_input("最晚回收章节（0=不限）", min_value=0,
                                                                   value=fs.collect_by_chapter or 0, key=f"fe_dl_{fs.id}")
                                    fe_notes = st.text_input("备注", value=fs.notes or "", key=f"fe_notes_{fs.id}")
                                fe_set_content = st.text_area("埋下时的内容", value=fs.set_content or "", height=60, key=f"fe_sc_{fs.id}")
                                fe_collect_content = st.text_area("回收时的内容", value=fs.collect_content or "", height=60, key=f"fe_cc_{fs.id}")
                                if st.form_submit_button("💾 保存修改"):
                                    db = get_db()
                                    obj = db.query(Foreshadowing).filter_by(id=fs.id).first()
                                    obj.name = fe_name.strip() or obj.name
                                    obj.description = fe_desc.strip()
                                    obj.set_chapter = fe_set_ch
                                    obj.importance = fe_imp
                                    obj.collect_by_chapter = fe_deadline if fe_deadline > 0 else None
                                    obj.notes = fe_notes.strip() or None
                                    obj.set_content = fe_set_content.strip() or None
                                    obj.collect_content = fe_collect_content.strip() or None
                                    db.commit()
                                    db.close()
                                    st.session_state[f"fs_editing_{fs.id}"] = False
                                    st.success("✅ 伏笔已更新")
                                    st.rerun()

            if collected:
                with st.expander(f"✅ 已回收（{len(collected)}条）", expanded=False):
                    for fs in collected:
                        imp_color = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(fs.importance, "⚪")
                        st.markdown(f"{imp_color} **{fs.name}** — 第{fs.set_chapter}章 → 第{fs.collect_chapter or '?'}章")
                        if fs.description: st.caption(fs.description)

            st.divider()
            st.markdown("### ➕ 手动记录伏笔")
            with st.form("add_foreshadowing"):
                fs_name = st.text_input("伏笔名称 *")
                fs_desc = st.text_area("详细描述", height=60)
                fa1, fa2 = st.columns(2)
                with fa1:
                    fs_ch  = st.number_input("埋下章节", min_value=1, value=max(1, current_ch_num))
                    fs_imp = st.selectbox("重要程度", ["high", "medium", "low"],
                                          format_func=lambda x: {"high": "高", "medium": "中", "low": "低"}[x])
                with fa2:
                    fs_deadline = st.number_input("最晚回收章节（0=不限）", min_value=0, value=0)
                    fs_notes    = st.text_input("备注（可选）")
                if st.form_submit_button("记录伏笔", disabled=not can_edit(novel_id)):
                    if fs_name.strip():
                        db = get_db()
                        db.add(Foreshadowing(
                            novel_id=novel_id, name=fs_name.strip(), description=fs_desc,
                            set_chapter=int(fs_ch), importance=fs_imp, status="active",
                            collect_by_chapter=int(fs_deadline) if fs_deadline > 0 else None,
                            notes=fs_notes.strip() or None,
                        ))
                        db.commit()
                        db.close()
                        st.success("✅ 伏笔已记录")
                        st.rerun()
                    else:
                        st.warning("请输入伏笔名称")

        # ──────────────────────────────────────────────────
        # Tab: 模型设置
        # ──────────────────────────────────────────────────
        with tab_model:
            st.markdown("### 🤖 项目默认模型")
            st.caption("所有未单独配置的 Agent 均跟随此模型")

            with st.form("model_change_form"):
                options, label_map = build_model_options()
                current_model = novel.llm_model or DEFAULT_MODEL_ID
                current_idx = next((i for i, lbl in enumerate(options) if label_map[lbl] == current_model), 0)
                new_label = st.selectbox("为本项目选择默认大模型", options, index=current_idx)
                if st.form_submit_button("💾 保存默认模型", use_container_width=True,
                                          disabled=not can_edit(novel_id)):
                    new_model_id = label_map.get(new_label, DEFAULT_MODEL_ID)
                    ok, err_msg = check_api_key(new_model_id)
                    if not ok:
                        st.error(f"API Key 未配置：{err_msg}")
                    else:
                        db = get_db()
                        obj = db.query(Novel).filter(Novel.id == novel_id).first()
                        obj.llm_model = new_model_id
                        db.commit()
                        db.close()
                        st.success(f"✅ 已切换为 {get_model_info(new_model_id)['display_name']}")
                        st.rerun()

            st.markdown("#### 当前默认模型详情")
            render_model_card(current_model)

            st.divider()

            _AGENT_RECOMMENDED = {
                "outline": "claude-opus-4-6", "character": "claude-sonnet-4-6",
                "writer":  "claude-sonnet-4-6", "reviewer":  "claude-opus-4-6",
                "polisher": "claude-opus-4-6",  "reader":    "claude-sonnet-4-6",
            }
            _AGENT_META = [
                ("outline",   "🗂 大纲师",   "奠定全书结构，一次性任务"),
                ("character", "👤 人设师",   "生成人物档案，一次性"),
                ("writer",    "✍️ 写手部",   "逐章写作，最高频调用"),
                ("reviewer",  "🔍 审核师",   "每章质检，质量把关"),
                ("polisher",  "✨ 润色师",   "每章润色"),
                ("reader",    "📖 读者模拟", "模拟读者视角，按需调用"),
            ]
            st.markdown("### ⚙️ 各 Agent 分工配置")
            st.caption("写手部用 Sonnet 节省成本，审核师/润色师用 Opus 保障质量")

            rc1, rc2 = st.columns(2)
            if rc1.button("🌟 一键推荐配置", use_container_width=True):
                st.session_state["agent_model_draft"] = dict(_AGENT_RECOMMENDED)
                st.rerun()
            if rc2.button("🔄 全部跟随默认", use_container_width=True):
                st.session_state["agent_model_draft"] = {k: "" for k in _AGENT_RECOMMENDED}
                st.rerun()

            agent_opts, agent_lmap = build_agent_model_options()
            with st.form("agent_model_form"):
                selections = {}
                for key, ag_label, ag_desc in _AGENT_META:
                    draft_vals   = st.session_state.get("agent_model_draft", {})
                    current_val  = draft_vals.get(key, getattr(novel, f"model_{key}", None) or "")
                    current_ag_label = next((l for l, v in agent_lmap.items() if v == current_val), FOLLOW_LABEL)
                    current_ag_idx   = agent_opts.index(current_ag_label) if current_ag_label in agent_opts else 0
                    cc1, cc2 = st.columns([2, 3])
                    with cc1:
                        st.markdown(f"**{ag_label}**")
                        st.caption(ag_desc)
                    with cc2:
                        sel = st.selectbox(ag_label, agent_opts, index=current_ag_idx,
                                           key=f"agent_model_{key}", label_visibility="collapsed")
                        selections[key] = agent_lmap.get(sel, "")

                if st.form_submit_button("💾 保存分工配置", use_container_width=True, type="primary",
                                          disabled=not can_edit(novel_id)):
                    missing = [mid for k, mid in selections.items() if mid and not check_api_key(mid)[0]]
                    if missing:
                        st.error(f"以下模型的 API Key 未配置：{', '.join(missing)}")
                    else:
                        workflow = load_novel(novel_id)
                        result = workflow.update_agent_models(selections)
                        workflow.close()
                        if result.success:
                            st.session_state.pop("agent_model_draft", None)
                            st.success(f"✅ {result.message}")
                            st.rerun()
                        else:
                            st.error(result.message)

            st.divider()
            st.markdown("### 所有可用模型")
            render_all_models_panel()

        # ──────────────────────────────────────────────────
        # Tab: 风格迁移
        # ──────────────────────────────────────────────────
        with tab_style:
            st.markdown("### ✍️ 风格迁移")
            st.caption("上传喜欢的作家作品片段，AI 提取写作风格，润色时自动模仿该风格")

            db = get_db()
            novel_fresh = db.query(Novel).filter(Novel.id == novel_id).first()
            db.close()
            current_profile = novel_fresh.get_style_profile() if novel_fresh else {}

            if current_profile:
                st.success(f"🎨 **风格迁移已启用**  ·  {current_profile.get('overall_style', '已设置风格档案')}")
                if current_profile.get("polish_instructions"):
                    st.caption(f"润色指令预览：{current_profile['polish_instructions'][:120]}…")
            else:
                st.info("📝 尚未设置风格档案，润色时使用默认风格")

            st.divider()

            # 从已完成章节自动学习
            db2 = get_db()
            approved_count = (
                db2.query(Chapter)
                .filter(
                    Chapter.novel_id == novel_id,
                    Chapter.approval_status == "approved",
                    Chapter.content.isnot(None),
                    Chapter.content != "",
                )
                .count()
            )
            db2.close()

            st.markdown("#### 🔄 从已完成章节自动学习风格")
            if approved_count == 0:
                st.caption("暂无已审核的章节，完成并审核章节后可使用此功能。")
                st.button("🔄 从已完成章节学习", disabled=True, use_container_width=True)
            else:
                st.caption(f"当前已有 **{approved_count}** 章审核通过的内容，AI 将从中提取写作风格特征。")
                if st.button("🔄 从已完成章节学习", use_container_width=True, type="primary",
                             key="auto_learn_style_btn"):
                    with st.spinner(f"正在从 {min(approved_count, 5)} 章内容中学习风格，约需 20-40 秒…"):
                        try:
                            workflow = load_novel(novel_id)
                            result = workflow.auto_learn_style_from_chapters(sample_chapters=5)
                            workflow.close()
                            if result.success:
                                st.session_state["style_profile_draft"] = result.data.get("profile", {})
                                st.success(f"✅ {result.message}")
                                st.rerun()
                            else:
                                st.error(result.message)
                        except Exception as e:
                            st.error(f"学习失败：{e}")

            st.divider()
            st.markdown("#### 第一步：提供参考文本（可选）")
            st.caption("建议选取最能代表该作者风格的段落，500-3000 字效果最佳")

            input_method = st.radio("输入方式", ["✏️ 粘贴文本", "📄 上传 .txt 文件"],
                                     horizontal=True, key="style_input_method")
            reference_text = ""
            if input_method == "✏️ 粘贴文本":
                reference_text = st.text_area("粘贴参考文本", height=200, key="style_paste_text",
                                               placeholder="粘贴喜欢的作家作品段落…")
                if reference_text:
                    st.caption(f"已输入 {len(reference_text)} 字符")
            else:
                uploaded_file = st.file_uploader("上传 .txt 文件", type=["txt"], key="style_file_upload")
                if uploaded_file:
                    try:
                        raw = uploaded_file.read()
                        try:    reference_text = raw.decode("utf-8")
                        except: reference_text = raw.decode("gbk", errors="replace")
                        st.success(f"✅ {uploaded_file.name}（{len(reference_text)} 字符）")
                        with st.expander("预览（前500字）"):
                            st.text(reference_text[:500])
                    except Exception as e:
                        st.error(f"文件读取失败：{e}")

            analyze_btn = st.button("🔍 分析写作风格",
                                     disabled=(not reference_text.strip() or not can_edit(novel_id)),
                                     use_container_width=True, type="primary")
            if analyze_btn:
                if len(reference_text.strip()) < 100:
                    st.warning("参考文本太短，建议至少 100 字")
                else:
                    progress_ph = st.empty()
                    with st.spinner("正在分析写作风格，约需 20-40 秒…"):
                        workflow = load_novel(novel_id)
                        result = workflow.analyze_writing_style(
                            reference_text, progress_callback=lambda m: progress_ph.info(m)
                        )
                        workflow.close()
                    progress_ph.empty()
                    if result.success:
                        st.session_state["style_profile_draft"] = result.data["profile"]
                        st.success("✅ 风格分析完成！请在下方确认后保存")
                        st.rerun()
                    else:
                        st.error(f"分析失败：{result.message}")

            st.divider()
            st.markdown("#### 第二步：确认并编辑风格特征")
            draft = st.session_state.get("style_profile_draft") or current_profile

            if not draft:
                st.info("完成第一步的风格分析后，这里将展示提取出的风格特征")
            else:
                STYLE_FIELDS = [
                    ("overall_style",        "总体风格定位",   60),
                    ("sentence_patterns",    "句式特征",       80),
                    ("vocabulary",           "词汇风格",       80),
                    ("narrative_voice",      "叙述风格",       80),
                    ("dialogue_style",       "对话特点",       70),
                    ("description_style",    "描写特点",       80),
                    ("rhythm_pacing",        "节奏特征",       70),
                    ("emotion_expression",   "情感表达方式",   70),
                    ("transition_style",     "转场方式",       60),
                    ("polish_instructions",  "润色指令（AI 据此润色）", 100),
                ]
                updated_profile = {}
                for key, label, height in STYLE_FIELDS:
                    updated_profile[key] = st.text_area(label, value=draft.get(key, ""), height=height)

                if st.button("💾 保存风格档案", use_container_width=True, type="primary",
                              disabled=not can_edit(novel_id)):
                    updated_profile = {k: v for k, v in updated_profile.items() if v.strip()}
                    with st.spinner("保存中…"):
                        workflow = load_novel(novel_id)
                        result = workflow.update_style_profile(updated_profile)
                        workflow.close()
                    if result.success:
                        st.session_state.pop("style_profile_draft", None)
                        st.success("✅ 风格档案已保存，润色时生效")
                        st.rerun()
                    else:
                        st.error(result.message)

        # ──────────────────────────────────────────────────
        # Tab: 平台风格
        # ──────────────────────────────────────────────────
        with tab_platform:
            st.markdown("### 📺 平台风格配置")
            st.caption("选择目标发布平台和标签，写作时会自动注入平台特有的写作规则")

            db = get_db()
            novel_platform = db.query(Novel).filter(Novel.id == novel_id).first()
            db.close()

            all_styles = load_platform_styles()
            platform_names = list(all_styles.keys())

            current_platform = novel_platform.target_platform or ""
            current_tags = novel_platform.get_target_tags()

            with st.form("platform_config_form"):
                selected_platform = st.selectbox(
                    "目标发布平台",
                    [""] + platform_names,
                    index=(platform_names.index(current_platform) + 1 if current_platform in platform_names else 0),
                    format_func=lambda x: x or "未选择"
                )
                # 显示选中平台下可用的标签
                available_tags = list(all_styles.get(selected_platform, {}).keys()) if selected_platform else []
                default_tag_vals = [t for t in current_tags if t in available_tags] if available_tags else current_tags
                selected_tags = st.multiselect(
                    "创作标签（可多选）",
                    options=available_tags,
                    default=default_tag_vals,
                    disabled=not available_tags,
                ) if available_tags else st.text_input(
                    "目标标签（逗号分隔）",
                    value=", ".join(current_tags) if current_tags else ""
                )
                if st.form_submit_button("💾 保存平台配置", use_container_width=True,
                                          disabled=not can_edit(novel_id)):
                    db = get_db()
                    obj = db.query(Novel).filter(Novel.id == novel_id).first()
                    obj.target_platform = selected_platform or None
                    if available_tags:
                        obj.set_target_tags(selected_tags)
                    else:
                        obj.set_target_tags([t.strip() for t in (selected_tags if isinstance(selected_tags, str) else "").split(",") if t.strip()])
                    db.commit()
                    db.close()
                    st.success("✅ 平台配置已保存")
                    st.rerun()

            # 当前生效的风格描述预览
            if current_platform and current_tags:
                from core.platform_styles import get_style_description
                preview = get_style_description(current_platform, current_tags)
                if preview:
                    with st.expander(f"📋 {current_platform} 风格描述预览", expanded=True):
                        st.info(preview)
            elif selected_platform:
                tags = list(all_styles.get(selected_platform, {}).keys())
                if tags:
                    st.caption(f"该平台可用标签：{'、'.join(tags[:8])}{'…' if len(tags) > 8 else ''}")

        # ──────────────────────────────────────────────────
        # Tab: 写作质量
        # ──────────────────────────────────────────────────
        with tab_quality:
            st.markdown("### ⚡ 写作质量参数")
            st.caption("控制每章写作的审核与重写策略，覆盖全局默认值。留空时使用全局默认。")

            from core.config import (
                AUTO_APPROVE_THRESHOLD as _DEF_APPROVE,
                REVIEW_SCORE_THRESHOLD as _DEF_REVIEW,
                LOW_SCORE_REWRITE_THRESHOLD as _DEF_REWRITE,
                MAX_REVIEW_ITERATIONS as _DEF_ITER,
                MAX_TOTAL_ATTEMPTS as _DEF_TOTAL,
                WORD_COUNT_TOLERANCE as _DEF_TOLERANCE,
            )

            db = get_db()
            novel_q = db.query(Novel).filter(Novel.id == novel_id).first()
            db.close()
            q_cfg = novel_q.get_quality_config() if novel_q else {}

            with st.form("quality_config_form"):
                st.markdown("#### 冲突检测")
                auto_approve = st.number_input(
                    f"严重冲突 severity 阈值（默认 {_DEF_APPROVE}）",
                    min_value=1, max_value=10,
                    value=int(q_cfg.get("auto_approve_threshold", _DEF_APPROVE)),
                    step=1,
                    help="severity ≥ 此值的冲突被认为是「严重冲突」，"
                         "章节含严重冲突时不会通过审核，会继续修改或重写。"
                )

                st.markdown("#### 审核评分")
                review_score = st.number_input(
                    f"审核通过评分（默认 {_DEF_REVIEW}）",
                    min_value=0.0, max_value=10.0,
                    value=float(q_cfg.get("review_score_threshold", _DEF_REVIEW)),
                    step=0.5,
                    help="评分 ≥ 此值且无严重冲突时退出修改循环，章节视为通过审核。"
                )
                rewrite_score = st.number_input(
                    f"触发重写评分（默认 {_DEF_REWRITE}）",
                    min_value=0.0, max_value=10.0,
                    value=float(q_cfg.get("low_score_rewrite_threshold", _DEF_REWRITE)),
                    step=0.5,
                    help="修改循环结束后若评分仍低于此值或含严重冲突，触发整章重写。设为 0 禁用重写。"
                )

                st.markdown("#### 字数控制")
                word_tol = st.number_input(
                    f"字数容差（默认 {int(_DEF_TOLERANCE * 100)}%）",
                    min_value=10, max_value=50,
                    value=int(float(q_cfg.get("word_count_tolerance", _DEF_TOLERANCE)) * 100),
                    step=5, format="%d%%",
                    help="允许实际字数偏离目标字数的比例。30% 时，目标 3000 字允许 2100~3900 字。"
                )

                st.markdown("#### 迭代次数")
                max_iter = st.number_input(
                    f"最大修改轮次（默认 {_DEF_ITER}）",
                    min_value=1, max_value=20,
                    value=int(q_cfg.get("max_review_iterations", _DEF_ITER)),
                    step=1,
                    help="每轮包含一次审核 + 一次修复。达到上限后进入重写阶段（如果评分未达标）。"
                )
                max_total = st.number_input(
                    f"全局审核次数上限（默认 {_DEF_TOTAL}）",
                    min_value=1, max_value=50,
                    value=int(q_cfg.get("max_total_attempts", _DEF_TOTAL)),
                    step=1,
                    help="跨修改轮次和重写轮次的审核总次数上限。达到后自动选历史最高分版本作为终稿。"
                )

                saved = st.form_submit_button("💾 保存质量参数", use_container_width=True,
                                              type="primary", disabled=not can_edit(novel_id))

            if saved:
                new_cfg = {
                    "auto_approve_threshold":    auto_approve,
                    "review_score_threshold":    review_score,
                    "low_score_rewrite_threshold": rewrite_score,
                    "max_review_iterations":     max_iter,
                    "max_total_attempts":        max_total,
                    "word_count_tolerance":      word_tol / 100,
                }
                db = get_db()
                obj = db.query(Novel).filter(Novel.id == novel_id).first()
                obj.set_quality_config(new_cfg)
                db.commit()
                db.close()
                st.success("✅ 写作质量参数已保存")
                st.rerun()

            st.divider()
            st.markdown("#### 当前有效参数")
            db = get_db()
            novel_q2 = db.query(Novel).filter(Novel.id == novel_id).first()
            db.close()
            q2 = novel_q2.get_quality_config() if novel_q2 else {}
            col_a, col_b = st.columns(2)
            with col_a:
                st.metric("严重冲突阈值", int(q2.get("auto_approve_threshold", _DEF_APPROVE)))
                st.metric("审核通过评分", f"{float(q2.get('review_score_threshold', _DEF_REVIEW)):.1f}")
                st.metric("触发重写评分", f"{float(q2.get('low_score_rewrite_threshold', _DEF_REWRITE)):.1f}")
                st.metric("字数容差", f"{int(float(q2.get('word_count_tolerance', _DEF_TOLERANCE)) * 100)}%")
            with col_b:
                st.metric("最大修改轮次", int(q2.get("max_review_iterations", _DEF_ITER)))
                st.metric("全局审核次数上限", int(q2.get("max_total_attempts", _DEF_TOTAL)))
            if not q2:
                st.info("当前使用全局默认值，保存后生效。")

        # ──────────────────────────────────────────────────
        # Tab: API Key
        # ──────────────────────────────────────────────────
        with tab_api:
            st.markdown("### 🔑 API Key 配置")
            st.caption("保存后立即生效，无需重启应用")
            render_api_key_form("settings_api_key_form")
