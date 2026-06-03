"""
警告与影响报告组件
展示设定变更影响、伏笔调度警告、章纲变更影响
"""

import streamlit as st

from core.workflow import load_novel


def show_impact_report(impact: dict, change_type: str):
    """
    展示设定变更的影响报告
    impact: {affected_chapters, unaffected_count, summary}
    """
    affected = impact.get("affected_chapters", [])
    if not affected:
        return
    severity_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    with st.expander(
        f"📊 {change_type}影响分析（{len(affected)} 个章节受影响）",
        expanded=True
    ):
        if impact.get("summary"):
            st.info(impact["summary"])
        for item in affected:
            ch_num = item.get("chapter_number", "?")
            reason = item.get("reason", "")
            sev = item.get("severity", "medium")
            icon = severity_icon.get(sev, "⚪")
            col1, col2 = st.columns([1, 4])
            col1.markdown(f"{icon} **第{ch_num}章**")
            col2.markdown(reason)
        st.caption("建议：进入「写作」页面，对受影响章节点击「重新生成」或「进入编辑模式」手动修正。")


def show_foreshadowing_alerts(novel_id: int, current_chapter: int):
    """
    检查伏笔调度状态，当有过期或即将到期伏笔时展示警告面板。
    current_chapter: 当前已写到的最大章节号
    """
    if current_chapter <= 0:
        return
    try:
        workflow = load_novel(novel_id)
        report = workflow.get_foreshadowing_schedule_report(current_chapter)
        workflow.close()
    except Exception:
        return

    overdue = report.get("overdue", [])
    due_soon = report.get("due_soon", [])

    if overdue:
        names = "、".join(f"《{f['name']}》" for f in overdue[:3])
        if len(overdue) > 3:
            names += f" 等{len(overdue)}条"
        st.error(
            f"❌ **伏笔逾期警告**：以下伏笔已超过截止章节但尚未回收 — {names}  \n"
            "请前往「设定管理 → 伏笔管理」安排回收，或修改截止章节。"
        )
    if due_soon:
        names = "、".join(f"《{f['name']}》（第{f['collect_by_chapter']}章前）" for f in due_soon[:3])
        if len(due_soon) > 3:
            names += f" 等{len(due_soon)}条"
        st.warning(
            f"⏰ **伏笔即将到期**：{names}  \n"
            "请在章纲中安排回收时机，避免遗漏。"
        )


def show_outline_impact(changed_chapter: int, affected: list[dict]):
    """
    展示章纲变更对后续章节的影响
    affected: [{"chapter_number": N, "reason": "...", "severity": "..."}]
    """
    if not affected:
        return
    severity_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    with st.expander(
        f"⚠️ 第{changed_chapter}章章纲变更 → {len(affected)} 个后续章节受影响",
        expanded=True
    ):
        st.markdown(f"以下章节的内容依赖第{changed_chapter}章的旧章纲，可能需要修改：")
        for item in affected:
            ch_num = item.get("chapter_number", "?")
            reason = item.get("reason", "")
            sev = item.get("severity", "medium")
            icon = severity_icon.get(sev, "⚪")
            col1, col2 = st.columns([1, 4])
            col1.markdown(f"{icon} **第{ch_num}章**")
            col2.markdown(reason)
        st.caption("这些章节已被标记为「需关注」。建议进入「写作」页面逐章核查。")
