import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('transport', Path(__file__).resolve().parents[1] / 'scripts/audit-usbdump-transport.py')
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)


class TransportSummary(unittest.TestCase):
    def test_retains_control_response_feedback_and_cancel(self):
        text = '''12:00:00.000000 usbus0.2 SUBM-CTRL-EP=00000000,SPD=HIGH,NFR=2,SLEN=8,IVAL=0
 frame[0] WRITE 8 bytes
 0000  A1 01 00 01 00 29 04 00  |........|
 frame[1] READ 4 bytes
12:00:00.001000 usbus0.2 DONE-CTRL-EP=00000000,SPD=HIGH,NFR=2,SLEN=4,IVAL=0,ERR=0
 frame[0] WRITE 8 bytes
 frame[1] READ 4 bytes
 0000  00 EE 02 00 -- -- -- --  |....|
12:00:00.002000 usbus0.2 DONE-ISOC-EP=00000081,SPD=HIGH,NFR=1,SLEN=4,IVAL=0,ERR=0
 frame[0] READ 4 bytes
 0000  D8 FF 17 00 -- -- -- --  |....|
12:00:00.003000 usbus0.2 DONE-ISOC-EP=00000001,SPD=HIGH,NFR=0,SLEN=0,IVAL=0,ERR=CANCELLED
'''
        result = transport.summarize(text.splitlines())
        self.assertEqual(result['controls'][0]['frames'][0]['data'], 'a101000100290400')
        self.assertEqual(result['controls'][1]['frames'][1]['data'], '00ee0200')
        self.assertEqual(result['feedback_raw_counts'], {1572824: 1})
        self.assertEqual(result['completion_status']['DONE/ISOC/00000001/CANCELLED'], 1)

    def test_counts_each_submitted_microframe(self):
        result = transport.summarize('''12:00:00.000000 usbus0.2 SUBM-ISOC-EP=00000001,SPD=HIGH,NFR=2,SLEN=376,IVAL=0
 frame[0] WRITE 192 bytes
 frame[1] WRITE 184 bytes
'''.splitlines())
        self.assertEqual(result['submitted_playback_frame_lengths'], {184: 1, 192: 1})


if __name__ == '__main__':
    unittest.main()
