import streamlit as st

from core.models import get_db, Novel, Chapter, Collaborator
from core.workflow import create_new_novel
from core.llm import DEFAULT_MODEL_ID, check_api_key
from core.permissions import generate_invite_code
from ui.helpers import get_all_novels, format_status, format_approval_badge
from ui.components.model_selector import build_model_options, render_model_card


def page_project_management():
    st.title("🏠 项目管理")

    tab1, tab2 = st.tabs(["📚 我的项目", "➕ 新建项目"])

    with tab1:
        novels = get_all_novels()
        if not novels:
            st.info("还没有任何小说项目，点击「新建项目」开始创作吧！")
        else:
            st.markdown(f"共有 **{len(novels)}** 个项目")
            for novel in novels:
                with st.container(border=True):
                    col1, col2, col3 = st.columns([4, 2, 1])
                    with col1:
                        st.markdown(f"### {novel.title}")
                        if novel.logline:
                            st.caption(novel.logline[:80] + "..." if len(novel.logline) > 80 else novel.logline)
                        tags = []
                        if novel.genre:
                            tags.append(f"`{novel.genre}`")
                        tags.append(format_status(novel.status))
                        st.markdown(" ".join(tags))
                    with col2:
                        db = get_db()
                        ch_count = db.query(Chapter).filter(
                            Chapter.novel_id == novel.id,
                            Chapter.content.isnot(None)
                        ).count()
                        total_words = db.query(Chapter).filter(
                            Chapter.novel_id == novel.id
                        ).with_entities(Chapter.word_count).all()
                        db.close()
                        words = sum(w[0] or 0 for w in total_words)
                        st.metric("已完成章节", ch_count)
                        st.metric("总字数", f"{words:,}")
                    with col3:
                        st.write("")
                        st.write("")
                        if st.button("打开", key=f"open_{novel.id}", use_container_width=True, type="primary"):
                            # 查找协作者身份
                            db = get_db()
                            collab = db.query(Collaborator).filter_by(
                                novel_id=novel.id,
                                display_name=st.session_state["collab_display_name"]
                            ).first()
                            db.close()
                            if collab:
                                st.session_state["collab_identity"] = {
                                    "collaborator_id": collab.id,
                                    "novel_id": novel.id,
                                    "display_name": collab.display_name,
                                    "role": collab.role,
                                }
                                st.session_state.novel_id = novel.id
                                st.session_state.page = "大纲管理"
                                st.rerun()
                            else:
                                st.warning(f"你尚未加入项目「{novel.title}」，请通过邀请码加入。")

        # 加入项目（通过邀请码）
        st.divider()
        st.markdown("### 🔗 通过邀请码加入项目")
        with st.form("join_project_form"):
            invite_input = st.text_input("邀请码", placeholder="输入主笔分享的邀请码")
            if st.form_submit_button("加入项目", use_container_width=True):
                if invite_input.strip():
                    db = get_db()
                    novel_match = db.query(Novel).filter_by(invite_code=invite_input.strip()).first()
                    if not novel_match:
                        db.close()
                        st.error("无效的邀请码，请检查后重试")
                    else:
                        display_name = st.session_state["collab_display_name"]
                        existing = db.query(Collaborator).filter_by(
                            novel_id=novel_match.id, display_name=display_name
                        ).first()
                        if existing:
                            st.session_state["collab_identity"] = {
                                "collaborator_id": existing.id,
                                "novel_id": novel_match.id,
                                "display_name": existing.display_name,
                                "role": existing.role,
                            }
                            st.session_state.novel_id = novel_match.id
                            st.session_state.page = "大纲管理"
                            db.close()
                            st.rerun()
                        else:
                            reviewer = Collaborator(
                                novel_id=novel_match.id,
                                display_name=display_name,
                                role="reviewer",
                            )
                            db.add(reviewer)
                            db.commit()
                            db.refresh(reviewer)
                            st.session_state["collab_identity"] = {
                                "collaborator_id": reviewer.id,
                                "novel_id": novel_match.id,
                                "display_name": reviewer.display_name,
                                "role": "reviewer",
                            }
                            st.session_state.novel_id = novel_match.id
                            st.session_state.page = "大纲管理"
                            db.close()
                            st.success(f"已加入项目「{novel_match.title}」，身份：审阅者")
                            st.rerun()
                else:
                    st.warning("请输入邀请码")

    with tab2:
        st.markdown("### 创建新小说项目")
        with st.form("new_novel_form"):
            title = st.text_input("📝 小说标题 *", placeholder="例如：斗破苍穹")
            logline = st.text_area(
                "💡 核心灵感（一句话简介）*",
                placeholder="例如：一个被废除魔法天赋的少年，凭借一本无名古籍，一步步登顶斗气大陆之巅。",
                height=80
            )
            col1, col2 = st.columns(2)
            with col1:
                genre = st.selectbox(
                    "📚 题材类型",
                    ["玄幻", "修仙", "都市", "言情", "悬疑", "历史", "科幻", "末世", "游戏", "其他"]
                )
            with col2:
                total_chapters = st.number_input("📊 预计总章节数", min_value=10, max_value=500, value=100, step=10)
            writing_style = st.text_area(
                "🖊️ 写作风格要求（可选）",
                placeholder="例如：文笔细腻，注重人物心理描写，对话自然流畅，适当幽默...",
                height=60
            )

            st.markdown("#### 🤖 选择大模型")
            options, label_map = build_model_options()
            default_label_idx = 0
            for i, label in enumerate(options):
                if label_map[label] == DEFAULT_MODEL_ID:
                    default_label_idx = i
                    break
            selected_label = st.selectbox(
                "使用哪个大模型写作？",
                options,
                index=default_label_idx,
                help="可在「设定管理 → 模型设置」中随时修改"
            )

            generate_outline = st.checkbox("创建后立即生成大纲", value=True)
            submitted = st.form_submit_button("🚀 创建项目", use_container_width=True, type="primary")

        # 显示所选模型的信息卡片
        if selected_label in label_map:
            chosen_model_id = label_map[selected_label]
            render_model_card(chosen_model_id)

        if submitted:
            if not title.strip():
                st.error("请输入小说标题")
            elif not logline.strip():
                st.error("请输入核心灵感")
            else:
                chosen_model_id = label_map.get(selected_label, DEFAULT_MODEL_ID)
                ok, err_msg = check_api_key(chosen_model_id)
                if not ok:
                    st.error(f"所选模型的 API Key 未配置：{err_msg}")
                else:
                    with st.spinner("正在创建项目..."):
                        try:
                            workflow = create_new_novel(
                                title.strip(), logline.strip(), genre, writing_style,
                                llm_model=chosen_model_id
                            )
                            st.session_state.novel_id = workflow.novel_id

                            # 生成邀请码 + 创建 owner 协作者
                            db = get_db()
                            novel_obj = db.query(Novel).filter(Novel.id == workflow.novel_id).first()
                            invite_code = generate_invite_code()
                            novel_obj.invite_code = invite_code
                            owner = Collaborator(
                                novel_id=workflow.novel_id,
                                display_name=st.session_state["collab_display_name"],
                                role="owner",
                            )
                            db.add(owner)
                            db.commit()
                            db.refresh(owner)
                            st.session_state["collab_identity"] = {
                                "collaborator_id": owner.id,
                                "novel_id": workflow.novel_id,
                                "display_name": owner.display_name,
                                "role": "owner",
                            }
                            db.close()
                            st.info(f"邀请码：**{invite_code}**（可在侧边栏查看，分享给协作者）")

                            if generate_outline:
                                progress_placeholder = st.empty()
                                def progress_cb(msg):
                                    progress_placeholder.info(msg)

                                result = workflow.generate_outline(
                                    total_chapters=total_chapters,
                                    progress_callback=progress_cb
                                )
                                workflow.close()
                                progress_placeholder.empty()

                                if result.success:
                                    st.success(f"✅ 项目创建成功！{result.message}")
                                    st.session_state.page = "大纲管理"
                                    st.rerun()
                                else:
                                    st.error(f"大纲生成失败：{result.message}")
                            else:
                                workflow.close()
                                st.success("✅ 项目创建成功！")
                                st.session_state.page = "大纲管理"
                                st.rerun()
                        except Exception as e:
                            st.error(f"创建失败：{e}")
