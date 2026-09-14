# FreeBSD uaudio and USB transport audit — 2026-09-14

## Conclusion and scope correction

No steady-playback transport failure or meaningful attenuation at the tested
high frequencies was observed in this run. This is not proof that the DAC's
analogue output is identical under FreeBSD and Linux, nor a measurement of
every frequency, distortion, or dynamic behaviour.

**The attached playback DAC was Cambridge Audio Azur DacMagic 100, not OKTO.**
USB identity: bus 0/address 2, VID 22e8/PID dac4, bcdDevice 0326. The other
attached audio device was ESI U24XL. The tested playback device was pcm0/uaudio0.
Earlier attribution of these live results to OKTO was incorrect.

This report supersedes the device-specific and "USB wire" claims in
HIGH-FREQUENCY-RESPONSE.md: the runner's `.wire.raw` contains host-side USB
**submitted** PCM, not a physical bus capture or proof of DAC receipt. No
OKTO capture-interface capability was established by these measurements.

## Why connecting the OKTO matters

Changing DAC does not invalidate arithmetic performed on already captured PCM.
The source audit also remains useful: the same driver implementation is being
reviewed. However, the live USB experiment is a driver–device interaction, not
a driver-only unit test. The connected device supplies descriptors and feedback
and implements the control requests. A different device can therefore exercise
different paths in the same code:

| Property to check on the actual OKTO | Why it matters |
| --- | --- |
| Clock entities, clock sharing and advertised controls | Determines which clock/control paths execute and whether shared-clock guards apply. |
| Alternate settings, channel count and sample container | Determines the selected format, payload layout and packet sizes. |
| Feedback encoding, interval and values | Drives packet sizing; exercises decoder, bounds and stale-feedback handling. |
| Firmware response to stop/start and rate changes | Determines whether successful requests really leave the device ready and in the intended state. |
| Internal clocking, buffering, processing and analogue output | These are downstream of our submitted-PCM measurement. |

These are variables to verify, not claims that the OKTO has a defect or that
its firmware differs in any particular way. Firmware need not explicitly
recognize the OS: different hosts can send different sequences or timings of
otherwise valid USB requests. Conversely, differing host callback timing alone
does not demonstrate changed DAC sample-clock jitter or audible degradation.

Thus the DacMagic trace establishes that the installed stack behaves correctly
in the observed conditions **with the DacMagic**. It cannot establish that the
same conditions hold with the OKTO. If the listening symptom occurs on OKTO,
repeat transport tracing with that device and compare the same physical OKTO,
settings, connection and correction under both OSes. If it occurs on DacMagic,
the present device is already the relevant target; attaching OKTO is not a
prerequisite for investigating that symptom.

## Chain and experiment

Installed `/usr/local/bin/omdrc status`, active process configuration and filter
manifest were checked: geometry `120.green`, design `v1-fdw6`, 192000 Hz,
BruteFIR included. Both channels use 1.1 dB attenuation; convolution uses
8192 × 64 taps, float64 internally and S32 I/O without output dithering.
Do not use checkout-local `./drc.sh status` as evidence of the installed service
configuration: its configuration/state can differ.

The source fixture is 44100 Hz, signed 24-bit, stereo, containing simultaneous
1, 4, 8, 12, 16 and 19 kHz tones. Source-to-chain resampling to 192 kHz is
intentional. A control sequence reopened at 192 kHz, switched to 44.1 kHz, and
restored 192 kHz before playing the probe through MPD, virtual_oss and BruteFIR.
The final recorded state is `120.green / v1-fdw6 / 192k`, MPD stopped.

Local evidence directories (large captures are ignored by Git):

- `bp-results/transport-audit-20260914-123745/`: descriptors, initial/final
  status, diagnostics, full capture and summary. Its first orchestration attempt
  timed out while waiting for inherited stdout after a successful same-rate
  reopen; capture was stopped and chain status checked. Do not treat this as a
  completed rate-transition test.
- `bp-results/transport-transitions-20260914-125413/`: completed experiment;
  `events.json` records commands, timestamps and successful exits;
  `transport.pcap`, `capture.log`, `summary.json`, before/after diagnostics,
  probe capture, `response.json`, and final status retain the evidence.

## Observations

All 13 captured control requests completed successfully. The same-rate reopen
used interface 1 alt 0, read back 192000 from clock entity 0x29, then selected
alt 1; **no redundant clock SET** occurred. Each rate change parked alt 0,
read the old clock, wrote the new rate, waited approximately 100 ms after the
write completed, read back the intended rate, then selected alt 1. No mute,
volume, vendor writes or audio-capture interface activation were observed in
this window. Startup before the capture is not covered.

The complete transition trace contains 168121 captured events, zero reported
kernel capture drops, 9338 successful playback completions and 74702 successful
feedback completions. Three deliberate stops each produced a feedback
CANCELLED, playback CANCELLED and playback TIMEOUT, immediately before alt 0.
These are retained in the summary, not discarded as if every completion passed.
The xHCI cleanup path deliberately completes remaining buffered transfers with
USB_ERR_TIMEOUT (`xhci_configure_msg`, local xhci.c around lines 3996–4030).
That provides a source-supported explanation for the stop-associated events;
the capture alone does not prove the exact call stack. No other error
completions were observed.

