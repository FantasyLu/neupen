#!/usr/bin/env python3
"""
patch_ace.py — 修复 streamlit-ace 折行悬挂缩进问题

streamlit-ace 0.1.1 在开启 wrap=True 时，折行续行会有悬挂缩进（padding-left），
导致每段第二行及后续行相对第一行右移。本脚本在 streamlit-ace 的构建产物里注入
`editor.setOption("indentedSoftWrap", false)`，关闭此行为。

用法：
    python3 scripts/patch_ace.py
    # 或在 pip install 后自动执行：
    pip install -r requirements.txt && python3 scripts/patch_ace.py
"""

import sys
import importlib.util
from pathlib import Path

OLD = b'e.getSession().setUseWrapMode(r.wrapEnabled)'
NEW = b'e.getSession().setUseWrapMode(r.wrapEnabled),e.setOption("indentedSoftWrap",!1)'

def find_ace_build_dir() -> Path | None:
    spec = importlib.util.find_spec("streamlit_ace")
    if spec is None:
        return None
    return Path(spec.origin).parent / "frontend" / "build" / "static" / "js"

def patch_file(path: Path) -> bool:
    data = path.read_bytes()
    if NEW in data:
        print(f"  [已打补丁] {path.name}")
        return False
    if OLD not in data:
        return False
    patched = data.replace(OLD, NEW)
    path.write_bytes(patched)
    print(f"  [已修复] {path.name}")
    return True

def main():
    build_js = find_ace_build_dir()
    if build_js is None or not build_js.exists():
        print("未找到 streamlit_ace 安装目录，请先运行: pip install streamlit-ace", file=sys.stderr)
        sys.exit(1)

    patched = 0
    for js_file in build_js.glob("*.chunk.js"):
        if patch_file(js_file):
            patched += 1

    if patched == 0:
        print("无需修复（所有文件已是最新状态）。")
    else:
        print(f"共修复 {patched} 个文件。")

if __name__ == "__main__":
    main()
