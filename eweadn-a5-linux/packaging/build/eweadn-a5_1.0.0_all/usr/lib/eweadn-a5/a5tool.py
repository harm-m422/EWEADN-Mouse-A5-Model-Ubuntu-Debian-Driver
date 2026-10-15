#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
__version__ = "1.0.0"

前行者 / EWEADN 鼠标 Linux 调参工具（纯 hidraw，不需要官方驱动、不需要 Windows）

协议来源：官方网页驱动 https://hub.eweadn.cn/yjs/web/detail 的前端代码
（chunk 515-6b86cd2869bf5bdc.js），报文与字段偏移都是从那里读出来的，
参数表来自其内置的 mouse_info_A5.json / mouse_info_A5Pro.json。

命令格式（65 字节，第 0 字节是 HID report ID = 0x00，后 64 字节是负载）：
    负载 p[0] = 0x55
    p[1] = 命令字：0x03 版本 / 0x08 读按键 / 0x09 写按键 / 0x0E 读配置
                   0x0F 写配置 / 0x30 读电量
    读配置：55 0E A5 0B 30 01 01 01 00 00
    写配置：55 0F AE 0A 30 01 01 01 00 00 + 参数

用法：
    python3 a5tool.py probe                 # 枚举所有 hidraw 设备 + report 结构
    python3 a5tool.py get                   # 读回当前配置（只读，安全）
    python3 a5tool.py set --dpi 800,1600,3200 --active 2 --rate 1000
    python3 a5tool.py raw --hex "55 0e a5 0b 30 01 01 01 00 00"

注意：官方网页驱动的协议走的是 report ID 0x00 的 OUTPUT report。
     如果你的鼠标当前是 2.4G 接收器（258a:0138）接的，probe 会告诉你
     那个接收器上根本没有 OUTPUT report —— 这种情况请改用 Type-C 有线连接。
