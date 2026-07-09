"""
平台风格全局管理页
用户可以查看和编辑各平台/标签的量化风格档案（与项目风格档案格式完全一致）。
"""

import streamlit as st

from core.platform_styles import (
    load_platform_styles, save_platform_styles,
    DEFAULT_PLATFORM_STYLES, SLIDER_FIELDS,
)
from core.agents import PolisherAgent


def page_platform_styles():
    st.title("📺 平台风格配置")
    st.caption("定义各平台标签的标准风格档案。用户在项目风格设置中以此为初始默认值，修改后保存为项目专属风格。")

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
            tag_map: dict[str, dict] = styles[platform]

            # 平台级操作
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

            # ── 已有标签 ──
            if tag_map:
                for tag, profile in list(tag_map.items()):
                    with st.expander(f"🏷️ {tag}", expanded=False):
                        new_profile = {}

                        # 总体风格（文本）
                        new_profile["overall_style"] = st.text_input(
                            "总体风格定位",
                            value=profile.get("overall_style", ""),
                            key=f"overall_{platform}_{tag}",
                            placeholder="一句话概括，如：男频玄幻热血升级爽文",
                        )

                        st.markdown("**量化维度**")
                        for field, label in SLIDER_FIELDS:
                            raw = profile.get(field)
                            default = raw if isinstance(raw, int) and 1 <= raw <= 5 else 3
                            chosen = st.select_slider(
                                label,
                                options=[1, 2, 3, 4, 5],
                                value=default,
                                key=f"slider_{platform}_{tag}_{field}",
                            )
                            semantic = PolisherAgent._STYLE_SLIDER_MAP.get(field, {}).get(chosen, "")
                            if semantic:
                                st.caption(f"▸ {semantic}")
                            new_profile[field] = chosen

                        st.markdown("**文本维度**")
                        new_profile["signature_techniques"] = st.text_area(
                            "标志性手法",
                            value=profile.get("signature_techniques", ""),
                            height=68,
                            key=f"sig_{platform}_{tag}",
                        )
                        new_profile["polish_instructions"] = st.text_area(
                            "润色指令",
                            value=profile.get("polish_instructions", ""),
                            height=80,
                            key=f"polish_{platform}_{tag}",
                        )
                        new_profile["custom_notes"] = st.text_area(
                            "补充说明",
                            value=profile.get("custom_notes", ""),
                            height=60,
                            key=f"notes_{platform}_{tag}",
                        )

                        c1, c2 = st.columns([3, 1])
                        if c1.button("💾 保存", key=f"save_{platform}_{tag}", width="stretch"):
                            styles[platform][tag] = {
                                k: v for k, v in new_profile.items()
                                if isinstance(v, int) or (isinstance(v, str) and v.strip())
                            }
                            save_platform_styles(styles)
                            st.toast(f"✅ 已保存「{tag}」风格档案")
                        if c2.button("🗑️", key=f"del_{platform}_{tag}",
                                     help=f"删除标签「{tag}」", width="stretch"):
                            del styles[platform][tag]
                            save_platform_styles(styles)
                            st.rerun()

            st.divider()

            # ── 新增标签 ──
            with st.expander("➕ 新增标签", expanded=False):
                with st.form(f"add_tag_{platform}"):
                    new_tag = st.text_input(
                        "标签名称",
                        placeholder="如：玄幻、古代言情…",
                        key=f"new_tag_name_{platform}",
                    )
                    st.caption("新增后可在上方展开该标签编辑风格档案")
                    if st.form_submit_button("新增标签", use_container_width=True):
                        tag_name = new_tag.strip()
                        if not tag_name:
                            st.warning("请输入标签名称")
                        elif tag_name in tag_map:
                            st.warning("该标签已存在")
                        else:
                            # 以中间值3初始化所有量化维度
                            styles[platform][tag_name] = {
                                f: 3 for f, _ in SLIDER_FIELDS
                            }
                            styles[platform][tag_name]["overall_style"] = ""
                            styles[platform][tag_name]["signature_techniques"] = ""
                            styles[platform][tag_name]["polish_instructions"] = ""
                            styles[platform][tag_name]["custom_notes"] = ""
                            save_platform_styles(styles)
                            st.success(f"✅ 已新增标签「{tag_name}」，请展开编辑风格档案")
                            st.rerun()

    # 最后一个 tab "+" 提示
    with tabs[-1]:
        st.info("使用页面顶部的「➕ 新增平台」区域添加新平台")
