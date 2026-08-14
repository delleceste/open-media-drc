#!/usr/bin/env python3
"""Deploy a committed, hash-pinned filter-design declaration.

Geometry describes the physical setup (for example ``120.blue``). A design ID
describes one immutable filter revision within it. The source declaration is
the human-reviewed statement that its measurement exports and filter exports
belong together. This command never starts REW and does not parse ``.mdat``.

Without ``--write`` all DSP and deployment checks run in temporary storage.
With ``--write`` the recipe, all sample-rate flavours, BruteFIR configs, graph
data and final manifest are published only after every check passes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from deploy_filter import (
    AuditError, DEFAULT_LIMITS, OPTIONAL_PREDICTION_ROLES, REQUIRED_ROLES,
    ROOT, atomic_json, git_blob, parse_rew_txt, run, safe_source,
    sha256_file, verify_git_sources,
)
from filter_workflow_next import print_deployed_next, print_dry_run_next


BUNDLE_NAMES = {
    "measurement_left": "measurement-L.txt",
    "measurement_right": "measurement-R.txt",
    "measurement_sum": "measurement-L+R.txt",
    "filter_left_txt": "filter-L.txt",
    "filter_right_txt": "filter-R.txt",
    "filter_left_wav": "filter-L.wav",
    "filter_right_wav": "filter-R.wav",
    "corrected_left_txt": "corrected-L-independent.txt",
    "corrected_right_txt": "corrected-R-independent.txt",
    "corrected_sum_txt": "corrected-L+R-independent.txt",
}


def require_name(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise AuditError(f"invalid {label}: {value!r}")
    return value


def load_declaration(source_root: Path, relative: str) -> tuple[Path, dict]:
    path = safe_source(source_root, relative)
    declaration = json.loads(path.read_text(encoding="utf-8"))
    if declaration.get("schema") != 1:
        raise AuditError("source declaration schema must be 1")
    if not isinstance(declaration.get("artifacts"), dict):
        raise AuditError("source declaration has no artifacts object")
    return path, declaration


def resolve_release(source_root: Path, source_ref: str, allow_commit: bool) -> dict:
    """Resolve an immutable release anchor, preferring an annotated tag."""
    tag_name = source_ref.removeprefix("refs/tags/")
    tag_ref = f"refs/tags/{tag_name}"
    try:
        tag_object = run(["git", "rev-parse", f"{tag_ref}^{{tag}}"], source_root)
        commit = run(["git", "rev-parse", f"{tag_ref}^{{commit}}"], source_root)
        return {
            "kind": "annotated_tag",
            "name": tag_name,
            "tag_object": tag_object,
            "commit": commit,
            "subject": run(
                ["git", "for-each-ref", "--format=%(subject)", tag_ref], source_root),
            "tagger": run(
                ["git", "for-each-ref", "--format=%(taggername) %(taggeremail)", tag_ref],
                source_root),
        }
    except AuditError:
        if not allow_commit:
            raise AuditError(
                f"{source_ref!r} is not an annotated tag; create one with "
                "'git tag -a <tag> -m <message>', or explicitly use --allow-commit-ref")
    commit = run(["git", "rev-parse", f"{source_ref}^{{commit}}"], source_root)
    return {"kind": "commit", "name": source_ref, "commit": commit}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    required = parser.add_argument_group("required tagged-design inputs")
    required.add_argument(
        "--source-root", required=True, type=Path,
        help="clean source Git checkout at the selected annotated tag")
    required.add_argument("--source-ref", required=True,
                          help="annotated Git tag; the source checkout HEAD must equal it")
    parser.add_argument("--allow-commit-ref", action="store_true",
                        help="accept a raw commit instead of the preferred annotated tag")
    required.add_argument("--declaration", required=True,
                          help="committed source-manifest path relative to source root")
    parser.add_argument("--rates", default="44100,48000,88200,96000,192000")
    parser.add_argument("--attenuation", default="auto",
                        help="auto, or one dB value used by every generated config")
    parser.add_argument("--safety-margin", type=float, default=1.0)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--replace-design", action="store_true",
                        help="explicitly replace an existing design ID")
    args = parser.parse_args()

    try:
        rates = sorted({int(value.strip()) for value in args.rates.split(",")})
    except ValueError as error:
        raise AuditError("--rates must be a comma-separated integer list") from error
    if not rates or any(rate < 8000 or rate > 384000 for rate in rates):
        raise AuditError("sample rates must be between 8000 and 384000 Hz")
    if args.attenuation != "auto":
        try:
            attenuation: str | float = float(args.attenuation)
        except ValueError as error:
            raise AuditError("--attenuation must be 'auto' or a number") from error
    else:
        attenuation = "auto"

    source_root = args.source_root.resolve()
    declaration_path, declaration = load_declaration(source_root, args.declaration)
    geometry = require_name(declaration.get("geometry"), "geometry")
    design_id = require_name(declaration.get("design_id"), "design ID")
    description = str(declaration.get("description", "")).strip()
    if not description:
        raise AuditError("source declaration has no human-readable description")
    if design_id == "default":
        raise AuditError("'default' is reserved; use a revision-specific design ID")

    declared_artifacts = declaration["artifacts"]
    missing = [role for role in REQUIRED_ROLES if role not in declared_artifacts]
    if missing:
        raise AuditError(f"source declaration is missing artifacts: {', '.join(missing)}")
    roles = REQUIRED_ROLES + tuple(
        role for role in OPTIONAL_PREDICTION_ROLES if role in declared_artifacts)
    unknown = sorted(set(declared_artifacts) - set(roles))
    if unknown:
        raise AuditError(f"source declaration has unknown artifact roles: {', '.join(unknown)}")

    selected_paths = [args.declaration]
    project = declaration.get("project")
    if project is not None:
        if not isinstance(project, dict) or not project.get("path"):
            raise AuditError("project must contain a relative path when supplied")
        selected_paths.append(project["path"])
    for role in roles:
        item = declared_artifacts[role]
        if not isinstance(item, dict) or not item.get("path") or not item.get("sha256"):
            raise AuditError(f"artifact {role} must contain path and sha256")
        selected_paths.append(item["path"])
    if len(selected_paths) != len(set(selected_paths)):
        raise AuditError("the declaration assigns one source path to multiple roles")

    git_info = verify_git_sources(source_root, selected_paths)
    release = resolve_release(source_root, args.source_ref, args.allow_commit_ref)
    resolved_ref = release["commit"]
    if resolved_ref != git_info["head"]:
        raise AuditError(
            f"source checkout HEAD {git_info['head']} is not requested ref "
            f"{args.source_ref} ({resolved_ref})")

    artifacts: dict[str, dict] = {}
    for role in roles:
        declared = declared_artifacts[role]
        source = safe_source(source_root, declared["path"])
        actual_hash = sha256_file(source)
        if actual_hash != declared["sha256"]:
            raise AuditError(
                f"{role} hash differs from committed source declaration: "
                f"{actual_hash} != {declared['sha256']}")
        item = {
            "path": declared["path"],
            "bundle_path": f"source/{design_id}/{BUNDLE_NAMES[role]}",
            "sha256": actual_hash,
        }
        if role.endswith("_txt") or role.startswith("measurement_"):
            headers = parse_rew_txt(source)[0]
            for key in ("measurement", "smoothing"):
                declared_value = declared.get(key)
                actual_value = headers.get(key, "")
                if declared_value is not None and declared_value != actual_value:
                    raise AuditError(
                        f"{role} {key} header differs from declaration: "
                        f"{actual_value!r} != {declared_value!r}")
                item[key] = actual_value
        artifacts[role] = item

    source_project = None
    if project is not None:
        project_path = safe_source(source_root, project["path"])
        project_hash = sha256_file(project_path)
        if project_hash != project.get("sha256"):
            raise AuditError("optional project hash differs from source declaration")
        source_project = {
            "path": project["path"],
            "sha256": project_hash,
            "last_commit": run(
                ["git", "log", "-1", "--format=%H", "--", project["path"]], source_root),
        }

    filter_settings = declaration.get("filter", {})
    if "sample_rate" not in filter_settings or "delay_samples" not in filter_settings:
        raise AuditError("source declaration filter needs sample_rate and delay_samples")
    filter_settings = {
        "sample_rate": int(filter_settings["sample_rate"]),
        "delay_samples": int(filter_settings["delay_samples"]),
        "txt_to_wav_gain_db": filter_settings.get(
            "txt_to_wav_gain_db", {"left": 0.0, "right": 0.0}),
        "txt_wav_limits": filter_settings.get("txt_wav_limits", DEFAULT_LIMITS),
    }
    selector = f"@{design_id}"
    source = {
        "repository": git_info["repository"],
        "source_ref": args.source_ref,
        "repository_head": git_info["head"],
        "release": release,
        "declaration": {
            "path": args.declaration,
            "sha256": sha256_file(declaration_path),
            "git_blob": git_blob(source_root, args.declaration),
            "last_commit": run(
                ["git", "log", "-1", "--format=%H", "--", args.declaration], source_root),
        },
        "artifacts": artifacts,
        "traces": declaration.get("traces", {}),
        "lineage": declaration.get("lineage", []),
        "attestation": declaration.get("attestation", {}),
    }
    if source_project is not None:
        source["project"] = source_project

    recipe = {
        "schema": 1,
        "geometry": geometry,
        "design_id": design_id,
        "variant": design_id,
        "description": description,
        "audited_at": dt.date.today().isoformat(),
        "measurement": declaration.get("measurement", {}),
        "prediction": declaration.get("prediction", {
            "limits": {"max_rms_magnitude_db": 0.1, "max_rms_phase_deg": 1.0}
        }),
        "source": source,
        "filter": filter_settings,
        "runtime": {
            "selector": selector,
            "generate_configs": True,
            "format": "FLOAT64_LE",
            "attenuation_db": attenuation,
            "safety_margin_db": args.safety_margin,
            "rates": {
                str(rate): f"configs/{geometry}/brutefir-{rate}{selector}.conf.in"
                for rate in rates
            },
        },
    }

    recipe_path = ROOT / "filters" / geometry / "provenance" / f"{design_id}.source.json"
    existing_manifest = recipe_path.with_name(f"{design_id}.json")
    if args.write and recipe_path.exists():
        existing = json.loads(recipe_path.read_text(encoding="utf-8"))
        if existing != recipe and not args.replace_design:
            raise AuditError(
                f"design ID already has a different source recipe: {recipe_path}; "
                "choose a new ID or add --replace-design")
    elif args.write and existing_manifest.exists() and not args.replace_design:
        raise AuditError(f"design ID already has a manifest: {existing_manifest}")
    with tempfile.NamedTemporaryFile(
            prefix="omdrc-design-", suffix=".source.json", delete=False) as temp:
        deploy_recipe = Path(temp.name)
    atomic_json(recipe, deploy_recipe)

    command = [sys.executable, str(ROOT / "scripts/deploy_filter.py"),
               "--recipe", str(deploy_recipe), "--source-root", str(source_root)]
    if args.write:
        command.append("--write")
    if args.replace_design:
        command.append("--replace-runtime")
    try:
        result = subprocess.run(command)
        if result.returncode:
            raise AuditError(f"deployment audit failed with exit {result.returncode}")
        # The runtime manifest is deploy_filter.py's commit marker. Keep this
        # development-only recipe out of the repository until publication has
        # completed successfully as well.
        if args.write:
            atomic_json(recipe, recipe_path)
    finally:
        deploy_recipe.unlink(missing_ok=True)
    print(f"Design: {geometry}/{design_id} (runtime selector {selector})")
    print(f"Source declaration: {args.declaration} at {args.source_ref} -> {resolved_ref}")
    if args.write:
        print(f"Recipe: {recipe_path}")
        manifest = json.loads(existing_manifest.read_text(encoding="utf-8"))
        print_deployed_next(ROOT, [{
            "geometry": manifest["geometry"],
            "design_id": manifest.get("design_id", manifest["variant"]),
            "bundle_id": manifest["bundle_id"],
            "manifest": existing_manifest.relative_to(ROOT),
            "release": manifest["source"].get("release", {}),
            "source_commit": manifest["source"]["repository_head"],
        }], include_verification=True)
    else:
        print("No repository files written. Add --write after reviewing the audit.")
        write_command = [
            "python3", "scripts/new_filter_design.py",
            "--source-root", str(source_root),
            "--source-ref", args.source_ref,
            "--declaration", args.declaration,
            "--rates", args.rates,
            "--attenuation", args.attenuation,
            "--safety-margin", str(args.safety_margin),
        ]
        if args.allow_commit_ref:
            write_command.append("--allow-commit-ref")
        if args.replace_design:
            write_command.append("--replace-design")
        write_command.append("--write")
        print_dry_run_next(ROOT, write_command)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AuditError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
