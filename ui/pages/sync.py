"""
云同步页面 — 基于私有 Git 仓库
"""

from __future__ import annotations

import time

import streamlit as st


def page_sync():
    st.title("☁️ 云同步")
    st.caption("将 data/ 目录同步到你的私有 Git 仓库，支持 GitHub / GitLab / Gitee 等。")

    # ── 检查依赖 ──────────────────────────────────────────────
    try:
        from core.sync import (
            GIT_AVAILABLE, CRYPTO_AVAILABLE,
            load_sync_config, save_sync_config,
            init_or_open_repo, set_remote, fetch_remote,
            get_sync_status, push, pull,
            ConflictError,
        )
    except Exception as e:
        st.error(f"同步模块加载失败：{e}")
        return

    missing = []
    if not GIT_AVAILABLE:
        missing.append("`pip install gitpython`")
    if not CRYPTO_AVAILABLE:
        missing.append("`pip install cryptography`")
    if missing:
        st.error("缺少必要依赖，请安装后重启应用：")
        for m in missing:
            st.code(m, language="bash")
        return

    cfg = load_sync_config()

    # ══════════════════════════════════════════════════════════
    # Tab 1: 配置  |  Tab 2: 同步操作
    # ══════════════════════════════════════════════════════════
    tab_cfg, tab_sync = st.tabs(["⚙️ 仓库配置", "🔄 推送 / 拉取"])

    # ── Tab 1：配置 ────────────────────────────────────────────
    with tab_cfg:
        st.subheader("远端仓库")

        with st.form("sync_config_form"):
            remote_url = st.text_input(
                "仓库地址（SSH 或 HTTPS）",
                value=cfg.get("remote_url", ""),
                placeholder="https://github.com/yourname/neupen-data.git",
                help="推荐使用 SSH（`git@github.com:yourname/neupen-data.git`）以避免每次输入密码；\n"
                     "HTTPS 需要在地址中内嵌 Token，如 `https://<token>@github.com/...`",
            )
            branch = st.text_input(
                "分支名",
                value=cfg.get("branch", "main"),
                placeholder="main",
            )

            st.divider()
            st.subheader("API Key 加密")
            st.caption(
                "api_keys.json 含敏感密钥，推送前将被 AES-256-GCM 加密。"
                "拉取后需输入相同密码解密还原。**如不设置密码，api_keys.json 将不会同步。**"
            )
            passphrase = st.text_input(
                "同步密码（加密 API Key 用）",
                value=cfg.get("passphrase", ""),
                type="password",
                placeholder="留空则不同步 API Key",
                help="密码仅保存在本地 sync_config.json，不会进入 Git 仓库",
            )
            passphrase_confirm = st.text_input(
                "确认密码",
                type="password",
                placeholder="再次输入密码",
            )

            saved = st.form_submit_button("💾 保存配置", type="primary")

        if saved:
            if passphrase and passphrase != passphrase_confirm:
                st.error("两次输入的密码不一致")
            elif not remote_url.strip():
                st.error("仓库地址不能为空")
            else:
                cfg.update({
                    "remote_url": remote_url.strip(),
                    "branch": branch.strip() or "main",
                    "passphrase": passphrase,
                })
                save_sync_config(cfg)
                st.success("配置已保存")

        # 连接测试
        if cfg.get("remote_url"):
            st.divider()
            if st.button("🔌 测试连接（Fetch）"):
                with st.spinner("正在连接远端仓库…"):
                    ok = fetch_remote()
                if ok:
                    st.success("连接成功")
                else:
                    st.error("连接失败，请检查仓库地址和访问权限")

        # 使用说明
        with st.expander("📖 使用说明"):
            st.markdown("""
**首次使用流程**

1. 在 GitHub / GitLab / Gitee 创建一个**私有仓库**（空仓库即可）
2. 在上方填写仓库地址、分支名（默认 `main`）和加密密码
3. 点击「保存配置」
4. 切换到「推送 / 拉取」Tab，点击「推送」完成首次备份

**SSH vs HTTPS**

| 方式 | 配置 | 推荐场景 |
|------|------|---------|
| SSH | `git@github.com:user/repo.git` | 已配置 SSH Key，免密推送 |
| HTTPS + Token | `https://<token>@github.com/user/repo.git` | 无 SSH 环境 |

GitHub Token 申请：Settings → Developer settings → Personal access tokens → Fine-grained tokens（需要 repo 读写权限）

**多设备同步**

- 设备 A 写作后点「推送」
- 设备 B 写作前先点「拉取」
- 两设备同时修改同一数据库时会出现**冲突提示**，按提示选择保留哪一方
""")

    # ── Tab 2：推送 / 拉取 ───────────────────────────────────────
    with tab_sync:
        if not cfg.get("remote_url"):
            st.info("请先在「⚙️ 仓库配置」Tab 中填写远端仓库地址")
            return

        # ── 当前状态 ──
        st.subheader("当前状态")
        status_placeholder = st.empty()

        def _show_status():
            try:
                repo = init_or_open_repo()
                set_remote(repo, cfg["remote_url"], cfg.get("branch", "main"))
                status = get_sync_status(repo)
                with status_placeholder.container():
                    col1, col2, col3 = st.columns(3)
                    col1.metric(
                        "本地领先",
                        f"{status['local_commits']} 个提交",
                        help="本地有多少个 commit 未推送到远端",
                    )
                    col2.metric(
                        "远端领先",
                        f"{status['remote_commits']} 个提交",
                        help="远端有多少个 commit 未拉取到本地",
                    )
                    col3.metric(
                        "未提交改动",
                        "有" if status["is_dirty"] else "无",
                    )
                    if status["last_commit_hash"]:
                        st.caption(
                            f"最新 commit：`{status['last_commit_hash']}` "
                            f"{status['last_commit_msg'][:50]}  · {status['last_commit_time']}"
                        )
                    if status["conflict"]:
                        st.warning(
                            "⚠️ 检测到冲突：本地和远端都有新的提交，推送前请先处理冲突"
                        )
            except Exception as e:
                status_placeholder.warning(f"无法获取状态：{e}")

        _show_status()

        if st.button("🔄 刷新状态"):
            with st.spinner("正在 fetch 远端状态…"):
                fetch_remote()
            _show_status()

        st.divider()

        # ── 推送 ──
        st.subheader("📤 推送（本地 → 远端）")
        commit_msg = st.text_input(
            "提交说明（可选）",
            placeholder=f"sync: Neupen backup {time.strftime('%Y-%m-%d')}",
            key="push_commit_msg",
        )

        if st.button("推送", type="primary", key="btn_push"):
            passphrase_val = cfg.get("passphrase", "") or None
            with st.spinner("正在推送…"):
                try:
                    result = push(
                        passphrase=passphrase_val,
                        commit_message=commit_msg.strip(),
                    )
                    st.success(result)
                    # 刷新状态
                    _show_status()
                except ValueError as e:
                    st.error(str(e))
                except RuntimeError as e:
                    st.error(str(e))
                    _show_push_tips()
                except Exception as e:
                    st.error(f"推送时发生意外错误：{e}")

        st.divider()

        # ── 拉取 ──
        st.subheader("📥 拉取（远端 → 本地）")
        st.caption("拉取会用远端数据覆盖本地 data/ 目录（api_keys.json 除外，需输入解密密码）。")
        st.warning("⚠️ 拉取前建议先手动备份 data/ 目录，以防数据丢失。")

        if st.button("拉取", key="btn_pull"):
            passphrase_val = cfg.get("passphrase", "") or None
            with st.spinner("正在拉取…"):
                try:
                    result = pull(passphrase=passphrase_val, strategy="ask")
                    st.success(result)
                    _show_status()
                except ConflictError as e:
                    # 冲突处理
                    st.session_state["_sync_conflict"] = {
                        "local_ahead": e.local_ahead,
                        "remote_ahead": e.remote_ahead,
                    }
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
                except RuntimeError as e:
                    st.error(str(e))
                except Exception as e:
                    st.error(f"拉取时发生意外错误：{e}")

        # ── 冲突处理弹窗 ──
        if "_sync_conflict" in st.session_state:
            conflict = st.session_state["_sync_conflict"]

            @st.dialog("⚠️ 数据冲突")
            def _conflict_dialog():
                st.markdown(
                    f"本地领先 **{conflict['local_ahead']}** 个提交，"
                    f"远端领先 **{conflict['remote_ahead']}** 个提交。\n\n"
                    "两端都有新数据，请选择保留哪一方："
                )
                col1, col2, col3 = st.columns(3)
                with col1:
                    if st.button("保留本地", type="primary"):
                        passphrase_val = cfg.get("passphrase", "") or None
                        with st.spinner("处理中…"):
                            try:
                                result = pull(passphrase=passphrase_val, strategy="local")
                                st.session_state.pop("_sync_conflict", None)
                                st.success(result)
                            except Exception as e:
                                st.error(str(e))
                with col2:
                    if st.button("使用远端", type="secondary"):
                        passphrase_val = cfg.get("passphrase", "") or None
                        with st.spinner("正在以远端覆盖本地…"):
                            try:
                                result = pull(passphrase=passphrase_val, strategy="remote")
                                st.session_state.pop("_sync_conflict", None)
                                st.success(result)
                                st.rerun()
                            except Exception as e:
                                st.error(str(e))
                with col3:
                    if st.button("取消"):
                        st.session_state.pop("_sync_conflict", None)
                        st.rerun()

            _conflict_dialog()


def _show_push_tips():
    with st.expander("常见推送失败原因"):
        st.markdown("""
- **Authentication failed**：Token 过期或权限不足，请在 GitHub 重新生成 Token 并更新仓库地址
- **rejected — non-fast-forward**：远端有本地没有的提交，先执行「拉取」再推送
- **remote: Repository not found**：仓库地址错误或无读写权限
- **SSL certificate problem**：企业内网代理问题，尝试 SSH 方式替代 HTTPS
""")
