# OKTO cross-OS analogue comparison with REW

This is the procedure for tonight’s comparison of the same computer running
FreeBSD and Linux, with the OKTO DAC8 Stereo at home.

The question is narrow: does the OKTO’s analogue output differ when the same
24-bit/44.1 kHz sweep is played through the normal MPD/Qobuz DRC path under the
two operating systems?

If left and right results match between Linux and FreeBSD within repeatability,
there is no reason to continue looking for an OS-dependent analogue sound
difference in this test.

## Prepare the sweep files

The repository already contains the two generated measurement-sweep WAV files;
use these exact files:

- `tests/512kMeasSweep_10_to_22050_-12_dBFS_44k_PCM24_L_refL.wav`: sweep on
  **Left**, timing reference on **Left**.
- `tests/512kMeasSweep_10_to_22050_-12_dBFS_44k_PCM24_R_refR.wav`: sweep on
  **Right**, timing reference on **Right**.

For both files use:

- 44,100 Hz;
- 24-bit PCM;
- identical sweep start/end frequencies, length and level;
- timing reference enabled;
- no added harmonic distortion; the timing-reference content remains part of
  the generated file.
- REW dither enabled in the Sweeps/measurements tab.

The files have been checked: both are stereo Microsoft PCM, 24-bit, 44,100 Hz,
851,968 frames and 19.319 seconds long. Their names and metadata show the same
10–22,050 Hz sweep, −12 dBFS level, format, length and rate. The inactive
program channel contains only the low-level timing-reference material, as
expected. Keep these original WAVs unchanged. REW recommends including the
timing reference in saved measurement sweeps. [REW generator documentation](https://www.roomeqwizard.com/help/help_en-GB/html/siggen.html)

The dither setting is intentional and must be identical for both files and for
any regenerated copies. The WAV header does not record every REW generator
checkbox, so retain the REW project or a screenshot of the Sweeps/measurements
tab with the files. REW may also include a short dithered pre-roll to let
playback devices lock.

The two sweeps are intentionally separate. REW’s ordinary measurement mode
uses one selected input channel at a time; simultaneous multi-input capture is
a Pro feature. [REW multi-input documentation](https://www.roomeqwizard.com/upgrades.html)

## Connect the equipment

Connect the OKTO to the playback computer using the same USB port and cable for
both OS runs. Connect the OKTO analogue output to the Creative SB1240 line
input using a safe, non-shorting connection. Do not use an ordinary
balanced-to-unbalanced cable that shorts an OKTO balanced output leg to ground.

The Creative is the capture interface, not the playback interface. Run REW on
the capture computer and play the sweep files through MPD on the playback
computer.

Before measuring, record the physical setup: OKTO volume, mute, balance, input
selection, PCM reconstruction filter, output routing and any other front-panel
settings. Keep them unchanged for both OS runs.

## Configure REW

On the capture computer:

1. Select the Creative SB1240 line input.
2. Set its recording rate to 48 kHz and the highest available input precision.
3. Select **Left** as REW’s input channel for the left measurement.
4. Select **Right** as REW’s input channel for the right measurement.
5. Disable input monitoring, software effects, automatic gain and any DSP.
6. Set the input level so the sweep has safe headroom and never clips.
7. Keep the Creative level, cables and REW settings unchanged after the first
   repeatability check.

The Creative is limited to 96 kHz/24-bit analogue recording, so 48 kHz capture
is suitable for the audible-band comparison. The OKTO must remain in the actual
playback configuration being investigated; do not change it to 48 kHz merely
because the Creative records at 48 kHz.

## Verify one channel before the full run

Play a short test signal through the OKTO and confirm that the selected Creative
input meter responds to the intended channel. Confirm that the unused channel is
not being mixed into the selected input. Check that the input has comfortable
headroom and no clipping.

Do this once for Left and once for Right before starting the OS comparison.

## Run FreeBSD

1. Boot FreeBSD and wait until the normal audio services are settled.
2. Confirm that `/dev/dsp.dac` points to the OKTO, not another DAC:

   ```sh
   usbconfig list
   cat /dev/sndstat
   ls -l /dev/dsp.dac
   /usr/local/bin/omdrc status
   ```

3. Select the normal 192 kHz `120.green` DRC configuration used for listening.
   Confirm that MPD, virtual_oss and BruteFIR are running at 192 kHz.
4. Save the active configuration, filter names/hashes, OKTO settings and the
   starting diagnostic state.
5. In REW, select the **Left** input and load
   `tests/512kMeasSweep_10_to_22050_-12_dBFS_44k_PCM24_L_refL.wav` using file
   playback. Start the REW measurement, then play the file through MPD’s normal
   DRC output when REW waits for the timing reference.
6. Save the resulting REW measurement as `freebsd-left-1`.
7. Repeat the identical left measurement as `freebsd-left-2`.
8. Select the **Right** input and repeat with
   `tests/512kMeasSweep_10_to_22050_-12_dBFS_44k_PCM24_R_refR.wav`, saving
   `freebsd-right-1` and `freebsd-right-2`.
9. Preserve the original captured files and REW project. Do not normalize,
   trim or level-match the raw files before saving them.
10. Record the final FreeBSD diagnostics and the active MPD/BruteFIR state.

If REW’s file-playback mode refuses a 44.1 kHz sweep while the capture device
is set to 48 kHz, stop and record that exact limitation. Do not silently replace
the 44.1 kHz source with a differently generated test. Preserve the original
44.1 kHz sweep and use the same documented conversion/analysis treatment for
both operating systems.

## Run Linux

1. Reboot the same computer into Linux, keeping the OKTO, cables, Creative
   capture computer and front-panel settings unchanged.
2. Confirm the same `120.green` DRC variant, 192 kHz output, MPD route and
   filter files. Record the active configuration and output device.
3. Repeat the left measurements with the same REW input and sweep file, saving
   `linux-left-1` and `linux-left-2`.
4. Repeat the right measurements, saving `linux-right-1` and `linux-right-2`.
5. Preserve all raw captures and record the Linux playback state.

Do not compare a FreeBSD left sweep to a Linux right sweep. The primary pairs
are:

| Channel | Repeatability | Cross-OS comparison |
|---|---|---|
| Left | `freebsd-left-1` vs `freebsd-left-2`; `linux-left-1` vs `linux-left-2` | FreeBSD left vs Linux left |
| Right | `freebsd-right-1` vs `freebsd-right-2`; `linux-right-1` vs `linux-right-2` | FreeBSD right vs Linux right |

## Interpret the result

First compare each OS with itself. The Linux repeat difference and FreeBSD
repeat difference establish the measurement floor.

Then compare FreeBSD with Linux, separately for Left and Right. Check:

- frequency-response magnitude across the audible band;
- overall level and level versus frequency;
- channel polarity and phase where the measurement supports it;
- impulse/step response and timing;
- distortion and noise results if REW’s input level permits them;
- any repeatable changes in peaks, roll-off or resonant behaviour.

Do not time-align, normalize or EQ away a difference before recording the raw
result. If a level adjustment is later used for a secondary plot, preserve the
unaltered comparison too.

If both channels agree across OSes within their own repeatability, the test has
found no measurable analogue-output difference at the Creative’s resolution.
That is the stopping condition for this investigation. If a difference exceeds
repeatability, repeat that channel before attributing it to FreeBSD’s USB audio
driver.

## Files to retain

Keep the REW project, both original 44.1 kHz sweep WAVs, every raw Creative
capture, screenshots or exports of each measurement, the exact REW version,
playback OS/kernel versions, OKTO settings, MPD/BruteFIR configuration and
filter hashes. Also note which OS was run first and whether the OKTO was power
cycled between runs.
