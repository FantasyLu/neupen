"""
模型选择器组件
模型选择列表、模型信息卡片、Agent 分工选项
"""

import streamlit as st

from core.llm import list_models_by_provider, check_api_key, get_model_info


def build_model_options() -> tuple[list[str], dict[str, str]]:
    """
    构建模型选择列表
    Returns: (options_list, label_to_id_map)
    """
    options = []
    label_map = {}
    for provider_name, models in list_models_by_provider().items():
        for model_id, info in models:
            ok, _ = check_api_key(model_id)
            key_badge = "✅" if ok else "🔑"
            speed = info.get("speed", "")
            cost = info.get("cost_level", "")
            label = f"{key_badge} {info['display_name']}（{provider_name}）· 速度:{speed} · 成本:{cost}"
            options.append(label)
            label_map[label] = model_id
    return options, label_map


def render_model_card(model_id: str):
    """展示单个模型的详细信息卡片"""
    info = get_model_info(model_id)
    ok, err_msg = check_api_key(model_id)

    with st.container(border=True):
        col1, col2 = st.columns([3, 1])
        with col1:
            st.markdown(f"**{info['display_name']}**  `{model_id}`")
            st.caption(info.get("writing_style", ""))
        with col2:
            if ok:
                st.success("API Key ✅")
            else:
                st.error("未配置 🔑")

        col_a, col_b, col_c = st.columns(3)
        col_a.metric("速度", info.get("speed", "-"))
        col_b.metric("成本", info.get("cost_level", "-"))
        col_c.metric("上下文", info.get("context_window", "-"))

        if info.get("best_genres"):
            st.markdown("**适合题材：** " + " · ".join(info["best_genres"]))
        if info.get("strengths"):
            st.markdown("**优势：** " + " · ".join(info["strengths"]))
        if info.get("note"):
            st.info(info["note"])


def render_all_models_panel():
    """展示所有模型的面板（用于设定页模型选项卡）"""
    for provider_name, models in list_models_by_provider().items():
        st.markdown(f"### {provider_name}")
        for model_id, info in models:
            with st.expander(f"{info['display_name']} — {info.get('writing_style', '')[:40]}...", expanded=False):
                render_model_card(model_id)
        st.divider()


FOLLOW_LABEL = "（跟随项目默认模型）"


def build_agent_model_options() -> tuple[list[str], dict[str, str]]:
    """
    为 Agent 分工配置构建选项列表，首项为「跟随项目默认」（model_id=""）。
    Returns: (options_list, label→model_id 映射)
    """
    opts, lmap = build_model_options()
    agent_opts = [FOLLOW_LABEL] + opts
    agent_lmap = {FOLLOW_LABEL: ""}
    agent_lmap.update(lmap)
    return agent_opts, agent_lmap
