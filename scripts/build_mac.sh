#!/bin/bash
set -e

APP_NAME="Neupen"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "📦 安装构建依赖..."
pip install pyinstaller

echo "🔨 开始 PyInstaller 打包..."

# 图标参数（如果存在 icon.icns 就使用）
ICON_FLAG=""
if [ -f "$SCRIPT_DIR/icon.icns" ]; then
    ICON_FLAG="--icon $SCRIPT_DIR/icon.icns"
fi

pyinstaller \
    --name "$APP_NAME" \
    --windowed \
    $ICON_FLAG \
    --add-data "app.py:." \
    --add-data "core:core" \
    --add-data "ui:ui" \
    --add-data "utils:utils" \
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
