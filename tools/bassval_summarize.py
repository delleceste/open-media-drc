"""Summarize completed Aalto validation runs, counting missing/constant cues.

Unlike the exploratory bassval table, strict monotonic counts here require
all four distances. Seeds are paired simulator repeats, not new recordings.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

from bassval import evaluate, scenes


def cue_summary(groups, cue, sign, xkey):
    complete, rhos = 0, []
    for rows in groups.values():
        if len(rows) != 4 or any(r['cues'].get(cue) is None for r in rows):
            continue
        values = [r['cues'][cue] for r in rows]
        if not np.isfinite(values).all():
            raise ValueError('Nonfinite cue')
        complete += 1
        if len(set(values)) > 1:
            rhos.append(float(spearmanr([xkey(r) for r in rows], values).statistic))
    return {'groups': len(groups), 'complete': complete, 'varying': len(rhos),
            'mean_rho': float(np.mean(rhos)) if rhos else None,
            'right_sign': sum(r*sign > 0 for r in rhos),
            'strictly_monotonic_right': sum(r*sign >= .999 for r in rhos)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--runs', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    paths = {key: args.runs / name for key, name in {
        'distance': 'distance-v2.jsonl', 'controls': 'controls-v2.jsonl',
        'radial': 'radial-v3.jsonl', 'annotations': 'annotations-v3.jsonl',
        'spot-levels': 'spot-levels-v3.jsonl'}.items()}
    data = {key: evaluate.load(str(p)) for key, p in paths.items()}
    expected = {sc.key for seed in (1, 2, 3) for grid in ('distance', 'hall', 'spot')
                for sc in scenes.grid(grid, ['beethoven', 'bruckner', 'mahler'], seed, 'recorded')}
    combined = data['distance'] + data['controls']
    if len(combined) != 198 or {r['scene']['key'] for r in combined} != expected:
        raise ValueError('Incomplete/duplicate primary grid (expected 198 unique scenes)')
    report = evaluate.report(combined)  # also validates provenance compatibility
    radial_expected = {(p, s, m, d) for p in ('beethoven', 'bruckner', 'mahler')
                       for s in (1, 2, 3) for m in ('AB', 'ORTF', 'XY') for d in (8., 10., 12., 14.)}
    if len(data['radial']) != 108 or {(r['piece'], r['seed'], r['setup'], r['distance'])
                                    for r in data['radial']} != radial_expected:
        raise ValueError('Incomplete/duplicate radial grid')
    annotated_expected = {(p, e) for p in ('beethoven', 'bruckner', 'mahler')
                          for e in ('annotations-dry-bass', 'annotations-dry-mix')}
    if len(data['annotations']) != 6 or {(r['piece'], r['experiment']) for r in data['annotations']} != annotated_expected:
        raise ValueError('Incomplete/duplicate annotation evaluation')
    level_expected = {scenes.Scene(p, setup='ORTF', seed=1, spot=spot, spot_db=level).key
                      for p in ('beethoven', 'bruckner', 'mahler')
                      for spot in ('aligned', 'unaligned') for level in (-6., 0., 6., 12.)}
    if len(data['spot-levels']) != 24 or {r['scene']['key'] for r in data['spot-levels']} != level_expected:
        raise ValueError('Incomplete/duplicate stronger-spot check')
    # Supplementary runs must use the same detector/renderer and stable inputs.
    algorithm = combined[0]['provenance']['algorithm']
    primary_inputs = {r['scene']['piece']: r['provenance'] for r in combined}
    for kind in ('radial', 'annotations', 'spot-levels'):
        identities = {}
        for row in data[kind]:
            piece = row.get('piece') or row['scene']['piece']
            provenance = row['provenance']
            if provenance['algorithm'] != algorithm or not provenance.get('experiment_sha256'):
                raise ValueError(f'Incompatible supplementary algorithm: {kind}')
            if piece in identities and identities[piece] != provenance:
                raise ValueError(f'Changed supplementary inputs/runner: {kind}/{piece}')
            identities[piece] = provenance
            if kind == 'spot-levels' and {k: v for k, v in provenance.items()
                                         if k != 'experiment_sha256'} != primary_inputs[piece]:
                raise ValueError(f'Spot sensitivity uses different inputs: {piece}')
            if kind == 'radial' and any(primary_inputs[piece]['sources'].get(Path(p).name) != digest
                                       for p, digest in provenance['sources'].items()):
                raise ValueError(f'Radial diagnostic uses different bass sources: {piece}')
    primary = {r['scene']['key']: r for r in combined}
    for r in data['spot-levels']:
        if r['scene']['spot_db'] != -6.:
            continue
        original = primary[r['scene']['key']]['cues']
        for key, value in r['cues'].items():
            if key == 'gcc_best':  # nested peak list; compare scalar outputs here
                continue
            other = original[key]
            if isinstance(value, (int, float)) and isinstance(other, (int, float)):
                if not np.isclose(value, other, rtol=1e-9, atol=1e-9):
                    raise ValueError(f'Remix baseline differs: {key}')
            elif value != other:
                raise ValueError(f'Remix baseline differs: {key}')
    groups = defaultdict(list)
    for r in data['distance']:
        s = r['scene']
        groups[s['piece'], s['setup'], s['seed']].append(r)
    radial_groups = defaultdict(list)
    for r in data['radial']:
        if r['frames'] >= 20:
            radial_groups[r['piece'], r['setup'], r['seed']].append(r)
    summary = {'primary_scenes': len(combined), 'distance_scenes': len(data['distance']),
               'radial_scenes': len(data['radial']), 'distance': {}, 'radial': {}, 'spot': {},
               'hall': {}, 'spot_cue_changes': {}, 'source_selection': {}, 'annotations': [],
               'spot_levels': [{'scene': r['scene'], 'cues': r['cues']} for r in data['spot-levels']]}
    for cue, sign in evaluate.CUES.items():
        summary['distance'][cue] = cue_summary(groups, cue, sign, lambda r: r['truth']['bass_distance_m'])
        summary['distance'][cue]['available_scenes'] = sum(r['cues'].get(cue) is not None for r in data['distance'])
        summary['distance'][cue]['by_piece'] = {piece: cue_summary(
            {k: v for k, v in groups.items() if k[0] == piece}, cue, sign,
            lambda r: r['truth']['bass_distance_m']) for piece in ('beethoven', 'bruckner', 'mahler')}
    hall_groups = defaultdict(dict)
    for r in combined:
        s = r['scene']
        if s['spot'] == 'none' and s['setup'] == 'ORTF' and s['bass_shift'] in (-2., 2.):
            hall_groups[s['piece'], s['seed']][s['rt_mid'], s['bass_shift']] = r['cues']
    for cue in evaluate.CUES:
        halls, distances, complete = [], [], 0
        for values in hall_groups.values():
            cells = [[values.get((rt, shift), {}).get(cue) for shift in (-2., 2.)]
                     for rt in (1.4, 1.9, 2.6)]
            if any(value is None for row in cells for value in row):
                continue
            matrix = np.array(cells)
            halls.extend(np.ptp(matrix, axis=0))
            distances.extend(np.ptp(matrix, axis=1))
            complete += 1
        summary['hall'][cue] = {'complete_groups': complete, 'total_groups': len(hall_groups),
            'hall_over_4m_stage_shift_spread': float(np.mean(halls)/np.mean(distances))
            if distances and np.mean(distances) > 0 else None}
    for cue, sign in (('coh_comb', -1), ('fund_db', 1), ('power_fund_db', 1)):
        summary['radial'][cue] = cue_summary(radial_groups, cue, sign, lambda r: r['distance'])
        summary['radial'][cue]['by_piece'] = {piece: cue_summary(
            {k: v for k, v in radial_groups.items() if k[0] == piece}, cue, sign,
            lambda r: r['distance']) for piece in ('beethoven', 'bruckner', 'mahler')}
    for piece in ('beethoven', 'bruckner', 'mahler'):
        rs = [r for r in data['radial'] if r['piece'] == piece]
        if len({r['selection_sha256'] for r in rs}) != 1:
            raise ValueError('Source selection changed within radial piece')
        stage = [r for r in data['distance'] if r['scene']['piece'] == piece]
        if len({r['cues']['fixed.selection_sha256'] for r in stage}) != 1:
            raise ValueError('Source selection changed within distance piece')
        summary['source_selection'][piece] = {'frames': rs[0]['frames'], 'seconds': rs[0]['frames']*.064}
    for spot in ('none', 'aligned', 'unaligned'):
        rs = [r for r in combined if r['scene']['spot'] == spot
              and r['scene']['bass_shift'] == 0 and r['scene']['rt_mid'] == 1.9]
        summary['spot'][spot] = dict(Counter(r['cues']['verdict'] for r in rs))
    baseline = {(r['scene']['piece'], r['scene']['setup'], r['scene']['seed']): r
                for r in combined if r['scene']['spot'] == 'none'
                and r['scene']['bass_shift'] == 0 and r['scene']['rt_mid'] == 1.9}
    for spot in ('aligned', 'unaligned'):
        summary['spot_cue_changes'][spot] = {}
        for cue, sign in (('score', -1), ('coh_ratio', 1), ('solo.fund_db', -1),
                          ('solo.coh_comb', 1), ('with-sections.coh_comb', 1)):
            deltas = []
            by_piece = defaultdict(list)
            for r in combined:
                s = r['scene']
                if s['spot'] != spot:
                    continue
                a = r['cues'].get(cue)
                b = baseline[s['piece'], s['setup'], s['seed']]['cues'].get(cue)
                if a is not None and b is not None:
                    deltas.append(a-b)
                    by_piece[s['piece']].append(a-b)
            def delta_stats(values):
                return {'available_pairs': len(values),
                        'median_spot_minus_control': float(np.median(values)) if values else None,
                        'expected_direction': sum(v*sign > 1e-8 for v in values),
                        'unchanged': sum(abs(v) <= 1e-8 for v in values)}
            summary['spot_cue_changes'][spot][cue] = {**delta_stats(deltas),
                'expected_sign': sign, 'total_pairs': 27,
                'by_piece': {p: delta_stats(v) for p, v in by_piece.items()}}
    scored = [r for r in data['distance'] if r['cues'].get('score') is not None]
    summary['score_coverage'] = {'available': len(scored),
        'coherence_at_upper_score_clip': sum(r['cues']['coh_ratio'] <= 1 for r in scored),
        'solo_support_under_10s': sum(0 < (r['cues'].get('solo.seconds') or 0) < 10 for r in scored)}
    summary['annotations'] = [{k: r[k] for k in ('experiment', 'piece', 'metrics')} for r in data['annotations']]
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    (args.out / 'report.txt').write_text('\n'.join(line.rstrip() for line in report.rstrip().splitlines()) + '\n')
    for key, path in paths.items():
        (args.out / (key + '.jsonl.gz')).write_bytes(gzip.compress(path.read_bytes(), mtime=0))
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
