> ⚠️ **本文档已过时（2026-10-15 晚）**
>
> 不需要 Windows 虚拟机了。官方驱动已经能在 **Wine** 里直接跑起来，
> 并且实测读写都生效（改 DPI 后回读确认）。日常用：
>
> ```bash
> eweadn-a5-setup   # 首次，装 Wine 环境 + 官方驱动
> eweadn-a5-gui
> ```
>
> 另外协议也已完全破解，纯 Linux 原生工具 `a5tool.py` 可用（目前支持 DPI）。
> 详见 [protocol.md](protocol.md)。
> 本文保留仅作备查（比如以后 Wine 出问题时的退路）。

---

# 用 Windows 虚拟机给前行者 A5 调参 —— 完整步骤

> 为什么非得这样：这只 A5 在 2.4G（`258a:0138`）和有线（`258a:0136`）两种模式下，
> HID 描述符里都**没有 OUTPUT report**，官方网页驱动那套协议在物理上就发不出去。
> 官方真正的 A5 驱动是 `A5系列鼠标驱动.exe`（Windows 原生程序），只能在 Windows 里跑。
> 好消息是：**设置存在鼠标板载，配一次就行，不用长期挂着 Windows。**
>
> 详细证据见 [README.md](../README.md) 的「实测结论」章节。

---

## 0. 硬件前提

| 项 | 要求 |
|---|---|
| CPU 虚拟化 | 需要 AMD-V（`svm`）或 Intel VT-x，且 `kvm` 模块已加载 |
| 内存 | 建议给虚拟机 4~6 GiB（Win11 最低 4 GiB） |
| 磁盘 | 给虚拟机镜像预留 ≥ 64 GiB（Win11 安装程序硬性检查） |
| 用户组 | 当前用户需加入 `libvirt`、`kvm` 组，这样 `virsh` 不用加 `sudo` |

> ⚠️ **两个坑（已实测确认）**
>
> **坑 1：镜像绝对不要放家目录 `~/vm/`。**
> ```
> drwxr-x---  /home/<用户名>     ← 家目录常见为 0750
> libvirt-qemu:x:64055           ← qemu 以 libvirt-qemu 用户跑，不是 owner、通常也不在你的组里
> ```
> 家目录没有 `o+x`，`libvirt-qemu` 连遍历都做不到，放这里必然 `Permission denied`。
> **统一放默认存储池 `/var/lib/libvirt/images/`**，
> libvirt 的 dynamic ownership 会自己处理属主，AppArmor 也对它完全放行。
>
> **坑 2：安装介质的路径可能带空格，而且可能是只读挂载。**
> 所以用 `cp` 而不是 `mv`（只读卷上删不掉源文件），路径记得加引号。

---

## 1. 装虚拟化环境

```bash
sudo apt update
sudo apt install -y qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients \
                    virt-manager virt-viewer virtinst ovmf swtpm
sudo usermod -aG libvirt,kvm "$USER"
```

然后**注销重新登录**（组权限要重新登录才生效）。用 `id` 确认已在 `libvirt`/`kvm` 组里。

验证：

```bash
ls -l /dev/kvm                      # 应该存在；不存在 → 进 BIOS 打开 SVM / AMD-V
virsh -c qemu:///system list --all  # 注意：不要加 sudo
```

---

## 2. 把 Windows 镜像拷进默认存储池

先确认真实路径（挂载点可能带空格）：

```bash
ls -l /media/"$USER"/*/*.iso        # 或 lsblk / df -h 找到挂载点
```

然后拷进去（`cp`，不是 `mv`——安装介质常常是只读挂载的）：

```bash
sudo cp /path/to/Win11_x64.iso \
        /var/lib/libvirt/images/
sudo chmod 644 /var/lib/libvirt/images/*.iso
df -h /                                        # 确认 / 还有空间（ISO 约 7 GiB）
```

关于 Windows 版本的选择：

- **Win11** 能用，但必须满足 TPM 2.0 + UEFI + 64 GiB 磁盘（见第 3 步）。
- 这只 A5 的驱动是老的 32 位原生程序，Win11 通过 WoW64 能跑；**万一装不上或跑不起来**，
  换 Windows 10 更省事：<https://www.microsoft.com/en-us/evalcenter/evaluate-windows-10-enterprise>

---

## 3. 建虚拟机

用 `virt-manager` 图形化建最省事，关键参数：

| 项 | 值 |
|---|---|
| 内存 | 4096 ~ 6144 MiB（Win11 最低 4 GiB） |
| CPU | 4 核 |
| 磁盘 | **64 GiB**（Win11 安装程序硬性检查，小于 64 会拒绝装），存在默认池 `/var/lib/libvirt/images/` |
| 网络 | 默认 NAT 即可 |
| 系统类型 | **Windows 11** |

> **在 virt-manager 里系统类型选 `Windows 11`**，它会自动帮你加上：
> - UEFI 固件（OVMF）
> - TPM 2.0 设备（走 `swtpm`，所以第 1 步必须装 `swtpm` 和 `ovmf`）
>
> 如果建完发现没有 TPM，手动补：`Add Hardware` → `TPM` → 版本选 `2.0`；
> 固件在虚拟机 `Overview` → `Firmware` 里选带 `UEFI` 的那项。
> 装完 `swtpm` 后 libvirt 需要重启一次：`sudo systemctl restart libvirtd`。

