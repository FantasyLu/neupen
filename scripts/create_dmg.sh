#!/bin/bash
set -e

APP_NAME="Neupen"
DMG_NAME="${APP_NAME}.dmg"
VOLUME_NAME="${APP_NAME}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

if [ ! -d "dist/${APP_NAME}.app" ]; then
    echo "❌ 未找到 dist/${APP_NAME}.app，请先运行 build_mac.sh"
    exit 1
fi

echo "📀 创建 DMG..."

# 准备临时目录
rm -rf dist/dmg
mkdir -p dist/dmg
cp -R "dist/${APP_NAME}.app" dist/dmg/
ln -sf /Applications dist/dmg/Applications

# 创建 DMG
hdiutil create -volname "$VOLUME_NAME" \
    -srcfolder dist/dmg \
    -ov -format UDZO \
    "dist/${DMG_NAME}"

rm -rf dist/dmg
echo "✅ DMG 创建完成: dist/${DMG_NAME}"
