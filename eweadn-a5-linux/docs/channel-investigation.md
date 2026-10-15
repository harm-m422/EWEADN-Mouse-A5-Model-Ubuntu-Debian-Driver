# 命令通道实测结论（2026-10-15，全部有仪器数据支撑）

## 一、为什么 libratbag 修不好

libratbag `driver-sinowealth.c` 的读取原语（`sinowealth_query_read`）：

```c
set_feature_report(5, [0x05, cmd, 0, 0, 0, 0], 6);   /* SINOWEALTH_CMD_SIZE = 6 */
get_feature_report(5, 6);                            /* ← 必须是 6 字节 */
```

`ratbag_hidraw_raw_request()` 里 `len` 就是**含 report ID 的总长度**：

```c
case HID_REQ_SET_REPORT: buf[0] = reportnum; ioctl(fd, HIDIOCSFEATURE(len), buf);
case HID_REQ_GET_REPORT: memset(tmp_buf,0,len); tmp_buf[0]=reportnum; ioctl(fd, HIDIOCGFEATURE(len), tmp_buf);
```

实测（有线 `258a:0136`，feature report 5 声明 5 字节）：

| 步骤 | 长度 | 结果 |
|---|---|---|
| **SET** feature 5 | 2 ~ 64 全部 | ✅ **全部成功** |
| **GET** feature 5 | 6 | ❌ **EPIPE** |
| GET feature 5 | 8 | ✅ 成功，但返回 `09 07 02 b1 02 c0 75 08` |

所以 libratbag 报 `Couldn't read firmware version: Broken pipe (-32)` 的原因就是：
**SET 成功、紧接着的 GET 在 6 字节上被设备 stall。**

## 二、但改长度也救不了——响应不是协议数据

`cmdsweep` 对 13 条不同的命令做 SET，**响应完全一致、一字不差**：

```
0138 接收器  feature 5 len=8  ->  06 a0 02 64 82 99 3c 08   （13/13 条命令）
0136 有线    feature 5 len=8  ->  09 07 02 b1 02 c0 75 08   （13/13 条命令）
```

协议应答不可能与命令无关。而且返回值与 HID 描述符尾部高度相似（两个设备的描述符
最后 16 字节完全相同：`00 26 ff 00 19 01 29 02 75 08 96 07 02 b1 02 c0`），
说明读回来的是**固件里的陈旧 / 未初始化内存**。

**结论：feature 通道读不回来任何协议数据，libratbag 的 sinowealth 驱动在这块硬件上
不可能工作——不是补一个长度常量能解决的。**

## 三、各通道普查（两个身份）

| 通道 | 0138 接收器 | 0136 有线 |
|---|---|---|
| OUTPUT report（网页驱动协议需要） | **不存在** | **不存在** |
| Feature 5 SET | 仅 len=8 成功 | len 2~64 全部成功 |
| Feature 5 GET | 仅 len=8，返回恒定垃圾 | 仅 len=8，返回恒定垃圾 |
| Feature 6 / 9 / 10 | GET 仅 len=8；SET 全部 ETIMEDOUT | Feature 6/9 GET 全失败 |
| 发命令后的 input report | 无（监听 0.25s / 3s 都是 0 条） | 无（两个节点各监听 3s 都是 0 条） |
| 厂商 class 0xFF 接口 / bulk 端点 | 无 | 无 |

鼠标链路本身是好的：`linktest` 10 秒收到 **8207 条**鼠标报文。

## 四、还剩下没测的

1. **蓝牙模式** —— 唯一没测过的连接方式（有线、2.4G 都测了）。
   官方网页驱动的设备库里 A5 登记的是 `A8A4`/`A8A5:2255`，**既不等于 0136 也不等于 0138**。
   很可能那就是蓝牙身份，而且蓝牙 HID 可能带 output report —— 那样官方网页驱动就能用了。
2. **厂商 USB 控制传输**（非 HID report，`bmRequestType` 0x40/0xC0 + 私有 `bRequest`）。
   hidraw 够不着，需要 libusb + `/dev/bus/usb` 权限。H2 那个工具的 udev 规则里
   同时写了 `SUBSYSTEM=="usb"` 规则，是个旁证。

## 五、Linux 侧能确定的结论

- ❌ 网页驱动协议（output report 0 + 64B）：**两个身份都没有 output report**，物理上不可能
- ❌ libratbag sinowealth 驱动：GET 读不到数据，**改长度也没用**
- ❌ H2-Control 那套（output report 0 + 33B）：同样需要 output report，不可能
- ⚠️ 剩下的可行路线只有：**抓官方 Windows 软件的 USB 流量**，或者**蓝牙模式**

## 六、附：udev 规则的两处更正

1. 我早先断言 `TAG+="uaccess"` 不生效——**这是错的**。`/dev/hidraw4/5` 上的 ACL
   （`user:<用户名>:rw-`）就是它给的。当时之所以全被拒，是因为规则**还没装**。
2. `udevadm trigger --action=add` 对**已存在的节点**不生效（实测 `hidraw6/9` 权限没变），
   必须**重新插拔**才能让新规则的 MODE 落到节点上。
