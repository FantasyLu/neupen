import streamlit as st

from core.models import get_db, Novel, Chapter, Collaborator
from core.workflow import create_new_novel, delete_novel
from core.llm import DEFAULT_MODEL_ID, check_api_key
from core.agents import IdeaAgent
from core.permissions import generate_invite_code
from core.platform_styles import load_platform_styles
from ui.helpers import get_all_novels, format_status, format_approval_badge
from ui.components.model_selector import build_model_options


def page_project_management():
    st.markdown(
        """
        <div style="
            padding: 2.4rem 0 0.6rem 0;
            border-bottom: 1px solid rgba(232,226,216,0.1);
            margin-bottom: 2rem;
        ">
            <div style="
                font-size: 0.6rem;
                letter-spacing: 0.22em;
                color: #8a8278;
                text-transform: uppercase;
                margin-bottom: 0.5rem;
            ">Studio</div>
            <div style="
                font-family: 'Cormorant Garamond', serif;
                font-size: 2.4rem;
                font-weight: 300;
                letter-spacing: 0.06em;
                color: #e8e2d8;
                line-height: 1.1;
            ">Projects</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    tab1, tab2, tab3 = st.tabs(["我的项目", "新建项目", "灵感对话"])

    with tab1:
        novels = get_all_novels()
        if not novels:
            st.markdown(
                """
                <div style="
                    padding: 3rem 0;
                    text-align: center;
                    color: #8a8278;
                    font-size: 0.85rem;
                    letter-spacing: 0.08em;
                    border: 1px solid rgba(232,226,216,0.06);
                    border-radius: 2px;
                    margin-top: 1rem;
                ">尚无项目 — 从下方「新建项目」开始</div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"""<div style="
                    font-size: 0.65rem;
                    letter-spacing: 0.16em;
                    color: #8a8278;
                    text-transform: uppercase;
                    margin-bottom: 1.4rem;
                ">{len(novels)} Projects</div>""",
                unsafe_allow_html=True,
            )
            for novel in novels:
                with st.container(border=True):
                    col1, col2, col3 = st.columns([4, 2, 1])
                    with col1:
                        st.markdown(
                            f"""
                            <div style="padding: 0.2rem 0 0.6rem 0;">
                                <div style="
                                    font-family: 'Cormorant Garamond', serif;
                                    font-size: 1.5rem;
                                    font-weight: 300;
                                    letter-spacing: 0.04em;
                                    color: #e8e2d8;
                                    line-height: 1.2;
                                    margin-bottom: 0.4rem;
                                ">{novel.title}</div>
                                <div style="
                                    font-size: 0.75rem;
                                    color: #8a8278;
                                    letter-spacing: 0.03em;
                                    line-height: 1.5;
                                ">{(novel.logline[:90] + '…') if novel.logline and len(novel.logline) > 90 else (novel.logline or '')}</div>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )
                        meta = []
                        if novel.genre:
                            meta.append(novel.genre)
                        meta.append(format_status(novel.status))
                        st.markdown(
                            " &nbsp;·&nbsp; ".join(
                                f'<span style="font-size:0.65rem;letter-spacing:0.12em;color:#8a8278;text-transform:uppercase;">{m}</span>'
                                for m in meta
                            ),
                            unsafe_allow_html=True,
                        )
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

                        # 删除按钮（仅 owner 可见）
                        identity = st.session_state.get("collab_identity") or {}
                        is_owner = (identity.get("role") == "owner" and
                                    identity.get("novel_id") == novel.id)
                        # 也对项目列表中当前用户是 owner 的项目显示（查 DB）
                        if not is_owner:
                            db = get_db()
                            own_collab = db.query(Collaborator).filter_by(
                                novel_id=novel.id,
                                display_name=st.session_state["collab_display_name"],
                                role="owner"
                            ).first()
                            db.close()
                            is_owner = own_collab is not None
                        if is_owner:
                            if st.button("🗑️ 删除", key=f"del_{novel.id}", use_container_width=True):
                                st.session_state["confirm_delete_id"] = novel.id

                # 删除确认弹窗（在卡片外渲染，避免嵌套问题）
                if st.session_state.get("confirm_delete_id") == novel.id:
                    with st.container(border=True):
                        st.warning(f"⚠️ 确认删除「{novel.title}」？此操作不可恢复，所有章节、人物、大纲数据将永久删除。")
                        c1, c2 = st.columns(2)
                        if c1.button("确认删除", key=f"confirm_del_{novel.id}", type="primary"):
                            delete_novel(novel.id)
                            if st.session_state.get("novel_id") == novel.id:
                                st.session_state.novel_id = None
                                st.session_state["collab_identity"] = None
                            st.session_state["confirm_delete_id"] = None
                            st.rerun()
                        if c2.button("取消", key=f"cancel_del_{novel.id}"):
                            st.session_state["confirm_delete_id"] = None
                            st.rerun()

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
            author = st.text_input("✍️ 作者/笔名", placeholder="例如：天蚕土豆")
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

            # 平台与标签
            all_styles = load_platform_styles()
            platform_names = [""] + list(all_styles.keys())
            col_p1, col_p2 = st.columns(2)
            with col_p1:
                target_platform = st.selectbox(
                    "📺 目标发布平台（可选）",
                    platform_names,
                    format_func=lambda x: x or "暂不选择"
                )
            with col_p2:
                available_tags = list(all_styles.get(target_platform, {}).keys()) if target_platform else []
                target_tags = st.multiselect(
                    "🏷️ 创作标签（可选，可多选）",
                    options=available_tags,
                    disabled=not target_platform,
                    placeholder="先选择平台" if not target_platform else "选择标签"
                )

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
                                llm_model=chosen_model_id,
                                author=author.strip()
                            )
                            st.session_state.novel_id = workflow.novel_id

                            # 生成邀请码 + 创建 owner 协作者
                            db = get_db()
                            novel_obj = db.query(Novel).filter(Novel.id == workflow.novel_id).first()
                            invite_code = generate_invite_code()
                            novel_obj.invite_code = invite_code
                            # 保存平台与标签配置
                            if target_platform:
                                novel_obj.target_platform = target_platform
                            if target_tags:
                                novel_obj.set_target_tags(target_tags)
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

    with tab3:
        st.markdown("### 💡 灵感对话")
        st.caption("有什么故事想法？随便说说，AI 帮你理清思路，整理好后一键创建项目。")

        # 选择对话用的模型
        options, label_map = build_model_options()
        default_label_idx = 0
        for i, label in enumerate(options):
            if label_map[label] == DEFAULT_MODEL_ID:
                default_label_idx = i
                break
        chat_model_label = st.selectbox(
            "对话模型", options, index=default_label_idx,
            key="idea_model_select",
            help="用于灵感对话和提取项目信息"
        )
        chat_model_id = label_map.get(chat_model_label, DEFAULT_MODEL_ID)

        ok, err_msg = check_api_key(chat_model_id)
        if not ok:
            st.warning(f"所选模型的 API Key 未配置：{err_msg}")
        else:
            history: list = st.session_state.idea_chat_history

            # 初始化开场白
            if not history:
                opening = "你好！有什么故事灵感想聊聊吗？可以是一句话、一个场景、甚至只是一种感觉——随便说说就行～"
                history.append({"role": "assistant", "content": opening})

            # 渲染历史消息
            for msg in history:
                with st.chat_message(msg["role"]):
                    st.write(msg["content"])

            # 用户输入
            user_input = st.chat_input("说说你的想法…")
            if user_input:
                history.append({"role": "user", "content": user_input})
                with st.chat_message("user"):
                    st.write(user_input)

                with st.chat_message("assistant"):
                    with st.spinner("思考中…"):
                        try:
                            agent = IdeaAgent(model_id=chat_model_id)
                            reply = agent.chat(history)
                        except Exception as e:
                            st.error(f"对话失败：{e}")
                            history.pop()  # 移除刚加的用户消息
                            st.stop()

                history.append({"role": "assistant", "content": reply})
                with st.chat_message("assistant"):
                    st.write(reply)
                st.session_state.idea_chat_history = history
                st.rerun()

            # 用户发过至少一条消息后显示创建按钮，由用户自己决定何时创建
            user_msg_count = sum(1 for m in history if m["role"] == "user")
            if user_msg_count >= 1:
                st.divider()
                col_btn, col_clear = st.columns([3, 1])
                with col_btn:
                    if st.button("✅ 整理完了，创建项目并生成大纲", type="primary", use_container_width=True):
                        with st.spinner("正在从对话中提取项目信息…"):
                            try:
                                agent = IdeaAgent(model_id=chat_model_id)
                                config = agent.extract_project_config(history)
                            except Exception as e:
                                st.error(f"信息提取失败：{e}")
                                st.stop()

                        title = config.get("title", "未命名").strip() or "未命名"
                        logline = config.get("logline", "").strip()
                        genre = config.get("genre", "其他")
                        writing_style = config.get("writing_style", "")
                        total_chapters = int(config.get("total_chapters", 100))

                        st.info(f"**标题**：{title}  |  **类型**：{genre}  |  **章节**：{total_chapters} 章\n\n**梗概**：{logline}")

                        with st.spinner("正在创建项目…"):
                            try:
                                workflow = create_new_novel(
                                    title, logline, genre, writing_style,
                                    llm_model=chat_model_id
                                )
                                st.session_state.novel_id = workflow.novel_id

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
                                    # 清空对话历史
                                    st.session_state.idea_chat_history = []
                                    st.success(f"✅ 项目「{title}」创建成功！")
                                    st.session_state.page = "大纲管理"
                                    st.rerun()
                                else:
                                    st.error(f"大纲生成失败：{result.message}")
                            except Exception as e:
                                st.error(f"创建失败：{e}")
                with col_clear:
                    if st.button("🗑️ 重新开始", use_container_width=True):
                        st.session_state.idea_chat_history = []
                        st.rerun()
