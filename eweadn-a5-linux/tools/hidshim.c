/*
 * hidshim.c —— LD_PRELOAD 拦截 ioctl()，抓 HIDIOCSFEATURE / HIDIOCGFEATURE 的完整缓冲。
 *
 * 用途：Wine 的 winebus 后端通过 /dev/hidraw 的 feature ioctl 跟设备通信，
 * 但 Wine 自己的 trace 只打缓冲地址不打内容、strace 也不 dump 这些 ioctl 的数据。
 * 这个 shim 把每一次 feature 读写的设备节点、长度、字节全部记到日志里。
 *
 * 编译：gcc -shared -fPIC -O2 -o hidshim.so hidshim.c -ldl
 * 使用：LD_PRELOAD=./hidshim.so HIDLOG=/path/hid.log wine xxx.exe
 *
 * HIDIOCSFEATURE(len) = _IOWR('H', 0x06, len)
 * HIDIOCGFEATURE(len) = _IOWR('H', 0x07, len)
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <dlfcn.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <string.h>

static int (*real_ioctl)(int, unsigned long, ...) = 0;

static void fd_path(int fd, char *out, size_t n)
{
    char link[64], buf[512];
    ssize_t r;
    snprintf(link, sizeof(link), "/proc/self/fd/%d", fd);
    r = readlink(link, buf, sizeof(buf) - 1);
    if (r < 0) { snprintf(out, n, "fd%d", fd); return; }
    buf[r] = 0;
    snprintf(out, n, "%s", buf);
}

static void hexdump(FILE *f, const unsigned char *b, int n)
{
    int i;
    for (i = 0; i < n; i++) fprintf(f, "%02x", b[i]);
}

int ioctl(int fd, unsigned long req, ...)
{
    va_list ap;
    void *arg = 0;
    int nr, type, len, is_set, is_get, r;
    unsigned char *buf;
    const char *logp;
    FILE *f;
    char dev[512];

    va_start(ap, req);
    arg = va_arg(ap, void *);
    va_end(ap);

    if (!real_ioctl) real_ioctl = dlsym(RTLD_NEXT, "ioctl");

    nr   = req & 0xff;
    type = (req >> 8) & 0xff;
    len  = (req >> 16) & 0x3fff;
    is_set = (type == 'H' && nr == 0x06);
    is_get = (type == 'H' && nr == 0x07);
    if (!is_set && !is_get)
        return real_ioctl(fd, req, arg);

    buf = (unsigned char *)arg;
    logp = getenv("HIDLOG");
    if (!logp) logp = "/tmp/hidlog.txt";

    if (is_set) {
        fd_path(fd, dev, sizeof(dev));
        f = fopen(logp, "a");
        if (f) {
            fprintf(f, "SET dev=%s len=%d data=", dev, len);
            hexdump(f, buf, len);
            fprintf(f, "\n");
            fclose(f);
        }
    }

    r = real_ioctl(fd, req, arg);

    if (is_get) {
        fd_path(fd, dev, sizeof(dev));
        f = fopen(logp, "a");
        if (f) {
            fprintf(f, "GET dev=%s len=%d ret=%d", dev, len, r);
            if (r > 0) { fprintf(f, " data="); hexdump(f, buf, r); }
            fprintf(f, "\n");
            fclose(f);
        }
    }
    return r;
}
