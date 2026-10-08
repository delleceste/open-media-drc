#!/usr/bin/env python3
"""Explicitly replace one FLAC+CUE album with verified track FLACs.

cuetools locates and tags tracks; shntool splits and verifies their combined
PCM. The source is removed only after a matching composite hash and a new
per-track DR report. Rescan calls this command only when CUE splitting is set.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

source_dir = Path(__file__).resolve().parents[1] / "omdrc-ctrl" / "src"
if not (source_dir / "drmeter.py").is_file():
    source_dir = Path("/usr/local/lib/omdrcctrl")
sys.path.insert(0, str(source_dir))
import drmeter  # noqa: E402
import dr_store  # noqa: E402


class SplitError(Exception):
    pass


def run(*args: str) -> str:
    try:
        return subprocess.run(args, text=True, capture_output=True, check=True).stdout.strip()
    except subprocess.CalledProcessError as error:
        raise SplitError(f"{args[0]} failed: {error.stderr.strip()[:300]}") from error


def fingerprint(*files: Path, composite: bool = False) -> str:
    args = ["shnhash", "-q", "-m"]
    if composite:
        args.append("-c")
    result = run(*args, *(str(p) for p in files))
    hashes = re.findall(r"\b[0-9a-fA-F]{32}\b", result)
    if len(hashes) != 1:
        raise SplitError(f"shnhash did not return one audio fingerprint: {result[:150]}")
    return hashes[0].lower()


def track_name(number: int, title: str) -> str:
    clean = re.sub(r"[\\/\x00-\x1f]+", " - ", title).strip(" .")[:120].rstrip(" .")
    return f"{number:02d} - {clean or f'Track {number:02d}'}.flac"


def source_tag(source: Path, name: str) -> str:
    shown = run("metaflac", f"--show-tag={name}", str(source))
    return shown.partition("=")[2] if shown else ""


def split(folder: Path, *, verify_only: bool = False) -> tuple[int, int, int | None]:
    for command in ("cuebreakpoints", "cueprint", "cuetag.sh", "shnsplit", "shnhash", "flac", "metaflac"):
        if not shutil.which(command):
            raise SplitError(f"install cuetools, shntool and flac first (missing {command})")
    folder = folder.resolve(strict=True)
    if not folder.is_dir():
        raise SplitError("album folder does not exist")
    files = [p for p in folder.iterdir() if p.is_file()]
    flacs = [p for p in files if p.suffix.lower() == ".flac"]
    cues = [p for p in files if p.suffix.lower() == ".cue"]
    if (len(flacs) != 1 or len(cues) != 1 or
            sum(p.suffix.lower() in drmeter.AUDIO_SUFFIXES for p in files) != 1):
        raise SplitError("folder needs one FLAC, one CUE and no other audio")
    source, cue = flacs[0], cues[0]
    if source.is_symlink() or cue.is_symlink():
        raise SplitError("source FLAC and CUE must be regular files")
    if shutil.disk_usage(folder).free < source.stat().st_size * 2:
        raise SplitError("not enough free space to verify tracks while keeping the source")
    # Some sheets retain the WAV name after the CD image was encoded as FLAC.
    # The sole audio file and an exact matching stem make that alias unambiguous.
    referenced = re.findall(r'^\s*FILE\s+"([^"]+)"\s+(\S+)',
                            cue.read_text(encoding="utf-8-sig", errors="replace"), re.I | re.M)
    direct = len(referenced) == 1 and referenced[0][0] == source.name
    wav_alias = (len(referenced) == 1 and referenced[0][1].upper() == "WAVE" and
                 referenced[0][0] == Path(referenced[0][0]).name and
                 Path(referenced[0][0]).stem == source.stem and
                 Path(referenced[0][0]).suffix.lower() == ".wav")
    if not (direct or wav_alias):
        raise SplitError("CUE must reference this FLAC or its same-name WAV image")
    track_modes = re.findall(r"^\s*TRACK\s+\d+\s+(\S+)",
                             cue.read_text(encoding="utf-8-sig", errors="replace"), re.I | re.M)
    if not track_modes or any(mode.upper() != "AUDIO" for mode in track_modes):
        raise SplitError("CUE must contain audio tracks only")
    count = int(run("cueprint", "-d", "%N", str(cue)))
    breaks = run("cuebreakpoints", str(cue)).splitlines()
    if count < 2 or len(breaks) != count - 1 or len(track_modes) != count:
        raise SplitError("CUE track count and breakpoints disagree")
    old_report = folder / "dr14.txt"
    prior_tracks = None
    if old_report.is_file():
        prior = dr_store.parse_report(old_report.read_text(encoding="utf-8", errors="replace"))
        prior_tracks = prior["tracks"] if prior else None
    archived_report = folder / "dr14.whole-flac.txt"
    archived_cue = folder / (cue.name + ".original")
    if archived_cue.exists() or (old_report.exists() and archived_report.exists()):
        raise SplitError("an original CUE or whole-file DR report is already archived")

    stage = Path(tempfile.mkdtemp(prefix=".omdrc-cue-split-", dir=folder))
    committed: list[tuple[Path, Path]] = []
    originals: list[tuple[Path, Path]] = []
    source_deleted = False
    try:
        breakpoint_file = stage / "breakpoints.txt"
        breakpoint_file.write_text("\n".join(breaks) + "\n")
        run("shnsplit", "-q", "-P", "none", "-f", str(breakpoint_file),
            "-o", "flac", "-d", str(stage), str(source))
        tracks = sorted(stage.glob("*.flac"))
        if len(tracks) != count:
            raise SplitError(f"expected {count} tracks, got {len(tracks)}")
        run("cuetag.sh", str(cue), *(str(p) for p in tracks))
        date = source_tag(source, "DATE")
        genre = source_tag(source, "GENRE")
        album_artist = run("cueprint", "-d", "%P", str(cue))
        for number, track in enumerate(tracks, 1):
            tags = []
            if date:
                tags.append(f"DATE={date}")
            if genre and not run("metaflac", "--show-tag=GENRE", str(track)):
                tags.append(f"GENRE={genre}")
            if album_artist:
                tags.append(f"ALBUMARTIST={album_artist}")
                if not run("metaflac", "--show-tag=ARTIST", str(track)):
                    tags.append(f"ARTIST={album_artist}")
            if tags:
                run("metaflac", *(f"--set-tag={tag}" for tag in tags), str(track))
            for tag in ("TITLE", "ALBUM", "TRACKNUMBER"):
                if not run("metaflac", f"--show-tag={tag}", str(track)):
                    raise SplitError(f"{track.name} is missing its {tag} tag")
            destination = stage / track_name(number, run("cueprint", "-n", str(number), "-t", "%t", str(cue)))
            if destination.exists():
                raise SplitError("two CUE tracks have the same output name")
            track.rename(destination)
        tracks = sorted(stage.glob("*.flac"))
        for track in tracks:
            run("flac", "-t", "-s", str(track))
        if fingerprint(source) != fingerprint(*tracks, composite=True):
            raise SplitError("split track audio differs from the original FLAC")
        album_dr = drmeter.write_album_report(str(stage))
        if album_dr is None:
            raise SplitError("could not generate the per-track DR report")
        if any((folder / p.name).exists() and (folder / p.name) != source for p in tracks):
            raise SplitError("a split track name already exists in the album")
        if verify_only:
            shutil.rmtree(stage)
            return album_dr, count, prior_tracks
        for path in (source, cue, old_report):
            if path.exists():
                parked = stage / ("original-" + path.name)
                os.replace(path, parked)
                originals.append((parked, path))
        for path in tracks + [stage / "dr14.txt"]:
            destination = folder / path.name
            os.replace(path, destination)
            committed.append((destination, path))
        os.replace(stage / ("original-" + cue.name), archived_cue)
        originals[1] = (archived_cue, cue)
        parked_report = stage / ("original-" + old_report.name)
        if parked_report.exists():
            os.replace(parked_report, archived_report)
            originals[-1] = (archived_report, old_report)
        (stage / ("original-" + source.name)).unlink()
        source_deleted = True
        shutil.rmtree(stage, ignore_errors=True)
        return album_dr, count, prior_tracks
    except BaseException:
        if not source_deleted:
            try:
                for destination, staged in reversed(committed):
                    if destination.exists():
                        os.replace(destination, staged)
                for parked, original in reversed(originals):
                    if parked.exists():
                        os.replace(parked, original)
            except OSError as recovery_error:
                raise SplitError(f"recovery needs attention; originals are in {stage}: {recovery_error}") from recovery_error
        shutil.rmtree(stage, ignore_errors=True)
        raise


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--verify-only":
        verify_only, folder = True, sys.argv[2]
    elif len(sys.argv) == 2:
        verify_only, folder = False, sys.argv[1]
    else:
        sys.exit(f"usage: {sys.argv[0]} [--verify-only] <album folder>")
    try:
        dr, count, prior_tracks = split(Path(folder), verify_only=verify_only)
    except (SplitError, OSError, ValueError) as error:
        sys.exit(f"CUE split failed; original retained: {error}")
    previous = ("previous dr14.txt had 1 whole-FLAC row; " if prior_tracks == 1
                else f"previous dr14.txt had {prior_tracks} rows; " if prior_tracks is not None else "")
    if verify_only:
        print(f"Verified {count} split tracks against the original PCM (DR{dr}); original album untouched.")
    else:
        print(f"1 FLAC + 1 CUE: {previous}split into {count} verified track FLACs; "
              f"recalculated per-track dr14.txt (DR{dr}); source FLAC removed.")
