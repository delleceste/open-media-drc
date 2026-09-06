#!/usr/bin/env python3
"""Open /dev/dsp0, set an exact format/rate, write digital silence, close.

Digital silence: every sample is 0, so nothing audible reaches the speakers.
The point is purely to trigger a PCM channel start/stop so the uaudio(4)
clock-programming path runs and can be traced.
"""
import sys

sys.path.insert(0, "/home/giacomo/open-media-drc/freebsd-uaudio-patch/bench")
import ossio

rate = int(sys.argv[1])
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0

d = ossio.Dsp("/dev/dsp0", "w", rate, channels=2, formats=(ossio.AFMT_S32_LE,))
print("granted: fmt=%s ch=%d rate=%d" % (d.fmt_name, d.channels, d.rate))
chunk = b"\x00" * (d.frame_bytes * 1024)
want = int(d.rate * secs) * d.frame_bytes
written = 0
while written < want:
    d.write(chunk)
    written += len(chunk)
d.drain()
d.close()
print("wrote %d bytes of digital silence at %d Hz" % (written, d.rate))
