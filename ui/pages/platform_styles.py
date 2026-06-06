"""
平台风格全局管理页
用户可以自定义各平台下各标签的写作风格描述
"""

import streamlit as st

from core.platform_styles import load_platform_styles, save_platform_styles


def page_platform_styles():
    st.title("📺 平台风格配置")
    st.caption("在此定义各发布平台及其标签对应的写作风格描述。写作和润色时会自动将选中标签的描述注入提示词。")

    styles = load_platform_styles()

    # ── 顶部：新增平台 ──────────────────────────────────────
    with st.expander("➕ 新增平台", expanded=False):
        with st.form("add_platform_form"):
            new_platform = st.text_input("平台名称", placeholder="如：起点中文网、晋江文学城…")
            if st.form_submit_button("新增平台"):
                name = new_platform.strip()
                if not name:
                    st.warning("请输入平台名称")
                elif name in styles:
                    st.warning("该平台已存在")
                else:
                    styles[name] = {}
                    save_platform_styles(styles)
                    st.success(f"✅ 已新增平台「{name}」")
                    st.rerun()

    st.divider()

    if not styles:
        st.info("暂无平台，点击上方「新增平台」开始配置")
        return

    # ── 各平台 Tab ──────────────────────────────────────────
    platform_names = list(styles.keys())
    tabs = st.tabs(platform_names + ["＋"])

    for tab_idx, platform in enumerate(platform_names):
        with tabs[tab_idx]:
            tag_map: dict[str, str] = styles[platform]

            # 平台级操作：重命名 / 删除
            col_title, col_del = st.columns([5, 1])
            with col_title:
                st.markdown(f"### {platform}")
            with col_del:
                if st.button("🗑️ 删除平台", key=f"del_plat_{platform}",
                             help=f"删除平台「{platform}」及其所有标签"):
                    del styles[platform]
                    save_platform_styles(styles)
                    st.rerun()

            st.caption(f"共 {len(tag_map)} 个标签")

            # 已有标签列表
            if tag_map:
                for tag, desc in list(tag_map.items()):
                    with st.expander(f"🏷️ {tag}", expanded=False):
                        new_desc = st.text_area(
                            "风格描述",
                            value=desc,
                            height=150,
                            key=f"desc_{platform}_{tag}",
                            help="写作和润色时会把这段描述注入提示词，建议具体描述节奏、语气、读者偏好等。"
                        )
                        c1, c2 = st.columns([3, 1])
                        if c1.button("💾 保存", key=f"save_{platform}_{tag}", use_container_width=True):
                            styles[platform][tag] = new_desc.strip()
                            save_platform_styles(styles)
                            st.toast(f"✅ 已保存「{tag}」")
                        if c2.button("🗑️", key=f"del_{platform}_{tag}",
                                     help=f"删除标签「{tag}」", use_container_width=True):
                            del styles[platform][tag]
                            save_platform_styles(styles)
                            st.rerun()

            st.divider()

            # 新增标签
            with st.form(f"add_tag_{platform}"):
                st.markdown("**➕ 新增标签**")
                new_tag = st.text_input("标签名称", placeholder="如：玄幻、古代言情…",
                                        key=f"new_tag_name_{platform}")
                new_tag_desc = st.text_area(
                    "风格描述",
                    height=120,
                    placeholder="描述该平台此标签下的写作风格要求：节奏、语气、情节偏好、读者期待等…",
                    key=f"new_tag_desc_{platform}"
                )
                if st.form_submit_button("新增标签", use_container_width=True):
                    tag_name = new_tag.strip()
                    if not tag_name:
                        st.warning("请输入标签名称")
                    elif tag_name in tag_map:
                        st.warning("该标签已存在")
                    else:
                        styles[platform][tag_name] = new_tag_desc.strip()
                        save_platform_styles(styles)
                        st.success(f"✅ 已新增标签「{tag_name}」")
                        st.rerun()

    # 最后一个 tab "+" 提示用户用顶部表单新增平台
    with tabs[-1]:
        st.info("使用页面顶部的「➕ 新增平台」区域添加新平台")
