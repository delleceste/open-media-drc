"""Spot sensitivity at fixed thresholds: ORTF, -6/0/+6/+12 dB, seed 1.

Use full renderer-2 scenes at every gain. An additive remix diagnostic is
retained for waveform regression only: roundoff-level waveform equivalence
does not guarantee identical detector outputs near discrete decisions.
"""
import argparse
import json
from pathlib import Path

import numpy as np

from bassval import evaluate, render, scenes, stems
from bassval.__main__ import FS, algorithm_identity, file_digest, input_identity


def remix(main, spot_pair, delay_ms, level, aligned):
    contribution = spot_pair * 10 ** ((level + 6) / 20)
    if aligned:
        shift = int(round(delay_ms * FS / 1000))
        contribution = np.pad(contribution, ((0, 0), (shift, 0)))[:, :main.shape[1]]
    mixed = main + contribution
    return mixed * (0.5 / (abs(mixed).max() + 1e-12))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stems', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    args = ap.parse_args()
    algorithm = algorithm_identity()
    with args.out.open('x') as output:
        for piece in ('beethoven', 'bruckner', 'mahler'):
            provenance = input_identity(str(args.stems / piece), None, algorithm)
            provenance['experiment_sha256'] = file_digest(__file__)
            parts = stems.load_piece(str(args.stems / piece), FS)
            for setup in ('ORTF',):
                for aligned in (False, True):
                    for level in (-6., 0., 6., 12.):
                        sc = scenes.Scene(piece, setup=setup, spot='aligned' if aligned else 'unaligned',
                                          spot_db=level, seed=1)
                        mixed, truth = render.render_scene(sc, parts, FS)
                        cues = evaluate.detector_cues(mixed, sc.key)
                        row = {'scene': sc.as_dict(), 'truth': truth,
                               'cues': cues, 'provenance': provenance}
                        output.write(json.dumps(row) + '\n')
                        output.flush()
                        print(piece, setup, sc.spot, level, cues['score'], cues['verdict'], flush=True)


if __name__ == '__main__':
    main()
