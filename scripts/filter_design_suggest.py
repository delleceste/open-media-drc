#!/usr/bin/env python3
"""Read-only discovery for a filter-design declaration command.

The newest .mdat is used only to derive a sibling <stem>.txts directory name.
No .mdat is opened or parsed, and no file is written.
"""

from __future__ import annotations

import datetime as dt
from difflib import SequenceMatcher
from pathlib import Path
import re
import shlex

import numpy as np

from deploy_filter import AuditError, parse_rew_txt


def normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def newest_mdat(root: Path) -> Path:
    projects = [
        path for path in root.glob("*.mdat")
        if path.is_file() and not path.is_symlink()
    ]
    if not projects:
        raise AuditError(f"no regular .mdat files directly under {root}")
    return max(projects, key=lambda path: (path.stat().st_mtime_ns, path.name))


def load_txt(path: Path) -> dict:
    headers, frequencies, _, _ = parse_rew_txt(path)
    return {
        "path": path,
        "headers": headers,
        "frequencies": frequencies,
        "title": headers.get("measurement", path.stem),
        "kind": normalized(
            headers.get("source", "") + " " + headers.get("format", "")),
        "mtime_ns": path.stat().st_mtime_ns,
    }


def measurement_suffix(title: str, channel: str) -> str | None:
    value = normalized(title)
    prefix = channel.lower() + " "
    if value.startswith(prefix):
        return value[len(prefix):]
    return None


def measurement_pairs(directory: Path, target: str) -> list[tuple[float, dict, dict]]:
    items = []
    for path in sorted(directory.glob("*.txt")):
        if path.is_file() and not path.is_symlink():
            try:
                item = load_txt(path)
            except (AuditError, OSError, UnicodeError, ValueError):
                continue
            if "acoustic timing reference" in item["kind"]:
                items.append(item)

    left = {}
    right = {}
    for item in items:
        left_suffix = measurement_suffix(item["title"], "l")
        right_suffix = measurement_suffix(item["title"], "r")
        if left_suffix is not None:
            left[left_suffix] = item
        if right_suffix is not None:
            right[right_suffix] = item

    target_value = normalized(target)
    target_without_blue = " ".join(
        token for token in target_value.split() if token != "blue")
    result = []
    for suffix in sorted(set(left) & set(right)):
        l_item = left[suffix]
        r_item = right[suffix]
        if not np.array_equal(l_item["frequencies"], r_item["frequencies"]):
            continue
        similarity = SequenceMatcher(None, suffix, target_without_blue).ratio()
        exact_bonus = 10.0 if suffix == target_without_blue else 0.0
        variant_penalty = 1.0 if re.search(r"\b(?:2l|3l|trad)\b", suffix) else 0.0
        result.append((exact_bonus + similarity - variant_penalty,
                       l_item, r_item))
    return sorted(result, key=lambda item: (
        item[0], item[1]["mtime_ns"], item[1]["path"].name), reverse=True)


def all_txt_candidates(root: Path) -> list[dict]:
    result = []
    for directory in sorted(root.glob("*.txts")):
        if not directory.is_dir() or directory.is_symlink():
            continue
        for path in sorted(directory.glob("*.txt")):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                result.append(load_txt(path))
            except (AuditError, OSError, UnicodeError, ValueError):
                continue
    return result


def prioritize_aggregate_grid(
        pairs: list[tuple[float, dict, dict]], aggregate: dict | None
        ) -> list[tuple[float, dict, dict]]:
    """Put declaration-compatible pairs first; fail if none can use aggregate."""
    if aggregate is None:
        return pairs
    compatible = [
        pair for pair in pairs
        if np.array_equal(pair[1]["frequencies"], aggregate["frequencies"])
    ]
    if not compatible:
        raise AuditError(
            f"no acoustic-timing L/R pair shares the frequency grid of "
            f"aggregate {aggregate['path'].name}")
    compatible_ids = {id(pair) for pair in compatible}
    return compatible + [pair for pair in pairs if id(pair) not in compatible_ids]


def select_title(items: list[dict], exact: tuple[str, ...],
                 prefix: str | None = None,
                 required_kind: str | None = None) -> dict | None:
    exact_normalized = tuple(normalized(value) for value in exact)
    candidates = []
    for item in items:
        title = normalized(item["title"])
        if required_kind and required_kind not in item["kind"]:
            continue
        try:
            exact_rank = len(exact_normalized) - exact_normalized.index(title)
        except ValueError:
            exact_rank = 0
        prefix_rank = 1 if prefix and title.startswith(normalized(prefix)) else 0
        if not exact_rank and not prefix_rank:
            continue
        if "diff" in title:
            continue
        candidates.append((exact_rank, prefix_rank, item["mtime_ns"], item))
    return max(candidates, default=(0, 0, 0, None), key=lambda value: value[:3])[3]


def select_wav(root: Path, channel: str) -> Path | None:
    wanted = f"{channel.lower()}-trimmed-48k.wav"
    candidates = []
    for path in root.glob("*.wav"):
        if not path.is_file() or path.is_symlink():
            continue
        name = path.name.lower()
        if not name.startswith(channel.lower()):
            continue
        score = 0
        if name == wanted:
            score += 100
        if "trimmed" in name:
            score += 20
        if "48k" in name:
            score += 10
        if "192k" in name or "+2db" in name or "1.5.4" in name or "diff" in name:
            score -= 50
        candidates.append((score, path.stat().st_mtime_ns, path.name, path))
    return max(candidates, default=(0, 0, "", None), key=lambda value: value[:3])[3]


def inferred_geometry(root: Path) -> str:
    name = root.name
    return name[4:] if name.startswith("DRC-") else name


