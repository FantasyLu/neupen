#!/bin/bash
set -e

APP_NAME="Neupen"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# ── 确定 Python / pip / pyinstaller 路径 ──────────────────────────
# 优先使用项目 .venv，其次 Homebrew python3
if [ -f "$PROJECT_DIR/.venv/bin/python3" ]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python3"
    PIP="$PROJECT_DIR/.venv/bin/pip"
    echo "🐍 使用项目 venv: $PYTHON"
else
    PYTHON="$(which python3)"
    PIP="$(which pip3)"
    echo "🐍 使用系统 Python: $PYTHON"
fi

echo "📦 安装构建依赖..."
"$PIP" install --quiet pyinstaller

# pyinstaller 优先读 venv bin 目录，fallback 到 python -m PyInstaller
PYINSTALLER="$("$PYTHON" -c "import sysconfig; print(sysconfig.get_path('scripts'))")/pyinstaller"
if [ ! -f "$PYINSTALLER" ]; then
    PYINSTALLER="pyinstaller"
fi

echo "🔨 开始 PyInstaller 打包..."

# 图标参数（如果存在 icon.icns 就使用）
ICON_FLAG=""
if [ -f "$SCRIPT_DIR/icon.icns" ]; then
    ICON_FLAG="--icon $SCRIPT_DIR/icon.icns"
fi

"$PYTHON" -m PyInstaller \
    --name "$APP_NAME" \
    --windowed \
    $ICON_FLAG \
    --add-data "app.py:." \
    --add-data "core:core" \
    --add-data "ui:ui" \
    --add-data "utils:utils" \
    --add-data ".streamlit:.streamlit" \
    --add-data ".env.example:." \
    --hidden-import streamlit \
    --hidden-import streamlit_agraph \
    --hidden-import anthropic \
    --hidden-import openai \
    --hidden-import lancedb \
    --hidden-import sentence_transformers \
    --hidden-import sqlalchemy \
    --hidden-import tiktoken \
    --hidden-import docx \
    --collect-all streamlit \
    --collect-all streamlit_agraph \
    scripts/launcher.py

echo "✅ 构建完成: dist/$APP_NAME.app"
