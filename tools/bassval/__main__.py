"""Command line: render scenes, run the detector on them, report.

  cd tools
  python3 -m bassval run --stems DIR --grid distance --pieces beethoven,mahler,bruckner --out results.jsonl
  python3 -m bassval report results.jsonl
  python3 -m bassval render --stems DIR --piece beethoven --shift -2 --setup AB --wav out.wav

DIR holds one folder per piece with the Aalto anechoic mp3 files (e.g.
DIR/beethoven/beethoven_kb_6.mp3).  `run` appends one JSON line per scene
and skips scenes already in the file, so it can be stopped and resumed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import scipy

from . import evaluate, geometry, render, scenes, stems

FS = 16000      # the detector's own rate: nothing above 4 kHz is used


def file_digest(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def algorithm_identity():
    folder = Path(__file__).parent
    paths = sorted(folder.glob("*.py")) + [folder.parent / "bass_depth.py"]
    return {"detector": evaluate.bass_depth.VERSION, "renderer": render.VERSION, "fs": FS,
            "numpy": np.__version__, "scipy": scipy.__version__,
            "code": {p.name: file_digest(p) for p in paths}}


def input_identity(folder, seconds, algorithm):
    files = stems.part_files(folder)
    if not files.get("kb"):
        raise ValueError(f"{folder}: no double-bass mic-6 parts found")
    return {"algorithm": algorithm, "seconds": seconds,
            "sources": {os.path.basename(p): file_digest(p) for paths in files.values() for p in paths}}


def validate_resume(rows, identities, algorithm):
    for row in rows:
        provenance = row.get("provenance")
        piece = row["scene"]["piece"]
        if (not provenance or provenance.get("algorithm") != algorithm
                or piece in identities and provenance != identities[piece]):
            raise ValueError("Existing results have different or missing provenance; choose a new --out file")


def cmd_run(a) -> int:
    pieces = a.pieces.split(",")
    algorithm = algorithm_identity()
    identities = {p: input_identity(os.path.join(a.stems, p), a.seconds, algorithm) for p in pieces}
    existing = evaluate.load(a.out)
    validate_resume(existing, identities, algorithm)
    done = {r["scene"]["key"] for r in existing}
    seeds = a.seeds if a.seeds is not None else [a.seed]
    todo = [s for seed in dict.fromkeys(seeds)
            for s in scenes.grid(a.grid, pieces, seed, a.section_mode) if s.key not in done]
    print(f"{len(todo)} scenes to run ({len(done)} already in {a.out})", file=sys.stderr)
    cache: dict[str, dict] = {}
    for i, sc in enumerate(todo, 1):
        if sc.piece not in cache:
            cache = {sc.piece: stems.load_piece(os.path.join(a.stems, sc.piece), FS, a.seconds)}
        t = time.time()
        components = {}
        x, truth = render.render_scene(sc, cache[sc.piece], FS, components)
        cues = evaluate.detector_cues(x, sc.key, components)
        with open(a.out, "a", encoding="utf-8") as f:
            f.write(json.dumps({"scene": sc.as_dict(), "truth": truth, "cues": cues,
                                "provenance": identities[sc.piece]}) + "\n")
        print(f"[{i}/{len(todo)}] {sc.key}: {time.time() - t:.0f} s, score {cues.get('score')}, "
              f"verdict {cues.get('verdict')}", file=sys.stderr)
    print(evaluate.report(evaluate.load(a.out)))
    return 0


def cmd_report(a) -> int:
    print(evaluate.report(evaluate.load(a.results)))
    return 0


def cmd_render(a) -> int:
    from scipy.io import wavfile
    parts = stems.load_piece(os.path.join(a.stems, a.piece), FS, a.seconds)
    sc = scenes.Scene(a.piece, a.setup, a.rt, a.shift, a.spot, a.spot_db, a.seed, a.section_mode)
    x, truth = render.render_scene(sc, parts, FS)
    wavfile.write(a.wav, FS, x.T.astype(np.float32))
    print(json.dumps({"scene": sc.as_dict(), "truth": truth}, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="bassval", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="render a grid, run the detector, append results")
    r.add_argument("--stems", required=True)
    r.add_argument("--grid", default="distance", choices=sorted(scenes.GRIDS))
    r.add_argument("--pieces", default="beethoven,bruckner,mahler")
    r.add_argument("--seconds", type=float, default=None, help="use only the first seconds")
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--seeds", type=lambda value: [int(s) for s in value.split(",")],
                   help="paired repeat seeds, e.g. 1,2,3 (overrides --seed)")
    r.add_argument("--section-mode", choices=("recorded", "copies"), default="recorded")
    r.add_argument("--out", default="bassval-results.jsonl")
    r.set_defaults(func=cmd_run)
    p = sub.add_parser("report", help="report a results file")
    p.add_argument("results")
    p.set_defaults(func=cmd_report)
    w = sub.add_parser("render", help="render one scene to a WAV file to listen to")
    w.add_argument("--stems", required=True)
    w.add_argument("--piece", required=True)
    w.add_argument("--setup", default="ORTF", choices=sorted(geometry.SETUPS))
    w.add_argument("--rt", type=float, default=1.9)
    w.add_argument("--shift", type=float, default=0.0)
    w.add_argument("--spot", default="none", choices=["none", "aligned", "unaligned"])
    w.add_argument("--spot-db", type=float, default=-6.0)
    w.add_argument("--seconds", type=float, default=None)
    w.add_argument("--seed", type=int, default=1)
    w.add_argument("--section-mode", choices=("recorded", "copies"), default="recorded")
    w.add_argument("--wav", required=True)
    w.set_defaults(func=cmd_render)
    a = ap.parse_args()
    try:
        return a.func(a)
    except ValueError as e:
        ap.error(str(e))


if __name__ == "__main__":
    sys.exit(main())
