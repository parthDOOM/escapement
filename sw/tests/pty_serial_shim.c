/* LD_PRELOAD shim that lets libserialport (and so libsigrok, sigrok-cli, PulseView) open a
 * pseudo-terminal, for test_sump.py's sigrok-cli test only. A real board is a USB CDC port
 * (/dev/ttyACM0) and needs none of this.
 *
 * libserialport refuses a pty twice: it looks the port up in /sys/class/tty/<name>, which
 * has no pts/N entries, and it reads and sets the modem lines (TIOCMGET, TIOCMBIS, ...),
 * which the pty driver does not implement (ENOTTY). The shim points the lookup at ptmx
 * and answers the modem-line ioctls on a pty with "DSR and CTS up".
 *
 *     cc -shared -fPIC -o pty_serial_shim.so pty_serial_shim.c -ldl
 *     LD_PRELOAD=./pty_serial_shim.so sigrok-cli -d ols:conn=/dev/pts/5 ...
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdarg.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

#define PTS_SYS "/sys/class/tty/pts/"

static const char *redirect(const char *path)
{
    return strncmp(path, PTS_SYS, sizeof PTS_SYS - 1) ? path : "/sys/class/tty/ptmx";
}

int lstat(const char *path, struct stat *st)
{
    static int (*real)(const char *, struct stat *);
    if (!real)
        real = dlsym(RTLD_NEXT, "lstat");
    return real(redirect(path), st);
}

ssize_t readlink(const char *path, char *buf, size_t n)
{
    static ssize_t (*real)(const char *, char *, size_t);
    if (!real)
        real = dlsym(RTLD_NEXT, "readlink");
    return real(redirect(path), buf, n);
}

static int is_pty(int fd)
{
    struct stat st;
    return fstat(fd, &st) == 0 && S_ISCHR(st.st_mode) && major(st.st_rdev) >= 136
           && major(st.st_rdev) <= 143;                 /* Unix98 pty slaves */
}

int ioctl(int fd, unsigned long req, ...)
{
    static int (*real)(int, unsigned long, void *);
    va_list ap;
    void *arg;
    va_start(ap, req);
    arg = va_arg(ap, void *);
    va_end(ap);
    if (!real)
        real = dlsym(RTLD_NEXT, "ioctl");
    if (is_pty(fd)) {
        switch (req) {
        case TIOCMGET:
            *(int *)arg = TIOCM_DSR | TIOCM_CTS | TIOCM_CAR;
            return 0;
        case TIOCMSET: case TIOCMBIS: case TIOCMBIC:
            return 0;
        }
    }
    return real(fd, req, arg);
}
