# GitHub 调研：有没有现成的 A5 Linux 方案？

调研时间：2026-10-15。结论：**没有能直接用的，但路径已经很清楚。**

---

## 一、确定没有现成的

| 查了什么 | 结果 |
|---|---|
| libratbag 上游 master 的 `data/devices/`（126 个文件） | sinowealth 只有 `0027` / `0029` / `0051` / `1007` 四个，**没有 `0136` / `0138`** |
| 全 GitHub 搜 `"258a:0136"` | **0 命中** |
| 全 GitHub 搜 `"258a:0138"` | **0 命中** |
| `repo:libratbag/libratbag eweadn` | **0 命中** |
| 搜 `eweadn` 相关仓库（9 个） | 只有 2 个跟鼠标有关，见下 |

---

## 二、但找到三件有用的

### 1. `esaporski/eweadn-a5-pro-udev` —— 直接确认了设备 ID

<https://github.com/esaporski/eweadn-a5-pro-udev>

```udev
# EWEADN A5 Pro (USB and Dongle)
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="258a", ATTRS{idProduct}=="0136", MODE="0666"
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", KERNELS=="0003:258A:0138.*", MODE="0666"
```

和实测完全一致。**说明 `258a:0136`（有线）/ `258a:0138`（接收器）就是 EWEADN A5 家族的 ID**
—— 我们那两份 `.device` 文件的 `DeviceMatch` 是对的。

仓库里只有 udev 规则（217 字节）+ Arch 打包文件，没有协议实现。

### 2. `libratbag/libratbag#1904` —— 现成的「怎么做」模板（open）

<https://github.com/libratbag/libratbag/pull/1904>（作者 LuckSide，2026-09-26）

给 Delux M800DB（`258a:002e/002f`）加支持，**关键点是它给 libratbag 新增了一个 config report ID**：

```c
enum sinowealth_report_id {
 	SINOWEALTH_REPORT_ID_CONFIG = 0x4,
 	SINOWEALTH_REPORT_ID_CMD = 0x5,
 	SINOWEALTH_REPORT_ID_CONFIG_LONG = 0x6,
+	/* Delux M800DB (258A:002E/002F) uses 0x08 for config+buttons. */
+	SINOWEALTH_REPORT_ID_M800DB = 0x8,
 };
```

PR 描述里的协议说明（从 USBPcap 抓官方软件得到）：

```
- 8-byte CMD channel on report 0x05, config/buttons on report 0x08.
- 154-byte config: u16LE DPI slots at 13–28 (8 slots, raw = DPI/100 up to 5000,
  raw = 39 + DPI/500 above, max 10000, 5500 unrepresentable),
  slot count in low nibble + 1-based active index in high nibble of byte 11,
  per-slot RGB colors at 45–68, LED effect at 69 (00 off, 02 steady, 03 breathing)
```

**这正是我们需要的先例**：不同 Sinowealth 型号用**不同的 config report ID 和不同的配置布局**，
社区就是一个个打补丁加上去的。你的 A5 用 report 6/9 属于同一类问题，不是"不可能"。

### 3. `MuhtasimJawad/EWEADN-H2-Control` —— 同厂兄弟型号的完整 Linux 工具

<https://github.com/MuhtasimJawad/EWEADN-H2-Control>

给 EWEADN **H2** 写的 Linux GUI + CLI，756 行 `h2_protocol.py`，**协议是逆向官方网页驱动得到的**。
功能：电量百分比、固件版本、回报率（125/250/500/1000）、6 档 DPI（50–32000）、完整改键。

协议形态：

```python
arr = [0x00, 0x30] + [0x00] * 31   # getDeviceInfo   → 33 字节
arr = [0x00, 0x11] + [0x00] * 31   # getMouseKeys
arr = [0x00, 0x12] + [0x00] * 31   # getReportRate
arr = [0x00, 0x13] + [0x00] * 31   # getDpiData
arr = [0x00] * 33; arr[1] = 3      # setDpiValue
dev.write(arr)
```

- **命令编号和 A5 网页协议同源**（都有 `0x08/0x09` 读/写按键、`0x11/0x12/0x13` 这类查询命令）
- **但传输层不同**：H2 走 `output report ID 0 + 33 字节`，而 A5 的 HID 描述符里
  **一个 output report 都没有** → **这份代码不能直接搬**
- 值得借鉴的是工程做法：udev 规则写法、CLI 设计、响应校验（它的 README 提到官方网页驱动
  有时读到陈旧/垃圾值，它加了校验）

H2 的 USB ID 是 `089d:062f`（接收器）/ `088d:062e`（有线），和 A5 的 `258a:*` 不是一个芯片组。

### 顺带确认的旁证

buildcores 设备库 PR #179 里有 **`MCHOSE A5 Pro`（PixArt PAW3395 / 26000 DPI）**
—— 就是 EWEADN A5 Pro 的马甲版（网页驱动 JS 里也确实引用了 `mchose.com.cn:9999`）。
同 PR 里还有 `EWEADN S9 Ultra`（PAW3950）、`EWEADN X23 SE`（Instant S205）。

---

## 三、libratbag 里的 sinowealth 生态（47 个 issue/PR）

有人在持续加型号，说明这条路是通的：

- [#1904](https://github.com/libratbag/libratbag/pull/1904) Delux M800DB（`258a:002e/002f`）— open，有完整协议文档
- [#1876](https://github.com/libratbag/libratbag/issues/1876) AOC GM510（Sinowealth）
- [#1892](https://github.com/libratbag/libratbag/issues/1892) Newskill Arakne Elite（sinowealth）
- [#1465](https://github.com/libratbag/libratbag/issues/1465) / [#1359](https://github.com/libratbag/libratbag/issues/1359) / [#1553](https://github.com/libratbag/libratbag/issues/1553) `258a:1007` 系列
- [#1565](https://github.com/libratbag/libratbag/issues/1565) Fantech Helios UX3 V2（`258a:0027`）
- [#1735](https://github.com/libratbag/libratbag/pull/1735) DM4 Evo（sinowealth-1007）

---

## 四、结论与下一步

**没有现成的 A5 方案，但这事的路径已经成熟：**

libratbag 自带的 sinowealth 驱动，常量跟你的鼠标对得上：

| libratbag 常量 | A5 的 HID 描述符 | |
|---|---|---|
| `SINOWEALTH_REPORT_ID_CMD = 0x5`，`SINOWEALTH_CMD_SIZE = 6`（1 report ID + 5 数据） | Feature **id=5 = 5 字节**（有线） | ✓ |
| `SINOWEALTH_REPORT_ID_CONFIG_LONG = 0x6`（`is_long` 判据） | Feature **id=6**（1031B） | ✓ |
| `SINOWEALTH_CONFIG_REPORT_SIZE = 520`（1 + 519） | Feature **id=9 = 519 字节** | ✓ 但驱动读的是 report 6，不是 9 ⚠ |

所以：

- **情况 A：只差设备文件** → 补 `[Driver/sinowealth/devices/<固件版本>]` 段，5 分钟修好 piper
- **情况 B：config report ID 不对**（很可能，因为 519 字节的配置在 report 9 而非 6）
  → 照 #1904 的样子给 libratbag 打补丁，本地编译安装

**无论哪种，下一步都是同一个：跑探针拿固件版本号。**

```bash
python3 src/a5tool.py probe      # 枚举所有 hidraw 设备 + report 结构
```
