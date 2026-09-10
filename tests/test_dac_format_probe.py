#!/usr/bin/env python3
"""The FreeBSD side of the cross-OS format probe must not fail quietly.

Every bug this covers produced a probe that still exited 0 and still printed a
verdict — it just had nothing in it. `alt_settings: []` reads as "FreeBSD
cannot show descriptors", an idle second sound card reads as "the chain
disagrees on rate", and a stereo stream decoded as zero channels reads as
nothing at all. A comparison that silently has no evidence in it is worse than
one that fails, because it gets reported as a MATCH.

The fixtures are verbatim output from 15.1-RELEASE-p2 with the DRC chain
running at 192 kHz.
"""
import builtins
import importlib.util
import io
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dacprobe",
                                              ROOT / "scripts/dac-format-probe.py")
PROBE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(PROBE)


# `cat /dev/sndstat` with hw.snd.verbose=2, trimmed to two devices: the DAC
# with brutefir on it, and an idle capture card sitting at its default.
SNDSTAT = """\
FreeBSD Audio Driver
Installed devices:
pcm0: <ESI U24XL> on uaudio0 (1p:0v/1r:0v)
\tsnddev flags=0xfc<SOFTPCMVOL,BUSY,MPSAFE,REGISTERED,BITPERFECT,VPC>
\t[dsp0.play.0]: spd 48000, fmt 0x00200010/0x00210000, flags 0x00000000, 0x00000023
\t\tinterrupts 0, underruns 0, feed 0, ready 0
\t\t[b:2304/1152/2|bs:65536/8192/8]
\t\tchannel flags=0x0
\t\t{userland} -> feeder_root(0x00200010) -> feeder_format(0x00200010 -> 0x00210000) -> feeder_volume(0x00210000) -> {hardware}
pcm1: <OKTO RESEARCH DAC8STEREO> on uaudio1 (1p:0v/1r:0v) default
\tsnddev flags=0xf8<BUSY,MPSAFE,REGISTERED,BITPERFECT,VPC>
\t[dsp1.play.0]: spd 192000, fmt 0x00201000, flags 0x2000110c, 0x00000001, pid 2067 (brutefir)
\t\tinterrupts 840030, underruns 0, feed 840029, ready 82920
\t\t[b:12288/6144/2|bs:131072/65536/2]
\t\tchannel flags=0x2000110c<RUNNING,TRIGGERED,BUSY,HAS_SIZE,BITPERFECT>
\t\t{userland} -> feeder_root(0x00201000) -> {hardware}
\t[dsp1.record.0]: spd 48000, fmt 0x00200010/0x00201000, flags 0x00000000, 0x00000023
\t\tinterrupts 0, overruns 0, feed 0, hfree 3072, sfree 65536
\t\t[b:3072/1536/2|bs:65536/8192/8]
\t\tchannel flags=0x0
\t\t{hardware} -> feeder_root(0x00201000) -> feeder_format(0x00201000 -> 0x00200010) -> feeder_volume(0x00200010) -> {userland}
Installed devices from userspace:
dsp.play: <virtual_oss device> (play/rec)
"""

# `usbconfig -d ugen0.3 dump_all_desc`, one AudioStreaming alt-setting.  Note
# what carries a RAW dump and what does not: that asymmetry is the bug.
USBCONFIG_ALT = """\
    Interface 1 Alt 1
      bLength = 0x0009
      bDescriptorType = 0x0004
      bInterfaceNumber = 0x0001
      bAlternateSetting = 0x0001
      bNumEndpoints = 0x0002
      bInterfaceClass = 0x0001  <Audio device>
      bInterfaceSubClass = 0x0002
      bInterfaceProtocol = 0x0020
      iInterface = 0x0004  <DAC8STEREO>

      Additional Descriptor

      bLength = 0x10
      bDescriptorType = 0x24
      bDescriptorSubType = 0x01
       RAW dump:
       0x00 | 0x10, 0x24, 0x01, 0x02, 0x00, 0x01, 0x01, 0x00,
       0x08 | 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00, 0x0b

      Additional Descriptor

      bLength = 0x06
      bDescriptorType = 0x24
      bDescriptorSubType = 0x02
       RAW dump:
       0x00 | 0x06, 0x24, 0x02, 0x01, 0x04, 0x18

     Endpoint 0
        bLength = 0x0007
        bDescriptorType = 0x0005
        bEndpointAddress = 0x0001  <OUT>
        bmAttributes = 0x0005  <ASYNC-ISOCHRONOUS>
        wMaxPacketSize = 0x0188
        bInterval = 0x0001
        bRefresh = 0x0000
        bSynchAddress = 0x0000
"""


