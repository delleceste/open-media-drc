#!/usr/bin/env python3
"""Remove one deployed room-correction design, completely.

    python3 scripts/remove_filter_design.py 120.blue@rscreen-20260812

A design is spread over the room's tree: a manifest, an analysis file, verbatim
copies of the exports it plots, one coefficient pair per sample rate, and one
BruteFIR config template per sample rate.  Deleting some of those leaves a
design that is half gone --- still listed by ``drc.sh design --list``, still
offered by the web remote, and no longer verifiable.  This removes all of it, in
an order that never leaves a manifest pointing at files that are already gone,
and records the removal in the room's history beside the deployment it undoes.

The reserved ``default`` design is refused: its un-suffixed configs and
coefficients are what a geometry falls back to, so removing it would take the
geometry with it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

from console_ui import CONSOLE, confirm, fail, human_bytes, relative, report_error
from deploy_filter import (
    AuditError, ROOT, add_site_root_argument, git_commit_paths, git_maybe,
    git_toplevel, resolve_site_root,
)
from filter_workflow_next import cmake_configure_command


WORKFLOW_STEPS = 5


def parse_selector(value: str) -> tuple[str, str]:
    geometry, separator, design_id = value.partition("@")
    if not separator:
        raise fail(
            f"{value!r} does not name a design",
            "use <geometry>@<design-id>, for example 120.blue@rscreen-20260812",
            "run with --list to see what is deployed")
    if not geometry or not design_id:
        raise fail(f"{value!r} does not name a design: both sides of @ are required")
    if design_id == "default":
        raise fail(
            "'default' is the geometry's base filter set, not a removable design",
            "its un-suffixed configs and coefficients are what a geometry falls",
            "back to; removing them would leave the geometry unusable",
            "remove the whole geometry by hand if that is really what you want")
    return geometry, design_id


def deployed_designs(site_root: Path) -> dict[str, list[str]]:
    """Every geometry's removable designs, read from the manifests on disk."""
    designs: dict[str, list[str]] = {}
    for manifest in sorted(site_root.glob("filters/*/provenance/*.json")):
        if manifest.name.endswith(".source.json"):
            continue
        geometry = manifest.parent.parent.name
        design_id = manifest.stem
        if design_id != "default":
            designs.setdefault(geometry, []).append(design_id)
    return designs


def owned_paths(site_root: Path, geometry: str, design_id: str) -> list[Path]:
    """Everything on disk that belongs to this design and to nothing else.

    Directories that a design owns outright are returned as directories; shared
    ones (a bare ``<rate>/`` holding the default set) never appear here.
    """
    geometry_root = site_root / "filters" / geometry
    config_root = site_root / "configs" / geometry
    found: list[Path] = []
    for candidate in (
        geometry_root / "provenance" / f"{design_id}.json",
        geometry_root / "provenance" / f"{design_id}.source.json",
        geometry_root / "analysis" / f"{design_id}.json",
        geometry_root / "source" / design_id,
    ):
        if candidate.exists():
            found.append(candidate)
    for rate_dir in sorted(geometry_root.glob("[0-9]*")):
        design_dir = rate_dir / f"@{design_id}"
        if design_dir.is_dir():
            found.append(design_dir)
    for pattern in (f"brutefir-*@{design_id}.conf.in", f"brutefir-*@{design_id}.conf"):
        found.extend(sorted(path for path in config_root.glob(pattern) if path.is_file()))
    return found


def tree_size(path: Path) -> tuple[int, int]:
    """(file count, bytes) for a file or a directory."""
    if path.is_file():
        return 1, path.stat().st_size
    count = total = 0
    for item in path.rglob("*"):
        if item.is_file():
            count += 1
            total += item.stat().st_size
    return count, total


