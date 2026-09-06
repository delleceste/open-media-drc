# Wire traces — what the driver actually does

DTrace records of the **USB control transfers** `uaudio(4)` issues around a
playback start, on the OKTO RESEARCH DAC8 STEREO (`0x152a:0x88c5`).

These trace `usbd_do_request_flags()` rather than `DPRINTF`, so what is recorded
is the request that really went on the wire — `bmRequestType`/`bRequest`/`wValue`
decoded, not a driver-side log line. `uaudio20_set_speed()` is inlined in the
module build and has no `fbt` probe of its own, which is the other reason to
trace at the USB layer.

| file | tree |
|---|---|
| [`uaudio-clock-trace.d`](uaudio-clock-trace.d) | the script |
| [`play-silence.py`](play-silence.py) | open `/dev/dsp0`, write digital silence, close |
| [`2026-09-06-before.log`](2026-09-06-before.log) | releng/15.1 + clock-before-alt + shared-clock-fix |
| [`2026-09-06-after.log`](2026-09-06-after.log) | the same, plus `uaudio-clock-transaction.c.patch` |

## Method

Three open/play/close cycles of **digital silence** — 3 s each, `S32_LE`, 2 ch,
direct to `/dev/dsp0` with nothing else holding the device:

1. 44.1 kHz, entered from a 48 kHz clock — a real rate change
2. 44.1 kHz again — the same rate as the previous stream
3. 48 kHz — a crystal-family change

Silence matters: the result is a statement about control-plane traffic and does
not depend on anything being audible, so it holds whether or not the
intermittent silent-open fault is present.

```sh
sudo dtrace -s uaudio-clock-trace.d -o trace.log &
python3 play-silence.py 44100 3
python3 play-silence.py 44100 3
python3 play-silence.py 48000 3
sudo pkill -INT dtrace
```

## Result

| | before | after |
|---|---|---|
| `SET_CUR` per start, same rate | 2 | **0** |
| `SET_CUR` per start, rate change | 2 | **1** |
| `SET_CUR` issued after playback armed | 1 | **0** |
| `SET_CUR` during steady-state playback | 0 | 0 |
| capture stream armed (`SET_INTERFACE iface=2`) | every playback | **never** |

`dev.pcm.0.feedback_rate` reads 44101 / 48001 in both legs, so the DAC's
explicit feedback endpoint is live and tracking — which is what makes
`hw.usb.uaudio.prefer_feedback=1` safe on this device.