### Win11 25H2 首次开机（OOBE）绕开微软账号

Win11 强制联网 + 微软账号。在 OOBE 的"让我们为你连接网络"界面：

1. 按 `Shift` + `F10` 打开命令行
2. 输入 `start ms-cxh:localonly` → 会弹出创建本地账户的界面
3. 如果上面那条无效，改用 `oobe\bypassnro`（回车后会自动重启回到 OOBE，出现"我没有 Internet 连接"选项）

虚拟机名字下面统一用 `win11`，你起别的名字就相应替换。

---

## 4. 关键一步：把鼠标直通给虚拟机

你的鼠标有**两个 USB 身份**，先决定直通哪个：

| 直通目标 | lsusb | 何时用 |
|---|---|---|
| **有线**（先用这个） | `258a:0136` `EWEADN A5` | 插上 USB-C 线。官方明说驱动不支持蓝牙，有线最直接 |
| 2.4G 接收器（备选） | `258a:0138` `SINO WEALTH Wireless Mouse` | 如果驱动认不到有线模式，拔线、插接收器，换成这个 |

图形界面做法：`Add Hardware` → `USB Host Device` → 选中对应设备 → Finish。

**命令行做法（推荐：能持久化，而且键盘就能操作）：**

```bash
lsusb | grep -i 258a          # 确认当前的 vendor:product

cat > /tmp/a5-usb.xml <<'EOF'
<hostdev mode='subsystem' type='usb' managed='yes'>
  <source>
    <vendor id='0x258a'/>
    <product id='0x0136'/>
  </source>
</hostdev>
EOF

# --config = 持久化，虚拟机一启动就自动接管鼠标
virsh -c qemu:///system attach-device win11 /tmp/a5-usb.xml --config
```

---

## 5. 全程只用键盘启动虚拟机

鼠标被直通之后**宿主机就没有指针了**，所以下面这套流程专门设计成键盘可完成：

```
Ctrl+Alt+T                                          # 打开终端
virsh -c qemu:///system start win11
virt-viewer -c qemu:///system win11
```

虚拟机窗口一出来，**A5 本身就是虚拟机的鼠标**，装 Windows、点驱动都有指针，不需要第二个鼠标。

> 如果有第二个鼠标会轻松很多，但按上面的流程单鼠标也能走完。

---

## 6. 在虚拟机里装驱动、配鼠标

1. 在虚拟机里打开驱动下载页：<https://www.eweadn.com/pages/driver-download>，找 **"A5系列鼠标驱动"**
   - 直链：`https://file.eweadn.cn/product-drive/A5系列鼠标驱动.exe`（1.93 MB，Inno Setup 安装包，一路下一步）
2. 打开驱动，应该能看到鼠标。
3. **如果提示找不到设备**：说明它要的是另一个身份。做法是
   - `virsh -c qemu:///system detach-device win11 /tmp/a5-usb.xml --config`
   - 把 xml 里的 `0x0136` 改成 `0x0138`，重开虚拟机（改用 2.4G 接收器）
4. 调你要的东西：DPI 档位、回报率、改键、宏、灯光。

---

## 7. 确认设置真的写进鼠标板载了（关键，别跳过）

这一步决定你以后还需不需要 Windows。

1. 在驱动里把 6 档 DPI 设成**一眼能区分的值**。用默认的就行：
   `800 / 1600 / 2400 / 3200 / 6400 / 12000`，对应 LED 颜色
   **红 / 绿 / 蓝 / 紫 / 黄 / 青**（这个对应表是从驱动内置的 `mouse_info_A5.json` 里读出来的）。
2. 在虚拟机里按鼠标上的 DPI 键，确认 LED 颜色顺序跟驱动里显示的一致。
3. **从虚拟机内部关机**（开始菜单 → 关机）。
   > 这一步很妙：虚拟机一关，A5 自动回到宿主机，不需要任何宿主机操作。
4. 回到 Linux，拔插一次鼠标，再按 DPI 键。LED 颜色顺序跟驱动里配的一致 → **说明确实存进板载了**。
5. 一致的话，以后彻底不需要 Windows 了。

---

## 8. 收尾：不想每次开机都被虚拟机抢走鼠标

```bash
virsh -c qemu:///system detach-device win11 /tmp/a5-usb.xml --config
```

---

## 如果驱动根本不认这只鼠标

那就说明这批货的 `258a:0136` / `0138` 硬件确实不在官方驱动支持范围内
（跟官方网页驱动设备库里查不到这两个 ID 是同一个信号）。那时候只剩两条路：

1. **鼠标上的物理 DPI 键** —— 零风险，设置写板载，配一次长期生效。
2. **抓包路线** —— 在虚拟机里用 USBPcap + Wireshark 抓官方驱动与鼠标之间的 USB 交互，
   把 pcap 提到项目 issue，照着把 `a5tool.py` 里的 feature report 协议补完，做成 Linux 原生工具。
