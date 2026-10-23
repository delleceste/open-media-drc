# virtual_oss bridge audit — 2026-10-23

This follows [the September source audit](FREEBSD-AUDIO-SOURCE-AUDIT-2026-09-11.md) on the running FreeBSD 15.1 host. The present DAC is a Cambridge Audio DacMagic 100 (`/dev/dsp.dac -> dsp0`); earlier cross-OS FIR nulls used an OKTO DAC. The DRC chain was off during this probe. No music service, BruteFIR configuration, DAC setting, or production audio route was changed.

## Findings

The deployed path is MPD `/dev/dsp.play` → `virtual_oss` `/dev/dsp.loop` → BruteFIR → `/dev/dsp.dac`. `drc.sh` gives `virtual_oss` `-r <selected rate> -C 2 -c 2 -b 32 -s 200ms -f /dev/null -a 0 -d dsp.play -L dsp.loop`. There is no `-S` resampling, compressor, EQ, or non-unity gain option. The installed MPD `DRC-native` output uses `format "*:*:*"` and `mixer_type "none"`; `DRC-resamp` deliberately requests `192000:24:2`. BruteFIR uses S32_LE input and output, 64-bit internal arithmetic, and no output dither by default. The installed user defaults name `/dev/dsp0` directly rather than the repository template's `/dev/dsp.dac`; on this host the role symlink currently resolves to `dsp0`. FreeBSD has `dev.pcm.0.bitperfect=1` and `dev.pcm.0.play.vchans=0` today.

The current `/usr/src/usr.sbin/virtual_oss/virtual_oss/{main,virtual_oss}.c` hashes match the September audit. The installed executable is `/usr/sbin/virtual_oss` (SHA256 `a48a8fb211888927ee40508f04087e6a2ea92c64b006cf9188cf5e37be257dbe`), different from the binary fingerprint in that audit. The live probe below verifies the relevant behavior of this installed executable directly.

**S32 at matched rate is value-transparent through this bridge.** The source imports S32 into `int64_t`, multiplies by the unity volume `128`, then shifts right by 7; with `shift_orig=0`, the noise branch is disabled. The same operation happens when the loopback exports S32. Earlier matched-rate MPD loopback capture found 175,376 value-matched frames with zero slips or corruption, and cross-OS submitted USB payloads through a real FIR nulled exactly for the tested S32 input and coefficients. This statement applies to those formats, rates, intervals, and successful delivery; it is not a blanket guarantee for every client or sustained playback.

**S16 is changed before BruteFIR.** When a client negotiates S16 with the 32-bit mixer, `shift_orig=16` selects `vclient_noise()` in `virtual_oss.c`; `-a 0` does not disable it. The standalone arithmetic test gives −95.08026 dBFS RMS added noise for zero-valued S16 samples, with residual range −65536…+65534 S32 units. S24 expansion gives −143.24503 dBFS, while S32 gives zero. The installed MPD native output is capable of negotiating S16 for 16-bit source material, but this probe did not trace MPD's ioctl for a listening session. Nor does this noise level establish that it explains the reported tonal difference.

**Clocking and delivery remain separate questions.** `-L` creates a synchronized loopback. With a reader, the null backend stops its nominal software wait and the ring can be paced by BruteFIR's DAC output. The older claim that two free-running clocks *must* cause periodic sample slips is unsupported. Underruns, client starvation, and DAC delivery faults still need event-time counters or captures; a byte-identical submitted USB payload does not prove that the DAC received and converted every packet.

## Live isolated probe

A second `virtual_oss` instance used unique CUSE nodes, `-f /dev/null`, and the production 32-bit, stereo, unity-gain mapping. It opened no DAC. The committed [probe](../freebsd-virtual-oss-patch/audit/virtual-oss-live-probe.c) negotiates the requested playback format with OSS ioctls, writes a constant tone, captures S32 from the synchronized loopback, and checks its residual. The service was stopped after each run; `/dev/dsp.omdrcaudit*` no longer exists and `drc.sh status` remains off.

| Client format | Matched loopback samples | Changed samples | Residual in S32 units |
|---|---:|---:|---:|
| S16_LE | 21,900 | 21,900 | −65536…+65519 |
| S32_LE | 20,480 | 0 | 0 |

To repeat while the production chain is idle, build the probe with `cc -O2 -Wall -Wextra -Werror freebsd-virtual-oss-patch/audit/virtual-oss-live-probe.c -o /tmp/omdrc-voss-live-probe`. Start an isolated instance with `sudo /usr/sbin/virtual_oss -B -D /tmp/omdrc-voss-audit.pid -r 44100 -C 2 -c 2 -b 32 -s 50ms -f /dev/null -a 0 -d dsp.omdrcauditplay -L dsp.omdrcauditloop`, run `/tmp/omdrc-voss-live-probe 16`, then stop only that instance (`sudo pkill -f dsp.omdrcauditplay`). Repeat for `32`. Check that the audit nodes disappear. The production `-s 200ms` differs only in buffering; this probe tests sample arithmetic rather than the production timing budget.

## Should we write a custom byte-copy bridge?

A bridge *after* BruteFIR would receive already processed samples. It would not remove the S16 expansion change, which happens *before* BruteFIR inside `virtual_oss`. BruteFIR currently writes directly to the DAC; inserting a post-FIR process adds an OSS-compatible output endpoint or a supported pipe backend, buffering, clock pacing, format negotiation, recovery from partial writes, and another underrun point. It cannot recover samples changed upstream.

If the S16 path matters, first test actual MPD negotiation and a matched-rate S16-versus-promoted-S32 loopback capture. The narrowest potential remedy is to make MPD send S32 to `dsp.play` and verify that its S16→S32 promotion zero-fills the low bits with no resampling. A custom bridge replacing `virtual_oss` itself would need a CUSE OSS playback endpoint for MPD and a compatible capture endpoint for BruteFIR, with blocking semantics and DAC-paced backpressure. That is a substantial replacement for a path already proven exact for S32; the present evidence does not justify building it as a sound-quality fix.

The next decisive measurements are an MPD S16 ioctl/loopback capture, long-run underrun and USB completion evidence during the audible complaint, and level-matched analogue output measurements if the submitted PCM remains identical. See the September audit for the existing limitations and procedures.
