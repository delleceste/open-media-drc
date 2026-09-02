#!/usr/bin/env python3
"""Publish a DRC design from a left/right pair of impulse WAV filters only."""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
import re

from deploy_filter import (
    AuditError, add_site_root_argument, atomic_json, commit_site, load_filter_wav,
    publish_bundle, resolve_site_root, sha256_file,
)


NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path,
                        help="directory containing L.wav and R.wav")
    parser.add_argument("--geometry", required=True)
    parser.add_argument("--design", required=True)
    parser.add_argument("--description", default="")
    parser.add_argument("--rates", default="44100,48000,88200,96000,192000")
    parser.add_argument("--safety-margin", type=float, default=1.0)
    parser.add_argument("--attenuation", default="auto")
    parser.add_argument("--replace-design", action="store_true")
    parser.add_argument("--no-commit", dest="commit", action="store_false")
    parser.add_argument("--require-commit", action="store_true")
    parser.add_argument("--yes", action="store_true",
                        help="accepted for parity with new_filter_design.py")
    parser.add_argument("--upload-provenance", action="store_true",
                        help="record that the files came through the web UI")
    parser.add_argument("--no-next", action="store_true",
                        help="accepted for the web workflow")
    add_site_root_argument(parser)
    args = parser.parse_args()

    if not NAME_PATTERN.fullmatch(args.geometry):
        raise AuditError("invalid geometry name")
    if not NAME_PATTERN.fullmatch(args.design) or args.design == "default":
        raise AuditError("invalid or reserved design ID")
    if args.require_commit and not args.commit:
        raise AuditError("--require-commit cannot be combined with --no-commit")
    if not args.yes:
        raise AuditError("refusing to publish without --yes")

    directory = args.directory.resolve()
    left, right = directory / "L.wav", directory / "R.wav"
    for path in (left, right):
        if not path.is_file() or path.is_symlink():
            raise AuditError(f"missing regular filter WAV: {path.name}")
    left_rate, _ = load_filter_wav(left)
    right_rate, _ = load_filter_wav(right)
    if left_rate != right_rate:
        raise AuditError(
            f"left/right filter WAV sample rates differ: {left_rate} vs {right_rate} Hz")

    try:
        rates = sorted({int(value.strip()) for value in args.rates.split(",")})
    except ValueError as error:
        raise AuditError("--rates must be a comma-separated integer list") from error
    if not rates or any(rate < 8000 or rate > 384000 for rate in rates):
        raise AuditError("sample rates must be between 8000 and 384000 Hz")
    attenuation: str | float = args.attenuation
    if attenuation != "auto":
        try:
            attenuation = float(attenuation)
        except ValueError as error:
            raise AuditError("--attenuation must be 'auto' or a number") from error

    site_root = resolve_site_root(args.site_root)
    description = args.description.strip() or f"{args.geometry} {args.design} correction"
    artifacts = {}
    for role, path in (("filter_left_wav", left), ("filter_right_wav", right)):
        artifacts[role] = {
            "path": path.name,
            "bundle_path": f"source/{args.design}/{path.name}",
            "sha256": sha256_file(path),
        }
    project = ({
        "name": "browser upload", "repository": "", "remote": "",
        "branch": "", "commit": "", "committed_at": "", "subject": "",
        "path": directory.name, "kind": "browser-upload",
        "archive_history": "required" if args.require_commit else "none",
        "clean": bool(args.require_commit), "uncommitted": [],
    } if args.upload_provenance else {})
    recipe = {
        "schema": 2,
        "geometry": args.geometry,
        "variant": args.design,
        "design_id": args.design,
        "description": description,
        "audited_at": dt.date.today().isoformat(),
        "response_available": False,
        "aggregate": {"style": "unavailable", "corrected": "unavailable"},
        "source": {
            "directory": directory.name,
            "project": project,
            "measurements": {},
            "artifacts": artifacts,
        },
        "filter": {"sample_rate": left_rate},
        "runtime": {
            "selector": f"@{args.design}",
            "generate_configs": True,
            "format": "FLOAT64_LE",
            "attenuation_db": attenuation,
            "safety_margin_db": args.safety_margin,
            "coefficient_root": "@REPO_DIR@",
            "rates": {
                str(rate): f"configs/{args.geometry}/brutefir-{rate}@{args.design}.conf.in"
                for rate in rates
            },
        },
    }
    manifest = publish_bundle(
        recipe, directory, site_root, apply=True, replace=args.replace_design)
    unchanged = manifest.pop("_publication_unchanged", False)
    if not unchanged:
        atomic_json(
            recipe,
            site_root / "filters" / args.geometry / "provenance" /
            f"{args.design}.source.json")
    if args.commit:
        result = commit_site(site_root, manifest)
        if args.require_commit and result["status"] not in {"committed", "unchanged"}:
            raise AuditError(
                "site repository did not record the deployment: "
                + result.get("error", result["status"]))
    print(f"WAV-only design {args.geometry}/{args.design} published; filter response unavailable")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as error:
        print(f"ERROR: {error}", file=__import__("sys").stderr)
        raise SystemExit(1)
