#!/usr/bin/env python3
"""Summarize `usbdump -r FILE -v` without discarding DONE or control events.

Reads text on stdin; writes JSON to stdout. Timestamps describe host trace
events, not electrical bus timing. USBPF has no transfer identity or per-frame
error status, so this does not claim submit/completion pairing or DAC receipt.
"""
import collections
import json
import re
import sys

HEADER = re.compile(r'^(\d\d:\d\d:\d\d\.\d+) usbus(\d+)\.(\d+) '
                    r'(SUBM|DONE)-(\w+)-EP=([0-9a-f]+),.*?NFR=(\d+),'
                    r'SLEN=(\d+),IVAL=(\d+)(?:,ERR=(\w+))?')
FRAME = re.compile(r' frame\[(\d+)\] (READ|WRITE) (\d+) bytes')
HEX = re.compile(r'^\s*[0-9a-f]+\s+((?:[0-9a-f]{2}\s+)+)', re.I)


def summarize(lines):
    counts = collections.Counter()
    errors = collections.Counter()
    sizes = collections.Counter()
    feedback = collections.Counter()
    controls = []
    gaps = collections.defaultdict(list)
    last = {}
    event = None
    frames = []
    def finish():
        if event is None:
            return
        t, bus, device, direction, kind, endpoint, nfr, slen, interval, error = event
        key = f'{direction}/{kind}/{endpoint}'
        counts[key] += 1
        if error:
            errors[key+'/'+error] += 1
        hh, mm, ss = map(float, t.split(':'))
        stamp = hh*3600+mm*60+ss
        if key in last:
            gap = (stamp-last[key]) % 86400
            gaps[key].append(gap*1000)
        last[key] = stamp
        if direction == 'SUBM' and kind == 'ISOC' and endpoint == '00000001':
            for f in frames:
                sizes[f['length']] += 1
        if direction == 'DONE' and kind == 'ISOC' and endpoint == '00000081':
            for f in frames:
                if f['length'] == 4 and len(f['data']) == 4:
                    feedback[int.from_bytes(f['data'], 'little')] += 1
        if kind == 'CTRL':
            controls.append(dict(time=t, direction=direction, endpoint=endpoint,
                                 error=error, frames=[dict(length=f['length'],
                                 data=f['data'].hex()) for f in frames]))
    for line in lines:
        match = HEADER.match(line)
        if match:
            finish()
            event = match.groups()
            frames = []
            continue
        match = FRAME.match(line)
        if match:
            frames.append(dict(length=int(match[3]), data=bytearray()))
            continue
        match = HEX.match(line)
        if match and frames:
            frames[-1]['data'].extend(bytes.fromhex(match[1]))
    finish()
    gap_report = {}
    for key, values in gaps.items():
        values.sort()
        gap_report[key] = dict(median_ms=values[len(values)//2],
                               p99_ms=values[min(len(values)-1,int(len(values)*.99))],
                               max_ms=values[-1])
    return dict(events=dict(counts), completion_status=dict(errors),
                submitted_playback_frame_lengths=dict(sorted(sizes.items())),
                feedback_raw_counts=dict(sorted(feedback.items())),
                host_event_gaps=gap_report, controls=controls,
                limitations='Host events only; gaps include stream stops; no transfer '
                'IDs/per-frame error codes in USBPF; not proof of DAC reception.')


if __name__ == '__main__':
    print(json.dumps(summarize(sys.stdin), indent=2))
