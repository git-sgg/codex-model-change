#!/bin/sh
# cx 安装脚本(备选方式,推荐 npm install -g codex-model-change)
set -e
SRC="$(cd "$(dirname "$0")" && pwd)/cx.py"
DEST="${1:-/usr/local/bin/cx}"

[ -f "$SRC" ] || { echo "❌ 找不到 $SRC"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "❌ 需要 python3(macOS 自带,或 brew install python3)"; exit 1; }
command -v codex >/dev/null 2>&1 || { echo "⚠️  未检测到 codex CLI,请先: brew install codex"; }

printf '#!/bin/sh\nexec python3 "%s" "$@"\n' "$SRC" > "$DEST"
chmod +x "$DEST"
echo "✅ 已安装: $DEST"
echo
echo "下一步:"
echo "  1. cx key <你的 DEEPSEEK_API_KEY>"
echo "  2. cx use deepseek"
echo "  3. 完全退出并重开 Codex App"
