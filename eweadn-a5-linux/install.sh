#!/usr/bin/env bash
#
# 安装 eweadn-a5 —— 桌面快捷方式 + 命令行工具
#
#   bash install.sh              # 用户级安装到 ~/.local（不需要 sudo）
#   bash install.sh --system     # 系统级安装到 /usr/local（需要 sudo）
#   bash install.sh --no-udev    # 跳过 udev 规则（之后可以手动装）
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE=user
WITH_UDEV=1
for arg in "$@"; do
    case "$arg" in
        --system)  MODE=system ;;
        --user)    MODE=user ;;
        --no-udev) WITH_UDEV=0 ;;
        -h|--help)
            sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "未知参数: $arg" >&2; exit 2 ;;
    esac
done

if [ "$MODE" = system ]; then
    PREFIX=/usr/local
    SUDO=sudo
else
    PREFIX="${HOME}/.local"
    SUDO=
fi

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31m错误:\033[0m %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || die "需要 python3"

say "安装到 $PREFIX"

# --- 1. 程序 ---
$SUDO install -d "$PREFIX/lib/eweadn-a5" "$PREFIX/bin"
$SUDO install -m 0644 "$HERE/src/a5tool.py" "$PREFIX/lib/eweadn-a5/a5tool.py"
for f in eweadn-a5 eweadn-a5-gui eweadn-a5-setup; do
    $SUDO install -m 0755 "$HERE/bin/$f" "$PREFIX/bin/$f"
done

# --- 2. 图标（多尺寸，装进 hicolor 主题）---
for png in "$HERE"/data/icons/eweadn-a5-*.png; do
    size="$(basename "$png" .png | sed 's/.*-//')"
    dir="$PREFIX/share/icons/hicolor/${size}x${size}/apps"
    $SUDO install -d "$dir"
    $SUDO install -m 0644 "$png" "$dir/eweadn-a5.png"
done
# 兼容只认单一路径的老桌面
$SUDO install -d "$PREFIX/share/pixmaps"
$SUDO install -m 0644 "$HERE/data/icons/eweadn-a5.png" "$PREFIX/share/pixmaps/eweadn-a5.png"

# --- 3. 桌面项 ---
$SUDO install -d "$PREFIX/share/applications"
$SUDO install -m 0644 "$HERE/data/eweadn-a5.desktop" "$PREFIX/share/applications/eweadn-a5.desktop"

# --- 4. 刷新桌面数据库 ---
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -qtf "$PREFIX/share/icons/hicolor" 2>/dev/null || true
fi

# --- 5. udev 规则 ---
if [ "$WITH_UDEV" = 1 ]; then
    say "安装 udev 规则（需要 sudo）"
    if sudo install -m 0644 "$HERE/data/99-eweadn-a5.rules" /etc/udev/rules.d/99-eweadn-a5.rules; then
        sudo udevadm control --reload 2>/dev/null || true
        echo "    注意：udevadm trigger 对已存在的设备节点不生效，请拔插一次鼠标。"
    else
        echo "    udev 规则没装上。手动装也可以："
        echo "      sudo cp '$HERE/data/99-eweadn-a5.rules' /etc/udev/rules.d/"
        echo "      sudo udevadm control --reload"
    fi
fi

# --- 6. PATH 检查 ---
case ":$PATH:" in
    *":$PREFIX/bin:"*) ;;
    *) echo
       echo "提醒：$PREFIX/bin 不在 PATH 里。加到 ~/.bashrc："
       echo "    export PATH=\"$PREFIX/bin:\$PATH\"" ;;
esac

echo
say "安装完成"
echo
echo "接下来："
if command -v wine >/dev/null 2>&1; then
    echo "  · 初始化官方驱动（只需一次，约 1 分钟）：eweadn-a5-setup"
    echo "  · 之后点菜单里的「EWEADN A5 鼠标设置」，或跑：eweadn-a5-gui"
else
    echo "  · 想要图形界面（功能最全）：sudo apt install wine && eweadn-a5-setup"
fi
echo "  · 纯命令行（不需要 Wine）：eweadn-a5 read --parse"
echo "                              eweadn-a5 set --slot 1 --value 1000"
echo
echo "  · 卸载：bash '$HERE/uninstall.sh'$( [ "$MODE" = system ] && echo ' --system' )"
echo
echo "记得鼠标要用 Type-C 线插着（配置通道只在线模式上）。"
