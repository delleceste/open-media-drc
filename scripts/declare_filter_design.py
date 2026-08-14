#!/usr/bin/env python3
"""Create the source-side declaration that binds a filter design together.

The declaration is deliberately independent of REW. The designer supplies the
semantic roles; this tool records the exact hashes and exported TXT headers.
Commit the declaration and its inputs together, then create an annotated tag.
``new_filter_design.py`` accepts only that committed declaration by default.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import platform
import re
import shlex
import sys

import numpy as np

from deploy_filter import (
    AuditError, DEFAULT_LIMITS, atomic_json, build_analysis, detect_filter_alignment,
    load_filter_wav, parse_rew_txt, run, safe_source, sha256_file,
)
from filter_design_suggest import suggest as suggest_design_command


FILE_OPTIONS = {
    "measurement_left": "measurement_left",
    "measurement_right": "measurement_right",
    "measurement_sum": "measurement_sum",
    "filter_left_txt": "filter_left_txt",
    "filter_right_txt": "filter_right_txt",
    "filter_left_wav": "filter_left_wav",
    "filter_right_wav": "filter_right_wav",
    "corrected_left_txt": "corrected_left_txt",
    "corrected_right_txt": "corrected_right_txt",
    "corrected_sum_txt": "corrected_sum_txt",
}


class Console:
    """Small dependency-free, TTY-aware formatter for the audit workflow."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"

    def __init__(self, stream=None, color: bool | None = None):
        self.stream = stream or sys.stdout
        if color is None:
            color = (
                hasattr(self.stream, "isatty") and self.stream.isatty() and
                "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
            )
        self.color = color

    def styled(self, value: object, *styles: str) -> str:
        text = str(value)
        if not self.color or not styles:
            return text
        return "".join(styles) + text + self.RESET

    def line(self, value: str = "") -> None:
        print(value, file=self.stream, flush=True)

    def banner(self, geometry: str, design_id: str) -> None:
        self.line(self.styled("FILTER DESIGN DECLARATION", self.BOLD, self.BLUE))
        self.line(
            f"  {self.styled(geometry, self.BOLD)} / "
            f"{self.styled(design_id, self.BOLD)}")
        self.line()

    def stage(self, number: int, total: int, label: str) -> None:
        if number > 1:
            self.line()
        marker = self.styled(f"[{number}/{total}]", self.BOLD, self.CYAN)
        self.line(f"{marker} {self.styled(label, self.BOLD)}")

    def ok(self, message: str) -> None:
        self.line(f"  {self.styled('OK', self.BOLD, self.GREEN)}  {message}")

    def note(self, message: str) -> None:
        self.line(f"  {self.styled('->', self.CYAN)}  {message}")

    def heading(self, title: str) -> None:
        self.line()
        self.line(self.styled(title, self.BOLD, self.BLUE))

    def directory(self, path: Path) -> None:
        self.line(
            f"  {self.styled('Run from:', self.BOLD)} "
            f"{self.styled(path, self.BOLD, self.UNDERLINE)}")

    def command(self, argv: list[str] | str) -> None:
        rendered = argv if isinstance(argv, str) else shlex.join(argv)
        self.line(f"  {self.styled('$', self.GREEN, self.BOLD)} {rendered}")


CONSOLE = Console()
WORKFLOW_STEPS = 8


def _write_invocation() -> list[str]:
    """Reproduce the current command exactly, adding --write once."""
    command = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
    if "--write" not in command:
        command.append("--write")
    return command


def _cmake_configure_command(open_media_root: Path, geometry: str) -> list[str]:
    """Preserve cached geometry sets while ensuring this one is installed."""
    default_geometry = "flat"
    extra_geometries: list[str] = []
    cache = open_media_root / "build/CMakeCache.txt"
    if cache.is_file():
        for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("GEOMETRY:STRING="):
                default_geometry = line.partition("=")[2] or "flat"
            elif line.startswith("GEOMETRIES:STRING="):
                extra_geometries = [
                    item for item in line.partition("=")[2].split(";") if item
                ]
    command = ["cmake", "-S", ".", "-B", "build"]
    if geometry != default_geometry and geometry not in extra_geometries:
        extra_geometries.append(geometry)
        command.append(f"-DGEOMETRIES={';'.join(extra_geometries)}")
    return command


