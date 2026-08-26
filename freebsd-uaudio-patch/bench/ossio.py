"""
Minimal FreeBSD OSS (/dev/dspN) helper.

Deliberately does not use ossaudiodev: that module is deprecated and slated
for removal, and it cannot express AFMT_S24_LE, which is the ESI U24XL's
native capture format on this host.

OSS requires the parameters to be set in the order format -> channels ->
speed, and each ioctl reports back what the driver actually granted.  We keep
the granted values: on a bitperfect device they are the only truth about what
is on the wire.
"""

import array
import errno
import fcntl
import os
import subprocess

IOCPARM_MASK = 0x1FFF
IOC_VOID, IOC_OUT, IOC_IN = 0x20000000, 0x40000000, 0x80000000


def _IOWR(g, n, sz):
    return (IOC_IN | IOC_OUT) | ((sz & IOCPARM_MASK) << 16) | (ord(g) << 8) | n


def _IO(g, n):
    return IOC_VOID | (ord(g) << 8) | n


SNDCTL_DSP_SYNC = _IO("P", 1)
SNDCTL_DSP_SPEED = _IOWR("P", 2, 4)
SNDCTL_DSP_SETFMT = _IOWR("P", 5, 4)
SNDCTL_DSP_CHANNELS = _IOWR("P", 6, 4)

AFMT_S16_LE = 0x00000010
AFMT_S32_LE = 0x00001000
AFMT_S24_LE = 0x00010000

FMT_NAME = {AFMT_S16_LE: "S16_LE", AFMT_S24_LE: "S24_LE", AFMT_S32_LE: "S32_LE"}
FMT_BYTES = {AFMT_S16_LE: 2, AFMT_S24_LE: 3, AFMT_S32_LE: 4}


def holders(path):
    """Who has this device open?  fstat(1) is the only way to ask on FreeBSD."""
    try:
        out = subprocess.run(["fstat", path], capture_output=True, text=True, timeout=10)
        rows = []
        for line in out.stdout.splitlines()[1:]:
            f = line.split()
            if len(f) >= 3:
                rows.append("%s (pid %s)" % (f[1], f[2]))
        return sorted(set(rows))
    except Exception:
        return []


class DeviceBusy(OSError):
    def __init__(self, path, who):
        self.path, self.who = path, who
        msg = "%s is busy" % path
        if who:
            msg += " -- held by " + ", ".join(who)
        msg += ("\n  The bench needs exclusive access to the hardware device.\n"
                "  Stop the DRC chain first (e.g. `drc.sh off`, or stop musicpd /\n"
                "  virtual_oss / brutefir), then re-run.  Playing through\n"
                "  virtual_oss would not test the driver path anyway: the rate\n"
                "  change would happen inside virtual_oss, not at the DAC.")
        super().__init__(msg)


def _ioctl_int(fd, req, value):
    buf = array.array("i", [value])
    fcntl.ioctl(fd, req, buf, True)
    return buf[0]


class Dsp:
    """An open OSS device with negotiated parameters."""

    def __init__(self, path, mode, rate, channels=2, formats=None):
        if formats is None:
            formats = (AFMT_S32_LE, AFMT_S24_LE, AFMT_S16_LE)
        flags = os.O_WRONLY if mode == "w" else os.O_RDONLY
        self.path = path
        self.mode = mode
        try:
            self.fd = os.open(path, flags)
        except OSError as e:
            if e.errno == errno.EBUSY:
                raise DeviceBusy(path, holders(path)) from None
            raise
        try:
            self.fmt = None
            for want in formats:
                got = _ioctl_int(self.fd, SNDCTL_DSP_SETFMT, want)
                if got == want:
                    self.fmt = got
                    break
            if self.fmt is None:
                # Take whatever the last attempt left us with rather than
                # writing samples in a format the device did not agree to.
                raise OSError(
                    "%s: none of %s accepted (device offered 0x%x)"
                    % (path, [FMT_NAME.get(f, hex(f)) for f in formats], got)
                )
            self.channels = _ioctl_int(self.fd, SNDCTL_DSP_CHANNELS, channels)
            self.rate = _ioctl_int(self.fd, SNDCTL_DSP_SPEED, rate)
        except Exception:
            os.close(self.fd)
            raise
        self.frame_bytes = FMT_BYTES[self.fmt] * self.channels

    @property
    def fmt_name(self):
        return FMT_NAME.get(self.fmt, hex(self.fmt))

    def write(self, data):
        mv = memoryview(data)
        while mv:
            mv = mv[os.write(self.fd, mv):]

    def read_exact(self, nbytes):
        chunks, got = [], 0
        while got < nbytes:
            b = os.read(self.fd, min(65536, nbytes - got))
            if not b:
                break
            chunks.append(b)
            got += len(b)
        return b"".join(chunks)

    def drain(self):
        if self.mode == "w":
            fcntl.ioctl(self.fd, SNDCTL_DSP_SYNC)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def to_float(raw, fmt, channels):
    """Decode interleaved OSS bytes to a float32 array of shape (frames, ch)."""
    import numpy as np

    if fmt == AFMT_S16_LE:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif fmt == AFMT_S32_LE:
        a = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif fmt == AFMT_S24_LE:
        b = np.frombuffer(raw, dtype=np.uint8)
        b = b[: (len(b) // 3) * 3].reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)  # sign extend
        a = v.astype(np.float32) / 8388608.0
    else:
        raise ValueError("unsupported format 0x%x" % fmt)
    n = (len(a) // channels) * channels
    return a[:n].reshape(-1, channels)
