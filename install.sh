#!/bin/sh
# cx 安装脚本: 复制到 PATH 并生成模型目录
set -e
SRC="$(cd "$(dirname "$0")" && pwd)/cx"
DEST="${1:-/usr/local/bin/cx}"

[ -f "$SRC" ] || { echo "❌ 找不到 $SRC"; exit 1; }
command -v codex >/dev/null 2>&1 || { echo "⚠️  未检测到 codex CLI,请先: brew install codex"; }
chmod +x "$SRC"
mkdir -p "$(dirname "$DEST")"
cp "$SRC" "$DEST"
echo "✅ 已安装: $DEST"
echo
echo "下一步:"
echo "  1. cx key <你的 DEEPSEEK_API_KEY>"
echo "  2. cx use deepseek"
echo "  3. 完全退出并重开 Codex App"
