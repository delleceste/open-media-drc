#!/usr/bin/env python3
"""Terminal formatting shared by the filter design commands.

Kept apart from the builder so that deploying and removing a design speak with
one voice: same stage numbering, same colours, same shape of error.
"""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import sys

from deploy_filter import AuditError


class Console:
    """Small dependency-free, TTY-aware formatter for the design workflows."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
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

    def banner(self, title: str, geometry: str, design_id: str,
               subtitle: object = "") -> None:
        self.line(self.styled(title, self.BOLD, self.BLUE))
        self.line(
            f"  {self.styled(geometry, self.BOLD)} / "
            f"{self.styled(design_id, self.BOLD)}")
        if subtitle:
            self.line(f"  {self.styled(subtitle, self.DIM)}")
        self.line()

    def stage(self, number: int, total: int, label: str) -> None:
        if number > 1:
            self.line()
        marker = self.styled(f"[{number}/{total}]", self.BOLD, self.CYAN)
        self.line(f"{marker} {self.styled(label, self.BOLD)}")

    def ok(self, message: str) -> None:
        self.line(f"  {self.styled('OK', self.BOLD, self.GREEN)}  {message}")

    def warn(self, message: str) -> None:
        self.line(f"  {self.styled('WARN', self.BOLD, self.YELLOW)}  {message}")

    def note(self, message: str) -> None:
        self.line(f"  {self.styled('->', self.CYAN)}  {message}")

    def heading(self, title: str) -> None:
        self.line()
        self.line(self.styled(title, self.BOLD, self.BLUE))

    def command(self, argv: list[str] | str) -> None:
        rendered = argv if isinstance(argv, str) else shlex.join(argv)
        self.line(f"  {self.styled('$', self.GREEN, self.BOLD)} {rendered}")


CONSOLE = Console()


def fail(message: str, *details: str) -> AuditError:
    """An AuditError whose extra lines are already coloured for the terminal."""
    if details:
        message += "\n" + "\n".join(
            f"       {CONSOLE.styled(line, CONSOLE.YELLOW)}" for line in details)
    return AuditError(message)


def confirm(console: Console, question: str, assume_yes: bool) -> bool:
    """Ask before doing something that changes a deployed room."""
    if assume_yes:
        console.line()
        console.ok("--yes given; proceeding without asking")
        return True
    if not sys.stdin.isatty():
        raise fail(
            "standard input is not a terminal, so the confirmation cannot be asked",
            "re-run with --yes to proceed non-interactively")
    console.line()
    answer = input(console.styled(f"{question} [y/N] ", console.BOLD, console.YELLOW))
    return answer.strip().lower() in ("y", "yes")


def report_error(error: Exception) -> None:
    """Uniform FAIL line on stderr for every design command."""
    errors = Console(sys.stderr)
    print(f"{errors.styled('FAIL', errors.BOLD, errors.RED)}: {error}", file=sys.stderr)


def human_bytes(count: int) -> str:
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024.0 or unit == "GiB":
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024.0
    return f"{count} B"


def relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
