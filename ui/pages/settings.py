"""
设定管理页面
世界观设定、人物档案、伏笔管理、模型设置、风格迁移、API Key
"""

import json

import streamlit as st

from core.models import get_db, Novel, Chapter, Character, Foreshadowing, NovelDocument
from core.workflow import load_novel
from core.llm import DEFAULT_MODEL_ID, check_api_key, get_model_info
from core.permissions import can_edit
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

    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(["📄 文档设定", "🌍 世界观设定", "👤 人物档案", "📌 伏笔管理", "🤖 模型设置", "✍️ 风格迁移", "🔑 API Key"])

    # ---- 文档设定 ----
    with tab1:
        st.markdown("### 📄 文档设定")
        st.caption("以自由 Markdown 格式记录小说的背景设定、科技/魔法体系、人物设定等，供创作时参考")

        db = get_db()
        docs = db.query(NovelDocument).filter(
            NovelDocument.novel_id == novel_id
        ).order_by(NovelDocument.sort_order, NovelDocument.id).all()
        db.close()

        doc_map = {d.doc_type: d for d in docs}

        # 预置三种文档类型（不存在时自动初始化空文档）
        DEFAULT_DOC_TYPES = [
            ("background",  "🌍 背景设定",       0),
            ("system",      "⚡ 科技/魔法体系",   1),
            ("characters",  "👤 人物设定",        2),
        ]

        for doc_type, doc_label, sort_idx in DEFAULT_DOC_TYPES:
            doc = doc_map.get(doc_type)
            content_val = doc.content if doc else ""

            with st.expander(doc_label, expanded=(not content_val)):
                view_mode = st.radio(
                    "模式", ["✏️ 编辑", "👁️ 预览"],
                    horizontal=True, key=f"docmode_{doc_type}",
                    label_visibility="collapsed"
                )
                if view_mode == "✏️ 编辑":
                    new_content = st.text_area(
                        "内容", value=content_val, height=320,
                        placeholder=f"在此以 Markdown 格式记录{doc_label.split(' ', 1)[-1]}…",
                        key=f"doctxt_{doc_type}", label_visibility="collapsed"
                    )
                    if st.button("💾 保存", key=f"docsave_{doc_type}",
                                 disabled=not can_edit(novel_id), use_container_width=True):
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
                else:
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
                with st.expander(f"📝 {cdoc.title}", expanded=False):
                    view_mode = st.radio(
                        "模式", ["✏️ 编辑", "👁️ 预览"],
                        horizontal=True, key=f"docmode_c{cdoc.id}",
                        label_visibility="collapsed"
                    )
                    if view_mode == "✏️ 编辑":
                        new_title = st.text_input("标题", value=cdoc.title, key=f"cdtitle_{cdoc.id}")
                        new_content = st.text_area(
                            "内容", value=cdoc.content or "", height=280,
                            key=f"cdtxt_{cdoc.id}", label_visibility="collapsed"
                        )
                        col_save, col_del = st.columns([3, 1])
                        if col_save.button("💾 保存", key=f"cdsave_{cdoc.id}",
                                           disabled=not can_edit(novel_id), use_container_width=True):
                            db = get_db()
                            obj = db.query(NovelDocument).filter_by(id=cdoc.id).first()
                            obj.title = new_title.strip() or obj.title
                            obj.content = new_content
                            db.commit()
                            db.close()
                            st.success("✅ 已保存")
                            st.rerun()
                        if col_del.button("🗑️", key=f"cddel_{cdoc.id}",
                                          disabled=not can_edit(novel_id), use_container_width=True):
                            db = get_db()
                            db.query(NovelDocument).filter_by(id=cdoc.id).delete()
                            db.commit()
                            db.close()
                            st.rerun()
                    else:
                        if cdoc.content:
                            st.markdown(cdoc.content)
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
                        title=new_doc_title.strip(),
                        content="",
                        sort_order=len(custom_docs) + 10
                    ))
                    db.commit()
                    db.close()
                    st.rerun()
                else:
                    st.warning("请输入文档标题")

    # ---- 世界观设定 ----
    with tab2:
        st.markdown("### 世界观设定")
        world_setting = novel.get_world_setting() if novel.world_setting else {}

        with st.form("world_setting_form"):
            updated_world = {}
            default_keys = ["基本规则", "特殊体系", "社会结构", "地理环境"]
            all_keys = list(set(list(world_setting.keys()) + default_keys))

            for key in all_keys:
                updated_world[key] = st.text_area(
                    key,
                    value=world_setting.get(key, ""),
                    height=80,
                    key=f"world_{key}"
                )

            custom_key = st.text_input("新增设定项名称（可选）")
            custom_val = st.text_area("新增设定内容", height=60)

            save_btn = st.form_submit_button("💾 保存世界观设定", use_container_width=True,
                                                disabled=not can_edit(novel_id))

        if save_btn:
            if custom_key.strip():
                updated_world[custom_key.strip()] = custom_val
            # 去除空值
            updated_world = {k: v for k, v in updated_world.items() if v.strip()}
            with st.spinner("正在保存并分析影响..."):
                workflow = load_novel(novel_id)
                result = workflow.update_world_setting(updated_world)
                workflow.close()

            if result.success:
                st.success(f"✅ {result.message}")
                impact = result.data.get("impact")
                if impact and impact.get("affected_chapters"):
                    show_impact_report(impact, "世界观设定")
                elif result.data.get("changes"):
                    st.info(f"影响分析：{impact.get('summary', '无已完成章节受影响') if impact else '无已完成章节'}")
                st.rerun()
            else:
                st.error(result.message)

        # 基本信息编辑
        st.divider()
        st.markdown("### 小说基本信息")
        with st.form("novel_info_form"):
            new_title = st.text_input("标题", value=novel.title)
            new_logline = st.text_area("简介（一句话灵感）", value=novel.logline or "", height=80)
            new_genre = st.text_input("题材", value=novel.genre or "")
            new_style = st.text_area("写作风格要求", value=novel.writing_style or "", height=80)
            info_save = st.form_submit_button("💾 保存基本信息",
                                                disabled=not can_edit(novel_id))

        if info_save:
            db = get_db()
            novel_obj = db.query(Novel).filter(Novel.id == novel_id).first()
            novel_obj.title = new_title.strip()
            novel_obj.logline = new_logline.strip()
            novel_obj.genre = new_genre.strip()
            novel_obj.writing_style = new_style.strip()
            db.commit()
            db.close()
            st.success("✅ 基本信息已更新")
            st.rerun()

    # ---- 人物档案 ----
    with tab3:
        db = get_db()
        chars = db.query(Character).filter(Character.novel_id == novel_id).order_by(
            Character.is_main.desc(), Character.name
        ).all()
        db.close()

        col_header, col_btn = st.columns([3, 1])
        with col_header:
            st.markdown(f"### 人物档案（共 {len(chars)} 位）")
        with col_btn:
            if st.button("🤖 AI生成人物", use_container_width=True,
                         disabled=not can_edit(novel_id)):
                with st.spinner("正在根据大纲生成人物档案..."):
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

        if not chars:
            st.info("暂无人物档案，可点击「AI生成人物」根据大纲自动生成")
        else:
            # 搜索过滤
            search = st.text_input("🔍 搜索人物", placeholder="输入姓名或角色")
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

                    # 编辑表单
                    with st.form(f"char_edit_{char.id}"):
                        st.markdown("**快速编辑**")
                        new_state = st.text_input("当前状态", value=char.current_state or "")
                        new_secrets = st.text_area("隐藏秘密", value=char.secrets or "", height=60)
                        new_personality = st.text_area(
                            "性格（如有修改）", value=char.personality or "", height=60
                        )
                        new_motivations = st.text_input(
                            "动机（如有修改）", value=char.motivations or ""
                        )
                        if st.form_submit_button("💾 保存并检测影响",
                                                    disabled=not can_edit(novel_id)):
                            updates = {}
                            if new_state != (char.current_state or ""):
                                updates["current_state"] = new_state
                            if new_secrets != (char.secrets or ""):
                                updates["secrets"] = new_secrets
                            if new_personality != (char.personality or ""):
                                updates["personality"] = new_personality
                            if new_motivations != (char.motivations or ""):
                                updates["motivations"] = new_motivations

                            if not updates:
                                st.info("没有检测到修改")
                            else:
                                with st.spinner("保存中并检测潜在冲突..."):
                                    workflow = load_novel(novel_id)
                                    result = workflow.update_character(char.name, updates)
                                    workflow.close()
                                if result.success:
                                    st.success(f"✅ {result.message}")
                                    conflicts = result.data.get("conflicts", [])
                                    if conflicts:
                                        with st.expander(f"⚠️ 发现 {len(conflicts)} 个潜在冲突"):
                                            for c in conflicts:
                                                st.markdown(f"- **{c.get('conflict_type','冲突')}**：{c.get('description','')}")
                                    st.rerun()
                                else:
                                    st.error(result.message)

    # ---- 伏笔管理 ----
    with tab4:
        db = get_db()
        fs_list = db.query(Foreshadowing).filter(
            Foreshadowing.novel_id == novel_id
        ).order_by(Foreshadowing.importance.desc(), Foreshadowing.set_chapter).all()
        # 当前写作进度（最大已发布章节号）
        published_ch_obj = db.query(Chapter).filter(
            Chapter.novel_id == novel_id,
            Chapter.status == "published"
        ).order_by(Chapter.chapter_number.desc()).first()
        current_ch_num = published_ch_obj.chapter_number if published_ch_obj else 0
        db.close()

        active = [f for f in fs_list if f.status == "active"]
        collected = [f for f in fs_list if f.status == "collected"]

        # 按紧急程度对 active 伏笔排序：过期 > 即将到期 > 正常（有截止）> 无截止
        def _urgency_key(f):
            if f.collect_by_chapter:
                if f.collect_by_chapter < current_ch_num:
                    return (0, f.collect_by_chapter)
                elif f.collect_by_chapter <= current_ch_num + 10:
                    return (1, f.collect_by_chapter)
                else:
                    return (2, f.collect_by_chapter)
            return (3, 999)

        active_sorted = sorted(active, key=_urgency_key)

        col1, col2, col3 = st.columns(3)
        col1.metric("未回收伏笔", len(active))
        col2.metric("已回收伏笔", len(collected))
        col3.metric("总计", len(fs_list))

        # 操作按钮
        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            no_deadline_count = sum(1 for f in active if not f.collect_by_chapter)
            if st.button(
                f"🤖 AI智能分配截止章节（{no_deadline_count}条待分配）",
                use_container_width=True,
                disabled=(no_deadline_count == 0 or not can_edit(novel_id)),
                help="使用AI根据故事结构，为没有截止章节的伏笔自动分配合理的回收时限"
            ):
                with st.spinner("正在AI分配截止章节..."):
                    workflow = load_novel(novel_id)
                    result = workflow.assign_foreshadowing_deadlines(
                        progress_callback=lambda m: st.toast(m)
                    )
                    workflow.close()
                if result.success:
                    st.success(result.message)
                    st.rerun()
                else:
                    st.error(result.message)
        with btn_col2:
            if st.button("🔄 同步大纲伏笔", use_container_width=True,
                         disabled=not can_edit(novel_id),
                         help="将章纲中记录的伏笔名称同步到伏笔库（只新增，不覆盖已有记录）"):
                with st.spinner("正在同步..."):
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

                # 截止状态 badge
                deadline_badge = ""
                if fs.collect_by_chapter:
                    if fs.collect_by_chapter < current_ch_num:
                        deadline_badge = f"  ❌ 已过期（应第{fs.collect_by_chapter}章前回收）"
                    elif fs.collect_by_chapter <= current_ch_num + 10:
                        remaining = fs.collect_by_chapter - current_ch_num
                        deadline_badge = f"  ⏰ 还有{remaining}章到期（第{fs.collect_by_chapter}章前）"
                    else:
                        deadline_badge = f"  📅 第{fs.collect_by_chapter}章前"

                with st.container(border=True):
                    c1, c2, c3 = st.columns([3, 1, 1])
                    with c1:
                        st.markdown(f"{imp_color} **{fs.name}**（第{fs.set_chapter}章埋下）{deadline_badge}")
                        if fs.description:
                            st.caption(fs.description)
                        if fs.notes:
                            st.caption(f"备注：{fs.notes}")
                    with c2:
                        collect_ch = st.number_input(
                            "回收章节", min_value=1, value=fs.set_chapter + 1,
                            key=f"collect_{fs.id}", label_visibility="collapsed"
                        )
                    with c3:
                        if st.button("标记回收", key=f"btn_collect_{fs.id}",
                                     disabled=not can_edit(novel_id)):
                            db = get_db()
                            fs_obj = db.query(Foreshadowing).filter(Foreshadowing.id == fs.id).first()
                            fs_obj.status = "collected"
                            fs_obj.collect_chapter = collect_ch
                            db.commit()
                            db.close()
                            st.rerun()

        # 已回收伏笔（折叠展示）
        if collected:
            with st.expander(f"✅ 已回收伏笔（{len(collected)}条）", expanded=False):
                for fs in collected:
                    imp_color = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(fs.importance, "⚪")
                    st.markdown(
                        f"{imp_color} **{fs.name}** — "
                        f"第{fs.set_chapter}章埋下 → 第{fs.collect_chapter or '?'}章回收"
                    )
                    if fs.description:
                        st.caption(fs.description)

        # 手动添加伏笔
        st.divider()
        st.markdown("### ➕ 手动记录伏笔")
        with st.form("add_foreshadowing"):
            fs_name = st.text_input("伏笔名称 *")
            fs_desc = st.text_area("详细描述", height=60)
            col_a, col_b = st.columns(2)
            with col_a:
                fs_ch = st.number_input("埋下章节", min_value=1, value=max(1, current_ch_num))
                fs_imp = st.selectbox(
                    "重要程度", ["high", "medium", "low"],
                    format_func=lambda x: {"high": "高", "medium": "中", "low": "低"}[x]
                )
            with col_b:
                fs_deadline = st.number_input(
                    "最晚回收章节（0 表示不限）",
                    min_value=0, value=0,
                    help="设置后系统会在截止前提醒你安排回收"
                )
                fs_notes = st.text_input("备注（可选）", placeholder="回收建议、关联线索等")

            if st.form_submit_button("记录伏笔", disabled=not can_edit(novel_id)):
                if fs_name.strip():
                    db = get_db()
                    new_fs = Foreshadowing(
                        novel_id=novel_id,
                        name=fs_name.strip(),
                        description=fs_desc,
                        set_chapter=int(fs_ch),
                        importance=fs_imp,
                        status="active",
                        collect_by_chapter=int(fs_deadline) if fs_deadline > 0 else None,
                        notes=fs_notes.strip() or None,
                    )
                    db.add(new_fs)
                    db.commit()
                    db.close()
                    st.success("✅ 伏笔已记录")
                    st.rerun()
                else:
                    st.warning("请输入伏笔名称")

    # ---- 模型设置 ----
    with tab5:
        # ── 区块 1：项目默认模型 ──────────────────────────────────────
        st.markdown("### 🤖 项目默认模型")
        st.caption("所有未单独配置的 Agent 均跟随此模型")

        with st.form("model_change_form"):
            options, label_map = build_model_options()
            current_model = novel.llm_model or DEFAULT_MODEL_ID
            current_idx = 0
            for i, label in enumerate(options):
                if label_map[label] == current_model:
                    current_idx = i
                    break

            new_label = st.selectbox(
                "为本项目选择默认大模型",
                options,
                index=current_idx,
                help="仅影响当前项目；全局默认模型在 .env 中的 DEFAULT_MODEL 配置"
            )
            if st.form_submit_button("💾 保存默认模型", use_container_width=True,
                                        disabled=not can_edit(novel_id)):
                new_model_id = label_map.get(new_label, DEFAULT_MODEL_ID)
                ok, err_msg = check_api_key(new_model_id)
                if not ok:
                    st.error(f"API Key 未配置：{err_msg}")
                else:
                    db = get_db()
                    novel_obj = db.query(Novel).filter(Novel.id == novel_id).first()
                    novel_obj.llm_model = new_model_id
                    db.commit()
                    db.close()
                    st.success(f"✅ 已切换为 {get_model_info(new_model_id)['display_name']}")
                    st.rerun()

        st.markdown("#### 当前默认模型详情")
        render_model_card(current_model)

        st.divider()

        # ── 区块 2：各 Agent 分工配置 ────────────────────────────────
        _AGENT_RECOMMENDED = {
            "outline":   "claude-opus-4-6",
            "character": "claude-sonnet-4-6",
            "writer":    "claude-sonnet-4-6",
            "reviewer":  "claude-opus-4-6",
            "polisher":  "claude-opus-4-6",
            "reader":    "claude-sonnet-4-6",
        }
        _AGENT_META = [
            ("outline",   "🗂 大纲师",  "奠定全书结构，一次性任务，质量优先"),
            ("character", "👤 人设师",  "生成人物档案，一次性，Sonnet 足够"),
            ("writer",    "✍️ 写手部",  "逐章写作，最高频调用，成本主体"),
            ("reviewer",  "🔍 审核师",  "每章质检，质量把关，Opus 更严格"),
            ("polisher",  "✨ 润色师",  "每章润色，文学质量优先"),
            ("reader",    "📖 读者模拟", "模拟不同读者视角，按需调用"),
        ]

        st.markdown("### ⚙️ 各 Agent 分工配置")
        st.caption(
            "为每个 Agent 单独指定模型。写手部用 Sonnet 节省成本，"
            "审核师/润色师用 Opus 保障质量，可大幅降低整体开销。"
        )

        # 推荐/重置按钮（必须在 form 之前，写入 draft 后 rerun 使 form 读到新值）
        rec_col1, rec_col2 = st.columns(2)
        if rec_col1.button(
            "🌟 一键推荐配置", use_container_width=True,
            help="大纲师/审核师/润色师 → Opus（质量）；写手部/人设师 → Sonnet（成本）"
        ):
            st.session_state["agent_model_draft"] = dict(_AGENT_RECOMMENDED)
            st.rerun()
        if rec_col2.button(
            "🔄 全部跟随默认", use_container_width=True,
            help="清除所有分工配置，所有 Agent 统一使用项目默认模型"
        ):
            st.session_state["agent_model_draft"] = {k: "" for k in _AGENT_RECOMMENDED}
            st.rerun()

        agent_opts, agent_lmap = build_agent_model_options()

        with st.form("agent_model_form"):
            selections = {}
            for key, ag_label, ag_desc in _AGENT_META:
                draft_vals = st.session_state.get("agent_model_draft", {})
                current_val = draft_vals.get(key, getattr(novel, f"model_{key}", None) or "")
                current_ag_label = next(
                    (l for l, v in agent_lmap.items() if v == current_val),
                    FOLLOW_LABEL
                )
                current_ag_idx = (
                    agent_opts.index(current_ag_label)
                    if current_ag_label in agent_opts else 0
                )
                col_lbl, col_sel = st.columns([2, 3])
                with col_lbl:
                    st.markdown(f"**{ag_label}**")
                    st.caption(ag_desc)
                with col_sel:
                    sel = st.selectbox(
                        ag_label, agent_opts, index=current_ag_idx,
                        key=f"agent_model_{key}", label_visibility="collapsed"
                    )
                    selections[key] = agent_lmap.get(sel, "")

            if st.form_submit_button("💾 保存分工配置", use_container_width=True, type="primary",
                                        disabled=not can_edit(novel_id)):
                # 验证 API Key
                missing = []
                for k, mid in selections.items():
                    if mid:
                        ok, _ = check_api_key(mid)
                        if not ok:
                            missing.append(mid)
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
        st.caption("选择时参考各模型的写作风格和适合题材")
        render_all_models_panel()

    # ---- 风格迁移 ----
    with tab6:
        st.markdown("### ✍️ 风格迁移")
        st.caption("上传喜欢的作家作品片段，AI 自动提取写作风格特征，润色时将自动模仿该风格")

        # 状态指示（置顶）
        db = get_db()
        novel_fresh = db.query(Novel).filter(Novel.id == novel_id).first()
        db.close()
        current_profile = novel_fresh.get_style_profile() if novel_fresh else {}
        if current_profile:
            st.success(
                f"🎨 **风格迁移已启用**  ·  {current_profile.get('overall_style', '已设置风格档案')}"
            )
            if current_profile.get("polish_instructions"):
                st.caption(f"润色指令预览：{current_profile['polish_instructions'][:120]}...")
        else:
            st.info("📝 尚未设置风格档案，润色时使用默认风格")

        st.divider()

        # ---------- 第一步：输入参考文本 ----------
        st.markdown("#### 第一步：提供参考文本")
        st.caption("建议选取最能代表该作者风格的段落，涵盖对话、叙述、景物描写等多种文体，500-3000 字效果最佳")

        input_method = st.radio(
            "输入方式", ["✏️ 粘贴文本", "📄 上传 .txt 文件"],
            horizontal=True, key="style_input_method"
        )

        reference_text = ""
        if input_method == "✏️ 粘贴文本":
            reference_text = st.text_area(
                "粘贴参考文本",
                height=220,
                placeholder="请在此粘贴喜欢的作家作品段落……",
                key="style_paste_text"
            )
            if reference_text:
                st.caption(f"已输入 {len(reference_text)} 字符")
        else:
            uploaded_file = st.file_uploader(
                "上传 .txt 文件",
                type=["txt"],
                key="style_file_upload"
            )
            if uploaded_file:
                try:
                    raw = uploaded_file.read()
                    try:
                        reference_text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        reference_text = raw.decode("gbk", errors="replace")
                    st.success(f"✅ 已读取文件：{uploaded_file.name}（{len(reference_text)} 字符）")
                    with st.expander("预览文件内容（前500字）"):
                        st.text(reference_text[:500])
                except Exception as e:
                    st.error(f"文件读取失败：{e}")

        # 分析按钮（表单外，独立触发）
        analyze_btn = st.button(
            "🔍 分析写作风格",
            disabled=(not reference_text.strip() or not can_edit(novel_id)),
            use_container_width=True,
            type="primary"
        )

        if analyze_btn:
            if len(reference_text.strip()) < 100:
                st.warning("参考文本太短，建议至少 100 字以上")
            else:
                progress_ph = st.empty()
                with st.spinner("正在分析写作风格特征，约需 20-40 秒……"):
                    def _style_progress(msg):
                        progress_ph.info(msg)

                    workflow = load_novel(novel_id)
                    result = workflow.analyze_writing_style(
                        reference_text, progress_callback=_style_progress
                    )
                    workflow.close()

                progress_ph.empty()
                if result.success:
                    st.session_state["style_profile_draft"] = result.data["profile"]
                    st.success("✅ 风格分析完成！请在下方确认并编辑后保存")
                    st.rerun()
                else:
                    st.error(f"分析失败：{result.message}")

        st.divider()

        # ---------- 第二步：编辑并保存 ----------
        st.markdown("#### 第二步：确认并编辑风格特征")

        # 优先使用刚分析的草稿，其次加载已保存的档案
        draft = st.session_state.get("style_profile_draft") or current_profile

        if not draft:
            st.info("完成第一步的风格分析后，这里将展示提取出的风格特征供你编辑")
        else:
            STYLE_FIELDS = [
                ("overall_style",        "总体风格定位",   60,  "一句话概括整体风格"),
                ("sentence_patterns",    "句式特征",       80,  "长短句比例、句式结构偏好、标点习惯等"),
                ("vocabulary",           "词汇风格",       80,  "雅俗程度、惯用词汇类型、文白比例"),
                ("narrative_voice",      "叙述风格",       80,  "叙述距离、视角特点、信息呈现方式"),
                ("dialogue_style",       "对话特点",       70,  "频率、长短偏好、口语化程度"),
                ("description_style",    "描写特点",       80,  "感官偏好、比喻手法、景物描写密度"),
                ("rhythm_pacing",        "节奏特征",       70,  "段落疏密规律、快慢切换方式"),
                ("emotion_expression",   "情感表达",       70,  "直抒胸臆 vs 含蓄克制的程度"),
                ("signature_techniques", "标志性手法",     80,  "该作者特有的技巧和意象"),
                ("polish_instructions",  "润色指令 ⭐️",  120,  "行动导向语言：具体告诉润色者该做什么（此字段直接决定润色效果）"),
            ]

            with st.form("style_profile_form"):
                updated_profile = {}
                for field_key, label, height, help_text in STYLE_FIELDS:
                    updated_profile[field_key] = st.text_area(
                        label,
                        value=draft.get(field_key, ""),
                        height=height,
                        help=help_text,
                        key=f"style_field_{field_key}"
                    )

                st.markdown("---")
                col_save, col_clear = st.columns(2)
                save_btn  = col_save.form_submit_button("💾 保存风格档案", use_container_width=True, type="primary",
                                                         disabled=not can_edit(novel_id))
                clear_btn = col_clear.form_submit_button("🗑️ 清除风格档案", use_container_width=True,
                                                         disabled=not can_edit(novel_id))

            if save_btn:
                workflow = load_novel(novel_id)
                result = workflow.update_style_profile(updated_profile)
                workflow.close()
                if result.success:
                    # 草稿已持久化，清除 session 草稿
                    st.session_state.pop("style_profile_draft", None)
                    st.success(f"✅ {result.message}，后续润色将自动应用此风格")
                    st.rerun()
                else:
                    st.error(result.message)

            if clear_btn:
                workflow = load_novel(novel_id)
                result = workflow.clear_writing_style()
                workflow.close()
                if result.success:
                    st.session_state.pop("style_profile_draft", None)
                    st.success(f"✅ {result.message}")
                    st.rerun()
                else:
                    st.error(result.message)

        # 参考文本节选（已保存时展示）
        if novel_fresh and novel_fresh.style_reference_text:
            with st.expander("📄 查看已保存的参考文本节选"):
                st.text(novel_fresh.style_reference_text[:1000]
                        + ("..." if len(novel_fresh.style_reference_text) > 1000 else ""))

    # ---- API Key 配置 ----
    with tab7:
        st.markdown("### 🔑 API Key 配置")
        st.caption("在此管理各大模型提供商的 API Key，保存后立即生效，无需重启应用")
        render_api_key_form("settings_api_key_form")
