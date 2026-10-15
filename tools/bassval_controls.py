"""Run nonduplicate hall/spot controls accompanying a full distance grid.

Uses the unmodified bassval renderer/detector; skips only no-spot, RT=1.9
scenes already present in the distance grid. Full excerpts, recorded parts,
seeds 1,2,3. Stores the same provenance as the standard CLI.
"""
import argparse
import json
import time
from pathlib import Path

from bassval import evaluate, render, scenes, stems
from bassval.__main__ import FS, algorithm_identity, input_identity, validate_resume


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stems', required=True, type=Path)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    algorithm = algorithm_identity()
    pieces = ('beethoven', 'bruckner', 'mahler')
    identities = {p: input_identity(str(args.stems / p), None, algorithm) for p in pieces}
    existing = evaluate.load(args.out)
    validate_resume(existing, identities, algorithm)
    done = {r['scene']['key'] for r in existing}
    with open(args.out, 'a') as output:
        for piece in pieces:
            parts = stems.load_piece(str(args.stems / piece), FS)
            for seed in (1, 2, 3):
                for grid in ('hall', 'spot'):
                    for scene in scenes.grid(grid, [piece], seed, 'recorded'):
                        if scene.spot == 'none' and scene.rt_mid == 1.9:
                            continue
                        if scene.key in done:
                            continue
                        start = time.monotonic()
                        components = {}
                        x, truth = render.render_scene(scene, parts, FS, components)
                        cues = evaluate.detector_cues(x, scene.key, components)
                        output.write(json.dumps({'scene': scene.as_dict(), 'truth': truth,
                            'cues': cues, 'provenance': identities[piece]}) + '\n')
                        output.flush()
                        print(scene.key, round(time.monotonic()-start, 1), cues.get('verdict'), flush=True)


if __name__ == '__main__':
    main()
