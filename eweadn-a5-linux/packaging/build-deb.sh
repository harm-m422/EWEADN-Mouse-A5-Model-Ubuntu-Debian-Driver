#!/usr/bin/env bash
#
# 构建 .deb 安装包
#
#   bash packaging/build-deb.sh
#
# 产物在 packaging/dist/ 下。装的时候：
#   sudo apt install ./packaging/dist/eweadn-a5_1.0.0_all.deb
#
# 注意：deb 只装程序本体（约 200 KB），不含 1.2 GB 的 Wine 环境 ——
# 那部分由 eweadn-a5-setup 在首次运行时按用户创建。
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

PKG=eweadn-a5
VER="$(sed -n 's/^__version__ *= *"\(.*\)".*/\1/p' "$ROOT/src/a5tool.py" 2>/dev/null | head -1)"
VER="${VER:-1.0.0}"
ARCH=all
OUT="$HERE/dist"
STAGE="$HERE/build/${PKG}_${VER}_${ARCH}"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

command -v dpkg-deb >/dev/null 2>&1 || { echo "需要 dpkg-deb" >&2; exit 1; }

rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN"

say "构建 $PKG $VER ($ARCH)"

# ---- 程序 ----
install -d "$STAGE/usr/lib/$PKG" "$STAGE/usr/bin"
install -m 0644 "$ROOT/src/a5tool.py" "$STAGE/usr/lib/$PKG/a5tool.py"
for f in eweadn-a5 eweadn-a5-gui eweadn-a5-setup; do
    install -m 0755 "$ROOT/bin/$f" "$STAGE/usr/bin/$f"
done

# ---- 图标 ----
for png in "$ROOT"/data/icons/${PKG}-*.png; do
    size="$(basename "$png" .png | sed 's/.*-//')"
    install -d "$STAGE/usr/share/icons/hicolor/${size}x${size}/apps"
    install -m 0644 "$png" "$STAGE/usr/share/icons/hicolor/${size}x${size}/apps/${PKG}.png"
done
install -d "$STAGE/usr/share/pixmaps"
install -m 0644 "$ROOT/data/icons/${PKG}.png" "$STAGE/usr/share/pixmaps/${PKG}.png"

# ---- 桌面项 ----
install -d "$STAGE/usr/share/applications"
install -m 0644 "$ROOT/data/${PKG}.desktop" "$STAGE/usr/share/applications/${PKG}.desktop"

# ---- udev ----
install -d "$STAGE/usr/lib/udev/rules.d"
install -m 0644 "$ROOT/data/99-${PKG}.rules" "$STAGE/usr/lib/udev/rules.d/99-${PKG}.rules"

# ---- 文档 ----
install -d "$STAGE/usr/share/doc/$PKG"
install -m 0644 "$ROOT/README.md" "$STAGE/usr/share/doc/$PKG/README.md"
install -m 0644 "$ROOT/LICENSE"   "$STAGE/usr/share/doc/$PKG/copyright"
[ -d "$ROOT/docs" ] && cp -r "$ROOT/docs" "$STAGE/usr/share/doc/$PKG/docs"
gzip -9n -c "$ROOT/README.md" > "$STAGE/usr/share/doc/$PKG/README.md.gz" 2>/dev/null || true

# ---- control ----
SIZE="$(du -sk "$STAGE/usr" | cut -f1)"
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VER
Section: utils
Priority: optional
Architecture: $ARCH
Depends: python3 (>= 3.8)
Recommends: wine, zenity
Installed-Size: $SIZE
Maintainer: eweadn-a5-linux contributors
Homepage: https://github.com/harm-m422/Pioneer-Gaming-Mouse-A5-Model-Ubuntu-Debian-Driver
Description: Linux configuration tool for the EWEADN/Qianxingzhe A5 gaming mouse
 Provides a native CLI (hidraw only, no Wine) for reading and writing the
 mouse's onboard config, plus a launcher for the official Windows driver
 under Wine which gives the full GUI (DPI, polling rate, buttons, macros,
 lighting).
 .
 The mouse's HID protocol was reverse engineered and is documented in
 /usr/share/doc/$PKG/docs/.
EOF

# ---- postinst / postrm：刷新 udev 和桌面数据库 ----
cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ -x /usr/bin/update-desktop-database ]; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload >/dev/null 2>&1 || true
fi
cat <<'MSG'

eweadn-a5 已安装。
  · 图形界面（功能最全）：eweadn-a5-setup  然后  eweadn-a5-gui
  · 纯命令行（不需要 Wine）：eweadn-a5 read --parse
注意：udev 权限规则要拔插一次鼠标才生效。
MSG
exit 0
EOF

cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload >/dev/null 2>&1 || true
fi
if [ -x /usr/bin/update-desktop-database ]; then
    update-desktop-database -q /usr/share/applications || true
fi
exit 0
EOF
chmod 0755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

# ---- 统一权限，别把宿主机的奇怪权限带进包里 ----
find "$STAGE" -type d -exec chmod 0755 {} +
find "$STAGE" -type f -exec chmod 0644 {} +
chmod 0755 "$STAGE/usr/bin/"* "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

# ---- 打 deb ----
mkdir -p "$OUT"
DEB="$OUT/${PKG}_${VER}_${ARCH}.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$DEB" >/dev/null

say "产物：$DEB  ($(du -h "$DEB" | cut -f1))"
echo
dpkg-deb --info "$DEB" | sed 's/^/  /'
