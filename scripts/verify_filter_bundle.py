#!/usr/bin/env python3
"""Read-only verification of committed filter provenance bundles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

from deploy_filter import (
    ROOT, AuditError, add_site_root_argument, bundle_identity_from_manifest,
    canonical_hash, parse_config, peak_gain_db, required_attenuation,
    resolve_site_root, sha256_file,
)
from filter_workflow_next import print_deployed_next


def inside(root: Path, relative: str) -> Path:
    if Path(relative).is_absolute():
        raise AuditError(f"absolute bundle path: {relative}")
    root = root.resolve()
    result = (root / relative).resolve()
    try:
        result.relative_to(root)
    except ValueError as exc:
        raise AuditError(f"bundle path escapes root: {relative}") from exc
    return result


def verify_manifest(path: Path, require_sources: bool, site_root: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    geometry_root = path.parent.parent
    expected_id = canonical_hash(bundle_identity_from_manifest(manifest))
    if expected_id != manifest["bundle_id"]:
        raise AuditError(f"{path}: bundle ID mismatch")
    if manifest.get("verification", {}).get("status") != "verified":
        raise AuditError(f"{path}: manifest status is not verified")
    response_available = manifest.get("response_available", True)
    # A response bundle has to say which REW session it came out of. A WAV-only
    # bundle deliberately has no measurements and makes no response claim.
    session = manifest["source"].get("measurements") or {}
    if response_available and not session.get("sha256"):
        raise AuditError(
            f"{path}: the manifest does not name the REW .mdat these exports were "
            "taken from; redeploy this design with new_filter_design.py")

    analysis_path = inside(geometry_root, manifest["analysis"]["path"])
    if sha256_file(analysis_path) != manifest["analysis"]["sha256"]:
        raise AuditError(f"{path}: analysis hash mismatch")
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    if analysis.get("schema") != 2:
        raise AuditError(f"{path}: unsupported analysis schema {analysis.get('schema')!r}")
    if (analysis.get("geometry") != manifest["geometry"] or
            analysis.get("variant") != manifest["variant"] or
            analysis.get("design_id", analysis.get("variant")) !=
            manifest.get("design_id", manifest["variant"])):
        raise AuditError(f"{path}: analysis identity mismatch")
    if ("description" in manifest and
            analysis.get("description") != manifest["description"]):
        raise AuditError(f"{path}: analysis description mismatch")
    if bool(analysis.get("response_available", True)) != bool(response_available):
        raise AuditError(f"{path}: response availability mismatch")
    if not response_available and (analysis.get("traces") or
                                   analysis.get("frequency_grids")):
        raise AuditError(f"{path}: WAV-only bundle unexpectedly contains response data")
    for role, item in manifest["source"]["artifacts"].items():
        if analysis["inputs"].get(role) != item["sha256"]:
            raise AuditError(f"{path}: analysis dependency mismatch for {role}")
        source_copy = inside(geometry_root, item["bundle_path"])
        if source_copy.exists():
            if sha256_file(source_copy) != item["sha256"]:
                raise AuditError(f"{path}: source copy mismatch for {role}")
        elif require_sources:
            raise AuditError(f"{path}: source copy missing for {role}")

    for rate_text, runtime in manifest["runtime"]["rates"].items():
        rate = int(rate_text)
        config_path = inside(site_root, runtime["config"])
        if sha256_file(config_path) != runtime["config_sha256"]:
            raise AuditError(f"{path}: config hash mismatch at {rate} Hz")
        config = parse_config(config_path, rate)
        if config["attenuation_db"] != runtime["attenuation_db"]:
            raise AuditError(f"{path}: config attenuation mismatch at {rate} Hz")
        peaks = []
        for channel in ("left", "right"):
            item = runtime["channels"][channel]
            raw_path = inside(geometry_root, item["path"])
            if sha256_file(raw_path) != item["sha256"]:
                raise AuditError(f"{path}: {rate} Hz {channel} RAW hash mismatch")
            if raw_path.stat().st_size != item["bytes"]:
                raise AuditError(f"{path}: {rate} Hz {channel} RAW size mismatch")
            values = np.fromfile(raw_path, dtype="<f8")
            if values.size != item["samples"]:
                raise AuditError(f"{path}: {rate} Hz {channel} sample count mismatch")
            peaks.append(peak_gain_db(values))
        needed = required_attenuation(max(peaks), runtime["safety_margin_db"])
        if needed != runtime["required_attenuation_db"]:
            raise AuditError(f"{path}: recorded headroom mismatch at {rate} Hz")
        if runtime["attenuation_db"] < needed:
            raise AuditError(f"{path}: insufficient attenuation at {rate} Hz")
    print(f"PASS {manifest['geometry']}/{manifest['variant']} {manifest['bundle_id']}")
    project = manifest["source"].get("project") or {}
    if not response_available:
        print("     source WAV filters only; FILTER RESPONSE unavailable")
    elif (project.get("kind") == "browser-upload" and
            project.get("archive_history") == "required"):
        print(f"     source browser upload archived in required site history"
              f" · session {session['file']} {session['sha256'][:12]}")
    elif project.get("commit"):
        print(f"     source {project.get('name', '?')} @ {project['commit'][:12]}"
              f" · session {session['file']} {session['sha256'][:12]}")
    else:
        print(f"     WARN: no source project commit; session {session['file']} "
              f"{session['sha256'][:12]} is hashed but may not be retrievable")
    if project and not project.get("clean", True):
        print(f"     WARN: {len(project.get('uncommitted', []))} source file(s) were "
              "not committed when this bundle was published")
    try:
        manifest_path = path.relative_to(site_root)
    except ValueError:
        manifest_path = path
    return {
        "geometry": manifest["geometry"],
        "design_id": manifest.get("design_id", manifest["variant"]),
        "bundle_id": manifest["bundle_id"],
        "manifest": manifest_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="*", type=Path)
    parser.add_argument("--all", action="store_true", help="verify every committed bundle")
    parser.add_argument("--require-sources", action="store_true",
                        help="fail if development-only source copies are absent")
    parser.add_argument(
        "--no-next", action="store_true",
        help="suppress operator handoff (for CMake/CI/internal invocation)")
    add_site_root_argument(parser)
    args = parser.parse_args()
    site_root = resolve_site_root(args.site_root)
    manifests = args.manifests
    if args.all or not manifests:
        manifests = sorted(site_root.glob("filters/*/provenance/*.json"))
        manifests = [path for path in manifests if not path.name.endswith(".source.json")]
    if not manifests:
        raise AuditError("no filter provenance manifests found")
    verified = [
        verify_manifest(manifest.resolve(), args.require_sources, site_root)
        for manifest in manifests
    ]
    if not args.no_next:
        print_deployed_next(ROOT, verified, include_verification=False, site_root=site_root)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AuditError, OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