def describe(console, site_root: Path, geometry: str, design_id: str) -> dict | None:
    """What this design claims to be, read from its manifest while it exists."""
    path = site_root / "filters" / geometry / "provenance" / f"{design_id}.json"
    if not path.is_file():
        console.warn(
            "no manifest: this design was never fully published, or was already "
            "partly removed")
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    console.note(f"{'description':<14} {manifest.get('description', design_id)}")
    console.line(f"      {'bundle':<14} {manifest.get('bundle_id', '?')}")
    console.line(f"      {'deployed':<14} "
                 f"{manifest.get('verification', {}).get('audited_at', '?')}")
    source = manifest.get("source", {})
    project = source.get("project") or {}
    session = source.get("measurements") or {}
    if project.get("name"):
        console.line(
            f"      {'project':<14} {project['name']}"
            + (f" @ {project['commit'][:12]}" if project.get("commit") else ""))
    if session.get("file"):
        console.line(f"      {'session':<14} {session['file']}  "
                     f"sha256 {session.get('sha256', '')[:16]}")
    rates = sorted(manifest.get("runtime", {}).get("rates", {}), key=int)
    if rates:
        console.line(f"      {'rates':<14} " + ", ".join(f"{int(r):,}" for r in rates))
    return manifest


def survivors(site_root: Path, geometry: str, removed: str) -> tuple[list[str], bool]:
    """What the geometry would still offer, so an empty room is not a surprise."""
    remaining = [item for item in deployed_designs(site_root).get(geometry, [])
                 if item != removed]
    geometry_root = site_root / "filters" / geometry
    has_default = any((rate_dir / "L.raw").is_file()
                      for rate_dir in geometry_root.glob("[0-9]*"))
    return remaining, has_default


def removal_message(geometry: str, design_id: str, manifest: dict | None,
                    paths: list[Path], site_root: Path) -> str:
    lines = [
        f"Remove {geometry} @{design_id}",
        "",
        f"Geometry:     {geometry}",
        f"Design:       {design_id}",
    ]
    if manifest:
        lines.append(f"Bundle:       {manifest.get('bundle_id', '?')}")
        project = (manifest.get("source") or {}).get("project") or {}
        session = (manifest.get("source") or {}).get("measurements") or {}
        if project.get("name"):
            lines.append(
                f"Project:      {project['name']}"
                + (f" @ {project['commit'][:12]}" if project.get("commit") else ""))
        if session.get("file"):
            lines.append(f"Measurements: {session['file']} "
                         f"sha256 {session.get('sha256', '')[:16]}")
    lines.append("")
    lines.append("Removed:")
    lines += [f"  {relative(path, site_root)}" for path in paths]
    lines.append("")
    lines.append("The deployment commit that introduced this design still holds every")
    lines.append("byte of it: git log --diff-filter=D -- the paths above.")
    return "\n".join(lines) + "\n"


