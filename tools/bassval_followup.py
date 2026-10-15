"""Supplementary Aalto controls; does not alter the production detector.

radial: isolated original bass parts, fixed source bearing, four mean ranges.
annotations: denoised PHENICX bass/mix against its aligned note annotations.
Audio and annotations remain local; JSON contains derived measurements only.
"""
import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np

import bass_depth as bass
from bassval import evaluate, geometry, render, scenes, stems
from bassval.__main__ import algorithm_identity, file_digest


def identity(paths):
    return {"algorithm": algorithm_identity(),
            "experiment_sha256": file_digest(__file__),
            "sources": {str(p): file_digest(p) for p in paths}}


def radial_positions(pos, centre, distance):
    rays = np.asarray(pos) - centre
    return centre + rays * distance / np.linalg.norm(rays, axis=1).mean()


def channel_power_fund(L, R, pitches, frames):
    """Diagnostic alternative: average channel powers before harmonic ratios."""
    values = []
    for start in range(0, len(frames), 256):
        fi = frames[start:start + 256]
        power = (abs(bass.spectra(L, fi))**2 + abs(bass.spectra(R, fi))**2) / 2
        for p, f in zip(power, pitches[fi]):
            bins = np.rint(np.arange(1, 7) * f * bass.NFFT / bass.SR).astype(int)
            h = [p[max(k-1, 0):k+2].max() for k in bins]
            values.append(10 * np.log10(h[0] / (np.mean(h[1:]) + bass.EPS) + bass.EPS))
    return float(np.median(values)) if values else None


def radial(args, emit):
    original_positions = geometry.positions
    for piece in args.pieces.split(','):
        files = stems.part_files(str(args.stems / piece))['kb']
        parts = {'kb': [stems.decode(p, bass.SR) for p in files]}
        n = max(map(len, parts['kb']))
        parts['kb'] = [np.pad(x, (0, n-len(x))) for x in parts['kb']]
        pitches, frames = evaluate.source_selection(sum(parts['kb']))
        provenance = identity(files)
        selection = hashlib.sha256(np.column_stack((frames, pitches[frames])).astype('<f8').tobytes()).hexdigest()
        for seed in (1, 2, 3):
            for setup in ('AB', 'ORTF', 'XY'):
                for distance in (8., 10., 12., 14.):
                    def positions(section, count, hall, rng, shift=(0., 0.)):
                        pos = original_positions(section, count, hall, rng, shift)
                        centre = np.array([hall.width / 2, 14., 3.5])
                        result = radial_positions(pos, centre, distance)
                        if not np.all((result > 0) & (result < hall.dims)):
                            raise ValueError('Radial source outside hall')
                        return result
                    components = {}
                    with patch.object(geometry, 'positions', positions):
                        _, truth = render.render_scene(scenes.Scene(piece, setup=setup, seed=seed),
                                                       parts, bass.SR, components)
                    L, R = components['bass_main']
                    cues = bass.context_cues(L, R, pitches, frames, bass.SR / bass.NFFT, None) if len(frames) >= 20 else {}
                    cues['power_fund_db'] = channel_power_fund(L, R, pitches, frames)
                    emit({'experiment': 'radial-isolated', 'piece': piece, 'seed': seed,
                          'setup': setup, 'distance': distance, 'truth': truth, 'cues': cues,
                          'frames': len(frames), 'selection_sha256': selection, 'provenance': provenance})


def note_hz(note):
    semis = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
    pitch = semis[note[0]] + (1 if '#' in note else 0)
    midi = 12 * (int(note[-1]) + 1) + pitch
    return 440. * 2 ** ((midi - 69) / 12)


def annotation_metrics(x, notes):
    opts = bass.build_parser().parse_args([])
    nfr = 1 + (len(x) - bass.NFFT) // bass.HOP
    t = (np.arange(nfr)*bass.HOP + bass.NFFT/2) / bass.SR
    f0, dom, peaks, energy = bass.bass_salience(x, nfr, opts.fmin, opts.fmax)
    gate = energy > np.percentile(energy, 99) * 10 ** (-opts.gate_db / 10)
    masks, _, _ = bass.classify_contexts(x, f0, dom, peaks, energy, gate, opts)
    detected = masks['solo'] | masks['with-sections'] | masks['tutti']
    active, eligible, correct = (np.zeros(nfr, bool) for _ in range(3))
    half = bass.NFFT / bass.SR / 2
    for onset, offset, frequency in notes:
        active |= (t >= onset) & (t < offset)
        if opts.fmin <= frequency <= opts.fmax:
            interior = (t >= onset + half) & (t < offset - half)
            eligible |= interior
            correct |= interior & (abs(12*np.log2(f0/frequency)) <= .5)
    def fraction(num, den):
        return float(num.sum()/den.sum()) if den.any() else None
    return {'annotated_active_frames': int(active.sum()), 'eligible_interior_frames': int(eligible.sum()),
            'detected_frames': int(detected.sum()),
            'activity_precision_all_notes': fraction(detected & active, detected),
            'in_range_interior_recall': fraction(detected & eligible, eligible),
            'pitch_accuracy_in_range_interiors': fraction(correct & eligible, eligible),
            'correct_pitch_detection_recall': fraction(correct & detected & eligible, eligible),
            'note': 'Activity at frame centres; pitch/recall only full FFT windows inside 29-66 Hz notes. Pizzicato excluded.'}


def annotations(args, emit):
    for piece in args.pieces.split(','):
        folder = args.phenicx / 'audio' / piece
        paths = sorted(folder.glob('*.wav'))
        label = args.phenicx / 'annotations' / piece / 'doublebass.txt'
        notes = [(float(a), float(b), note_hz(c)) for a, b, c in
                 (line.split(',') for line in label.read_text().splitlines() if line.strip())]
        provenance = identity([*paths, label])
        mixture = None
        lengths = {}
        for path in paths:
            x = stems.decode(str(path), bass.SR)
            lengths[path.name] = len(x)
            if mixture is None:
                mixture = np.zeros_like(x)
            if len(x) > len(mixture):
                mixture = np.pad(mixture, (0, len(x)-len(mixture)))
            # Some released stems end earlier; preserve their time origin.
            mixture[:len(x)] += x
            if path.stem == 'doublebass':
                emit({'experiment': 'annotations-dry-bass', 'piece': piece,
                      'metrics': annotation_metrics(x, notes), 'provenance': provenance})
        emit({'experiment': 'annotations-dry-mix', 'piece': piece,
              'metrics': annotation_metrics(mixture, notes), 'source_samples': lengths,
              'provenance': provenance})


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('experiment', choices=('radial', 'annotations'))
    ap.add_argument('--stems', type=Path)
    ap.add_argument('--phenicx', type=Path)
    ap.add_argument('--pieces', default='beethoven,bruckner,mahler')
    ap.add_argument('--out', required=True, type=Path)
    args = ap.parse_args()
    # Never append to unverified/stale supplementary runs.
    with args.out.open('x') as f:
        def emit(row):
            f.write(json.dumps(row) + '\n')
            f.flush()
            print(row['experiment'], row['piece'], row.get('seed', ''),
                  row.get('setup', ''), row.get('distance', ''), flush=True)
        {'radial': radial, 'annotations': annotations}[args.experiment](args, emit)


if __name__ == '__main__':
    main()
