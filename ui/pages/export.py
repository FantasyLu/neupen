"""
导出页面
TXT / Markdown / Word 格式导出
"""

from pathlib import Path

import streamlit as st

from utils.export import NovelExporter


def page_export():
    st.title("📤 导出")
    novel_id = st.session_state.novel_id

    exporter = NovelExporter(novel_id)
    stats = exporter.get_export_stats()
    exporter.close()

    # 导出统计
    col1, col2, col3 = st.columns(3)
    col1.metric("可导出章节", stats["published_chapters"])
    col2.metric("总字数", f"{stats['total_words']:,}")
    col3.metric("小说名称", stats["novel_title"])

    if not stats["can_export"]:
        st.warning("暂无已完成的章节，请先在「写作」页面生成章节内容")
        return

    st.divider()

    # 导出选项
    st.markdown("### 选择导出格式")
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        with st.container(border=True):
            st.markdown("### 📄 TXT 纯文本")
            st.caption("兼容性最强，适合任何设备阅读")
            include_outline_txt = st.checkbox("附上大纲摘要", key="txt_outline")
            if st.button("导出 TXT", use_container_width=True, type="primary", key="btn_txt"):
                with st.spinner("正在生成TXT文件..."):
                    try:
                        exporter = NovelExporter(novel_id)
                        filepath = exporter.export_txt(include_outline=include_outline_txt)
                        exporter.close()
                        st.success(f"✅ 导出成功！")
                        with open(filepath, "r", encoding="utf-8") as f:
                            content = f.read()
                        st.download_button(
                            "⬇️ 下载 TXT 文件",
                            data=content.encode("utf-8"),
                            file_name=Path(filepath).name,
                            mime="text/plain",
                            use_container_width=True
                        )
                    except Exception as e:
                        st.error(f"导出失败：{e}")

    with col2:
        with st.container(border=True):
            st.markdown("### 📝 Markdown")
            st.caption("适合 Obsidian、Typora 等编辑器")
            include_chars_md = st.checkbox("包含人物档案", value=True, key="md_chars")
            include_fs_md = st.checkbox("包含伏笔追踪", key="md_fs")
            if st.button("导出 Markdown", use_container_width=True, type="primary", key="btn_md"):
                with st.spinner("正在生成Markdown文件..."):
                    try:
                        exporter = NovelExporter(novel_id)
                        filepath = exporter.export_markdown(
                            include_characters=include_chars_md,
                            include_foreshadowings=include_fs_md
                        )
                        exporter.close()
                        st.success(f"✅ 导出成功！")
                        with open(filepath, "r", encoding="utf-8") as f:
                            content = f.read()
                        st.download_button(
                            "⬇️ 下载 Markdown 文件",
                            data=content.encode("utf-8"),
                            file_name=Path(filepath).name,
                            mime="text/markdown",
                            use_container_width=True
                        )
                    except Exception as e:
                        st.error(f"导出失败：{e}")

    with col3:
        with st.container(border=True):
            st.markdown("### 📘 Word 文档")
            st.caption("适合出版投稿，格式规范")
            include_chars_word = st.checkbox("包含人物档案", value=True, key="word_chars")
            if st.button("导出 Word", use_container_width=True, type="primary", key="btn_word"):
                with st.spinner("正在生成Word文件..."):
                    try:
                        exporter = NovelExporter(novel_id)
                        filepath = exporter.export_word(include_characters=include_chars_word)
                        exporter.close()
                        st.success(f"✅ 导出成功！")
                        with open(filepath, "rb") as f:
                            content = f.read()
                        st.download_button(
                            "⬇️ 下载 Word 文件",
                            data=content,
                            file_name=Path(filepath).name,
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            use_container_width=True
                        )
                    except ImportError:
                        st.error("请先安装 python-docx：`pip install python-docx`")
                    except Exception as e:
                        st.error(f"导出失败：{e}")

    with col4:
        with st.container(border=True):
            st.markdown("### 📱 EPUB 电子书")
            st.caption("适合 Kindle、Apple Books 等阅读器")
            include_chars_epub = st.checkbox("包含人物档案", value=True, key="epub_chars")
            include_fs_epub = st.checkbox("包含伏笔追踪", key="epub_fs")
            if st.button("导出 EPUB", use_container_width=True, type="primary", key="btn_epub"):
                with st.spinner("正在生成EPUB文件..."):
                    try:
                        exporter = NovelExporter(novel_id)
                        filepath = exporter.export_epub(
                            include_characters=include_chars_epub,
                            include_foreshadowings=include_fs_epub
                        )
                        exporter.close()
                        st.success("✅ 导出成功！")
                        with open(filepath, "rb") as f:
                            content = f.read()
                        st.download_button(
                            "⬇️ 下载 EPUB 文件",
                            data=content,
                            file_name=Path(filepath).name,
                            mime="application/epub+zip",
                            use_container_width=True
                        )
                    except ImportError:
                        st.error("请先安装 EbookLib：`pip install EbookLib`")
                    except Exception as e:
                        st.error(f"导出失败：{e}")

    # 已导出文件列表
    st.divider()
    st.markdown("### 📁 已导出文件")
    export_dir = Path("data/exports")
    if export_dir.exists():
        files = sorted(export_dir.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)[:10]
        if files:
            for file in files:
                size_kb = file.stat().st_size / 1024
                c1, c2, c3 = st.columns([4, 1, 1])
                c1.markdown(f"📄 {file.name}")
                c2.caption(f"{size_kb:.1f} KB")
                with open(file, "rb") as f:
                    c3.download_button(
                        "下载",
                        data=f.read(),
                        file_name=file.name,
                        key=f"dl_{file.name}"
                    )
        else:
            st.info("暂无已导出文件")
    else:
        st.info("暂无已导出文件")
