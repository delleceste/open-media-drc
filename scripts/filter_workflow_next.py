#!/usr/bin/env python3
"""Shared operator handoff for filter publication and verification commands."""

from __future__ import annotations

import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys


class Console:
    """Small dependency-free, TTY-aware formatter."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    GREEN = "\033[32m"
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

    def heading(self, value: str) -> None:
        self.line()
        self.line(self.styled(value, self.BOLD, self.BLUE))

    def directory(self, path: Path) -> None:
        self.line(
            f"  {self.styled('Run from:', self.BOLD)} "
            f"{self.styled(path, self.BOLD, self.UNDERLINE)}")

    def command(self, command: list[str] | str) -> None:
        rendered = command if isinstance(command, str) else shlex.join(command)
        self.line(f"  {self.styled('$', self.BOLD, self.GREEN)} {rendered}")

    def note(self, value: str) -> None:
        self.line(f"  {self.styled('->', self.CYAN)}  {value}")


CONSOLE = Console()


def _cache_values(root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    cache = root / "build/CMakeCache.txt"
    if not cache.is_file():
        return values
    for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
        if ":" not in line or "=" not in line:
            continue
        key = line.partition(":")[0]
        values[key] = line.partition("=")[2]
    return values


def cmake_configure_command(
        root: Path, geometries: list[str], site_root: Path | None = None) -> list[str]:
    """Preserve the cached geometry set and add every verified geometry.

    When the data lives in a separate site checkout, also carry the search path
    so a first configure of a split layout finds configs/ and filters/ at all.
    """
    cache = _cache_values(root)
    default = cache.get("GEOMETRY", "flat") or "flat"
    extras = [item for item in cache.get("GEOMETRIES", "").split(";") if item]
    changed = False
    for geometry in geometries:
        if geometry != default and geometry not in extras:
            extras.append(geometry)
            changed = True
    command = ["cmake", "-S", ".", "-B", "build"]
    if changed:
        command.append(f"-DGEOMETRIES={';'.join(extras)}")
    if site_root is not None and site_root != root:
        cached_dirs = [item for item in cache.get("OMDRC_SITE_DATA_DIRS", "").split(";") if item]
        if str(site_root) not in cached_dirs:
            search = cached_dirs or [str(root)]
            command.append(f"-DOMDRC_SITE_DATA_DIRS={';'.join([*search, str(site_root)])}")
    return command


def installed_omdrc(root: Path) -> str:
    prefix = _cache_values(root).get("CMAKE_INSTALL_PREFIX", "/usr/local")
    return str(Path(prefix or "/usr/local") / "bin/omdrc")


def _scoped_status(root: Path, paths: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "status", "--short", "--", *paths], cwd=root,
            text=True, capture_output=True, check=False,
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _restart_command() -> str | None:
    if platform.system() == "FreeBSD":
        return "sudo service omdrcctrl restart"
    if platform.system() == "Linux":
        return "sudo systemctl restart omdrcctrl"
    return None


def print_deployed_next(
        root: Path, bundles: list[dict], *, include_verification: bool,
        site_root: Path | None = None) -> None:
    """Print the repository-to-running-system handoff for verified bundles.

    ``root`` is the engine checkout the tooling and CMake live in; ``site_root``
    is where configs/<geometry> and filters/<geometry> were written.  They are
    the same directory in the single-repository layout, and then the handoff
    names one working directory as before.  When they differ, each step carries
    its own, because the commit happens in the site repository and the build in
    the engine one.
    """
    site = site_root or root
    split = site != root
    geometries = sorted({str(item["geometry"]) for item in bundles})
    paths = [part for geometry in geometries
             for part in (f"configs/{geometry}", f"filters/{geometry}")]
    labels = sorted({f"{item['geometry']}/{item['design_id']}" for item in bundles})
    manifests = sorted({str(item["manifest"]) for item in bundles})
    if split:
        # Manifest paths are relative to the site root, but the checker runs
        # from the engine checkout: hand it absolute paths instead.
        manifests = [str(path) if Path(path).is_absolute() else str(site / path)
                     for path in manifests]

    CONSOLE.heading("NEXT")
    step = 1
    if not split:
        CONSOLE.directory(root)
    if include_verification:
        CONSOLE.line(f"{step}. Re-verify the published files through the standalone checker")
        if split:
            CONSOLE.directory(root)
        CONSOLE.command([
            "python3", "scripts/verify_filter_bundle.py", "--require-sources",
            "--no-next", *(["--site-root", str(site)] if split else []), *manifests,
        ])
        step += 1
        CONSOLE.line()

    CONSOLE.line(f"{step}. Review and commit only the deployed geometry paths")
    if split:
        CONSOLE.directory(site)
    CONSOLE.command(["git", "status", "--short", "--", *paths])
    status = _scoped_status(site, paths)
    if status:
        CONSOLE.note("Changes are currently present in those paths; review them before staging.")
        CONSOLE.command(["git", "add", "--", *paths])
        CONSOLE.command([
            "git", "commit", "-m", f"Deploy verified filter design: {', '.join(labels)}",
        ])
    elif status == "":
        CONSOLE.note("Those paths are currently clean; there is no deployment commit to make.")
    else:
        CONSOLE.note("Git status could not be inspected; do not stage anything until it is cleanly resolved.")
    step += 1

    CONSOLE.line()
    CONSOLE.line(f"{step}. Configure, build and install every verified geometry")
    if split:
        CONSOLE.directory(root)
    CONSOLE.command(cmake_configure_command(root, geometries, site))
    CONSOLE.directory(root / "build")
    CONSOLE.command(["make"])
    CONSOLE.command("sudo make install")
    restart = _restart_command()
    if restart:
        CONSOLE.command(restart)
    step += 1

    wrapper = installed_omdrc(root)
    CONSOLE.line()
    CONSOLE.line(f"{step}. Select the geometry/design to test")
    if len(bundles) > 1:
        CONSOLE.note("Choose one of the following exact geometry/design pairs; do not run all as a batch.")
    for item in sorted(bundles, key=lambda value: (value["geometry"], value["design_id"])):
        CONSOLE.command([wrapper, "geometry", str(item["geometry"])])
        CONSOLE.command([wrapper, "design", "--list"])
        design_id = str(item["design_id"])
        selector = "default" if design_id == "default" else f"@{design_id}"
        CONSOLE.command([wrapper, "design", selector])
        if len(bundles) > 1:
            CONSOLE.line()
    step += 1

    if len(bundles) == 1:
        CONSOLE.line()
    CONSOLE.line(f"{step}. Confirm the running identity in the web UI")
    for item in sorted(bundles, key=lambda value: (value["geometry"], value["design_id"])):
        CONSOLE.note(
            f"{item['geometry']}/{item['design_id']}: bundle {item['bundle_id']}")
    CONSOLE.note(
        "Open Filter response and require its green verified identity to match "
        "the selected geometry/design and bundle hash above.")
    CONSOLE.note("A missing or different identity means the displayed curves must not be trusted.")
