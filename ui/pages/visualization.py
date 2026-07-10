"""
结构可视化页面
人物关系网络、伏笔分布甘特图、情感曲线
"""

import json

import streamlit as st


from core.models import get_db, Character, Chapter, Foreshadowing
from core.workflow import load_novel
from core.permissions import can_edit


def render_character_network(novel_id: int):
    """Tab 1：人物关系网络（pyvis 力导向图，嵌入 HTML）"""
    import tempfile, os
    from pyvis.network import Network

    db = get_db()
    chars = db.query(Character).filter(Character.novel_id == novel_id).all()
    db.close()

    if not chars:
        st.info("暂无人物档案，请前往「设定管理 → 人物档案」添加人物后查看关系网络")
        return

    # ── 控制栏 ────────────────────────────────────────────────────────
    ctrl1, ctrl2 = st.columns([3, 1])
    with ctrl1:
        only_main = st.checkbox("只显示主要人物", value=False)
    with ctrl2:
        sync_btn = st.button(
            "🔄 AI 同步人物关系", width="stretch",
            disabled=not can_edit(novel_id),
            help="逐章调用 AI 分析，章节较多时耗时较长且消耗较多 token，请谨慎使用"
        )

    if sync_btn:
        workflow = load_novel(novel_id)
        db = get_db()
        published = db.query(Chapter).filter(
            Chapter.novel_id == novel_id,
            Chapter.status.in_(["review_pending", "reviewed", "polished", "published"]),
        ).order_by(Chapter.chapter_number).all()
        db.close()

        if not published:
            st.warning("暂无已写内容，无法同步")
        else:
            total_synced = 0
            progress_bar = st.progress(0, text="准备同步…")
            for idx, ch in enumerate(published):
                progress_bar.progress(
                    (idx + 1) / len(published),
                    text=f"正在分析第 {ch.chapter_number} 章（{idx + 1}/{len(published)}）"
                )
                try:
                    sync_result = workflow.analyze_and_sync_chapter(ch.chapter_number)
                    if sync_result and sync_result.success:
                        total_synced += sync_result.data.get("synced_count", 0)
                except Exception:
                    pass
            progress_bar.empty()
            workflow.close()
            if total_synced > 0:
                st.success(f"✅ 同步完成，更新了 {total_synced} 条人物关系")
            else:
                st.info("所有人物关系已是最新，无需更新")
            st.rerun()

    if only_main:
        chars = [c for c in chars if c.is_main]

    if not chars:
        st.info("没有符合条件的人物")
        return

    char_names = {c.name for c in chars}

    # ── 颜色映射 ──────────────────────────────────────────────────────
    role_colors = {
        "主角": "#E05C5C",
        "女主": "#E07A7A",
        "反派": "#3A9AC9",
        "配角": "#3BB8A8",
    }
    default_color = "#6AAF8A"

    # ── 构建 pyvis 网络 ───────────────────────────────────────────────
    net = Network(
        height="640px",
        width="100%",
        bgcolor="#1a1a2e",          # 深色背景，图形更清晰
        font_color="#e0e0e0",
        directed=False,
    )

    # 关闭默认工具栏（界面更简洁），启用物理引擎选项
    net.set_options("""
    {
      "nodes": {
        "font": { "size": 14, "face": "Arial", "strokeWidth": 2, "strokeColor": "#1a1a2e" },
        "borderWidth": 2,
        "borderWidthSelected": 4,
        "shadow": { "enabled": true, "size": 8, "x": 2, "y": 2 }
      },
      "edges": {
        "color": { "color": "#555577", "highlight": "#aaaaff", "hover": "#8888ff" },
        "width": 1.5,
        "selectionWidth": 3,
        "smooth": { "type": "dynamic" },
        "font": { "size": 0 },
        "shadow": false
      },
      "physics": {
        "enabled": true,
        "forceAtlas2Based": {
          "gravitationalConstant": -60,
          "centralGravity": 0.005,
          "springLength": 120,
          "springConstant": 0.08,
          "damping": 0.6,
          "avoidOverlap": 0.8
        },
        "solver": "forceAtlas2Based",
        "stabilization": { "iterations": 200, "updateInterval": 25 }
      },
      "interaction": {
        "hover": true,
        "tooltipDelay": 100,
        "zoomView": true,
        "dragView": true,
        "navigationButtons": false,
        "keyboard": false
      }
    }
    """)

    # 添加节点
    for c in chars:
        color = role_colors.get(c.role, default_color)
        size = 36 if c.is_main else 22
        # hover 弹窗：富文本 HTML
        lines = [f"<b>{c.name}</b>"]
        if c.role:
            lines.append(f"角色：{c.role}")
        if c.personality:
            lines.append(f"性格：{c.personality[:60]}{'…' if len(c.personality or '') > 60 else ''}")
        if c.background:
            lines.append(f"背景：{c.background[:60]}{'…' if len(c.background or '') > 60 else ''}")
        if c.current_state:
            lines.append(f"当前状态：{c.current_state[:40]}{'…' if len(c.current_state or '') > 40 else ''}")
        tooltip = "<br>".join(lines)

        net.add_node(
            c.name,
            label=c.name,
            title=tooltip,
            color={
                "background": color,
                "border": "#ffffff44",
                "highlight": {"background": color, "border": "#ffffff"},
                "hover": {"background": color, "border": "#ffffffcc"},
            },
            size=size,
            shape="dot",
            font={"size": 13 if c.is_main else 11, "color": "#ffffff"},
        )

    # 收集边（去重：A→B 与 B→A 合并）
    edge_data: dict[tuple, dict] = {}
    for c in chars:
        for target_name, desc in c.get_relationships().items():
            if target_name not in char_names:
                continue
            key = tuple(sorted([c.name, target_name]))
            edge_data.setdefault(key, {})[f"{c.name} → {target_name}"] = desc

    for (src, tgt), rel_map in edge_data.items():
        # 边 hover：完整双向描述，HTML 格式
        hover_lines = [f"<b>{k}</b>：{v}" for k, v in rel_map.items()]
        tooltip = "<br>".join(hover_lines)
        # 边粗细：双向都有关系时更粗
        width = 2.5 if len(rel_map) > 1 else 1.5
        net.add_edge(src, tgt, title=tooltip, width=width)

    # ── 渲染为临时 HTML 再嵌入 Streamlit ─────────────────────────────
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w",
                                     encoding="utf-8") as f:
        tmp_path = f.name
    try:
        net.save_graph(tmp_path)
        with open(tmp_path, "r", encoding="utf-8") as f:
            html_content = f.read()
    finally:
        os.unlink(tmp_path)

    # 图例（Markdown）
    st.markdown(
        "**图例：** "
        "<span style='color:#E05C5C'>● 主角</span> · "
        "<span style='color:#E07A7A'>● 女主</span> · "
        "<span style='color:#3A9AC9'>● 反派</span> · "
        "<span style='color:#3BB8A8'>● 配角</span> · "
        "<span style='color:#6AAF8A'>● 其他</span>　"
        "节点越大 = 主要人物　**鼠标悬停节点/边查看详情，可拖拽/缩放**",
        unsafe_allow_html=True,
    )

    st.html(f'<div style="height:660px;overflow:hidden;">{html_content}</div>')
    st.caption(f"共 {len(net.nodes)} 个人物，{len(net.edges)} 条关系")