def _installed_omdrc(open_media_root: Path) -> str:
    """Resolve the wrapper path produced by the current CMake build."""
    prefix = "/usr/local"
    cache = open_media_root / "build/CMakeCache.txt"
    if cache.is_file():
        for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("CMAKE_INSTALL_PREFIX:PATH="):
                prefix = line.partition("=")[2] or prefix
                break
    return str(Path(prefix) / "bin/omdrc")


def print_next_steps(
        source_root: Path, relative_destination: Path, selected: list[str],
        geometry: str, design_id: str, wrote: bool) -> None:
    """Print a complete source-tag -> repository -> installed-system handoff."""
    open_media_root = Path(__file__).resolve().parents[1]
    tag = f"{geometry}-{design_id}"
    source_commit_message = f"Declare {geometry}/{design_id} filter design"
    deploy_commit_message = f"Deploy {geometry}/{design_id} filter design"

    CONSOLE.heading("NEXT")
    if not wrote:
        CONSOLE.line("1. Write the reviewed declaration")
        CONSOLE.directory(Path.cwd().resolve())
        CONSOLE.command(_write_invocation())
        CONSOLE.line()
        step = 2
    else:
        step = 1

    CONSOLE.line(f"{step}. Commit the source artifacts and create the annotated tag")
    CONSOLE.directory(source_root)
    CONSOLE.command(["cd", str(source_root)])
    CONSOLE.command(["git", "add", "--", str(relative_destination), *selected])
    CONSOLE.command(["git", "commit", "-m", source_commit_message])
    CONSOLE.command([
        "git", "tag", "-a", tag, "-m",
        f"room correction {geometry}/{design_id}",
    ])
    CONSOLE.command(["git", "push", "origin", "HEAD"])
    CONSOLE.command(["git", "push", "origin", tag])

    CONSOLE.line()
    CONSOLE.line(f"{step + 1}. Audit and publish the tagged design into open-media-drc")
    CONSOLE.directory(open_media_root)
    CONSOLE.command(["cd", str(open_media_root)])
    deployment = [
        sys.executable, "scripts/new_filter_design.py",
        "--source-root", str(source_root),
        "--source-ref", tag,
        "--declaration", str(relative_destination),
    ]
    CONSOLE.command(deployment)
    CONSOLE.note("Review that command's dry-run audit, then publish the immutable bundle:")
    CONSOLE.command([*deployment, "--write"])
    CONSOLE.command(["python3", "scripts/verify_filter_bundle.py", "--all", "--require-sources"])
    CONSOLE.command(["git", "status", "--short", "--", f"configs/{geometry}", f"filters/{geometry}"])
    CONSOLE.note("Confirm the listed changes belong only to this deployment before staging them.")
    CONSOLE.command(["git", "add", "--", f"configs/{geometry}", f"filters/{geometry}"])
    CONSOLE.command(["git", "commit", "-m", deploy_commit_message])

    CONSOLE.line()
    CONSOLE.line(f"{step + 2}. Install and select it (after reviewing the open-media-drc commit)")
    CONSOLE.directory(open_media_root)
    CONSOLE.command(_cmake_configure_command(open_media_root, geometry))
    CONSOLE.command(["cmake", "--build", "build"])
    CONSOLE.command("sudo cmake --install build")
    if platform.system() == "FreeBSD":
        CONSOLE.command("sudo service omdrcctrl restart")
    elif platform.system() == "Linux":
        CONSOLE.command("sudo systemctl restart omdrcctrl")
    installed_omdrc = _installed_omdrc(open_media_root)
    CONSOLE.command([installed_omdrc, "design", "--list"])
    CONSOLE.command([installed_omdrc, "design", f"@{design_id}"])
    CONSOLE.note("The web UI response page becomes available from the deployed manifest and analysis JSON.")


