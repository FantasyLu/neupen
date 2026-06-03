"""
API Key 配置组件
提供商检测、配置表单、首次引导页
"""

import os

import streamlit as st

from core.config import load_saved_keys, save_api_keys, apply_saved_keys
from core.llm import MODEL_REGISTRY


def get_provider_key_info() -> list[dict]:
    """
    从 MODEL_REGISTRY 提取提供商元数据（按 api_key_env 去重）。
    返回: [{"env_var": "ANTHROPIC_API_KEY", "provider_name": "Anthropic", "url": "..."}]
    """
    seen = {}
    for model_id, info in MODEL_REGISTRY.items():
        env_var = info.get("api_key_env", "")
        if env_var and env_var not in seen:
            seen[env_var] = {
                "env_var": env_var,
                "provider_name": info.get("provider_name", ""),
                "url": info.get("api_key_url", ""),
            }
    return list(seen.values())


def any_api_key_configured() -> bool:
    """检测是否至少有一个提供商的 API Key 已配置"""
    for p in get_provider_key_info():
        val = os.environ.get(p["env_var"], "")
        if val.strip():
            return True
    return False


def render_api_key_form(form_key: str = "api_key_form"):
    """
    渲染 API Key 配置表单（共用于首次引导页和设定管理 tab）。
    返回是否成功保存。
    """
    providers = get_provider_key_info()

    with st.form(form_key):
        st.markdown("至少配置一个提供商的 API Key 即可使用对应模型。推荐 Anthropic Claude。")
        inputs = {}
        for p in providers:
            env_var = p["env_var"]
            current = os.environ.get(env_var, "")
            has_key = bool(current.strip())
            badge = "✅ 已配置" if has_key else "🔑 未配置"
            st.markdown(f"**{p['provider_name']}** {badge}")
            inputs[env_var] = st.text_input(
                f"{p['provider_name']} API Key",
                value="",
                type="password",
                placeholder="保持为空则不修改" if has_key else "粘贴你的 API Key",
                key=f"{form_key}_{env_var}",
                label_visibility="collapsed",
            )
            if p["url"]:
                st.caption(f"获取地址：{p['url']}")
            st.markdown("")  # spacing

        if st.form_submit_button("💾 保存配置", use_container_width=True, type="primary"):
            to_save = {k: v.strip() for k, v in inputs.items() if v.strip()}
            if to_save:
                save_api_keys(to_save)
                apply_saved_keys()
                st.success("✅ API Key 已保存并生效！")
                st.rerun()
            else:
                st.warning("没有输入新的 API Key")
            return True
    return False


def render_api_key_setup():
    """首次使用引导页面：未配置任何 API Key 时展示"""
    st.markdown("---")
    col_l, col_c, col_r = st.columns([1, 3, 1])
    with col_c:
        st.markdown("## 🔑 配置 API Key")
        st.markdown(
            "首次使用需要配置至少一个大模型提供商的 API Key。\n\n"
            "配置将安全保存在本地 `data/api_keys.json` 中，无需手动编辑 `.env` 文件。"
        )
        render_api_key_form("setup_api_key_form")