class AfmtDecode(unittest.TestCase):
    """sound.h puts the channel count at bits 20-26, not 24-28."""

    def test_stereo_s32_is_not_zero_channels(self):
        # The format the DAC actually runs at.  Decoded with a shift of 24
        # this reported `channels: None`, i.e. the comparison had no channel
        # count to check at all.
        got = PROBE.afmt_decode(0x00201000)
        self.assertEqual(got["encoding"], "S32_LE")
        self.assertEqual(got["channels"], 2)
        self.assertEqual(got["wire_bytes"], 4)

    def test_three_byte_encoding_is_still_distinguished(self):
        # The whole point of the probe: a 3-byte stride must not decode as 4.
        self.assertEqual(PROBE.afmt_decode(0x00210000)["wire_bytes"], 3)
        self.assertEqual(PROBE.afmt_decode(0x00200010)["wire_bytes"], 2)


class SndstatParsing(unittest.TestCase):

    def setUp(self):
        self.state = self._parse()

    def _parse(self):
        real_open = builtins.open

        def fake_open(path, *a, **kw):
            if str(path) == "/dev/sndstat":
                return io.StringIO(SNDSTAT)
            return real_open(path, *a, **kw)

        # sysctl lookups would hit the real machine; they only add knobs.
        real_run = PROBE.run
        PROBE.run = lambda cmd, timeout=15: ""
        with mock.patch("builtins.open", fake_open):
            try:
                return PROBE.freebsd_pcm_state()
            finally:
                PROBE.run = real_run

    def test_15x_channel_lines_are_found(self):
        # 15.1 spells these [dsp1.play.0]; the 13.x-era regex wanted
        # [pcm1:play:dsp1.p0] and matched nothing, so `streams` was empty
        # even with hw.snd.verbose=2 set.
        nodes = {s["node"] for s in self.state["streams"]}
        self.assertEqual(nodes, {"dsp0.play.0", "dsp1.play.0", "dsp1.record.0"})

    def test_record_channel_does_not_steal_the_playback_feeder_chain(self):
        # "record" is not "rec": the record line failed to match, and its
        # converting feeder chain was then attributed to the playback channel
        # above it — reporting the bit-perfect path as non-transparent.
        play = next(s for s in self.state["streams"] if s["node"] == "dsp1.play.0")
        self.assertTrue(play["transparent"])
        self.assertEqual(play["converting_feeders"], [])
        self.assertEqual(play["owner"], "brutefir")
        self.assertEqual(play["owner_pid"], 2067)
        self.assertEqual(play["rate"], 192000)

    def test_converting_chain_is_reported_as_such(self):
        idle = next(s for s in self.state["streams"] if s["node"] == "dsp0.play.0")
        self.assertFalse(idle["transparent"])
        self.assertIn("feeder_format", idle["converting_feeders"])
        self.assertIn("feeder_volume", idle["converting_feeders"])

    def test_idle_channels_are_not_open_streams(self):
        # Every channel is listed whether open or not.  Counting the idle ones
        # made a second, unused sound card look like a rate and format
        # disagreement inside the chain — the suite's hard stop for "something
        # is resampling".
        summary = PROBE.summarize({"os": {"name": "FreeBSD"},
                                   "oss": self.state,
                                   "usb_audio_devices": []})
        self.assertEqual([s["where"] for s in summary["open_streams"]],
                         ["pcm1/dsp1.play.0"])
        self.assertTrue(summary["all_open_streams_same_rate"])
        self.assertTrue(summary["all_open_streams_same_format"])
        self.assertEqual(summary["open_stream_rates"], [192000])


class UsbconfigDescriptors(unittest.TestCase):
    """usbconfig prints standard descriptors as fields, never as hex."""

    def test_standard_descriptors_are_re_encoded(self):
        blob = PROBE.usbconfig_raw_bytes(USBCONFIG_ALT)
        # interface(9) + AS_GENERAL(16) + FORMAT_TYPE_I(6) + endpoint(7)
        self.assertEqual(len(blob), 9 + 16 + 6 + 7)
        # The endpoint descriptor must be cut to its bLength: usbconfig prints
        # bRefresh and bSynchAddress that a 7-byte descriptor does not carry,
        # and emitting them desynchronises every descriptor after it.
        self.assertEqual(blob[-7:], bytes([0x07, 0x05, 0x01, 0x05, 0x88, 0x01, 0x01]))

    def test_alt_setting_is_recovered(self):
        alts = PROBE.parse_descriptors(PROBE.usbconfig_raw_bytes(USBCONFIG_ALT))
        self.assertEqual(len(alts), 1)
        alt = alts[0]
        self.assertEqual(alt["interface"], 1)
        self.assertEqual(alt["alt"], 1)
        self.assertEqual(alt["uac"], 2)
        # The two fields the whole comparison turns on.
        self.assertEqual(alt["subslot_bytes"], 4)
        self.assertEqual(alt["bit_resolution"], 24)
        self.assertEqual(alt["bm_formats"], 0x00000001)
        self.assertEqual([e["address"] for e in alt["endpoints"]], ["0x01"])
        self.assertEqual(alt["endpoints"][0]["max_packet_size"], 392)


if __name__ == "__main__":
    unittest.main()
