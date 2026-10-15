# EWEADN / 前行者 A5 鼠标的 Linux 配置工具

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux-blue.svg)](#)

给**前行者 A5**（EWEADN A5 / A5 Pro / A5 Ultra，Sinowealth `258a:0136` / `258a:0138`）
用的 Linux 配置工具。官方只出 Windows 驱动，这个项目把它带到了 Linux：

- **原生 CLI**（`eweadn-a5`）—— 纯 `hidraw`，**不需要 Wine**，秒级读写
- **图形界面**（`eweadn-a5-gui`）—— 把官方驱动跑在 Wine 里，功能最全（DPI、回报率、改键、宏、灯光）
- **桌面快捷方式** —— 装完在应用菜单里点一下就行，和 Windows 一样

> Linux configuration tool for the EWEADN (前行者) A5 gaming mouse.
> Native `hidraw` CLI (no Wine needed) + a launcher for the official Windows
> driver under Wine, plus a desktop entry. Protocol reverse engineered and
> documented in [`docs/protocol.md`](docs/protocol.md).

---

## 为什么需要它

这只鼠标的官方驱动只有 Windows 版。Linux 上：

- ❌ `libratbag` / `piper` 不支持（虽然 libratbag 有 sinowealth 驱动，但这只鼠标的
  report ID 和命令集跟它的不一致）
- ❌ 官方网页驱动（WebHID）不支持 —— 它要求设备有 OUTPUT report，而这只鼠标的
  HID 描述符里**一个都没有**
- ✅ 本项目：把官方驱动的真实协议抓出来，做成原生 Linux 工具

## 快速开始

### 方式一：安装包（推荐）

```bash
# 从 release 下载 .deb 后
sudo apt install ./eweadn-a5_1.0.0_all.deb
```

或者从源码构建：

```bash
git clone https://github.com/harm-m422/Pioneer-Gaming-Mouse-A5-Model-Ubuntu-Debian-Driver.git
cd Pioneer-Gaming-Mouse-A5-Model-Ubuntu-Debian-Driver
bash packaging/build-deb.sh
sudo apt install ./packaging/dist/eweadn-a5_1.0.0_all.deb
```

### 方式二：脚本安装（不需要打包）

```bash
git clone https://github.com/harm-m422/Pioneer-Gaming-Mouse-A5-Model-Ubuntu-Debian-Driver.git
cd Pioneer-Gaming-Mouse-A5-Model-Ubuntu-Debian-Driver
bash install.sh              # 装到 ~/.local，不需要 sudo
```

### 方式三：直接用，不安装

```bash
python3 src/a5tool.py read --parse
```

### 装完之后

```bash
# 图形界面：功能最全，需要 Wine（首次要初始化，约 1 分钟）
sudo apt install wine
eweadn-a5-setup        # 只需一次
eweadn-a5-gui          # 或者点菜单里的「EWEADN A5 鼠标设置」

# 纯命令行：不需要 Wine
eweadn-a5 read --parse
```

> ⚠️ **鼠标要用 Type-C 线插着。** 配置通道只在线模式的接口 1.1 上，
> 2.4G 接收器身份下官方驱动也读不到配置。

## 用法

```bash
# 看设备信息和完整配置（只读）
eweadn-a5 read --parse

# 改一档 DPI
eweadn-a5 set --slot 1 --value 1000

# 一次设 6 档
eweadn-a5 set --dpi 800,1600,2400,3200,6400,12000

# 只看会发什么，不实际写
eweadn-a5 set --slot 1 --value 1000 --dry-run
```

输出示例：

```
/dev/hidraw9  258a:0136  'SINO WEALTH EWEADN A5'
  [设备信息] 14 字节: 09 0d 05 01 01 00 06 00 04 00 00 00 00 1a
             型号标识 : 04 00 00 00 00 1a  (Cfg.ini 里 A5 的 Psd=4,0,0,0,0,1a)
  [读配置] 有效 372 字节
    ...
  [解析] 偏移 0x0f (X): raw=[7, 15, 23, 31, 63, 119] -> [800, 1600, 2400, 3200, 6400, 12000]
         偏移 0x23 (Y): 同上（XY 独立 DPI）
```

## 功能支持情况

| 功能 | 原生 CLI | 官方驱动（Wine） |
|---|---|---|
| 读取设备信息 / 完整配置 | ✅ | ✅ |
| DPI 档位（XY 独立） | ✅ | ✅ |
| 回报率 | ⬜ 待补 | ✅ |
| 灯光 | ⬜ 待补 | ✅ |
| 按键重映射 | ⬜ 待补 | ✅ |
| 宏 | ⬜ 待补 | ✅ |

CLI 目前只做了 DPI —— 但**协议已经全部摊开**（见 [`docs/protocol.md`](docs/protocol.md)），
补其他项只需要用 `tools/capture-write.sh` 再抓一次官方驱动的报文、diff 出偏移量。
欢迎 PR（见「如何贡献」）。

## 它是怎么工作的

协议是从零逆向出来的，完整过程在 [`docs/protocol.md`](docs/protocol.md)。要点：

```
feature report ID = 0x09
缓冲长度 = 固定 520 字节（1 字节 report ID + 519 数据）
时序     = 必须先 SET 命令、再 GET 响应
```

读配置：

```
SET 09 d6 44 00 01 00 90 01 ...(补零到 520)
GET -> 09 58 44 00 01 00 90 01 <配置体 372 字节>
```

写配置 = 把读响应**就地**改 3 个字节（`0x01→0xfa`、`0x02→0x04`、DPI 值），
补零到 520 再 SET 回去。DPI 编码是 `raw = DPI/100 - 1`。

**逆向方法也一并记录**（这是本项目最有价值的部分）：

1. 官方驱动的安装包是 Inno Setup 5.3.3，负载是 LZMA1 —— 从 `zlb\x1a` 块签名后
   第 9 字节开始解（`5d 00 00 80 00` 是属性头）
2. 官方驱动走 `HidD_SetFeature`/`HidD_GetFeature`，而 **Wine 的 winebus 后端
   就是通过 `/dev/hidraw` 的 feature ioctl 跟设备通信的**
3. 所以写了个 `LD_PRELOAD` 的 `ioctl()` 拦截器（[`tools/hidshim.c`](tools/hidshim.c)），
   把官方驱动跑在 Wine 里，直接抓它发出的每一个字节
4. 前提：**必须先 `wineserver -k`** —— winebus 跑在常驻的 plugplay 服务进程里

## 支持的设备

| 模式 | USB ID | 产品串 | 配置通道 |
|---|---|---|---|
| Type-C 有线 | `258a:0136` | `EWEADN A5` | ✅ 接口 1.1 |
| 2.4G 接收器 | `258a:0138` | `SINO WEALTH Wireless Mouse` | ❌ 不响应 |

设备型号标识（读设备信息命令返回的 6 字节）：

| 型号 | 标识 | 传感器 |
|---|---|---|
| EWEADN A5 | `04 00 00 00 00 1a` | PAW3311 |
| EWEADN A5 Pro | `04 00 00 00 00 29` | PAW3311 |
| EWEADN A5 Ultra | `04 00 00 00 00 1d` | PAW3395 |

## 常见问题

**`read` 报 `EPIPE` 或 `ETIMEDOUT`**
配置通道只在线模式的接口 1.1 上。请用 Type-C 线插着鼠标，别用 2.4G 接收器。
用 `eweadn-a5 read` 不带 `--dev` 会自动找对的节点。

**权限不够（`Permission denied`）**
装 udev 规则，然后**拔插一次鼠标**（`udevadm trigger` 对已存在的节点不生效）：

```bash
sudo cp data/99-eweadn-a5.rules /etc/udev/rules.d/
sudo udevadm control --reload
# 拔插鼠标
```

**Wine 界面里看不到鼠标**
`eweadn-a5-gui` 会先跑 `wineserver -k`；如果你手动启动，也要先杀掉残留的
wine 进程，否则 winebus 可能还拿着旧的设备列表。

**写坏了怎么办**
设置是板载的、可逆的。把 DPI 设回出厂值即可：
`eweadn-a5 set --dpi 800,1600,2400,3200,6400,12000`

## 如何贡献

最有用的贡献是**补充 CLI 支持的功能项**：

1. `eweadn-a5-setup`（或用你自己现成的 Wine 环境）
2. `bash tools/capture-write.sh` —— 在界面里改那一项设置并应用
3. 把日志拿来和 `eweadn-a5 read` 的响应做逐字节 diff，定位偏移量
4. 照着 `src/a5tool.py` 里 `cmd_set` 的写法实现，然后提 PR

回报率、灯光、按键重映射的命令都还没抓 —— 欢迎认领。

## 文档

| 文件 | 内容 |
|---|---|
| [docs/protocol.md](docs/protocol.md) | **完整协议**：读写命令、配置体偏移、DPI 编码、抓包方法、踩过的坑 |
| [docs/driver-reverse-engineering.md](docs/driver-reverse-engineering.md) | 官方驱动的结构、设备库、参数表 |
| [docs/channel-investigation.md](docs/channel-investigation.md) | 走了哪些弯路（为什么 output report / libratbag / piper 都不行） |
| [docs/prior-art.md](docs/prior-art.md) | 同类项目调研 |
| [docs/windows-vm-fallback.md](docs/windows-vm-fallback.md) | 备查：用 Windows 虚拟机的老方案（已被 Wine 取代） |

## 许可与声明

MIT（见 [LICENSE](LICENSE)）。

本项目与前行者 / EWEADN 无隶属关系，相关商标归其各自所有者。
官方 Windows 驱动由用户自行从厂商官网下载，本项目不再分发。
协议实现是为互操作性目的、通过观察公有硬件行为得出的。
