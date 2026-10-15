# 官方 A5 驱动逆向结论（2026-10-15）

## 一、怎么把驱动挖出来的

官网给的 `A5系列鼠标驱动.exe`（1.93 MB）是个 **Inno Setup 5.3.3** 安装包，
真正的驱动在 LZMA 压缩负载里。之前失败是因为：

1. 先用 `strings` 扫——**完全没用**，负载是压缩的
2. 再暴力扫 LZMA1 流——失败，因为漏了正确的起始偏移

**正确的解包方法**（Inno Setup 5.3.x 的 `zlb` 块格式）：

```python
off = raw.find(b'zlb\x1a')            # 块签名
# off+4 起的 5 字节是 LZMA1 属性头：5d 00 00 80 00
#   props=0x5d → lc=3, lp=0, pb=2
#   dict_size = 0x00800000 (8 MiB)
# LZMA 数据从 off+9 开始，且没有 8 字节的 uncompressed_size 字段
d = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[{
        "id": lzma.FILTER_LZMA1, "dict_size": 1 << 23,
        "lc": 3, "lp": 0, "pb": 2}])
out = d.decompress(raw[off+9:], max_length=16_000_000)
# -> 3,763,371 字节
```

产出物在 `vendor/`：
- `a5drv.exe` —— 原始安装包
- `inno_chunk.bin` —— 解出的 3.76 MB 负载（驱动文件内容）
- `pe_10ae99.bin` —— 主程序 PE（2.67 MB，MSVC 2008 / i386 / MFC）
- `pe_0d87d9.bin`、`pe_0e9561.bin` —— 辅助（CRT 工具、`D:\OEM\Tools\icondll` 卸载器）

## 二、官方驱动的设备数据库（关键）

负载里带一份 INI 风格的设备表，**明确列出了你这只鼠标**：

```
[OPT]
UID=058
VID=0x258A          PID=0x0136
VID_Wireless=0x258A PID_Wireless=0x0138
Fw=25
Psd=4,0,0,0,0,1a
Name=EWEADN A5      ShortName=A5
FwVerNeedToUpdate=0x206
Sensor = 0x3311                       ← PAW3311
Pop=1  PopByte1=1  AutoSw=0  ShowEffect=0  ShowPower=1  ShowRGBSw=1
LedMask=0x200  LEDParam=0,2,0x75,0,0,0x3F  ShowSleep=1  SleepTime=3
Debounce=0x0206  DebounceUI=2,4,6,8,10,12  DebounceHW=1,2,3,4,5,6
DR=0x1000
```

同一份表里另外两个型号：

| Name | Psd | Sensor |
|---|---|---|
| EWEADN A5 | `4,0,0,0,0,1a` | 0x3311 (PAW3311) |
| EWEADN A5 PRO | `4,0,0,0,0,29` | 0x3311 |
| EWEADN A5 Ultra | `4,0,0,0,0,1D` | 0x3395 (PAW3395) |

**意义：官方驱动支持的 VID/PID 就是 `258a:0136`（有线）和 `258a:0138`（无线），
和实测完全一致。** 这基本排除了"驱动认不到你这批货"的风险。

## 三、官方驱动用什么通道

主程序导入表（`HID.DLL`）：

```
HidD_GetAttributes  HidD_GetHidGuid  HidD_GetPreparsedData  HidP_GetCaps
HidD_FreePreparsedData  HidP_GetSpecificButtonCaps  HidP_GetSpecificValueCaps
HidD_SetFeature     ← 写
HidD_GetFeature     ← 读
```

外加 `SETUPAPI.dll` 的 `SetupDiGetClassDevsW` / `SetupDiEnumDeviceInterfaces` /
`SetupDiGetDeviceInterfaceDetailW`（枚举设备），以及 `KERNEL32.WriteLine` 之类。

**关键结论：官方驱动走的是 Feature report（`HidD_SetFeature` / `HidD_GetFeature`），
不是 output report，也不是厂商控制传输。** 这和我们一直在敲的通道一致。

## 四、参数表（和网页驱动数据完全吻合）

```
[KEY]
KM=12  KEYH=12  MDNUM=3
K1_1=0x01,0x11,0x00,0x01     K6_1=0x08,0xAA,0x00,0x06
K2_1=0x01,0x13,0x00,0x02     K7_1=0x08,0xA2,0x08,0x07
K3_1=0x01,0x12,0x00,0x03     K8_1=0x08,0xA2,0x00,0x08
K4_1=0x01,0x14,0x00,0x04     K9_1=0x08,0xA2,0x00,0x09
K5_1=0x01,0x15,0x00,0x05     K10_1=0x08,0xA2,0x00,0x0A

[SENSOR_1]
Sensor = 0x3311
DM=6
DPI=800,1600,2400,3200,6400,12000
DC=255,0,0, 0,255,0, 0,0,255, 255,0,255, 255,255,0, 0,255,255
DEFLEVEL=1
;DPIRANGE=50,10000,50,1,1,10200,26000,200,204,4
```

