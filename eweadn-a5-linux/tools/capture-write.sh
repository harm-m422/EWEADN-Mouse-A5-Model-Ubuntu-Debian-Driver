#!/usr/bin/env bash
#
# capture-write.sh —— 抓取官方驱动发给鼠标的原始 feature 报文
#
# 用途：当你想让 a5tool.py 支持更多设置项（回报率、灯光、改键…）时，
#       先在这里抓一份"官方驱动是怎么发的"，再照着实现。
#
#   bash tools/capture-write.sh [日志路径]
#
# 原理：官方驱动是 Windows 程序，但它走 HID feature report；
#       Wine 的 winebus 后端就是通过 /dev/hidraw 的 feature ioctl 跟设备通信的。
#       所以用 LD_PRELOAD 拦 ioctl()，就能拿到每一次读写的完整字节。
#
# 关键：必须先 `wineserver -k`。winebus 的 HID 后端跑在常驻的 plugplay
#       服务进程里，残留的旧进程没加载 shim，就什么都抓不到。
#
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/eweadn-a5"
PREFIX="$DATA/wineprefix"
EXE="$PREFIX/drive_c/eweadn-a5/OemDrv.exe"
SHIM="$HERE/hidshim.so"
LOG="${1:-$ROOT/hid-write.log}"

command -v wine >/dev/null 2>&1 || { echo "需要 wine：sudo apt install wine" >&2; exit 1; }
[ -x "$EXE" ] || { echo "官方驱动还没装。先跑：eweadn-a5-setup" >&2; exit 1; }

if [ ! -f "$SHIM" ] || [ "$HERE/hidshim.c" -nt "$SHIM" ]; then
    command -v gcc >/dev/null 2>&1 || { echo "需要 gcc 来编译 hidshim" >&2; exit 1; }
    gcc -shared -fPIC -O2 -o "$SHIM" "$HERE/hidshim.c" -ldl || exit 1
fi

export WINEPREFIX="$PREFIX"
export WINEDLLOVERRIDES="mscoree,mshtml="
export HIDLOG="$LOG"

echo ">>> 结束残留的 Wine 进程（让 winebus 重新加载 shim）..."
wineserver -k 2>/dev/null
sleep 3
rm -f "$LOG"

cat <<'EOF'

接下来请在驱动窗口里：
  1. 改一项设置（比如把第 1 档 DPI 从 800 改成 1000）
  2. 点 Apply / 应用
  3. 关掉窗口

按回车开始...
EOF
read -r _

LD_PRELOAD="$SHIM" wine "$EXE" >/dev/null 2>&1

echo
echo "==================== 抓到的 feature 报文 ===================="
if [ -s "$LOG" ]; then
    awk '{
        split($0, a, "data=");
        head = a[1]; data = (length(a) > 1) ? a[2] : "";
        if (length(data) <= 96) print head "data=" data;
        else print head "data=" substr(data, 1, 128) " ...(共 " length(data)/2 " 字节)";
    }' "$LOG"
    echo
    echo "共 $(wc -l < "$LOG") 条。完整内容在 $LOG"
    if grep -qE 'SET .*data=09(fa|[0-9a-f]{2})' "$LOG"; then
        echo "✅ 抓到了写命令（SET）"
    fi
else
    echo "（没抓到任何报文）"
    echo
    echo "排查："
    echo "  · 驱动窗口真的出现了吗？（wine 启动要 10~20 秒）"
    echo "  · 确认脚本里先跑了 wineserver -k"
    echo "  · 鼠标必须用 Type-C 线插着"
fi
echo "=============================================================="
echo
echo "把上面这段发到项目 issue 里，或者在仓库里 diff 出新的偏移量，"
echo "然后照着 docs/protocol.md 的格式补进 src/a5tool.py。"
