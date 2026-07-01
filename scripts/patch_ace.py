#!/usr/bin/env python3
"""
patch_ace.py — 修复 streamlit-ace 折行悬挂缩进问题

streamlit-ace 0.1.1 在开启 wrap=True 时，折行续行会有悬挂缩进（padding-left），
导致每段第二行及后续行相对第一行右移约两字符。

根因：Ace Editor 的 indentedSoftWrap 选项默认为 true，需要在初始化和
更新两个时机都调用 setOption("indentedSoftWrap", false) 才能彻底关闭。

用法：
    python3 scripts/patch_ace.py
    # 重装依赖后重新执行：
    pip install -r requirements.txt && python3 scripts/patch_ace.py
"""

import sys
import importlib.util
from pathlib import Path

# (old_bytes, new_bytes) 替换对列表
# 覆盖两个组件（react-ace v1 / v2）的 init + update 两个时机，共 4 处
PATCHES = [
    # ── 初始化 (componentDidMount)，第一个组件 ──
    (
        b't.getSession().setUseWrapMode(m),t.setShowPrintMargin(v)',
        b't.getSession().setUseWrapMode(m),t.setOption("indentedSoftWrap",!1),t.setShowPrintMargin(v)',
    ),
    # ── 初始化 (componentDidMount)，第二个组件 ──
    (
        b'this.editor.getSession().setUseWrapMode(p),this.editor.setShowPrintMargin(g)',
        b'this.editor.getSession().setUseWrapMode(p),this.editor.setOption("indentedSoftWrap",!1),this.editor.setShowPrintMargin(g)',
    ),
    # ── 更新 (componentDidUpdate)，第一个组件 ──
    (
        b'r.wrapEnabled!==n.wrapEnabled&&e.getSession().setUseWrapMode(r.wrapEnabled),r.showPrintMargin',
        b'r.wrapEnabled!==n.wrapEnabled&&e.getSession().setUseWrapMode(r.wrapEnabled),e.setOption("indentedSoftWrap",!1),r.showPrintMargin',
    ),
    # ── 更新 (componentDidUpdate)，第二个组件 ──
    (
        b'n.wrapEnabled!==t.wrapEnabled&&this.editor.getSession().setUseWrapMode(n.wrapEnabled),n.showPrintMargin',
        b'n.wrapEnabled!==t.wrapEnabled&&this.editor.getSession().setUseWrapMode(n.wrapEnabled),this.editor.setOption("indentedSoftWrap",!1),n.showPrintMargin',
    ),
]


def find_ace_js_dir() -> Path | None:
    spec = importlib.util.find_spec("streamlit_ace")
    if spec is None:
        return None
    return Path(spec.origin).parent / "frontend" / "build" / "static" / "js"


def patch_file(path: Path) -> int:
    """返回实际替换的次数（0 表示已是最新或不适用）"""
    data = path.read_bytes()
    applied = 0
    for old, new in PATCHES:
        if new in data:
            continue  # 已打过
        if old in data:
            data = data.replace(old, new)
            applied += 1
    if applied:
        path.write_bytes(data)
    return applied


def main():
    js_dir = find_ace_js_dir()
    if js_dir is None or not js_dir.exists():
        print("未找到 streamlit_ace 安装目录，请先运行: pip install streamlit-ace", file=sys.stderr)
        sys.exit(1)

    total = 0
    for js_file in sorted(js_dir.glob("*.chunk.js")):
        n = patch_file(js_file)
        if n:
            print(f"  [已修复 {n} 处] {js_file.name}")
        else:
            print(f"  [无需修复] {js_file.name}")
        total += n

    if total == 0:
        print("所有文件已是最新状态，无需修复。")
    else:
        print(f"\n共修复 {total} 处。")


if __name__ == "__main__":
    main()