`DPI` 和 `DC` 与网页驱动 `mouse_info_A5.json` 里的
800/1600/2400/3200/6400/12000 + 红绿蓝紫黄青 **完全一致**，
证明桌面驱动和网页驱动共用同一套型号数据。

## 五、试过但无效的：把网页协议改走 feature report

假设"协议一样、只是传输层不同"，把网页驱动的命令序列
（`55 0E A5 0B 30 …`、`55 0F AE 0A 30 …`、`55 08 A5 0B 2C …`、`55 03`、
`55 30 A5 0B 2E …`）以及设备表里的 `Psd` 序列 `04 00 00 00 00 1A`，
逐个用 `SET_FEATURE`（report 5/6/9，长度 8/11/16/64）发过去，然后 `GET_FEATURE`。

**结果：GET 响应一字未变（仍然是与命令无关的恒定值）。**

## 六、HID 侧已经穷尽的测试

| 测试 | 结果 |
|---|---|
| OUTPUT report 存在性 | ❌ 两个身份都没有 |
| report ID 全扫（0..255） | 只有描述符声明的 5/9(/0a) 应答，无隐藏通道 |
| class 0xFF 厂商接口 / bulk 端点 | ❌ 无 |
| 厂商控制传输（非 HID） | 需要 root + libusb；`/dev/bus/usb` 无可写权限，系统无 libusb/pyusb |
| GET 长度扫描 | 仅 len=8 成功，返回恒定垃圾 |
| SET 长度扫描 | 有线 2~64 全成功；接收器仅 8 |
| 发命令后监听 input（0.25s / 3s，两个接口） | ❌ 0 条 |
| 蓝牙模式 | ❌ 你的 PC 没有蓝牙适配器 |

**结论：不知道官方驱动的确切命令序列，就无法在 Linux 上复现。**

## 七、下一步的两条路

1. **Wine 跑官方驱动**（推荐先试，代价小）
   Wine 10 在 apt 里有，且 winebus 后端能访问 `/dev/hidraw`。
   官方驱动是静态链接 MFC 的原生 i386 程序，理论上能在 Wine 里跑起来。
   成了的话**不用装 Windows**。

2. **Windows 虚拟机 + 官方驱动**（保底）
   现在已经确认官方驱动支持 `258a:0136/0138`，风险基本消除。
   配一次即可，设置存板载。

3. 抓包（USBPcap）能让我把 Linux 原生工具补完。

---

## 八、补充：静态分析的局限（为什么转去抓包）

对主程序 `pe_10ae99.bin`（MFC / MSVC 2008 / 未优化）做 `FF 15`（`call dword ptr [绝对地址]`）目标统计：

| 导入函数 | IAT | 静态统计的调用次数 |
|---|---|---|
| `CreateFileW` | 0x5d7274 | 29 |
| `WriteFile` | 0x5d7220 | 14（存疑，见下） |
| `ReadFile` | 0x5d7238 | 15（存疑） |
| `SetupDiGetClassDevsW` | 0x5d7508 | 1 |
| `SetupDiEnumDeviceInterfaces` | 0x5d7514 | 2 |
| `MessageBoxW` | 0x5d784c | 40 |
| **`HidD_SetFeature`** | 0x5d71fc | **0** |
| **`HidD_GetFeature`** | 0x5d7200 | **0** |

即：官方驱动**静态上不调用** feature 接口，反而像在用 `WriteFile`/`ReadFile`。

但**这个统计不可靠**：`0x1b0dce`、`0x1b148d` 等"函数"反汇编出来
会 `call 0xff355458`、`jmp 0x36158e` 这类跳到映像外的目标，
说明 `.text` 里混着跳转表/数据，我的 `FF 15` 匹配吃进了假阳性。
（`.text` 里 `FF 15` 共 7731 条、`FF 25` 147 条、`E8` 目标落在 thunk 区的只有 33 条，
典型的 `/OPT:REF` 关闭 + 未优化构建的特征。）

另外还挖到这些字符串，说明程序自带配置读写：
- `%sgSetting.dct`
- `save gsetting: CreateFile err=%d`

**结论：静态分析在这个二进制上产出太低，改走"让官方驱动真跑 + 抓实际报文"的路线。**