def next_steps(console, site_root: Path, geometry: str, remaining: list[str]) -> None:
    console.heading("NEXT")
    console.note("reinstall so the removal reaches the playback machine")
    console.command(cmake_configure_command(ROOT, [geometry]))
    console.command("make -C build && sudo make -C build install")
    console.note("then confirm what the room offers")
    console.command("omdrc design --list")
    if remaining:
        console.note("designs still deployed for this geometry: "
                     + ", ".join(f"@{item}" for item in remaining))
    else:
        console.warn(
            f"{geometry} now has no immutable design left; it falls back to the "
            "un-suffixed default set")
    console.note(
        "to bring this design back, check out the commit that deployed it: "
        f"git -C {site_root} log --oneline -- filters/{geometry}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("design", nargs="?", metavar="GEOMETRY@DESIGN-ID",
                        help="the design to remove, as shown by 'omdrc design --list'")
    parser.add_argument("--list", action="store_true",
                        help="show every removable design and exit")
    parser.add_argument("--no-commit", dest="commit", action="store_false",
                        help="do not record the removal in the room repository")
    parser.add_argument("--dry-run", action="store_true",
                        help="report exactly what would be deleted, then stop")
    parser.add_argument("--yes", action="store_true",
                        help="skip the confirmation prompt")
    parser.add_argument("--live", action="store_true",
                        help="the site is the runtime tree; do not print CMake install steps")
    add_site_root_argument(parser)
    args = parser.parse_args()
    site_root = resolve_site_root(args.site_root)

    if args.list or not args.design:
        designs = deployed_designs(site_root)
        CONSOLE.heading(f"REMOVABLE DESIGNS UNDER {site_root}")
        if not designs:
            CONSOLE.note("none")
        for geometry, items in sorted(designs.items()):
            for design_id in sorted(items):
                CONSOLE.note(f"{geometry}@{design_id}")
        if not args.list:
            raise fail("name the design to remove, as <geometry>@<design-id>")
        return 0

    geometry, design_id = parse_selector(args.design)
    CONSOLE.banner("REMOVE ROOM CORRECTION DESIGN", geometry, design_id, site_root)

    CONSOLE.stage(1, WORKFLOW_STEPS, "Find everything this design owns")
    paths = owned_paths(site_root, geometry, design_id)
    if not paths:
        designs = deployed_designs(site_root)
        raise fail(
            f"nothing deployed for {geometry}@{design_id} under {site_root}",
            *([f"{geo}@{item}" for geo, items in sorted(designs.items())
               for item in sorted(items)] or ["no designs are deployed at all"]))
    files = sum(tree_size(path)[0] for path in paths)
    total = sum(tree_size(path)[1] for path in paths)
    CONSOLE.ok(f"{len(paths)} entries, {files} files, {human_bytes(total)}")

    CONSOLE.stage(2, WORKFLOW_STEPS, "Read what is about to be removed")
    manifest = describe(CONSOLE, site_root, geometry, design_id)

    CONSOLE.stage(3, WORKFLOW_STEPS, "Report")
    CONSOLE.heading("WHAT WILL BE DELETED")
    for path in paths:
        count, size = tree_size(path)
        marker = "/" if path.is_dir() else " "
        CONSOLE.line(f"      {relative(path, site_root)}{marker:<2} "
                     f"{CONSOLE.styled(f'{count} file(s), {human_bytes(size)}', CONSOLE.DIM)}")
    remaining, has_default = survivors(site_root, geometry, design_id)
    if not remaining and not has_default:
        CONSOLE.warn(
            f"{geometry} would be left with no filters at all: no other design and "
            "no un-suffixed default set")
    elif not remaining:
        CONSOLE.note(f"{geometry} would fall back to its un-suffixed default set")
    else:
        CONSOLE.note("still deployed afterwards: "
                     + ", ".join(f"@{item}" for item in sorted(remaining)))
    CONSOLE.warn(
        "if this design is running now, BruteFIR keeps playing until it is "
        "restarted on another one")

    CONSOLE.stage(4, WORKFLOW_STEPS, "Confirm")
    if args.dry_run:
        CONSOLE.note("--dry-run: nothing was deleted")
        return 0
    if not confirm(CONSOLE, f"Remove {geometry}@{design_id}?", args.yes):
        CONSOLE.note("nothing was deleted")
        return 1

    CONSOLE.stage(5, WORKFLOW_STEPS, "Remove")
    message = removal_message(geometry, design_id, manifest, paths, site_root)
    # The manifest goes first: while it exists it is the claim that every other
    # file is present, and nothing should ever read it after that stops being
    # true.  Once it is gone the design is already invisible to the web remote.
    for path in sorted(paths, key=lambda item: 0 if item.suffix == ".json" and
                       item.parent.name == "provenance" else 1):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        CONSOLE.note(f"removed {relative(path, site_root)}")
    CONSOLE.ok(f"{geometry}@{design_id} removed from {site_root}")

    if args.commit:
        record_removal(CONSOLE, site_root, geometry, message)
    if args.live:
        CONSOLE.heading("NEXT")
        CONSOLE.note("live runtime removal complete; the remaining designs are available now")
    else:
        next_steps(CONSOLE, site_root, geometry, sorted(remaining))
    return 0


def record_removal(console, site_root: Path, geometry: str, message: str) -> None:
    """Commit the deletion where the room keeps its history."""
    repo = git_toplevel(site_root)
    if repo is None:
        console.warn(
            f"{site_root} is not a Git work tree, so these files are simply gone; "
            "there is no commit to return to")
        return
    tracked = [item for item in (f"filters/{geometry}", f"configs/{geometry}")
               if (repo / item).exists() or git_maybe(repo, "ls-files", "--", item)]
    result = git_commit_paths(repo, tracked, message)
    if result["status"] == "committed":
        console.ok(f"room history: {result['commit'][:12]} {result['subject']}")
    elif result["status"] == "unchanged":
        console.note("room history: nothing changed for Git to record")
    else:
        console.warn("room history: commit failed: " + result.get("error", ""))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\ninterrupted; nothing was deleted", file=sys.stderr)
        raise SystemExit(1)
    except (AuditError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        report_error(error)
        raise SystemExit(1)
