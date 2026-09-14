# Reproducing the uaudio/DRC test with the OKTO DAC

This procedure repeats the 44.1 kHz/24-bit high-frequency test through the
`120.green` `v1-fdw6` 192 kHz DRC chain while recording USB control,
submission, completion and feedback events. Turn the amplifier down first:
the fixture is audible.

## 1. Identify and select the actual OKTO

Connect the OKTO, then run:

```sh
sudo usbconfig list
cat /dev/sndstat
ls -l /dev/dsp.dac
/usr/local/bin/omdrc status
```

Map the OKTO's `ugenB.A` identity to its `pcmN` unit and confirm that
`/dev/dsp.dac` targets that PCM device. Do not proceed while it still targets
another DAC. USB and PCM numbers can change after reconnecting hardware.

The examples below assume `ugen0.4` and `pcm2`; replace both values with the
observed ones. Save the descriptors and initial diagnostics:

```sh
sudo usbconfig -d ugen0.4 dump_all_desc > /tmp/okto-descriptors.txt
sysctl dev.pcm.2.uaudio_diagnostics
```

The descriptor dump is important because OKTO can advertise different clock
entities, formats and endpoint addresses than the DacMagic used in the first
live audit.

## 2. Start the full USB trace

In a second terminal, use the bus and address from `ugenB.A`. For `ugen0.4`:

```sh
sudo usbdump -i usbus0 -f 4 -s 65536 \
  -w /tmp/okto-uaudio-session.pcap
```

Leave it running through all following steps. Stop it with Ctrl-C only after
the final diagnostics have been collected.

## 3. Exercise clock transitions and verify the correction

```sh
/usr/local/bin/omdrc geometry 120.green
/usr/local/bin/omdrc 192000 @v1-fdw6
/usr/local/bin/omdrc status
sysctl dev.pcm.2.uaudio_diagnostics
sleep 3
/usr/local/bin/omdrc 44100 @v1-fdw6
sleep 3
/usr/local/bin/omdrc 192000 @v1-fdw6
sleep 3
/usr/local/bin/omdrc status
```

The last status must show geometry `120.green`, active configuration
`v1-fdw6 192k`, and both virtual_oss and BruteFIR at 192000 Hz. Use the
installed `/usr/local/bin/omdrc`; checkout-local status can refer to different
configuration and state.

## 4. Play and capture the probe through BruteFIR

```sh
python3 scripts/bitperfect_runner.py \
  --source mpd --route drc --drc-output DRC-native \
  --reference capture --allow-resample \
  --input tests/high-frequency-test-44100-s24-stereo.wav \
  --out bp-results/high-frequency-120.green-v1-fdw6-192k-okto-freebsd

python3 scripts/high-frequency-response.py \
  bp-results/high-frequency-120.green-v1-fdw6-192k-okto-freebsd \
  --json bp-results/high-frequency-120.green-v1-fdw6-192k-okto-freebsd-response.json
```

The source fixture is signed 24-bit stereo at 44100 Hz. Resampling to the live
192 kHz chain is intentional. The capture measures host-side submitted PCM; it
is not proof of physical USB delivery or the OKTO's analogue output.

## 5. Collect final diagnostics

Use the OKTO's actual PCM unit:

```sh
sysctl dev.pcm.2.uaudio_diagnostics
sudo sysctl hw.snd.verbose=2
cat /dev/sndstat
sudo sysctl hw.snd.verbose=0
```

Check for zero feedback bad/error/stale counters, zero playback short/error
counters and zero PCM underruns. Confirm that BruteFIR owns the OKTO playback
channel and that its feeder path contains no unexpected format or rate stage.
Restore `hw.snd.verbose` to its original value if it was not initially zero.

Now stop `usbdump` in the second terminal with Ctrl-C.

## 6. Decode the transport trace

```sh
usbdump -r /tmp/okto-uaudio-session.pcap -v | \
  python3 scripts/audit-usbdump-transport.py \
  > bp-results/okto-usb-transport-summary.json
```

The analyzer currently recognizes playback endpoint `0x01` and feedback
endpoint `0x81`, matching the DacMagic capture. Inspect
`/tmp/okto-descriptors.txt`. If OKTO uses other endpoints, update those
assumptions before trusting the packet-length and feedback sections. The raw
pcap and control/error counts remain valuable evidence.

Run the regression tests:

```sh
python3 -m unittest discover -s tests -p 'test_usbdump_transport.py'
python3 -m unittest discover -s tests -p 'test_high_frequency_response.py'
```

## 7. Compare with Linux

Repeat on Linux with the same physical OKTO, connection, channel mapping,
`120.green` correction, `v1-fdw6` design, rate and attenuation. Copy the Linux
capture prefix beside the FreeBSD one, then compare responses:

```sh
python3 scripts/high-frequency-response.py \
  bp-results/high-frequency-120.green-v1-fdw6-192k-okto-linux \
  bp-results/high-frequency-120.green-v1-fdw6-192k-okto-freebsd \
  --json bp-results/high-frequency-120.green-v1-fdw6-192k-okto-xos.json
```

Do not require generated RAW filter hashes to match when host SoX versions
differ. Record the canonical filter WAV hashes and both generated RAW hashes,
then quantify the measured response difference.

An OKTO run matters because the DAC supplies the descriptors and feedback and
implements clock controls, buffering and firmware behaviour. A clean DacMagic
trace tests the same driver source but cannot prove that OKTO exercises the same
paths. If submitted PCM and transport are also clean with OKTO, the decisive
next experiment is a level-matched analogue capture of that same OKTO under
both operating systems using an independent ADC.