def valid_name(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise AuditError(f"invalid {label}: {value!r}")
    return value


def source_git_context(source_root: Path) -> dict[str, str]:
    """Inspect the source checkout without requiring pre-committed inputs.

    The declaration is intended to be committed together with newly exported
    artifacts, so requiring a clean checkout here would make that workflow
    impossible.  Publication later requires every declared input to be tracked,
    clean, hash-identical and reachable from the selected annotated tag.
    """
    top = Path(run(["git", "rev-parse", "--show-toplevel"], source_root)).resolve()
    if top != source_root:
        raise AuditError(f"--source-root must be the Git top level (got {top})")
    return {
        "head": run(["git", "rev-parse", "HEAD"], source_root),
        "repository": run(["git", "config", "--get", "remote.origin.url"], source_root),
    }


def main() -> int:
    if "--suggest-from-source-root" in sys.argv[1:]:
        discovery = argparse.ArgumentParser(
            description=(
                "Suggest a complete declaration command from the newest .mdat "
                "name and its sibling <stem>.txts directory. Read-only: the "
                ".mdat is never opened."))
        discovery.add_argument(
            "--suggest-from-source-root", required=True, type=Path,
            metavar="SOURCE_ROOT")
        return suggest_design_command(
            discovery.parse_args().suggest_from_source_root)

    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Run without --write first. The declaration destination is forced to "
            "omdrc-designs/<geometry>/<design-id>/design.json under --source-root. "
            "For a read-only candidate command, use --suggest-from-source-root."),
    )
    required = parser.add_argument_group("required role assignments")
    required.add_argument(
        "--source-root", required=True, type=Path,
        help="Git top-level directory containing all selected source artifacts")
    required.add_argument(
        "--geometry", required=True,
        help="physical geometry identifier, for example 120.blue")
    required.add_argument(
        "--design-id", required=True,
        help="new immutable design identifier; default is reserved")
    required.add_argument(
        "--description", required=True,
        help="human-readable description of this filter design")
    required.add_argument(
        "--measurement-left", required=True,
        help="REW TXT export of the original left measurement")
    required.add_argument(
        "--measurement-right", required=True,
        help="REW TXT export of the original right measurement")
    required.add_argument(
        "--measurement-sum", required=True,
        help="REW TXT export of the original L/R aggregate")
    required.add_argument(
        "--filter-left-txt", required=True,
        help="REW TXT frequency response of the designed left filter")
    required.add_argument(
        "--filter-right-txt", required=True,
        help="REW TXT frequency response of the designed right filter")
    required.add_argument(
        "--filter-left-wav", required=True,
        help="final deployable left filter impulse WAV")
    required.add_argument(
        "--filter-right-wav", required=True,
        help="final deployable right filter impulse WAV")
    required.add_argument(
        "--sum-mode",
        choices=("vector_average", "coherent_sum", "independent"),
        required=True,
        help="meaning of the selected measurement/corrected aggregate exports")

    optional = parser.add_argument_group("optional evidence and controls")
    optional.add_argument(
        "--suggest-from-source-root", type=Path, metavar="SOURCE_ROOT",
        help=("read-only discovery mode; newest .mdat names the sibling .txts "
              "directory used to suggest a complete command"))
    optional.add_argument(
        "--project", help="optional archived .mdat path; never opened")
    optional.add_argument("--corrected-left-txt")
    optional.add_argument("--corrected-right-txt")
    optional.add_argument("--corrected-sum-txt")
    optional.add_argument(
        "--note-fragment", action="append", default=[],
        help="room/setup text required in the timed measurement notes")
    optional.add_argument(
        "--lineage", action="append", default=[],
        help="human-readable design step; repeat in chronological order")
    optional.add_argument(
        "--write", action="store_true",
        help="write the declaration after the dry-run output has been reviewed")
    optional.add_argument(
        "--replace", action="store_true",
        help="explicitly replace an existing declaration path")
    args = parser.parse_args()

    CONSOLE.banner(args.geometry, args.design_id)
    CONSOLE.stage(1, WORKFLOW_STEPS, "Validate role assignments and destination")
    geometry = valid_name(args.geometry, "geometry")
    design_id = valid_name(args.design_id, "design ID")
    if design_id == "default":
        raise AuditError("'default' is reserved; choose a revision-specific design ID")
    source_root = args.source_root.resolve()
    relative_artifacts = {
        role: getattr(args, option)
        for role, option in FILE_OPTIONS.items()
        if getattr(args, option) is not None
    }
    selected = list(relative_artifacts.values())
    if args.project:
        selected.append(args.project)
    if len(selected) != len(set(selected)):
        raise AuditError("one source path was assigned to multiple roles")
    CONSOLE.ok(
        f"{len(relative_artifacts)} unique artifact roles; destination "
        f"omdrc-designs/{geometry}/{design_id}/design.json")

    CONSOLE.stage(2, WORKFLOW_STEPS, "Inspect the source Git checkout")
    git_info = source_git_context(source_root)
    CONSOLE.ok(
        f"Git top level {source_root} at commit {git_info['head'][:12]}")
    CONSOLE.note(f"remote {git_info['repository']}")

    CONSOLE.stage(3, WORKFLOW_STEPS, "Hash and parse the selected exports")
    artifacts: dict[str, dict] = {}
    parsed_txts: dict[str, tuple] = {}
    for role, relative in relative_artifacts.items():
        path = safe_source(source_root, relative)
        item = {"path": relative, "sha256": sha256_file(path)}
        if role.endswith("_txt") or role.startswith("measurement_"):
            parsed_txts[role] = parse_rew_txt(path)
            headers = parsed_txts[role][0]
            item.update({
                "measurement": headers.get("measurement", ""),
                "smoothing": headers.get("smoothing", ""),
            })
        artifacts[role] = item
        detail = item.get("measurement") or item.get("smoothing") or path.name
        CONSOLE.note(
            f"{role:<22} {relative}  sha256:{item['sha256'][:12]}  {detail}")
    CONSOLE.ok(f"parsed and hashed {len(artifacts)} artifacts")

    CONSOLE.stage(4, WORKFLOW_STEPS, "Validate measurement timing, grids and aggregate meaning")
    measurement_grids = [
        parsed_txts[role][1]
        for role in ("measurement_left", "measurement_right", "measurement_sum")
    ]
    if not all(np.array_equal(measurement_grids[0], grid)
               for grid in measurement_grids[1:]):
        raise AuditError("left, right and aggregate measurements need the same frequency grid")
    for role in ("measurement_left", "measurement_right"):
        headers = parsed_txts[role][0]
        if "acoustic timing reference" not in headers.get("format", "").lower():
            raise AuditError(f"{role} has no acoustic timing reference")
    aggregate_headers = parsed_txts["measurement_sum"][0]
    if args.sum_mode == "vector_average" and "vector average" not in (
            aggregate_headers.get("source", "") + " " +
            aggregate_headers.get("format", "")).lower():
        raise AuditError(
            "--sum-mode vector_average contradicts the aggregate TXT headers")
    if args.sum_mode == "vector_average" and "corrected_sum_txt" in parsed_txts:
        corrected_headers = parsed_txts["corrected_sum_txt"][0]
        corrected_kind = (
            corrected_headers.get("source", "") + " " +
            corrected_headers.get("format", "")).lower()
        if "vector average" not in corrected_kind:
            raise AuditError(
                "--sum-mode vector_average contradicts corrected aggregate TXT headers")
    CONSOLE.ok(
        f"L/R/aggregate share {len(measurement_grids[0]):,} frequency points; "
        f"sum mode is {args.sum_mode}")

    CONSOLE.stage(5, WORKFLOW_STEPS, "Relate filter-response TXT exports to impulse WAVs")
    wav_rates = []
    alignments: dict[str, dict] = {}
    for channel in ("left", "right"):
        wav_role = f"filter_{channel}_wav"
        txt_role = f"filter_{channel}_txt"
        rate, impulse = load_filter_wav(
            safe_source(source_root, relative_artifacts[wav_role]))
        _, freqs, magnitude, phase = parsed_txts[txt_role]
        alignment = detect_filter_alignment(
            freqs, magnitude, phase, rate, impulse)
        limits = DEFAULT_LIMITS
        metrics = alignment["metrics"]
        metrics["passed"] = (
            metrics["rms_magnitude_db"] <= limits["max_rms_magnitude_db"] and
            metrics["rms_phase_deg"] <= limits["max_rms_phase_deg"] and
            metrics["above_100_hz_max_magnitude_db"] <=
            limits["above_100_hz_max_magnitude_db"] and
            metrics["above_100_hz_max_phase_deg"] <=
            limits["above_100_hz_max_phase_deg"]
        )
        if not metrics["passed"]:
            raise AuditError(
                f"{channel} filter TXT/WAV cannot be related by one fixed "
                f"delay and gain: {metrics}")
        alignments[channel] = alignment
        wav_rates.append(rate)
        CONSOLE.note(
            f"{channel:<5} {rate:,} Hz  delay {alignment['delay_samples']} samples  "
            f"gain {alignment['txt_to_wav_gain_db']:+.4f} dB  "
            f"RMS {metrics['rms_magnitude_db']:.4f} dB / "
            f"{metrics['rms_phase_deg']:.3f}°")
    if wav_rates[0] != wav_rates[1]:
        raise AuditError(f"left/right filter WAV rates differ: {wav_rates}")
    delays = {item["delay_samples"] for item in alignments.values()}
    if len(delays) != 1:
        raise AuditError(
            "left/right filter WAV causal delays differ: "
            + ", ".join(f"{key}={value['delay_samples']}" for key, value in alignments.items()))
    delay_samples = delays.pop()
    CONSOLE.ok(
        f"both channels pass TXT/WAV residual limits at {wav_rates[0]:,} Hz "
        f"with common delay {delay_samples} samples")

    CONSOLE.stage(6, WORKFLOW_STEPS, "Assemble the provenance declaration")
    declaration = {
        "schema": 1,
        "geometry": geometry,
        "design_id": design_id,
        "declared_at": dt.date.today().isoformat(),
        "description": args.description,
        "attestation": {
            "statement": (
                "The designer declares that the named measurement exports, filter-response "
                "exports and filter impulse WAVs are the inputs and outputs of this design."
            ),
            "source_head_before_declaration_commit": git_info["head"],
        },
        "measurement": {"required_note_fragments": args.note_fragment},
        "artifacts": artifacts,
        "lineage": args.lineage,
        "filter": {
            "sample_rate": wav_rates[0],
            "delay_samples": delay_samples,
            "txt_to_wav_gain_db": {
                channel: round(float(alignment["txt_to_wav_gain_db"]), 6)
                for channel, alignment in alignments.items()
            },
            "detected_alignment": alignments,
            "txt_wav_limits": DEFAULT_LIMITS,
        },
        "prediction": {
            "sum_mode": args.sum_mode,
            "limits": {"max_rms_magnitude_db": 0.1, "max_rms_phase_deg": 1.0}
        },
    }
    if args.project:
        project = safe_source(source_root, args.project)
        declaration["project"] = {"path": args.project, "sha256": sha256_file(project)}
        CONSOLE.note(f"archival project recorded by hash only: {args.project}")
    else:
        CONSOLE.note("no .mdat dependency; exported artifacts are the trust boundary")
    CONSOLE.ok("semantic roles, hashes, alignment and designer attestation assembled")

    CONSOLE.stage(7, WORKFLOW_STEPS, "Calculate predicted responses and cross-check exports")
    preflight_recipe = {
        "geometry": geometry,
        "variant": design_id,
        "measurement": declaration["measurement"],
        "prediction": declaration["prediction"],
        "filter": declaration["filter"],
        "source": {"artifacts": artifacts},
    }
    source_paths = {
        role: safe_source(source_root, relative)
        for role, relative in relative_artifacts.items()
    }
    analysis, _ = build_analysis(preflight_recipe, source_paths)
    declaration["preflight"] = {
        "calculation": analysis["calculation"],
        "validation": analysis["validation"],
    }
    independent = analysis["validation"].get("independent_corrected_exports", {})
    CONSOLE.ok(
        "deterministic L/R/filter convolution completed"
        + (f"; {len(independent)} independent corrected export checks passed"
           if independent else "; no independent corrected export was supplied"))
    CONSOLE.note(
        "SoX has NOT run in this declaration preflight. Per-rate FLOAT64_LE "
        "generation runs visibly in the tagged deployment step "
        "(new_filter_design.py -> deploy_filter.py).")

    CONSOLE.stage(8, WORKFLOW_STEPS, "Write declaration" if args.write else "Preview declaration")
    relative_destination = Path(
        "omdrc-designs") / geometry / design_id / "design.json"
    destination = source_root / relative_destination
    if args.write:
        if destination.exists() and not args.replace:
            raise AuditError(f"declaration already exists: {destination}; use --replace explicitly")
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(declaration, destination)
        CONSOLE.ok(f"WROTE {destination}")
    else:
        CONSOLE.heading("DECLARATION PREVIEW")
        CONSOLE.line(json.dumps(declaration, indent=2, ensure_ascii=False))
        CONSOLE.line()
        CONSOLE.note("DRY RUN: no files were written.")
    print_next_steps(
        source_root, relative_destination, selected,
        geometry, design_id, args.write)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AuditError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        errors = Console(sys.stderr)
        print(
            f"{errors.styled('FAIL', errors.BOLD, errors.RED)}: {error}",
            file=sys.stderr)
        raise SystemExit(1)