Submission packet sizes were 40/48 bytes at 44.1 kHz and 184/192 bytes at
192 kHz, consistent with stereo 4-byte containers and feedback-driven 5/6 or
23/24 sample-frame packets. Four-byte feedback raw values were 361256/361264
and 1572816/1572824. Playback host events were typically 8 ms apart and feedback
events 1 ms apart. These are grouped host events, not individual microframe
measurements. Maximum gaps include deliberate stops and must not be presented
as steady-state jitter.

The before/after uaudio snapshots retained zero bad/error/stale feedback and
zero short playback transfers/bytes or playback errors. A later verbose
`/dev/sndstat` snapshot showed pcm0 at 192000 Hz, BruteFIR as owner, BITPERFECT,
`userland -> feeder_root -> hardware`, and zero underruns. This later snapshot
is not a synchronized before/after underrun history of all prior stream
generations. Diagnostic verbosity was restored from 2 to its original 0.

The settled submitted-PCM probe response, relative to 1 kHz, was approximately
0 dB at 4/8/12 kHz, −0.0002 dB at 16 kHz, and −0.003 dB at 19 kHz on both
channels. These six measurements do not establish "absolutely no loss"
everywhere or analogue equality. They provide no support for a meaningful
broad treble roll-off in the submitted PCM under this tested condition.

## Source audit findings

Reviewed the applied local uaudio implementation, PCM feeding, USB trace
placement and xHCI completion handling alongside the repository patch material.
Local uaudio source SHA256:
`efb03e61db945e50b9197f21d24e2d3d09538e303477c73298a7f1e8c5233520`.
On-disk snd_uaudio.ko SHA256:
`186c9f1f621ccc18e2a2c1b4285b4370b9d1b1c4b388b47d7bbb60a83fd8865b`.
These match the earlier audit fingerprints; on-disk hashes alone are not a
readback of loaded module memory.

- Clock configuration (uaudio.c around 1597–1770) parks the stream and avoids
  redundant rate writes. Hardening opportunities remain: park/write failures
  and mismatching readback do not reliably prevent proceeding, and clock
  validity is not queried. None of those failures was observed here.
- The shared-clock guard skips writes when the other direction is active;
  conflicting requested rates deserve a targeted duplex test. The attached
  DacMagic playback-only configuration does not exercise that case.
- Feedback decoding and playback packetization (around 2623–2785 and 2840
  onward) select packet lengths and copy consecutive samples. No treble filter
  was found in those paths. Invalid/stale feedback can change scheduling, but
  those diagnostics remained zero in this experiment.
- Error counters depend on what reaches the audio callback. USB completion
  records retain shutdown errors that a zero driver counter does not expose.
  PCM underrun silence is another reason to inspect PCM counters separately.
- USBPF SUBM records occur before controller submission; DONE records are
  host-stack completion observations before the client callback. USBPF exposes
  neither a unique transfer ID nor individual isochronous-frame error codes.
  Do not pair exact transfers by index, infer loss from unequal window totals,
  or equate a successful OUT completion with acknowledged DAC receipt.

No production kernel or virtual_oss changes were made. The evidence supports
targeted clock-error/shared-clock hardening tests, not a speculative audio
quality patch or an immediate broad virtual_oss rewrite.

## Reproduction commands

Check identity first; bus addresses can change when devices are reconnected:

```sh
sudo usbconfig list
/usr/local/bin/omdrc status
sysctl dev.pcm.0.uaudio_diagnostics
```

For the recorded DacMagic address only, start a full capture in another terminal
and stop it with Ctrl-C after the experiment. Use a new filename each time:

```sh
sudo usbdump -i usbus0 -f 2 -s 65536 -w /tmp/uaudio-session.pcap
```

This sequence changes/restarts the audio chain and plays audible tones; lower
amplifier gain first. Confirm geometry is 120.green before proceeding:

```sh
/usr/local/bin/omdrc 192000 @v1-fdw6
/usr/local/bin/omdrc 44100 @v1-fdw6
/usr/local/bin/omdrc 192000 @v1-fdw6
python3 scripts/bitperfect_runner.py \
  --source mpd --route drc --drc-output DRC-native \
  --reference capture --allow-resample \
  --input tests/high-frequency-test-44100-s24-stereo.wav \
  --out bp-results/hf-transport-repeat
python3 scripts/high-frequency-response.py bp-results/hf-transport-repeat
```

After stopping the full capture, retain both submission and completion records:

```sh
usbdump -r /tmp/uaudio-session.pcap -v | \
  python3 scripts/audit-usbdump-transport.py
python3 -m unittest discover -s tests -p 'test_usbdump_transport.py'
python3 -m unittest discover -s tests -p 'test_high_frequency_response.py'
```

The summary helper currently interprets playback endpoint 0x01 and feedback
0x81, as observed here. For OKTO, inspect actual descriptors first and adapt
those endpoint assumptions if needed. It is an evidence summarizer, not an
automated compliance verdict or physical USB analyzer.

## Next decision

For the affected DAC, compare FreeBSD and Linux control sequences, negotiated
format, feedback and error completions under the same correction. Record
canonical filter WAV and generated RAW hashes on both hosts: differing SoX
versions can produce different RAW bytes; quantify the response difference
rather than assuming either audibility or exact equivalence.

If the affected DAC also has clean submitted PCM and transport traces, the
decisive next test is level-matched analogue capture of that same DAC under
both OSes using an independent ADC. That can test the downstream difference
the present host-side measurements cannot resolve. More driver auditing alone
cannot establish an audible analogue difference.