"""

import argparse
import errno
import glob
import os
import select
import struct
import sys
import time

# ---------------------------------------------------------------- hidraw ioctl

_IOC_READ = 2
_IOC_WRITE = 1


def _IOC(direction, typ, nr, size):
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr


def _IOR(typ, nr, size):
    return _IOC(_IOC_READ, typ, nr, size)


def _IOWR(typ, nr, size):
    return _IOC(_IOC_READ | _IOC_WRITE, typ, nr, size)


HIDIOCGRAWINFO = _IOR("H", 0x03, 8)          # struct hidraw_devinfo
HIDIOCGRDESCSIZE = _IOR("H", 0x01, 4)        # int
HIDIOCGRDESC = _IOR("H", 0x02, 4 + 4096)     # struct {u32 size; u8 value[4096];}
HIDIOCGRAWNAME = lambda n: _IOC(_IOC_READ, "H", 0x04, n)   # noqa: E731
HIDIOCGFEATURE = lambda n: _IOWR("H", 0x07, n)             # noqa: E731
HIDIOCSFEATURE = lambda n: _IOWR("H", 0x06, n)             # noqa: E731

BUS_NAMES = {1: "PCI", 2: "ISDN", 3: "USB", 4: "ADB", 5: "I2C", 0x18: "BT"}


_libc = None


def _ioctl(fd, req, size_or_init):
    """
    直接走 ctypes 调 libc 的 ioctl(2)。

    为什么不用 fcntl.ioctl —— 实测（Python 3.14）三条路都有问题：
      fcntl.ioctl(fd, req, bytes(4100))            -> ValueError: ioctl argument 3 is too long
      fcntl.ioctl(fd, req, bytearray(4100), True)  -> 返回 int（ioctl 返回值），拿不到内核回填的数据
      fcntl.ioctl(fd, req, bytearray(n), False)    -> 能拿到数据，但 n > 1024 仍抛 ValueError
    HIDIOCGRDESC 按内核定义需要 4 + 4096 = 4100 字节缓冲，所以直接调 syscall 最稳。

    size_or_init: int -> 申请这么大的零缓冲；bytes/bytearray -> 用它的内容初始化，长度即缓冲大小。
    返回内核回填后的 bytes（长度与缓冲一致）。
    """
    global _libc
    import ctypes
    if _libc is None:
        _libc = ctypes.CDLL(None, use_errno=True)
        _libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
        _libc.ioctl.restype = ctypes.c_int
    if isinstance(size_or_init, int):
        buf = (ctypes.c_char * size_or_init)()
    else:
        data = bytes(size_or_init)
        buf = (ctypes.c_char * len(data)).from_buffer_copy(data)
    if _libc.ioctl(fd, req, ctypes.byref(buf)) < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return bytes(buf)


def dev_info(fd):
    """返回 (bus, vid, pid)。struct hidraw_devinfo = u32 bus + s16 vendor + s16 product。"""
    bus, vid, pid = struct.unpack("=ihh", _ioctl(fd, HIDIOCGRAWINFO, 8))
    return bus, vid & 0xFFFF, pid & 0xFFFF


def dev_name(fd):
    try:
        return _ioctl(fd, HIDIOCGRAWNAME(256), 256).split(b"\x00")[0].decode("utf-8", "replace")
    except OSError:
        return "?"


def report_descriptor(fd, devpath=None):
    """
    取 HID report descriptor 原始字节。
    优先读 sysfs（/sys/class/hidraw/hidrawN/device/report_descriptor，全局可读，不需要 ioctl），
    读不到再退回 HIDIOCGRDESC。
    """
    if devpath:
        sysfs = "/sys/class/hidraw/%s/device/report_descriptor" % os.path.basename(devpath)
        try:
            with open(sysfs, "rb") as fh:
                data = fh.read(4096)
            if data:
                return data
        except OSError:
            pass
    try:
        size = struct.unpack("=I", _ioctl(fd, HIDIOCGRDESCSIZE, 4))[0]
        if not size or size > 4096:
            return b""
        buf = _ioctl(fd, HIDIOCGRDESC, 4 + 4096)
        return buf[4:4 + size]
    except OSError:
        return b""


# ------------------------------------------------- HID report descriptor 解析

def parse_reports(desc):
    """
    极简 HID report descriptor 解析器。
    返回 [(kind, report_id, size_bytes), ...]，kind ∈ {'Input','Output','Feature'}
    同一个 (kind, report_id) 的多个 main item 会累加，得到该 report 的总长度。
    """
    KIND = {0x8: "Input", 0x9: "Output", 0xB: "Feature"}
    acc = {}
    i = 0
    usage_page = report_size = report_count = report_id = None
    while i < len(desc):
        b = desc[i]
        if b == 0xFE and i + 2 < len(desc):          # long item
            ln = desc[i + 1]
            i += 3 + ln
            continue
        size = b & 0x03
        size = 4 if size == 3 else size
        typ = (b >> 2) & 0x03
        tag = (b >> 4) & 0x0F
        data = desc[i + 1:i + 1 + size]
        val = int.from_bytes(data, "little") if data else 0
        i += 1 + size

        if typ == 0x01:      # Global
            if tag == 0x0:
                usage_page = val
            elif tag == 0x7:
                report_size = val
            elif tag == 0x8:
                report_id = val
            elif tag == 0x9:
                report_count = val
        elif typ == 0x00 and tag in KIND:   # Main
            total_bits = (report_size or 0) * (report_count or 0)
            # descriptor 里没出现过 Report ID 项 = 该设备不用编号 report。
            # hidraw 语义下就等同于 report ID 0（write() 的首字节填 0），所以归一到 0，
            # 否则这里会是 None，后面格式化/比较都得单独处理。
            key = (KIND[tag], 0 if report_id is None else report_id)
            acc[key] = acc.get(key, 0) + (total_bits + 7) // 8
    return [(k, rid, size) for (k, rid), size in acc.items()]


def list_devices():
    devs = []
    for path in sorted(glob.glob("/dev/hidraw*"), key=lambda p: int("".join(c for c in p if c.isdigit()) or 0)):
        try:
            fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            devs.append({"path": path, "error": str(exc)})
            continue
        try:
            # 单个设备出问题不能拖垮整份报告：拆开各自兜底
            try:
                bus, vid, pid = dev_info(fd)
            except OSError as exc:
                devs.append({"path": path, "error": "HIDIOCGRAWINFO 失败: %s" % exc})
                continue
            try:
                name = dev_name(fd)
            except Exception as exc:            # noqa: BLE001
                name = "? (%s)" % exc
            try:
                rep = parse_reports(report_descriptor(fd, path))
            except Exception as exc:            # noqa: BLE001
                rep = []
                name += "  [descriptor 读取失败: %s]" % exc
        finally:
            os.close(fd)
        devs.append({"path": path, "bus": bus, "vid": vid, "pid": pid, "name": name, "reports": rep})
    return devs


# ------------------------------------------------------------------ 传输层

REPORT_ID = 0x00          # 官方网页驱动用的 report ID
PAYLOAD = 64              # 负载长度


def build_cmd(payload, report_id=REPORT_ID):
    """把负载补零到 64 字节，前面加上 report ID，得到要 write() 的 65 字节。"""
    if len(payload) > PAYLOAD:
        raise ValueError("负载超过 64 字节")
    return bytes([report_id]) + bytes(payload) + bytes(PAYLOAD - len(payload))


def write_read(path, cmd65, timeout=1.0, tries=3, verbose=True):
    """
    写一条 output report，然后等一条 input report。
    返回读到的 bytes（不含 report ID），超时返回 None。
    """
    fd = os.open(path, os.O_RDWR)
    try:
        # 先清掉输入队列里的陈旧数据
        while select.select([fd], [], [], 0)[0]:
            try:
                if not os.read(fd, PAYLOAD):
                    break
            except OSError:
                break
        os.write(fd, cmd65)
        for _ in range(tries):
            if not select.select([fd], [], [], timeout)[0]:
                continue
            data = os.read(fd, PAYLOAD)
            if verbose:
                print(f"  <- [{len(data):>2}B] {data.hex(' ')}")
            return data
        return None
    finally:
        os.close(fd)


def feature_roundtrip(path, report_id, payload_len):
    """按 feature report 读一把（缓冲首字节必须是 report number）。"""
    fd = os.open(path, os.O_RDWR)
    try:
        init = bytearray(payload_len + 1)
        init[0] = report_id & 0xFF
        return _ioctl(fd, HIDIOCGFEATURE(payload_len + 1), init)
    finally:
        os.close(fd)


def feature_get(path, report_id, total_len):
    """
    GET_FEATURE。total_len 是**含 report ID 字节**的缓冲总长度。
    返回 (data, None) 或 (None, OSError)。
    """
    fd = None
    try:
        fd = os.open(path, os.O_RDWR)
        init = bytearray(total_len)
        init[0] = report_id & 0xFF
        return _ioctl(fd, HIDIOCGFEATURE(total_len), init), None
    except OSError as exc:
        return None, exc
    finally:
        if fd is not None:
            os.close(fd)


def feature_set(path, report_id, payload, total_len=None):
    """
    SET_FEATURE。payload 不含 report ID；缓冲 = [report_id] + payload，补零到 total_len。
    返回 None（成功）或 OSError。
    """
    if total_len is None:
        total_len = 1 + len(payload)
    buf = bytearray(total_len)
    buf[0] = report_id & 0xFF
    body = payload[:max(0, total_len - 1)]
    buf[1:1 + len(body)] = body
    fd = None
    try:
        fd = os.open(path, os.O_RDWR)
        _ioctl(fd, HIDIOCSFEATURE(total_len), buf)
        return None
    except OSError as exc:
        return exc
    finally:
        if fd is not None:
            os.close(fd)


# ------------------------------------------------------------ 配置字段布局
# 偏移是在“负载 p[]”上的（对应前端 JS 里 t[i] 的 i-1）。
# 读回来的响应数组下标与负载下标一致（前端就是这么用的）。

OFF = {
    "report_rate": 0x0A,     # 设备值 = 实际档位索引 + 1
    "dpi_flag": 0x0B,        # 同时被前端当成 dpi_count
    "dpi_index": 0x0C,       # 设备值 = 实际档位索引 + 1
    "dpi1": 0x0D,            # 小端 u16，dpi2..dpi6 依次 +2
    "scroll_flag": 0x30,
    "lod_value": 0x31,
    "sensor_flag": 0x32,
    "key_respond": 0x33,
    "sleep_light": 0x34,
    "highspeed_mode": 0x35,
    "wakeup_flag": 0x36,
    "dpi_tip": 0x37,
}

CMD_GET_VERSION = [0x55, 0x03]
CMD_GET_KEYS = [0x55, 0x08, 0xA5, 0x0B, 0x2C, 0, 0, 0, 0, 0]
CMD_GET_CONFIG = [0x55, 0x0E, 0xA5, 0x0B, 0x30, 0x01, 0x01, 0x01, 0x00, 0x00]
CMD_SET_CONFIG = [0x55, 0x0F, 0xAE, 0x0A, 0x30, 0x01, 0x01, 0x01, 0x00, 0x00]
CMD_GET_BATTERY = [0x55, 0x30, 0xA5, 0x0B, 0x2E, 0x01, 0x01, 0x01, 0x00, 0x00]

RATE_TABLE = [125, 500, 1000]


def decode_config(o):
    """把 64 字节响应解成 dict（前端 getMouseConfigInfo 的等价实现）。"""
    if o is None or len(o) < 56:
        return None
    dpi = [o[OFF["dpi1"] + 2 * i] | (o[OFF["dpi1"] + 2 * i + 1] << 8) for i in range(6)]
    wake = (o[0x36] >> 4) & 0x0F
    return {
        "report_rate_raw": o[OFF["report_rate"]],
        "report_rate": (o[OFF["report_rate"]] - 1) if o[OFF["report_rate"]] else 0,
        "dpi_count": o[OFF["dpi_flag"]],
        "dpi_index_raw": o[OFF["dpi_index"]],
        "dpi_index": max(o[OFF["dpi_index"]] - 1, 0),
        "dpi_values": dpi,
        "scroll_flag": o[OFF["scroll_flag"]],
        "lod_value": o[OFF["lod_value"]],
        "sensor_flag": o[OFF["sensor_flag"]],
        "key_respond": o[OFF["key_respond"]],
        "sleep_light": o[OFF["sleep_light"]],
        "highspeed_mode": o[OFF["highspeed_mode"]],
        "wakeup_flag": wake,
        "dpi_tip": o[OFF["dpi_tip"]],
    }


def build_set_payload(cfg):
    """按 setMouseConfigData 的规则拼写配置报文。"""
    p = bytearray(PAYLOAD)
    for i, b in enumerate(CMD_SET_CONFIG):
        p[i] = b
    p[OFF["report_rate"]] = (cfg["report_rate"] + 1) & 0xFF
    p[OFF["dpi_flag"]] = cfg["dpi_count"] & 0xFF
    p[OFF["dpi_index"]] = (cfg["dpi_index"] + 1) & 0xFF
    for i in range(6):
        v = cfg["dpi_values"][i] & 0xFFFF
        p[OFF["dpi1"] + 2 * i] = v & 0xFF
        p[OFF["dpi1"] + 2 * i + 1] = (v >> 8) & 0xFF
    p[OFF["scroll_flag"]] = cfg["scroll_flag"] & 0xFF
    p[OFF["lod_value"]] = cfg["lod_value"] & 0xFF
    p[OFF["sensor_flag"]] = cfg["sensor_flag"] & 0xFF
    p[OFF["key_respond"]] = cfg["key_respond"] & 0xFF
    p[OFF["sleep_light"]] = cfg["sleep_light"] & 0xFF
    p[OFF["highspeed_mode"]] = cfg["highspeed_mode"] & 0xFF
    p[OFF["wakeup_flag"]] = (cfg["wakeup_flag"] << 4) & 0xF0
    p[OFF["dpi_tip"]] = cfg["dpi_tip"] & 0xFF
    return bytes(p)


# ------------------------------------------------------------------ 设备选择

def pick_device(arg, require_output=True):
    devs = [d for d in list_devices() if "error" not in d]
    if not devs:
        print("没找到任何 /dev/hidraw* 设备。", file=sys.stderr)
        print("检查：ls -l /dev/hidraw*  —— 如果没有，说明 udev 没建节点。", file=sys.stderr)
        print("权限不足时：sudo usermod -aG plugdev $USER && 重新登录。", file=sys.stderr)
        sys.exit(2)
    if arg:
        for d in devs:
            if d["path"].endswith(str(arg)) or d["path"] == arg:
                return d
        print(f"找不到设备 {arg}", file=sys.stderr)
        sys.exit(2)
    if not require_output:
        if len(devs) == 1:
            return devs[0]
        print("有多个设备，请用 --dev 指定：", file=sys.stderr)
        for d in devs:
            print(f"  {d['path']}  {d['vid']:04x}:{d['pid']:04x}  {d['name']}", file=sys.stderr)
        sys.exit(2)
    cand = [d for d in devs if any(k == "Output" and s >= 64 for k, _i, s in d["reports"])]
    if len(cand) == 1:
        return cand[0]
    if not cand:
        print("没有任何 hidraw 设备带 >=64 字节的 OUTPUT report。", file=sys.stderr)
        print("→ 你的鼠标多半还挂在 2.4G 接收器上，请改用 Type-C 有线连接再跑一次。", file=sys.stderr)
        print("  用 `probe` 可以看到每个设备的详细 report 结构。", file=sys.stderr)
        sys.exit(3)
    print("有多个候选设备，请用 --dev 指定：", file=sys.stderr)
    for d in cand:
        print(f"  {d['path']}  {d['vid']:04x}:{d['pid']:04x}  {d['name']}", file=sys.stderr)
    sys.exit(2)


# ------------------------------------------------------------------ 子命令

def cmd_probe(args):
    devs = list_devices()
    if not devs:
        print("没找到 /dev/hidraw*。")
        return 1
    print(f"共 {len(devs)} 个 hidraw 设备\n")
    for d in devs:
        try:
            if "error" in d:
                print(f"{d['path']}  <打不开: {d['error']}>")
                continue
            print(f"{d['path']}  {d['vid']:04x}:{d['pid']:04x}  bus={BUS_NAMES.get(d['bus'], d['bus'])}  {d['name']!r}")
            if not d["reports"]:
                print("    (report descriptor 读不到，可能需要 root)")
            for kind, rid, size in d["reports"]:
                flag = ""
                if kind == "Output" and rid == REPORT_ID and size >= PAYLOAD:
                    flag = "   <== A5 网页驱动协议用的通道"
                print(f"    {kind:<7} report_id={rid}  {size:>4} B{flag}")
            print()
        except Exception as exc:                # noqa: BLE001
            print(f"    <解析该设备时出错，跳过: {type(exc).__name__}: {exc}>\n")
    print("提示：A5 协议需要 report_id=0 的 OUTPUT report（>=64B）。")
    return 0


def _open_and_get(dev, args):
    cmd = build_cmd(CMD_GET_CONFIG, args.report_id)
    print(f"发往 {dev['path']} (report_id={args.report_id}): {cmd.hex(' ')}")
    return write_read(dev["path"], cmd, timeout=args.timeout)


def cmd_get(args):
    dev = pick_device(args.dev)
    print(f"设备 {dev['path']}  {dev['vid']:04x}:{dev['pid']:04x}  {dev['name']!r}\n")
    resp = _open_and_get(dev, args)
    if resp is None:
        print("\n没收到任何回复。可能原因：")
        print("  1) 该接口不是配置通道（例如还在 2.4G 接收器上）")
        print("  2) report ID 不对，试试 --report-id 0/1/5/6/9")
        return 1
    print(f"\n原始响应: {resp.hex(' ')}")
    cfg = decode_config(resp)
    if cfg is None:
        print("响应太短，无法解析。")
        return 1
    print("\n解析结果:")
    rate = RATE_TABLE[cfg["report_rate"]] if 0 <= cfg["report_rate"] < len(RATE_TABLE) else "?"
    print(f"  回报率      : raw={cfg['report_rate_raw']} -> {rate} Hz")
    print(f"  当前 DPI 档 : {cfg['dpi_index'] + 1} / {cfg['dpi_count']}")
    print(f"  各档 DPI    : {cfg['dpi_values']}")
    print(f"  lod / 按键响应 / 休眠灯 / 高速模式 : "
          f"{cfg['lod_value']} / {cfg['key_respond']} / {cfg['sleep_light']} / {cfg['highspeed_mode']}")
    print(f"  sensor_flag={cfg['sensor_flag']} scroll_flag={cfg['scroll_flag']} "
          f"wakeup_flag={cfg['wakeup_flag']} dpi_tip={cfg['dpi_tip']}")
    return 0


def cmd_set(args):
    """
    用抓包得到的**真实写协议**修改配置。

    写协议：拿"读配置"的响应当模板 → byte1 改成 0xfa、byte2 改成 0x04
            → 改 DPI → 补零到 520 → SET(9, 520)
    """
    devs = [d for d in list_devices() if "error" not in d and d["vid"] == 0x258A]
    if args.dev:
        devs = [d for d in devs if d["path"].endswith(str(args.dev))]
    dev = None
    for d in devs:
        _probe, err = cfg_query(d["path"], CMD_DEVINFO)
        if err is None:
            dev = d
            break
    if dev is None:
        print("没有可用的配置通道。只在线模式的接口 1.1 上才有，请插 Type-C 线。", file=sys.stderr)
        return 1
    path = dev["path"]
    print(f"设备 {path}  {dev['vid']:04x}:{dev['pid']:04x}  {dev['name']!r}")

    cfg, err = cfg_query(path, CMD_READCFG)
    if err is not None:
        print(f"读配置失败: {_en(err)} —— 拒绝盲写", file=sys.stderr)
        return 1
    cur = [int.from_bytes(cfg[DPI_OFF_X + 2 * i:DPI_OFF_X + 2 * i + 2], "little")
           for i in range(DPI_SLOTS)]
    print(f"当前 DPI (X): raw={cur} -> {[dpi_raw_to_value(v) for v in cur]}")

    new = bytearray(cfg)
    edits = []
    if args.dpi:
        vals = [int(x) for x in args.dpi.replace(" ", "").split(",") if x]
        if not 1 <= len(vals) <= DPI_SLOTS:
            print(f"--dpi 需要 1~{DPI_SLOTS} 个值", file=sys.stderr)
            return 2
        vals += [vals[-1]] * (DPI_SLOTS - len(vals))
        raws = []
        for v in vals:
            r = dpi_value_to_raw(v)
            if not 0 <= r <= 0xFFFF or v % 100:
                print(f"DPI {v} 无法编码（必须是 100 的倍数，且 raw 在 0~65535）", file=sys.stderr)
                return 2
            raws.append(r)
        for i, r in enumerate(raws):
            for base in (DPI_OFF_X, DPI_OFF_Y):
                new[base + 2 * i] = r & 0xFF
                new[base + 2 * i + 1] = (r >> 8) & 0xFF
        edits.append(("--dpi", raws))
        print(f"目标 DPI      : raw={raws} -> {vals}")
    if args.slot:
        if args.value is None:
            print("--slot 需要配合 --value", file=sys.stderr)
            return 2
        if not 1 <= args.slot <= DPI_SLOTS:
            print(f"--slot 要在 1~{DPI_SLOTS}", file=sys.stderr)
            return 2
        r = dpi_value_to_raw(args.value)
        if not 0 <= r <= 0xFFFF or args.value % 100:
            print(f"DPI {args.value} 无法编码", file=sys.stderr)
            return 2
        for base in (DPI_OFF_X, DPI_OFF_Y):
            new[base + 2 * (args.slot - 1)] = r & 0xFF
            new[base + 2 * (args.slot - 1) + 1] = (r >> 8) & 0xFF
        edits.append((f"--slot {args.slot}", r))
        print(f"第 {args.slot} 档      : {args.value} -> raw {r}")

    if not edits:
        print("没有指定要改什么：用 --dpi 或 --slot/--value。", file=sys.stderr)
        return 2
    if args.dry_run:
        print("(--dry-run，未写入)")
        return 0

    err = cfg_write(path, bytes(new))
    print(f"写入 -> {'成功' if err is None else _en(err)}")
    if err is not None:
        return 1

    time.sleep(0.3)
    back, err = cfg_query(path, CMD_READCFG)
    if err is not None:
        print(f"回读失败: {_en(err)}", file=sys.stderr)
        return 1
    after = [int.from_bytes(back[DPI_OFF_X + 2 * i:DPI_OFF_X + 2 * i + 2], "little")
             for i in range(DPI_SLOTS)]
    want = [int.from_bytes(new[DPI_OFF_X + 2 * i:DPI_OFF_X + 2 * i + 2], "little")
            for i in range(DPI_SLOTS)]
    print(f"回读 DPI (X)  : raw={after} -> {[dpi_raw_to_value(v) for v in after]}")
    print("✅ 校验一致，已写入鼠标" if after == want else "⚠️ 回读与预期不一致！")
    return 0 if after == want else 1

def cmd_raw(args):
    dev = pick_device(args.dev)
    payload = bytes.fromhex(args.hex.replace(" ", "").replace(",", ""))
    cmd = build_cmd(payload, args.report_id)
    print(f"{dev['path']}  ->  {cmd.hex(' ')}")
    resp = write_read(dev["path"], cmd, timeout=args.timeout)
    if resp is None:
        print("无响应。")
        return 1
    print(f"响应: {resp.hex(' ')}")
    print(f"ASCII: {''.join(chr(c) if 32 <= c < 127 else '.' for c in resp)}")
    return 0


def cmd_feature(args):
    """按 feature report 读一把（给 258a:0138 那种只有 feature 通道的接收器用）。只读，安全。"""
    dev = pick_device(args.dev, require_output=False)
    print(f"{dev['path']}  {dev['vid']:04x}:{dev['pid']:04x}  {dev['name']!r}")
    if args.spec:
        spec = [(int(x.split(":")[0], 0), int(x.split(":")[1])) for x in args.spec.split(",")]
    else:
        spec = [(rid, size) for kind, rid, size in dev["reports"] if kind == "Feature"]
        if not spec:
            print("  这个设备的 report descriptor 里没有 Feature report。")
            return 1
    for rid, size in spec:
        # 大 feature report（比如 1031B）只读前 64 字节，避免无意义的大块读取
        ln = min(size, args.max_len) if not args.spec else size
        try:
            data = feature_roundtrip(dev["path"], rid, ln)
        except OSError as exc:
            print(f"  feature id={rid} len={ln}: 失败 {exc}")
            continue
        body = data[1:1 + ln]
        nonzero = sum(1 for b in body if b)
        print(f"  feature id={rid:>3} (声明 {size}B, 读 {ln}B, 非零 {nonzero}B): {body[:48].hex(' ')}")
    return 0


def cmd_sinowealth(args):
    """
    精确复刻 libratbag sinowealth 驱动的交互，逐步定位卡在哪一步。

    libratbag 的做法（driver-sinowealth.c 的 sinowealth_query_read）：
        set_feature_report(5, [0x05, cmd, 0, 0, 0, 0], 6)
        get_feature_report(5, 6)
    它报 "Couldn't read firmware version: Broken pipe (-32)"，本命令就是把这
    两步拆开分别报告结果，并做一次只读的 GET 长度扫描。
    """
    devs = [d for d in list_devices()
            if "error" not in d and d["vid"] == 0x258A
            and any(k == "Feature" for k, _r, _s in d["reports"])]
    if args.dev:
        devs = [d for d in devs if d["path"].endswith(str(args.dev))]
    if not devs:
        print("没找到 258a 且带 Feature report 的设备，先跑 `probe` 确认连接。", file=sys.stderr)
        return 1

    for dev in devs:
        path = dev["path"]
        print("=" * 72)
        print(f"{path}  {dev['vid']:04x}:{dev['pid']:04x}  {dev['name']!r}")
        print("=" * 72)
        feats = [(rid, size) for k, rid, size in dev["reports"] if k == "Feature"]

        print("\n[1] GET_FEATURE 长度扫描（只读，不会改任何设置）")
        print(f"      {'rid':>4} {'声明':>6}   " + "".join(f"len={n:<9}" for n in (6, 7, 8, 520)))
        for rid, size in feats:
            cells = []
            for total in (6, 7, 8, 520):
                data, err = feature_get(path, rid, total)
                if err is None:
                    cells.append(f"OK({sum(1 for b in data[1:] if b)}非零)".ljust(10))
                else:
                    cells.append(f"E{err.errno}".ljust(10))
            print(f"      {rid:>4} {size:>6}   " + "".join(cells))

        print("\n[2] 复刻 libratbag 的固件版本查询（唯一一次写操作，内容只有 05 01 00 00 00 00）")
        err = feature_set(path, 5, bytes([0x01, 0, 0, 0, 0]), total_len=6)
        print(f"      SET_FEATURE id=5 len=6  [05 01 00 00 00 00] -> "
              f"{'OK' if err is None else type(err).__name__ + ': ' + str(err)}")
        data, err = feature_get(path, 5, 6)
        if err is None:
            print(f"      GET_FEATURE id=5 len=6                     -> OK  {data.hex(' ')}")
        else:
            print(f"      GET_FEATURE id=5 len=6                     -> {type(err).__name__}: {err}")
            print("      ^ libratbag 报的就是这一步（Broken pipe = -32）")

        print("\n[3] 只对「小」feature report（<=64B，命令型通道）再试几组长度组合")
        for rid, size in [f for f in feats if f[1] <= 64]:
            for total in sorted({6, size + 1}):
                err = feature_set(path, rid, bytes([0x01] + [0] * (total - 2)), total_len=total)
                s = "OK" if err is None else f"E{err.errno}"
                data, err2 = feature_get(path, rid, total)
                g = f"OK {data.hex(' ')}" if err2 is None else f"E{err2.errno}"
                print(f"      id={rid:<3} len={total:<4} SET={s:<6} GET={g}")
        if not [f for f in feats if f[1] <= 64]:
            print("      （这个设备没有小 feature report）")

        print("\n[4] 监听 500ms，看设备有没有主动上报 input report")
        try:
            fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            print(f"      打不开设备：{exc}")
            print()
            continue
        try:
            got = []
            end = time.time() + 0.5
            while time.time() < end:
                if select.select([fd], [], [], 0.1)[0]:
                    try:
                        got.append(os.read(fd, 64))
                    except OSError:
                        break
            print(f"      收到 {len(got)} 条" + (f"：{got[0].hex(' ')}" if got else ""))
        finally:
            os.close(fd)
        print()
    return 0


def _en(err):
    """把 OSError 格式化成 E32:EPIPE 这种，便于阅读。"""
    if err is None:
        return "OK"
    code = err.errno
    return f"E{code}:{errno.errorcode.get(code, '?')}"


def cmd_linktest(args):
    """
    同时监听所有 258a hidraw 节点若干秒，看接收器到底有没有在和鼠标通信。

    用法：跑这条命令的同时**晃动鼠标**。鼠标节点（接口 1.0）应该开始刷报文。
    一条都没有 = 接收器和鼠标没连上（模式开关不在 2.4G / 鼠标睡着了 / 没电）。
    """
    devs = [d for d in list_devices() if "error" not in d and d["vid"] == 0x258A]
    if not devs:
        print("没找到 258a 设备。", file=sys.stderr)
        return 1

    fds, meta = [], {}
    for d in devs:
        try:
            fd = os.open(d["path"], os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            print(f"  {d['path']} 打不开: {exc}")
            continue
        fds.append(fd)
        meta[fd] = d
        print(f"  监听 {d['path']}  {d['vid']:04x}:{d['pid']:04x}  {d['name']!r}")

    counts = {fd: 0 for fd in fds}
    first = {}
    print(f"\n现在开始监听 {args.secs} 秒 —— 请在这段时间里**持续晃动鼠标**...\n")
    end = time.time() + args.secs
    try:
        while time.time() < end and fds:
            ready, _w, _x = select.select(fds, [], [], 0.2)
            for fd in ready:
                try:
                    data = os.read(fd, 64)
                except OSError:
                    fds.remove(fd)
                    continue
                counts[fd] += 1
                first.setdefault(fd, data)
    finally:
        for fd in fds:
            os.close(fd)

    print("结果：")
    total_mouse = 0
    for fd, d in meta.items():
        n = counts.get(fd, 0)
        line = f"  {d['path']}  {n:>4} 条"
        if fd in first:
            line += f"   第一条: {first[fd].hex(' ')}"
        print(line)
        if d["reports"] and d["reports"][0][0] == "Input" and not any(
                k == "Feature" for k, _r, _s in d["reports"]):
            total_mouse += n
    print()
    if total_mouse == 0:
        print("  ❌ 鼠标接口一条报文都没收到 —— 接收器和鼠标没有在通信。")
        print("     先确认：鼠标底部的模式开关切到 2.4G、鼠标开着、鼠标能动。")
    else:
        print(f"  ✅ 鼠标接口收到 {total_mouse} 条报文 —— 接收器和鼠标是连着的。")
    return 0


def _feature_set_fd(fd, rid, payload, total):
    buf = bytearray(total)
    buf[0] = rid & 0xFF
    body = payload[:max(0, total - 1)]
    buf[1:1 + len(body)] = body
    try:
        _ioctl(fd, HIDIOCSFEATURE(total), buf)
        return None
    except OSError as exc:
        return exc


def _feature_get_fd(fd, rid, total):
    buf = bytearray(total)
    buf[0] = rid & 0xFF
    try:
        return _ioctl(fd, HIDIOCGFEATURE(total), buf), None
    except OSError as exc:
        return None, exc


def cmd_cmdsweep(args):
    """
    在命令通道上扫一遍**只读**命令：每条命令都做
        SET feature -> 立刻监听 input report（这是关键，之前漏了）-> GET feature
    这样能区分「响应走 feature 回读」和「响应走 input 上报」两种协议形态。

    白名单里的每一条都对着 libratbag driver-sinowealth.c 核对过，确认是
    `sinowealth_query_read`（读）才会发；下面这些**明确排除**：

      0x02  set active profile        （libratbag sinowealth_set_active_profile）
      0x09  setMouseKeys              （A5 网页驱动 p[1]=0x09）
      0x0F  setMouseConfigData        （A5 网页驱动 p[1]=0x0F）
      0x1A  set debounce time         （libratbag sinowealth_set_debounce_time，实测是写！）
      0x30  macro write               （libratbag sinowealth_write_macros 的 command_id）
      0x75  DFU 模式                  （libratbag 注释：要靠重新插拔才能退出）
    """
    READ_ONLY = [0x00, 0x01, 0x03, 0x08, 0x0E, 0x11, 0x12, 0x13,
                 0x1B, 0x21, 0x22, 0x31, 0x32]
    FORBIDDEN = {0x02, 0x09, 0x0F, 0x1A, 0x30, 0x75}

    devs = [d for d in list_devices()
            if "error" not in d and d["vid"] == 0x258A
            and any(k == "Feature" and s <= 64 for k, _r, s in d["reports"])]
    if args.dev:
        devs = [d for d in devs if d["path"].endswith(str(args.dev))]
    if not devs:
        print("没找到 258a 且带小 Feature report 的设备。", file=sys.stderr)
        return 1

    rid, total = args.rid, args.len
    for dev in devs:
        path = dev["path"]
        print("=" * 86)
        print(f"{path}  {dev['vid']:04x}:{dev['pid']:04x}  {dev['name']!r}")
        print("=" * 86)
        # rid=0/len=0 表示自动：把所有小的 Feature report（<=64B）都用「声明长度+1」各测一遍
        if rid == 0:
            combos = [(r, s + 1) for k, r, s in dev["reports"] if k == "Feature" and s <= 64]
        else:
            combos = [(rid, total)]
        if not combos:
            print("  这个设备没有 <=64B 的 Feature report，跳过。\n")
            continue
        try:
            fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            print(f"  打不开: {exc}\n")
            continue
        try:
            for r, tot in combos:
                print(f"  --- feature report id={r}, 缓冲 {tot} 字节 ---")
                print(f"  {'cmd':>4}  {'SET':<13} {'发命令后 input':<26} {'GET':<26}")
                for cmd in READ_ONLY:
                    assert cmd not in FORBIDDEN
                    while select.select([fd], [], [], 0)[0]:      # 清空旧数据
                        try:
                            os.read(fd, 64)
                        except OSError:
                            break
                    err = _feature_set_fd(fd, r, bytes([cmd] + [0] * (tot - 2)), tot)
                    ins = []
                    end = time.time() + args.listen
                    while time.time() < end:
                        if select.select([fd], [], [], 0.05)[0]:
                            try:
                                ins.append(os.read(fd, 64))
                            except OSError:
                                break
                    data, err2 = _feature_get_fd(fd, r, tot)
                    g = data.hex(' ') if err2 is None else _en(err2)
                    i = "; ".join(x.hex(' ') for x in ins) if ins else "-"
                    print(f"  0x{cmd:02x}  {_en(err):<13} {i:<26} {g:<26}")
                print()
        finally:
            os.close(fd)
    return 0


# ============================================================ 实测协议（已破解）
# 2026-10-15 用 LD_PRELOAD shim 拦截官方驱动（Wine 里跑）对 /dev/hidraw 的 ioctl
# 抓到真实报文，再用本工具复现成功。三条关键点：
#   1) report ID = 0x09（不是 5）
#   2) 缓冲固定 520 字节（1 字节 report ID + 519 数据，正好是描述符声明的 519）
#   3) 必须**先 SET 命令、再 GET 响应** —— 设备只在有待处理命令时才应答 GET，
#      单独 GET 会被 stall（EPIPE）。这是之前所有测试失败的原因。
CFG_REPORT_ID = 0x09
CFG_BUF = 520
CMD_DEVINFO = bytes([0x0D, 0x05, 0x01, 0x01, 0x00, 0x06, 0x00])
CMD_READCFG = bytes([0xD6, 0x44, 0x00, 0x01, 0x00, 0x90, 0x01])


def cfg_query(path, cmd, report_id=CFG_REPORT_ID, total=CFG_BUF):
    """
    发一条命令并取回响应。返回 (有效数据 bytes, None) 或 (None, OSError)。
    "有效数据" 指去掉尾部连续 0 之后的部分（设备的响应长度是可变的）。
    """
    payload = bytearray(total - 1)
    payload[0:len(cmd)] = cmd
    err = feature_set(path, report_id, bytes(payload), total_len=total)
    if err is not None:
        return None, err
    data, err = feature_get(path, report_id, total)
    if err is not None:
        return None, err
    n = max((i for i, b in enumerate(data) if b), default=-1) + 1
    return data[:n], None


# 写协议：拿"读配置"的响应当模板，把 byte1 改成 0xfa、byte2 改成 0x04，
# 改完 DPI 补零到 520 再 SET 回去。全部来自抓包 diff。
WRITE_B1 = 0xFA
WRITE_B2 = 0x04

# 配置体里 DPI 档位的位置（u16LE，各 6 档；X 与 Y 独立）
DPI_OFF_X = 0x0F
DPI_OFF_Y = 0x23
DPI_SLOTS = 6
# 编码：raw = DPI/100 - 1（实测：把某档设成 1000，raw 从 16 变成 9）
def dpi_raw_to_value(raw):
    return (raw + 1) * 100


def dpi_value_to_raw(value):
    return value // 100 - 1


def cfg_write(path, cfg, report_id=CFG_REPORT_ID, total=CFG_BUF):
    """
    把配置体写回设备。

    注意：cfg 是 cfg_query 读回来的**完整响应**（首字节就是 report ID 0x09）。
    写入报文 = 读响应**就地**改 3 个字节（byte1→0xfa、byte2→0x04、DPI 已改），
    再补零到 total。
    **不能把响应挪到 buf[1:] —— 那会整体错位一字节，设备会 NAK（ETIMEDOUT）。**
    实测：原样重放官方抓到的字节三次全 OK；错位版本立刻 ETIMEDOUT。
    """
    buf = bytearray(total)
    body = bytes(cfg[:total])
    buf[0:len(body)] = body
    buf[0] = report_id
    buf[1] = WRITE_B1
    buf[2] = WRITE_B2
    return feature_set(path, report_id, bytes(buf[1:]), total_len=total)


def cmd_read(args):
    """用抓包得到的真实协议读取设备信息和完整配置（只读）。"""
    devs = [d for d in list_devices() if "error" not in d and d["vid"] == 0x258A]
    if args.dev:
        devs = [d for d in devs if d["path"].endswith(str(args.dev))]
    if not devs:
        print("没找到 258a 设备。", file=sys.stderr)
        return 1

    for dev in devs:
        path = dev["path"]
        print("=" * 78)
        print(f"{path}  {dev['vid']:04x}:{dev['pid']:04x}  {dev['name']!r}")
        print("=" * 78)

        info, err = cfg_query(path, CMD_DEVINFO)
        if err is not None:
            print(f"  [设备信息] 失败: {_en(err)}")
            print("             （接收器身份可能不响应这条命令，试试插 Type-C 线走有线模式）")
            print()
            continue
        print(f"  [设备信息] {len(info)} 字节: {info.hex(' ')}")
        if len(info) >= 14:
            print(f"             回显命令 : {info[1:8].hex(' ')}")
            print(f"             型号标识 : {info[8:14].hex(' ')}"
                  f"  (Cfg.ini 里 A5 的 Psd=4,0,0,0,0,1a)")

        cfg, err = cfg_query(path, CMD_READCFG)
        if err is not None:
            print(f"  [读配置] 失败: {_en(err)}")
            print()
            continue
        print(f"\n  [读配置] 有效 {len(cfg)} 字节")
        for off in range(0, len(cfg), 16):
            chunk = cfg[off:off + 16]
            asc = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
            print(f"    {off:04x}  {chunk.hex(' '):<47} {asc}")

        if args.parse and len(cfg) > 50:
            print("\n  [解析] 按 u16LE 读 DPI 档位组（两处各 6 个，XY 独立）")
            for base, tag in ((0x0F, "X"), (0x23, "Y")):
                vals = [int.from_bytes(cfg[base + 2 * i:base + 2 * i + 2], "little")
                        for i in range(6)]
                print(f"    偏移 0x{base:02x} ({tag}): raw={vals}"
                      f"  ->  (raw+1)*100 = {[(v + 1) * 100 for v in vals]}")
        print()
    return 0


def cmd_report(args):
    """一把梭：把所有能读的信息都读出来，方便直接粘贴回帖。只读，不改任何设置。"""
    print("=" * 72)
    print("前行者/EWEADN 鼠标诊断报告")
    print("=" * 72)
    devs = list_devices()
    print(f"\n[1] /dev/hidraw* 共 {len(devs)} 个\n")
    for d in devs:
        # 单个设备解析/打印出问题不能拖垮整份报告（前面崩过三次，都是死在这里）
        try:
            if "error" in d:
                print(f"  {d['path']}  <打不开: {d['error']}>")
                continue
            print(f"  {d['path']}  {d['vid']:04x}:{d['pid']:04x}  bus={BUS_NAMES.get(d['bus'], d['bus'])}  {d['name']!r}")
            for kind, rid, size in d["reports"]:
                mark = "  <== 可写 64B 配置通道" if (kind == "Output" and rid == args.report_id and size >= 64) else ""
                print(f"      {kind:<7} id={rid:<3} {size:>5} B{mark}")
        except Exception as exc:                # noqa: BLE001
            print(f"      <解析该设备时出错，跳过: {type(exc).__name__}: {exc}>")

    print(f"\n[2] 读各设备的 Feature report（只读）\n")
    for d in devs:
        if "error" in d:
            continue
        try:
            feats = [(rid, size) for k, rid, size in d["reports"] if k == "Feature"]
            if not feats:
                continue
            print(f"  {d['path']}  {d['vid']:04x}:{d['pid']:04x}")
            for rid, size in feats:
                ln = min(size, 64)
                try:
                    data = feature_roundtrip(d["path"], rid, ln)
                    body = data[1:1 + ln]
                    nz = sum(1 for b in body if b)
                    print(f"      id={rid:<3} 声明{size:>5}B 读{ln:>3}B 非零{nz:>3}B : {body[:32].hex(' ')}")
                except OSError as exc:
                    print(f"      id={rid:<3} 读取失败: {exc}")
        except Exception as exc:                # noqa: BLE001
            print(f"      <该设备 feature 读取出错，跳过: {type(exc).__name__}: {exc}>")

    print(f"\n[3] 尝试发 A5 读配置命令（report_id={args.report_id}）\n")
    cands = [d for d in devs if "error" not in d and any(k == "Output" for k, _i, _s in d["reports"])]
    if not cands:
        print("  跳过：没有任何设备带 OUTPUT report。")
        print("  → 说明当前连接方式不支持 A5 网页驱动协议，请插 Type-C 线切到有线模式后再跑一次。")
    for d in cands:
        print(f"  {d['path']}  {d['vid']:04x}:{d['pid']:04x}  {d['name']!r}")
        try:
            resp = write_read(d["path"], build_cmd(CMD_GET_CONFIG, args.report_id), timeout=args.timeout)
        except OSError as exc:
            print(f"      写入失败: {exc}")
            continue
        if resp is None:
            print("      无响应（超时）")
            continue
        print(f"      响应: {resp.hex(' ')}")
        cfg = decode_config(resp)
        if cfg:
            print(f"      解析: rate_raw={cfg['report_rate_raw']} dpi_index={cfg['dpi_index']} "
                  f"dpi_count={cfg['dpi_count']} dpi={cfg['dpi_values']}")
    print("\n" + "=" * 72)
    print("以上内容整段贴回去即可。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="前行者/EWEADN 鼠标 Linux hidraw 调参工具")
    ap.add_argument("--dev", help="指定 hidraw 编号，例如 4 （对应 /dev/hidraw4）")
    ap.add_argument("--report-id", type=lambda s: int(s, 0), default=REPORT_ID)
    ap.add_argument("--timeout", type=float, default=1.0)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe", help="枚举设备与 report 结构（只读）").set_defaults(func=cmd_probe)

    p = sub.add_parser("report", help="生成一份完整诊断报告，方便粘贴（只读）")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("read", help="用破解的真实协议读设备信息+完整配置（只读）")
    p.add_argument("--parse", action="store_true", help="顺带解析 DPI 档位组")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("sinowealth", help="复刻 libratbag 的 sinowealth 交互，定位卡在哪一步")
    p.set_defaults(func=cmd_sinowealth)

    p = sub.add_parser("cmdsweep", help="扫一遍只读命令，探测命令集（只发白名单读命令）")
    p.add_argument("--rid", type=lambda s: int(s, 0), default=0,
                   help="命令通道的 feature report id；0=自动（所有 <=64B 的 Feature 各测一遍）")
    p.add_argument("--len", type=int, default=0,
                   help="缓冲总长度（含 report ID 字节）；0=自动（声明长度+1）")
    p.add_argument("--listen", type=float, default=0.25, help="每条命令后监听 input 的秒数，默认 0.25")
    p.set_defaults(func=cmd_cmdsweep)

    p = sub.add_parser("linktest", help="晃动鼠标，确认接收器和鼠标是否真的在通信")
    p.add_argument("--secs", type=float, default=8.0, help="监听秒数，默认 8")
    p.set_defaults(func=cmd_linktest)

    p = sub.add_parser("get", help="读回当前配置（只读）")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("set", help="用真实协议写配置（DPI 档位，可逆）")
    p.add_argument("--dpi", help='6 档 DPI，逗号分隔，100 的倍数，如 "800,1600,2400,3200,6400,12000"')
    p.add_argument("--slot", type=int, help="只改第 N 档（1~6），配合 --value")
    p.add_argument("--value", type=int, help="配合 --slot 的 DPI 值")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("raw", help="发任意负载")
    p.add_argument("--hex", required=True)
    p.set_defaults(func=cmd_raw)

    p = sub.add_parser("feature", help="读 feature report（探测用，只读）")
    p.add_argument("--spec", help="覆盖自动探测，格式 id:len,id:len")
    p.add_argument("--max-len", type=int, default=64, help="自动探测时单个 feature report 最多读多少字节（默认 64）")
    p.set_defaults(func=cmd_feature)

    args = ap.parse_args()
    try:
        sys.exit(args.func(args))
    except PermissionError as exc:
        print(f"权限不足: {exc}\n试试: sudo usermod -aG plugdev $USER 然后重新登录，"
              f"或临时用 sudo 跑一次。", file=sys.stderr)
        sys.exit(4)
    except OSError as exc:
        print(f"IO 错误: {exc}", file=sys.stderr)
        sys.exit(4)


if __name__ == "__main__":
    main()
