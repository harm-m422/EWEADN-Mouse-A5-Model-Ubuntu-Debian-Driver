#!/usr/bin/env bash
#
# 卸载 eweadn-a5（不会删除 ~/.local/share/eweadn-a5 里的 Wine 环境和驱动缓存）
#
#   bash uninstall.sh            # 卸载用户级安装
#   bash uninstall.sh --system   # 卸载系统级安装
#   bash uninstall.sh --purge    # 连 Wine 环境一起删（约 1.2 GB）
#
set -euo pipefail

MODE=user
PURGE=0
for arg in "$@"; do
    case "$arg" in
        --system) MODE=system ;;
        --user)   MODE=user ;;
        --purge)  PURGE=1 ;;
        -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "未知参数: $arg" >&2; exit 2 ;;
    esac
done

if [ "$MODE" = system ]; then PREFIX=/usr/local; SUDO=sudo; else PREFIX="${HOME}/.local"; SUDO=; fi
say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

say "从 $PREFIX 卸载"
$SUDO rm -f "$PREFIX/bin/eweadn-a5" "$PREFIX/bin/eweadn-a5-gui" "$PREFIX/bin/eweadn-a5-setup"
$SUDO rm -rf "$PREFIX/lib/eweadn-a5"
$SUDO rm -f "$PREFIX/share/applications/eweadn-a5.desktop"
$SUDO rm -f "$PREFIX/share/pixmaps/eweadn-a5.png"
for size in 16 24 32 48 64 128 256; do
    $SUDO rm -f "$PREFIX/share/icons/hicolor/${size}x${size}/apps/eweadn-a5.png"
done

command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true

if [ -f /etc/udev/rules.d/99-eweadn-a5.rules ]; then
    say "移除 udev 规则"
    sudo rm -f /etc/udev/rules.d/99-eweadn-a5.rules
    sudo udevadm control --reload 2>/dev/null || true
fi

DATA="${XDG_DATA_HOME:-$HOME/.local/share}/eweadn-a5"
if [ "$PURGE" = 1 ]; then
    say "删除 Wine 环境和驱动缓存：$DATA"
    rm -rf "$DATA"
else
    [ -d "$DATA" ] && echo "（保留了 $DATA，含 Wine 环境和驱动缓存；要一起删就用 --purge）"
fi

say "卸载完成"