def render_foreshadowing_heatmap(novel_id: int):
    """Tab 2：伏笔分布甘特图（Altair 横向条形图）"""
    import altair as alt
    import pandas as pd

    db = get_db()
    fs_list = db.query(Foreshadowing).filter(
        Foreshadowing.novel_id == novel_id
    ).order_by(Foreshadowing.set_chapter).all()
    chapters = db.query(Chapter).filter(
        Chapter.novel_id == novel_id
    ).all()
    db.close()

    if not fs_list:
        st.info("暂无伏笔记录，请前往「设定管理 → 伏笔管理」添加伏笔后查看分布图")
        return

    max_chapter = max((c.chapter_number for c in chapters), default=100)

    # 统计面板
    active_count = sum(1 for f in fs_list if f.status == "active")
    collected_count = sum(1 for f in fs_list if f.status == "collected")
    # 计算当前写作进度
    published_max = max(
        (c.chapter_number for c in chapters if c.status == "published"),
        default=0
    )
    overdue_count = sum(
        1 for f in fs_list
        if f.status == "active" and f.collect_by_chapter
        and f.collect_by_chapter < published_max
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("总计", len(fs_list))
    m2.metric("活跃", active_count)
    m3.metric("已回收", collected_count)
    m4.metric("逾期", overdue_count)

    # 筛选
    show_collected = st.checkbox("显示已回收伏笔", value=True)
    if not show_collected:
        fs_list = [f for f in fs_list if f.status != "collected"]

    if not fs_list:
        st.info("没有符合条件的伏笔")
        return

    # 构建数据
    bar_data = []
    deadline_data = []
    importance_label = {"high": "高", "medium": "中", "low": "低"}
    status_label = {"active": "活跃", "collected": "已回收", "abandoned": "已放弃"}

    for f in fs_list:
        start = f.set_chapter or 1
        end = f.collect_chapter if f.collect_chapter else max_chapter
        bar_data.append({
            "name": f.name,
            "start": start,
            "end": end,
            "importance": importance_label.get(f.importance, f.importance),
            "status": status_label.get(f.status, f.status),
            "raw_importance": f.importance,
        })
        if f.collect_by_chapter:
            deadline_data.append({
                "name": f.name,
                "deadline": f.collect_by_chapter,
                "label": f"截止第{f.collect_by_chapter}章",
            })

    df = pd.DataFrame(bar_data)

    # 甘特图：水平条形
    bars = alt.Chart(df).mark_bar(cornerRadius=3).encode(
        x=alt.X("start:Q", title="章节号", scale=alt.Scale(domain=[0, max_chapter + 1])),
        x2="end:Q",
        y=alt.Y("name:N", sort=alt.SortField("start", order="ascending"), title="伏笔"),
        color=alt.Color("importance:N",
                        scale=alt.Scale(
                            domain=["高", "中", "低"],
                            range=["#FF6B6B", "#FFD93D", "#6BCB77"]
                        ),
                        legend=alt.Legend(title="重要度")),
        opacity=alt.condition(
            alt.datum.status == "已回收",
            alt.value(0.4),
            alt.value(0.9)
        ),
        tooltip=[
            alt.Tooltip("name:N", title="伏笔"),
            alt.Tooltip("start:Q", title="埋下章节"),
            alt.Tooltip("end:Q", title="回收章节"),
            alt.Tooltip("importance:N", title="重要度"),
            alt.Tooltip("status:N", title="状态"),
        ],
    ).properties(height=max(200, len(fs_list) * 28))

    chart = bars

    # 截止标记（菱形）
    if deadline_data:
        dl_df = pd.DataFrame(deadline_data)
        deadlines = alt.Chart(dl_df).mark_point(
            shape="diamond", size=100, filled=True
        ).encode(
            x=alt.X("deadline:Q"),
            y=alt.Y("name:N"),
            color=alt.value("#E74C3C"),
            tooltip=[
                alt.Tooltip("name:N", title="伏笔"),
                alt.Tooltip("label:N", title="截止"),
            ],
        )
        chart = chart + deadlines

    st.altair_chart(chart, width="stretch")

    st.caption(
        "**图例：** 条形 = 伏笔生命周期（从埋下到回收），"
        "半透明 = 已回收，◆ 红色菱形 = 截止章节"
    )


def render_emotion_curve(novel_id: int):
    """Tab 3：情感曲线（Altair 多折线图 + 字数柱状图）"""
    import altair as alt
    import pandas as pd

    db = get_db()
    chapters = db.query(Chapter).filter(
        Chapter.novel_id == novel_id
    ).order_by(Chapter.chapter_number).all()
    db.close()

    if not chapters:
        st.info("暂无章节数据，请先生成大纲")
        return

    # 构建数据
    data = []
    available_metrics = set()

    for ch in chapters:
        row = {
            "章节": ch.chapter_number,
            "标题": ch.title or "",
            "情感基调": ch.outline_emotion or "",
        }
        if ch.review_score and ch.review_score > 0:
            row["审核评分"] = ch.review_score
            available_metrics.add("审核评分")
        if ch.reader_score and ch.reader_score > 0:
            row["读者综合评分"] = ch.reader_score
            available_metrics.add("读者综合评分")
        if ch.reader_feedback:
            try:
                fb = json.loads(ch.reader_feedback)
                readers = fb.get("readers", {})
                for rkey, label in [("power_fantasy", "爽文读者"),
                                     ("literary", "文学读者"),
                                     ("light_novel", "轻小说读者")]:
                    if rkey in readers and readers[rkey].get("average"):
                        row[label] = readers[rkey]["average"]
                        available_metrics.add(label)
            except (json.JSONDecodeError, TypeError):
                pass
        if ch.word_count and ch.word_count > 0:
            row["字数"] = ch.word_count
        data.append(row)

    df = pd.DataFrame(data)

    if not available_metrics:
        st.warning(
            "尚无评分数据可供绘制曲线。完成章节写作后会有「审核评分」，"
            "运行读者模拟后会有各类型读者评分。"
        )
        # 仍然展示字数柱状图（如果有数据）
        if "字数" in df.columns and df["字数"].notna().any():
            st.markdown("#### 各章字数统计")
            word_chart = alt.Chart(df[df["字数"].notna()]).mark_bar(
                color="#B8D4E3", cornerRadius=2
            ).encode(
                x=alt.X("章节:Q", title="章节号"),
                y=alt.Y("字数:Q", title="字数"),
                tooltip=[
                    alt.Tooltip("章节:Q"),
                    alt.Tooltip("标题:N"),
                    alt.Tooltip("字数:Q"),
                    alt.Tooltip("情感基调:N"),
                ],
            ).properties(height=250)
            st.altair_chart(word_chart, width="stretch")
        return

    # 用户筛选要展示的曲线
    selected_metrics = st.multiselect(
        "选择要展示的曲线",
        sorted(available_metrics),
        default=sorted(available_metrics),
    )

    if not selected_metrics:
        st.info("请至少选择一条曲线")
        return

    # 转为长格式（melt）
    id_vars = ["章节", "标题", "情感基调"]
    melt_cols = [m for m in selected_metrics if m in df.columns]
    df_long = df[id_vars + melt_cols].melt(
        id_vars=id_vars,
        var_name="指标",
        value_name="评分",
    ).dropna(subset=["评分"])

    if df_long.empty:
        st.info("所选指标暂无数据")
        return

    # 多折线图
    line = alt.Chart(df_long).mark_line(point=True).encode(
        x=alt.X("章节:Q", title="章节号"),
        y=alt.Y("评分:Q", title="评分（0-10）", scale=alt.Scale(domain=[0, 10])),
        color=alt.Color("指标:N", legend=alt.Legend(title="评分类型")),
        tooltip=[
            alt.Tooltip("章节:Q"),
            alt.Tooltip("标题:N"),
            alt.Tooltip("指标:N"),
            alt.Tooltip("评分:Q", format=".1f"),
            alt.Tooltip("情感基调:N"),
        ],
    ).properties(height=400)

    st.altair_chart(line, width="stretch")

    # 字数柱状图（辅助参考）
    if "字数" in df.columns and df["字数"].notna().any():
        st.markdown("#### 各章字数统计")
        word_chart = alt.Chart(df[df["字数"].notna()]).mark_bar(
            color="#B8D4E3", cornerRadius=2
        ).encode(
            x=alt.X("章节:Q", title="章节号"),
            y=alt.Y("字数:Q", title="字数"),
            tooltip=[
                alt.Tooltip("章节:Q"),
                alt.Tooltip("标题:N"),
                alt.Tooltip("字数:Q"),
                alt.Tooltip("情感基调:N"),
            ],
        ).properties(height=200)
        st.altair_chart(word_chart, width="stretch")

    # 情感基调文本表（辅助参考）
    emotion_data = [(ch.chapter_number, ch.title or "", ch.outline_emotion or "")
                     for ch in chapters if ch.outline_emotion]
    if emotion_data:
        with st.expander("📝 各章情感基调（章纲设定）"):
            for ch_num, title, emotion in emotion_data:
                st.markdown(f"**第{ch_num}章**《{title}》：{emotion}")


def page_visualization():
    """页面 5：结构可视化"""
    st.title("📊 结构可视化")
    novel_id = st.session_state.novel_id

    tab1, tab2, tab3 = st.tabs(["🕸 人物关系网络", "📊 伏笔分布", "📈 情感曲线"])

    with tab1:
        render_character_network(novel_id)
    with tab2:
        render_foreshadowing_heatmap(novel_id)
    with tab3:
        render_emotion_curve(novel_id)