def inferred_design_id(mdat: Path, geometry: str) -> str:
    stem_tokens = normalized(mdat.stem).split()
    geometry_tokens = set(normalized(geometry).split())
    label_tokens = [token for token in stem_tokens if token not in geometry_tokens]
    label = "-".join(label_tokens) or "filter"
    date = dt.datetime.fromtimestamp(mdat.stat().st_mtime).strftime("%Y%m%d")
    return f"{label}-{date}"


def command_lines(display_root: Path, geometry: str, design_id: str,
                  selected: dict[str, str], sum_mode: str) -> list[str]:
    values = [
        ("--source-root", str(display_root)),
        ("--geometry", geometry),
        ("--design-id", design_id),
        ("--description", f"{geometry} {design_id} correction"),
        ("--measurement-left", selected["measurement_left"]),
        ("--measurement-right", selected["measurement_right"]),
        ("--measurement-sum", selected["measurement_sum"]),
        ("--filter-left-txt", selected["filter_left_txt"]),
        ("--filter-right-txt", selected["filter_right_txt"]),
        ("--filter-left-wav", selected["filter_left_wav"]),
        ("--filter-right-wav", selected["filter_right_wav"]),
    ]
    for role, option in (
            ("corrected_left_txt", "--corrected-left-txt"),
            ("corrected_right_txt", "--corrected-right-txt"),
            ("corrected_sum_txt", "--corrected-sum-txt")):
        if role in selected:
            values.append((option, selected[role]))
    values.append(("--sum-mode", sum_mode))

    lines = ["python3 scripts/declare_filter_design.py \\"]
    for index, (option, value) in enumerate(values):
        continuation = " \\" if index < len(values) - 1 else ""
        lines.append(f"  {option} {shlex.quote(str(value))}{continuation}")
    return lines


def suggest(source_root: Path) -> int:
    display_root = source_root
    root = source_root.resolve()
    if not root.is_dir():
        raise AuditError(f"source root is not a directory: {root}")

    mdat = newest_mdat(root)
    measurement_directory = root / f"{mdat.stem}.txts"
    if not measurement_directory.is_dir() or measurement_directory.is_symlink():
        raise AuditError(
            f"newest project is {mdat.name}, but expected measurement directory "
            f"does not exist: {measurement_directory}")

    pairs = measurement_pairs(measurement_directory, mdat.stem)
    if not pairs:
        raise AuditError(
            f"no compatible acoustic-timing L/R pair in {measurement_directory}")
    text_items = all_txt_candidates(root)
    aggregate = select_title(
        text_items, ("LR.orig", "L+R.orig", "LR"), "LR",
        required_kind="vector average")
    # A timing-compatible L/R pair can still use a different REW export grid
    # from the aggregate. Prefer only pairs that the real declaration preflight
    # can combine with the selected aggregate, so the suggested command is
    # genuinely runnable rather than merely plausible by filename/title.
    pairs = prioritize_aggregate_grid(pairs, aggregate)
    _, left, right = pairs[0]
    filter_left = select_title(text_items, ("FLX",), "FLX")
    filter_right = select_title(text_items, ("FRX",), "FRX")
    corrected_left = select_title(
        text_items, ("L.Filtered", "LFiltered"), "L filtered")
    corrected_right = select_title(
        text_items, ("R.Filtered", "RFiltered"), "R filtered")
    corrected_sum = select_title(
        text_items, ("LR.Filtered", "LRFiltered"), "LR filtered",
        required_kind="vector average")
    wav_left = select_wav(root, "FLX")
    wav_right = select_wav(root, "FRX")

    required = {
        "measurement_left": left["path"],
        "measurement_right": right["path"],
        "measurement_sum": aggregate["path"] if aggregate else None,
        "filter_left_txt": filter_left["path"] if filter_left else None,
        "filter_right_txt": filter_right["path"] if filter_right else None,
        "filter_left_wav": wav_left,
        "filter_right_wav": wav_right,
    }
    missing = [role for role, path in required.items() if path is None]

    print(f"Newest .mdat by filesystem modification time: {mdat.name}")
    print("Naming hint only; the .mdat will not be opened or added to the command.")
    print(f"Measurement directory: {measurement_directory.name}")
    print(f"Selected L/R pair: {left['title']} / {right['title']}")
    if len(pairs) > 1:
        alternatives = ", ".join(
            f"{item[1]['title']} / {item[2]['title']}" for item in pairs[1:])
        print(f"Other compatible L/R pairs: {alternatives}")

    if missing:
        print("INCOMPLETE: no safe candidate for " + ", ".join(missing))
        print("Add/export those roles, then run this suggestion again.")
        return 1

    selected = {
        role: relative(root, path)
        for role, path in required.items()
        if path is not None
    }
    for role, item in (
            ("corrected_left_txt", corrected_left),
            ("corrected_right_txt", corrected_right),
            ("corrected_sum_txt", corrected_sum)):
        if item is not None:
            selected[role] = relative(root, item["path"])

    sum_mode = (
        "vector_average"
        if aggregate and "vector average" in aggregate["kind"]
        else "independent")
    geometry = inferred_geometry(root)
    design_id = inferred_design_id(mdat, geometry)

    print("")
    print("SUGGESTED DRY-RUN COMMAND (review every role before running):")
    print("\n".join(command_lines(
        display_root, geometry, design_id, selected, sum_mode)))
    print("")
    print("The supporting aggregate/filter files may come from another sibling")
    print("*.txts directory. The real dry run will verify headers, grids, hashes,")
    print("TXT/WAV response, corrected exports, and aggregate convention.")
    print("Add --write only after that dry run passes and the role choices are correct.")
    return 0
