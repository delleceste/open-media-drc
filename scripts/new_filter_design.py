#!/usr/bin/env python3
"""Deploy one room-correction design from a directory of REW text exports.

Point this at the directory REW exported into and nothing else::

    python3 scripts/new_filter_design.py ../DRC/DRC-120.blue/120.blue.Rscreen.txts

The file names carry the meaning, so no command line flag has to assert what a
file is.  Every curve the web remote draws is one of these exports, plotted
exactly as REW wrote it -- nothing is averaged, summed, convolved or smoothed
along the way.

Required names (``.txt`` optional, case-insensitive).  A bare name is the room
as it is; a suffix says what was done to it:

    L.txt  R.txt                    measured left/right, before correction
    LR.txt  or  L+R.txt             measured pair, before correction.  LR is
                                    REW's vector average, L+R is the sum.
                                    Exactly one of the two, never both.
    FLX-trimmed.txt                 exported response of the left filter
    FRX-trimmed.txt                 exported response of the right filter
    FLX-trimmed-<rate>k.wav         deployable left impulse, e.g. -48k.wav
    FRX-trimmed-<rate>k.wav         deployable right impulse
    L.filtered.txt  R.filtered.txt  REW's filtered result: the measurement with
                                    the filter applied to it
    LR.filtered.txt                 the filtered pair, with LR.txt
    L+R.filtered.txt                the filtered pair, with L+R.txt
    L+R.remeasured.txt              instead of L+R.filtered.txt: the room
                                    measured again with the DRC running.  Always
                                    a sum, because both speakers played.

All eight text exports must be unsmoothed and must come from a measurement at
48 kHz or below; a smoothed curve cannot be un-smoothed later, and a wider
measurement describes a band the deployed 48 kHz filters were never designed
against.  Each filter TXT is additionally proved to be the exported response of
the WAV that becomes the runtime coefficients.  Any of these failing stops the
run before a byte is written.

The measurements are bound to their origin as well as to their bytes: the
``.mdat`` REW exported them from is hashed into the manifest, and the project
that produced them is named by its Git commit.  Each deployment is then one
commit in the room's own repository, so ``git log`` there is the history of
every filter set that was ever live.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import shlex
import sys

from deploy_filter import (
    ARTIFACT_ROLES, AuditError, DEFAULT_LIMITS, MAX_EXPORT_FREQUENCY_HZ,
    MAX_MEASUREMENT_RATE_HZ, TRACE_ROLES, add_site_root_argument, aggregate_labels,
    atomic_json, commit_site, detect_filter_alignment, export_defects, git_blobs,
    git_project, git_relative, git_toplevel, git_uncommitted, load_filter_wav,
    parse_rew_txt, publish_bundle, resolve_site_root, sha256_file,
)
from console_ui import CONSOLE, Console, confirm, fail, report_error
from filter_workflow_next import print_deployed_next


NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")

# role -> accepted base names, in preference order.  A trailing ".txt" is
# optional on every one of them and matching ignores case, so REW's own
# "L.Filtered.txt" and a hand-typed "l.filtered" name the same thing.
TXT_NAMES = {
    "original_left":   ("L",),
    "original_right":  ("R",),
    "filter_left":     ("FLX-trimmed",),
    "filter_right":    ("FRX-trimmed",),
    "corrected_left":  ("L.filtered",),
    "corrected_right": ("R.filtered",),
}
# The aggregate pair: the original's name decides the design's aggregate style,
# and each style accepts only its own corrected companion.  A vector average
# has no re-measured form -- you cannot play one speaker and hear both -- so
# "L+R.remeasured" belongs to the sum style alone.
AGGREGATE_NAMES = {
    "LR":  {"original": ("LR",), "corrected": ("LR.filtered",)},
    "L+R": {"original": ("L+R",),
            "corrected": ("L+R.filtered", "L+R.remeasured", "L+R.measured")},
}
REMEASURED_NAMES = ("l+r.remeasured", "l+r.measured")
WAV_PATTERNS = {
    "filter_left_wav":  (re.compile(r"^FLX-trimmed-(\d+)k\.wav$", re.IGNORECASE),
                         "FLX-trimmed-<rate>k.wav, for example FLX-trimmed-48k.wav"),
    "filter_right_wav": (re.compile(r"^FRX-trimmed-(\d+)k\.wav$", re.IGNORECASE),
                         "FRX-trimmed-<rate>k.wav, for example FRX-trimmed-48k.wav"),
}
ROLE_TITLES = {
    "original_left": "measured L",
    "original_right": "measured R",
    "original_sum": "measured aggregate",
    "filter_left": "filter FLX response",
    "filter_right": "filter FRX response",
    "corrected_left": "corrected L",
    "corrected_right": "corrected R",
    "corrected_sum": "corrected aggregate",
    "filter_left_wav": "left impulse WAV",
    "filter_right_wav": "right impulse WAV",
}


WORKFLOW_STEPS = 6


def valid_name(value: str, label: str) -> str:
    if not NAME_PATTERN.fullmatch(value):
        raise fail(f"{label} {value!r} is not usable in a file name")
    return value


def design_identity(directory: Path, geometry: str | None = None,
                    design_id: str | None = None) -> tuple[str, str]:
    """Read geometry and design ID out of the directory path itself.

    REW exports into ``<session>.txts`` beside the ``<session>.mdat``, inside a
    project directory named for the geometry, so
    ``DRC-120.blue/120.blue.Rscreen.txts`` reads as geometry ``120.blue``,
    design ``Rscreen``.  A single ``<geometry>@<design-id>`` directory and a
    nested ``<geometry>/<design-id>/`` pair are read the same way, and
    ``--geometry`` / ``--design`` override whatever the path suggests.
    """
    name = directory.name
    stem = name[:-len(".txts")] if name.lower().endswith(".txts") else name
    if not stem:
        raise fail(f"cannot read a design name from {directory}")
    if "@" in stem:
        auto_geometry, _, auto_design = stem.partition("@")
    else:
        auto_geometry = re.sub(r"^drc[-_.]", "", directory.parent.name, flags=re.IGNORECASE)
        auto_design = stem
        for separator in (".", "-", "_"):
            prefix = f"{auto_geometry}{separator}".lower()
            if auto_geometry and auto_design.lower().startswith(prefix) and \
                    len(auto_design) > len(prefix):
                auto_design = auto_design[len(prefix):]
                break
    geometry = geometry or auto_geometry
    design_id = design_id or auto_design
    if not geometry or not design_id:
        raise fail(
            f"cannot read a geometry and design ID from {directory}",
            "name the export directory <geometry>.<session>.txts inside the",
            "project directory, or pass --geometry and --design")
    geometry = valid_name(geometry, "geometry")
    design_id = valid_name(design_id, "design ID")
    if design_id == "default":
        raise fail("'default' is reserved; use a revision-specific design ID")
    return geometry, design_id


def candidate_names(base: str) -> tuple[str, ...]:
    return (base.lower(), f"{base.lower()}.txt")


def resolve_one(entries: dict[str, list[Path]], role: str,
                bases: tuple[str, ...]) -> Path:
    """Find the single file naming `role`, or say precisely what is wrong."""
    found: list[Path] = []
    for base in bases:
        for name in candidate_names(base):
            found.extend(entries.get(name, []))
    if not found:
        wanted = " or ".join(f"{base}.txt" for base in bases)
        raise fail(f"no file for the {ROLE_TITLES[role]}: expected {wanted}")
    if len(found) > 1:
        raise fail(
            f"more than one file could be the {ROLE_TITLES[role]}",
            *(f"{path.name}" for path in sorted(found)),
            "these say different things about the same curve; keep exactly one")
    return found[0]


def resolve_wav(entries: dict[str, list[Path]], role: str) -> tuple[Path, int]:
    pattern, example = WAV_PATTERNS[role]
    found = [path for paths in entries.values() for path in paths
             if pattern.match(path.name)]
    if not found:
        raise fail(
            f"no file for the {ROLE_TITLES[role]}: expected {example}")
    if len(found) > 1:
        raise fail(
            f"more than one file could be the {ROLE_TITLES[role]}",
            *(f"{path.name}" for path in sorted(found)),
            "keep exactly one sample rate in this directory")
    return found[0], int(pattern.match(found[0].name).group(1)) * 1000


def discover(directory: Path) -> tuple[dict[str, Path], dict[str, str], int, list[str]]:
    """Map every required role onto exactly one file in `directory`."""
    if not directory.is_dir():
        raise fail(f"not a directory: {directory}")
    entries: dict[str, list[Path]] = {}
    for path in sorted(directory.iterdir()):
        if path.is_file() and not path.is_symlink():
            entries.setdefault(path.name.lower(), []).append(path)

    styles = [style for style, names in AGGREGATE_NAMES.items()
              if any(name in entries
                     for base in names["original"] for name in candidate_names(base))]
    if not styles:
        raise fail(
            "no measured aggregate: nothing says what the pair did before correction",
            "expected LR.txt (REW vector average) or L+R.txt (sum)")
    if len(styles) > 1:
        raise fail(
            "both LR.txt and L+R.txt are present",
            "a design shows either the vector average or the sum, not both",
            "keep the one this correction was designed around")
    style = styles[0]

    paths = {role: resolve_one(entries, role, bases)
             for role, bases in TXT_NAMES.items()}
    paths["original_sum"] = resolve_one(
        entries, "original_sum", AGGREGATE_NAMES[style]["original"])
    corrected_bases = AGGREGATE_NAMES[style]["corrected"]
    other_style = "L+R" if style == "LR" else "LR"
    for base in AGGREGATE_NAMES[other_style]["corrected"]:
        for name in candidate_names(base):
            if name in entries:
                raise fail(
                    f"{entries[name][0].name} does not belong with {style}.txt",
                    f"{style}.txt is "
                    + ("REW's vector average" if style == "LR" else "the L+R sum")
                    + f", {base}.txt is "
                    + ("REW's vector average" if other_style == "LR" else "a sum"),
                    "one is not the corrected form of the other; they cannot be",
                    "plotted against each other.  With "
                    f"{style}.txt the corrected aggregate must be "
                    f"{' or '.join(corrected_bases[:2])}.txt")
    paths["corrected_sum"] = resolve_one(entries, "corrected_sum", corrected_bases)

    rates = {}
    for role in WAV_PATTERNS:
        paths[role], rates[role] = resolve_wav(entries, role)
    if rates["filter_left_wav"] != rates["filter_right_wav"]:
        raise fail(
            "the two impulse WAVs name different sample rates: "
            f"{rates['filter_left_wav']} vs {rates['filter_right_wav']} Hz")

    corrected_name = paths["corrected_sum"].name.lower()
    remeasured = any(corrected_name.startswith(base) for base in REMEASURED_NAMES)
    notes: list[str] = []
    if corrected_name.startswith("l+r.measured"):
        notes.append(
            f"{paths['corrected_sum'].name} is read as the room measured again "
            "after correction; prefer L+R.remeasured.txt, because '.measured' "
            "reads like an uncorrected measurement")
    aggregate = {"style": style,
                 "corrected": "remeasured" if remeasured else "filtered"}
    return paths, aggregate, rates["filter_left_wav"], notes


def find_mdat(directory: Path, explicit: Path | None) -> Path:
    """The REW file these exports came out of.

    Every txt in the directory is a view of one measurement session; the
    ``.mdat`` is that session.  Hashing it into the manifest is what makes a
    deployed filter set traceable back to the sweeps that produced it.
    """
    if explicit is not None:
        path = explicit.expanduser().resolve()
        if not path.is_file():
            raise fail(f"--mdat is not a file: {path}")
        return path
    inside = sorted(path for path in directory.iterdir()
                    if path.is_file() and path.suffix.lower() == ".mdat")
    if len(inside) == 1:
        return inside[0]
    if len(inside) > 1:
        raise fail(
            f"{len(inside)} .mdat files sit in the export directory",
            *(path.name for path in inside),
            "name the session these exports came from with --mdat <file>")
    stems = []
    if directory.name.lower().endswith(".txts"):
        stems.append(directory.name[:-len(".txts")])
    stems.append(directory.name)
    for stem in stems:
        candidate = directory.parent / f"{stem}.mdat"
        if candidate.is_file():
            return candidate
    siblings = sorted(path.name for path in directory.parent.glob("*.mdat"))
    raise fail(
        "cannot tell which REW .mdat these exports were taken from",
        f"expected {stems[0]}.mdat beside the export directory, or one .mdat",
        "inside it",
        *([f"found in {directory.parent}:"] + [f"  {name}" for name in siblings[:12]]
          if siblings else []),
        "name it with --mdat <file>")


def source_provenance(console: Console, directory: Path, paths: dict[str, Path],
                      mdat: Path, *, allow_uncommitted: bool) -> tuple[dict, dict]:
    """Name the project that produced these files, and prove they survive.

    A content hash says the bundle plots what it claims.  It cannot say where
    those numbers came from or how to get the session back.  A commit that
    holds every export and the .mdat can, so a deployment refuses to proceed
    until one exists.
    """
    files = sorted({*(paths[role] for role in ARTIFACT_ROLES), mdat})
    record = {
        "name": directory.parent.name,
        "repository": str(directory.parent),
        "remote": "", "branch": "", "commit": "", "committed_at": "", "subject": "",
        "path": directory.name,
        "clean": False,
        "uncommitted": [],
    }
    repo = git_toplevel(directory)
    if repo is None:
        if not allow_uncommitted:
            raise fail(
                f"{directory} is not inside a Git repository",
                "nothing would link the deployed filters back to the project and",
                "the measurements that produced them.  Either put the project",
                "under version control:",
                f"  git -C {directory.parent} init",
                f"  git -C {directory.parent} add -A && "
                f"git -C {directory.parent} commit -m 'measurements'",
                "or re-run with --allow-uncommitted to publish an unanchored bundle")
        console.warn(
            "not a Git work tree: this bundle cannot name the project it came from")
        record["measurements_path"] = str(mdat)
        return record, {}

    record.update(git_project(repo))
    record["path"] = git_relative(repo, directory)
    relatives = [git_relative(repo, path) for path in files]
    uncommitted = git_uncommitted(repo, relatives)
    blobs = git_blobs(repo, relatives)
    missing = [item for item in relatives if item not in blobs and item not in uncommitted]
    record["uncommitted"] = sorted(uncommitted + missing)
    record["clean"] = not record["uncommitted"]
    if record["uncommitted"]:
        if not allow_uncommitted:
            quoted = " ".join(shlex.quote(item) for item in record["uncommitted"][:8])
            raise fail(
                f"{len(record['uncommitted'])} of these files are not committed in "
                f"{repo.name}",
                *(f"  {item}" for item in record["uncommitted"][:12]),
                "the manifest names a commit so the exports and the .mdat can be",
                "read back exactly as deployed; uncommitted bytes cannot.  Run:",
                f"  git -C {repo} add -- {quoted}",
                f"  git -C {repo} commit -m 'measurements for {directory.name}'",
                "or re-run with --allow-uncommitted, which records the gap in the "
                "manifest")
        console.warn(
            f"{len(record['uncommitted'])} file(s) are not committed; the manifest "
            "will say so and the exports may become unrecoverable")
    return record, blobs


def _relative_to(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return ""


def measurement_record(mdat: Path, project: dict, blobs: dict[str, str]) -> dict:
    """Identity of the REW session, enough to fetch the .mdat back."""
    repository = project.get("repository", "")
    relative = ""
    if repository:
        try:
            relative = mdat.resolve().relative_to(Path(repository)).as_posix()
        except ValueError:
            relative = ""
    return {
        "file": mdat.name,
        "path": relative or str(mdat),
        "sha256": sha256_file(mdat),
        "bytes": mdat.stat().st_size,
        "git_blob": blobs.get(relative, ""),
    }


def inspect(paths: dict[str, Path], named_rate: int) -> dict:
    """Parse and check every export, then relate each filter TXT to its WAV.

    Three refusals live here, and each of them would otherwise become a silent
    property of a deployed bundle: a smoothed export, a measurement wider than
    48 kHz, and a filter TXT that is not the exported response of the impulse
    about to become the runtime coefficients.
    """
    parsed = {role: parse_rew_txt(paths[role]) for role in TRACE_ROLES}
    defects = export_defects(parsed)
    if defects:
        raise fail(
            f"{len({role for role, _ in defects})} of the eight exports cannot be "
            "deployed",
            *(f"{paths[role].name:<24} {reason}" for role, reason in defects),
            "every plotted curve must be an unsmoothed REW export from a measurement",
            f"at {MAX_MEASUREMENT_RATE_HZ:,} Hz or below")
    alignments: dict[str, dict] = {}
    wav_rates: dict[str, int] = {}
    for channel in ("left", "right"):
        rate, impulse = load_filter_wav(paths[f"filter_{channel}_wav"])
        wav_rates[channel] = rate
        _, freqs, magnitude, phase = parsed[f"filter_{channel}"]
        alignment = detect_filter_alignment(freqs, magnitude, phase, rate, impulse)
        metrics = alignment["metrics"]
        metrics["passed"] = (
            metrics["rms_magnitude_db"] <= DEFAULT_LIMITS["max_rms_magnitude_db"] and
            metrics["rms_phase_deg"] <= DEFAULT_LIMITS["max_rms_phase_deg"] and
            metrics["above_100_hz_max_magnitude_db"] <=
            DEFAULT_LIMITS["above_100_hz_max_magnitude_db"] and
            metrics["above_100_hz_max_phase_deg"] <=
            DEFAULT_LIMITS["above_100_hz_max_phase_deg"]
        )
        if not metrics["passed"]:
            raise fail(
                f"{paths[f'filter_{channel}'].name} is not the exported response of "
                f"{paths[f'filter_{channel}_wav'].name}",
                f"residuals RMS {metrics['rms_magnitude_db']:.4f} dB / "
                f"{metrics['rms_phase_deg']:.3f} deg exceed the limits",
                "one fixed delay and one gain cannot relate these two files")
        alignments[channel] = alignment
    if wav_rates["left"] != wav_rates["right"]:
        raise fail(
            "the two impulse WAVs are at different sample rates: "
            f"{wav_rates['left']} vs {wav_rates['right']} Hz")
    if wav_rates["left"] != named_rate:
        raise fail(
            f"the impulse WAVs contain {wav_rates['left']} Hz audio but are named "
            f"for {named_rate} Hz",
            f"rename them to ...-{wav_rates['left'] // 1000}k.wav")
    delays = {item["delay_samples"] for item in alignments.values()}
    if len(delays) != 1:
        raise fail(
            "left and right impulses have different causal delays: "
            + ", ".join(f"{channel}={item['delay_samples']}"
                        for channel, item in alignments.items()),
            "both channels must share one delay or the stereo image shifts")
    return {
        "parsed": parsed,
        "alignments": alignments,
        "sample_rate": wav_rates["left"],
        "delay_samples": delays.pop(),
    }


def report(console: Console, paths: dict[str, Path], aggregate: dict, facts: dict,
           project: dict, measurements: dict, geometry: str, design_id: str,
           site_root: Path, rates: list[int], selector: str, commit: bool) -> None:
    """Print exactly what will be plotted, deployed and written."""
    original_label, corrected_label = aggregate_labels(aggregate)
    console.heading("CURVES THE WEB REMOTE WILL PLOT")
    console.line(
        f"  {'file':<24} {'trace':<34} {'points':>7}  {'range (Hz)':>17}  smoothing")
    for role in TRACE_ROLES:
        headers, freqs, _, _ = facts["parsed"][role]
        label = {
            "original_left": "Measured L", "original_right": "Measured R",
            "original_sum": original_label, "filter_left": "Filter FLX",
            "filter_right": "Filter FRX", "corrected_left": "Corrected L",
            "corrected_right": "Corrected R", "corrected_sum": corrected_label,
        }[role]
        smoothing = headers.get("smoothing", "").strip() or "unstated"
        styles = (console.YELLOW,) if smoothing.lower() not in ("none",) else ()
        console.line(
            f"  {paths[role].name:<24} {label:<34} {freqs.size:>7,}  "
            f"{freqs[0]:>7.2f}-{freqs[-1]:<9.0f}  "
            f"{console.styled(smoothing, *styles)}")
    console.note(
        "each curve is plotted as exported: no average, sum, convolution or "
        "smoothing is applied")
    if aggregate["corrected"] == "remeasured":
        console.note(
            "the corrected aggregate is a fresh measurement of the room with the "
            "DRC running, not a filtered result")
    top = max(float(facts["parsed"][role][1][-1]) for role in TRACE_ROLES)
    console.note(
        f"all eight are unsmoothed and stop at {top:,.1f} Hz, within the "
        f"{MAX_MEASUREMENT_RATE_HZ:,} Hz measurement limit "
        f"({MAX_EXPORT_FREQUENCY_HZ:,.0f} Hz)")

    console.heading("WHERE THE MEASUREMENTS COME FROM")
    console.note(
        f"{'project':<14} {project.get('name', '?')}"
        + (f"  (branch {project['branch']})" if project.get("branch") else ""))
    if project.get("commit"):
        console.line(
            f"      {'commit':<14} {project['commit'][:12]}  "
            f"{console.styled(project.get('subject', '')[:60], console.DIM)}")
    else:
        console.line(f"      {console.styled('commit         none', console.YELLOW)}")
    if project.get("remote"):
        console.line(f"      {'remote':<14} {project['remote']}")
    exports_state = (
        console.styled("all files committed", console.GREEN) if project.get("clean")
        else console.styled(
            f"{len(project.get('uncommitted', []))} file(s) NOT committed",
            console.BOLD, console.YELLOW))
    console.line(f"      {'exports':<14} {project.get('path', '?')}  {exports_state}")
    console.line(
        f"      {'session':<14} {measurements['file']}  "
        f"{measurements['bytes']:,} bytes  sha256 {measurements['sha256'][:16]}")
    if measurements.get("git_blob"):
        console.line(
            f"      {'restore':<14} " + console.styled(
                f"git -C {project.get('repository', '.')} cat-file blob "
                f"{measurements['git_blob'][:12]} > {measurements['file']}",
                console.DIM))

    console.heading("MEASUREMENT SESSION (as REW recorded it)")
    for role in ("original_left", "original_right", "original_sum"):
        headers = facts["parsed"][role][0]
        console.note(
            f"{headers.get('measurement', '?'):<24} {headers.get('dated', '?')}")
    note = facts["parsed"]["original_left"][0].get("note", "").lstrip("; ")
    if note:
        console.line(f"      {console.styled(note[:200], console.DIM)}")

    console.heading("FILTERS TO DEPLOY")
    for channel in ("left", "right"):
        alignment = facts["alignments"][channel]
        metrics = alignment["metrics"]
        console.note(
            f"{paths[f'filter_{channel}_wav'].name:<26} "
            f"{facts['sample_rate']:,} Hz  delay {alignment['delay_samples']} samples  "
            f"gain {alignment['txt_to_wav_gain_db']:+.4f} dB")
        console.line(
            f"      matches {paths[f'filter_{channel}'].name} within "
            f"{metrics['rms_magnitude_db']:.4f} dB / "
            f"{metrics['rms_phase_deg']:.3f} deg RMS")
    console.note(
        f"SoX will resample both into {len(rates)} rates: "
        + ", ".join(f"{rate:,}" for rate in rates))

    console.heading("WHAT WILL BE WRITTEN")
    console.note(f"site root      {site_root}")
    for rate in rates:
        console.line(
            f"      filters/{geometry}/{rate}/{selector}/"
            + console.styled("{L,R}.raw", console.BOLD))
    console.line(
        f"      configs/{geometry}/brutefir-<rate>{selector}.conf.in  "
        f"({len(rates)} files)")
    console.line(f"      filters/{geometry}/source/{design_id}/  (the ten files above)")
    console.line(f"      filters/{geometry}/analysis/{design_id}.json")
    console.line(f"      filters/{geometry}/provenance/{design_id}.json")
    if commit:
        console.note(
            "then one commit in the room repository, so this filter set stays "
            "retrievable")
    else:
        console.warn(
            "--no-commit: the room repository will not record this deployment")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "directory", type=Path,
        help="directory holding this design's REW exports and impulse WAVs")
    parser.add_argument("--geometry", default=None,
                        help="override the geometry read from the directory path")
    parser.add_argument("--design", default=None,
                        help="override the design ID read from the directory path")
    parser.add_argument("--mdat", type=Path, default=None,
                        help="REW file these exports came from "
                             "(default: the .mdat matching the directory name)")
    parser.add_argument("--rates", default="44100,48000,88200,96000,192000",
                        help="BruteFIR sample rates to generate")
    parser.add_argument("--safety-margin", type=float, default=1.0,
                        help="headroom margin in dB above the worst filter peak")
    parser.add_argument("--attenuation", default="auto",
                        help="auto, or one dB value used by every generated config")
    parser.add_argument("--replace-design", action="store_true",
                        help="allow overwriting files already deployed for this design ID")
    parser.add_argument("--allow-uncommitted", action="store_true",
                        help="deploy exports that are not committed in their project, "
                             "recording the gap in the manifest")
    parser.add_argument("--no-commit", dest="commit", action="store_false",
                        help="do not commit the deployment in the room repository")
    parser.add_argument("--require-commit", action="store_true",
                        help="fail unless the site repository records the deployment")
    parser.add_argument("--dry-run", action="store_true",
                        help="run every check and report, then stop without asking")
    parser.add_argument("--yes", action="store_true",
                        help="skip the confirmation prompt")
    parser.add_argument("--live", action="store_true",
                        help="publish runnable .conf files directly into --site-root")
    parser.add_argument("--archive-mdat", action="store_true",
                        help="copy the complete .mdat into the deployed source bundle")
    parser.add_argument("--coefficient-root", type=Path, default=None,
                        help="absolute live root baked into --live configs when staging elsewhere")
    parser.add_argument("--upload-provenance", action="store_true",
                        help="record stable browser-upload provenance without temporary paths")
    parser.add_argument("--no-next", action="store_true",
                        help="suppress the repository-to-runtime handoff (for web/CI use)")
    add_site_root_argument(parser)
    args = parser.parse_args()

    if args.require_commit and not args.commit:
        raise fail("--require-commit cannot be combined with --no-commit")

    site_root = resolve_site_root(args.site_root)
    try:
        rates = sorted({int(value.strip()) for value in args.rates.split(",")})
    except ValueError as error:
        raise fail("--rates must be a comma-separated integer list") from error
    if not rates or any(rate < 8000 or rate > 384000 for rate in rates):
        raise fail("sample rates must be between 8000 and 384000 Hz")
    if args.attenuation != "auto":
        try:
            attenuation: str | float = float(args.attenuation)
        except ValueError as error:
            raise fail("--attenuation must be 'auto' or a number") from error
    else:
        attenuation = "auto"

    directory = args.directory.resolve()
    geometry, design_id = design_identity(directory, args.geometry, args.design)
    CONSOLE.banner("ROOM CORRECTION DESIGN", geometry, design_id, directory)

    CONSOLE.stage(1, WORKFLOW_STEPS, "Find the design's files by name")
    paths, aggregate, named_rate, notes = discover(directory)
    for role in ARTIFACT_ROLES:
        CONSOLE.note(f"{ROLE_TITLES[role]:<22} {paths[role].name}")
    for message in notes:
        CONSOLE.warn(message)
    CONSOLE.ok(
        f"all {len(ARTIFACT_ROLES)} required files found; aggregate is "
        f"{'REW vector average' if aggregate['style'] == 'LR' else 'L+R sum'}"
        + (", corrected by a fresh measurement" if aggregate["corrected"] == "remeasured"
           else ", corrected by REW's filtered result"))

    CONSOLE.stage(2, WORKFLOW_STEPS,
                  "Read and check the exports, verify each filter against its WAV")
    facts = inspect(paths, named_rate)
    CONSOLE.ok(
        f"8 exports parsed, all unsmoothed and within {MAX_MEASUREMENT_RATE_HZ:,} Hz; "
        f"both filter TXTs are the exported response of their WAV, related by one "
        f"{facts['delay_samples']}-sample delay at {facts['sample_rate']:,} Hz")

    CONSOLE.stage(3, WORKFLOW_STEPS,
                  "Trace the exports back to their project and REW session")
    mdat = find_mdat(directory, args.mdat)
    project, blobs = source_provenance(
        CONSOLE, directory, paths, mdat, allow_uncommitted=args.allow_uncommitted)
    if args.upload_provenance:
        project = {
            "name": "browser upload", "repository": "", "remote": "",
            "branch": "", "commit": "", "committed_at": "", "subject": "",
            "path": directory.name, "clean": False,
            "uncommitted": sorted([path.name for path in paths.values()] + [mdat.name]),
        }
        blobs = {}
    measurements = measurement_record(mdat, project, blobs)
    if args.upload_provenance:
        measurements["path"] = mdat.name
    if args.archive_mdat:
        measurements["archive_source"] = str(mdat.resolve())
    CONSOLE.ok(
        f"{mdat.name} hashed into the manifest"
        + (f"; project {project['name']} at {project['commit'][:12]}"
           if project.get("commit") else "; no project commit"))

    selector = f"@{design_id}"
    CONSOLE.stage(4, WORKFLOW_STEPS, "Report what will be used")
    report(CONSOLE, paths, aggregate, facts, project, measurements,
           geometry, design_id, site_root, rates, selector, args.commit)

    CONSOLE.stage(5, WORKFLOW_STEPS, "Confirm")
    apply = args.dry_run is False and confirm(
        CONSOLE, "Apply this design?", args.yes)
    if args.dry_run:
        CONSOLE.note("--dry-run: every check still runs, nothing is written")
    elif not apply:
        CONSOLE.note("nothing was written")
        return 1

    repo_root = Path(project.get("repository", str(directory.parent)))
    recipe = {
        "schema": 2,
        "geometry": geometry,
        "design_id": design_id,
        "variant": design_id,
        "description": f"{geometry} {design_id} correction",
        "audited_at": dt.date.today().isoformat(),
        "aggregate": aggregate,
        "source": {
            "directory": directory.name if args.upload_provenance else str(directory),
            "project": project,
            "measurements": measurements,
            "artifacts": {
                role: {
                    "path": paths[role].name,
                    "bundle_path": f"source/{design_id}/{paths[role].name}",
                    "sha256": sha256_file(paths[role]),
                    "git_blob": blobs.get(
                        _relative_to(paths[role], repo_root), ""),
                    **({"measurement": facts["parsed"][role][0].get("measurement", ""),
                        "smoothing": facts["parsed"][role][0].get("smoothing", "")}
                       if role in TRACE_ROLES else {}),
                }
                for role in ARTIFACT_ROLES
            },
        },
        "filter": {
            "sample_rate": facts["sample_rate"],
            "delay_samples": facts["delay_samples"],
            "txt_to_wav_gain_db": {
                channel: round(float(alignment["txt_to_wav_gain_db"]), 6)
                for channel, alignment in facts["alignments"].items()
            },
            "txt_wav_limits": DEFAULT_LIMITS,
        },
        "runtime": {
            "selector": selector,
            "generate_configs": True,
            "format": "FLOAT64_LE",
            "attenuation_db": attenuation,
            "safety_margin_db": args.safety_margin,
            "coefficient_root": str((args.coefficient_root or site_root).resolve())
                                if args.live else "@REPO_DIR@",
            "rates": {
                str(rate): (f"configs/{geometry}/brutefir-{rate}{selector}.conf"
                            if args.live else
                            f"configs/{geometry}/brutefir-{rate}{selector}.conf.in")
                for rate in rates
            },
        },
    }

    CONSOLE.stage(6, WORKFLOW_STEPS, "Deploy" if apply else "Audit without writing")
    CONSOLE.line()
    manifest = publish_bundle(
        recipe, directory, site_root, apply=apply, replace=args.replace_design)
    if not apply:
        return 0
    # The runtime manifest is publish_bundle's commit marker. Keep this
    # reproduction recipe out of the bundle until publication has succeeded.
    unchanged = manifest.pop("_publication_unchanged", False)
    recipe["source"]["measurements"].pop("archive_source", None)
    if unchanged:
        if args.commit:
            record_history(CONSOLE, site_root, manifest,
                           require=args.require_commit)
        CONSOLE.note("already installed: every filter, config and archived source is identical")
        return 0
    atomic_json(recipe, site_root / "filters" / geometry / "provenance" /
                f"{design_id}.source.json")
    CONSOLE.line()
    CONSOLE.ok(f"design {geometry}/{design_id} deployed (runtime selector {selector})")
    if args.commit:
        record_history(CONSOLE, site_root, manifest, require=args.require_commit)
    if not args.no_next:
        print_deployed_next(Path(__file__).resolve().parents[1], [{
            "geometry": manifest["geometry"],
            "design_id": manifest["design_id"],
            "bundle_id": manifest["bundle_id"],
            "manifest": Path("filters") / geometry / "provenance" / f"{design_id}.json",
        }], include_verification=True, site_root=site_root)
    return 0


def record_history(console: Console, site_root: Path, manifest: dict,
                   *, require: bool = False) -> dict:
    """Commit the deployment where the room keeps its history."""
    result = commit_site(site_root, manifest)
    status = result["status"]
    if status == "committed":
        console.ok(
            f"room history: {result['commit'][:12]} {result['subject']}")
    elif status == "unchanged":
        console.note(
            f"room history: {result['repository']} already holds these exact files")
    elif status == "no-repo":
        console.warn(
            f"{result['root']} is not a Git work tree, so this deployment leaves no "
            "history; run 'git init' there to be able to return to it later")
    else:
        console.warn(f"room history: commit failed: {result.get('error', '')}")
        console.command(
            f"git -C {result.get('repository', site_root)} commit -a")
    if require and result["status"] not in {"committed", "unchanged"}:
        raise AuditError(
            "site repository did not record the deployment: "
            + result.get("error", result["status"]))
    return result


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\ninterrupted; nothing was written", file=sys.stderr)
        raise SystemExit(1)
    except (AuditError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        report_error(error)
        raise SystemExit(1)
