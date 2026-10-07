#!/usr/bin/env python3
"""Find likely album covers and place them where MPD's albumart lookup sees them.

The default is a dry run. Use --apply to move the selected image into the
directory containing the album's audio files as cover.jpg or cover.png.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil


AUDIO_SUFFIXES = {
    ".aac", ".aif", ".aiff", ".ape", ".dsf", ".dff", ".flac", ".m4a",
    ".mka", ".mp3", ".mpc", ".oga", ".ogg", ".opus", ".tak", ".tta",
    ".wav", ".wma", ".wv",
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
MPD_COVERS = {"cover.jpg", "cover.png", "cover.webp"}
BAD_NAME_WORDS = {
    "back", "backcover", "back-cover", "booklet", "cd", "disc", "inlay",
    "inside", "rear", "spine", "tray",
}


def image_type(path: Path) -> str | None:
    """Return the actual supported image type, independent of the extension."""
    try:
        with path.open("rb") as image:
            head = image.read(12)
    except OSError:
        return None
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    return None


def contains_audio(directory: Path, names: list[str]) -> bool:
    return any(Path(name).suffix.lower() in AUDIO_SUFFIXES or Path(name).suffix.lower() == ".cue"
               for name in names)


def image_score(path: Path, album_dir: Path) -> tuple[int, int, str]:
    """Prefer named front covers, then useful resolution, then stable names."""
    stem = path.stem.casefold().replace("_", " ").replace("-", " ")
    words = set(stem.split())
    score = 0
    if words & BAD_NAME_WORDS:
        score -= 1000
    if "cover" in words or "front" in words or "folder" in words:
        score += 100
    if "small" in words or "thumb" in words or "thumbnail" in words:
        score -= 30
    if path.parent == album_dir:
        score += 10
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return score, size, path.name.casefold()


def candidates(album_dir: Path) -> list[Path]:
    found = []
    for directory in (album_dir, album_dir / "Artwork"):
        if not directory.is_dir():
            continue
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        found.extend(path for path in entries
                     if path.is_file() and not path.is_symlink()
                     and path.suffix.lower() in IMAGE_SUFFIXES and image_type(path))
    return found


def recognized_cover_exists(album_dir: Path) -> bool:
    try:
        return any(path.is_file() and path.name in MPD_COVERS
                   for path in album_dir.iterdir())
    except OSError:
        return False


def album_directories(root: Path):
    for directory, subdirs, files in os.walk(root, followlinks=False):
        base = Path(directory)
        subdirs[:] = [name for name in subdirs if not (base / name).is_symlink()]
        if contains_audio(base, files):
            yield base


def normalize(root: Path, apply: bool) -> tuple[int, int, int]:
    planned = moved = skipped = 0
    for album_dir in album_directories(root):
        if recognized_cover_exists(album_dir):
            skipped += 1
            continue
        options = candidates(album_dir)
        if not options:
            continue
        ranked = sorted(options, key=lambda path: image_score(path, album_dir), reverse=True)
        source = ranked[0]
        if image_score(source, album_dir)[0] <= -100:
            print(f"REVIEW: only back, disc, booklet, or inlay art found in {album_dir}")
            skipped += 1
            continue
        suffix = image_type(source)
        target = album_dir / ("cover" + suffix)
        if target.exists() or target.is_symlink():
            print(f"SKIP target exists: {target}")
            skipped += 1
            continue
        planned += 1
        action = "MOVE" if apply else "DRY-RUN"
        print(f"{action}: {source} -> {target}")
        if apply:
            try:
                # Recheck immediately before moving; never replace artwork.
                if target.exists() or target.is_symlink():
                    print(f"SKIP target appeared: {target}")
                    skipped += 1
                    planned -= 1
                    continue
                shutil.move(str(source), str(target))
                moved += 1
            except OSError as error:
                print(f"ERROR: could not move {source}: {error}")
    return planned, moved, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("music_root", type=Path, help="root directory to scan")
    parser.add_argument("--apply", action="store_true", help="perform the planned moves (default: dry run)")
    args = parser.parse_args()
    root = args.music_root.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")
    planned, moved, skipped = normalize(root, args.apply)
    print(f"\nCandidates: {planned}; moved: {moved}; skipped: {skipped}")
    if not args.apply:
        print("Dry run only. Review the list, then repeat with --apply to move files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
