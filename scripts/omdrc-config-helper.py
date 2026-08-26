#!/usr/bin/env python3
"""Narrow privileged helper for web-selected physical audio roles.

It accepts only USB identities, writes fixed configuration keys/files, invokes
the existing reconciler, and verifies the resolved device before returning.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import time


IDENTITY = re.compile(r"0x[0-9a-fA-F]{4}:0x[0-9a-fA-F]{4}(?::[A-Za-z0-9._+-]+)?")
MANAGED = re.compile(r'^(\s*device:\s*)"[^"]+";(\s*#\s*omdrc-managed-dac\s*)$', re.M)


def run(argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print("$ " + " ".join(repr(item) if " " in item else item for item in argv), flush=True)
    result = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n", flush=True)
    if check and result.returncode:
        raise RuntimeError(f"{argv[0]} exited with status {result.returncode}")
    return result


def parse_identity(value: str, optional: bool = False) -> str:
    if optional and not value:
        return ""
    if not IDENTITY.fullmatch(value):
        raise ValueError(f"invalid USB identity: {value!r}")
    return value.lower()


def atomic_text(path: Path, text: str, mode: int = 0o644,
                owner: tuple[int, int] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.chmod(name, mode)
        if owner:
            os.chown(name, *owner)
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def freebsd_apply(dac: str, capture: str, timeout: int) -> None:
    run(["/usr/sbin/sysrc", f"omdrc_audio_dac={dac}"])
    run(["/usr/sbin/sysrc", f"omdrc_audio_capture={capture}"])
    run(["/usr/sbin/service", "omdrc_audio", "reconcile"])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = run(["/usr/sbin/service", "omdrc_audio", "status"], check=False)
        text = status.stdout
        dac_ok = status.returncode == 0 and "/dev/dsp" in text and " dac " in text
        capture_ok = not capture or " capture " in text
        if dac_ok and capture_ok:
            print("VERIFIED: configured USB roles resolve to /dev/dsp nodes")
            return
        time.sleep(1)
    raise RuntimeError("saved card selection, but omdrc_audio did not publish the requested roles")


def linux_usb_cards() -> list[dict]:
    cards = []
    for card in sorted(Path("/sys/class/sound").glob("card[0-9]*")):
        usb = (card / "device").resolve()
        while usb != usb.parent and not (usb / "idVendor").is_file():
            usb = usb.parent
        if not (usb / "idVendor").is_file():
            continue
        read = lambda name: ((usb / name).read_text(errors="replace").strip()
                             if (usb / name).is_file() else "")
        identity = f"0x{read('idVendor').lower()}:0x{read('idProduct').lower()}"
        serial = read("serial")
        cards.append({"number": card.name[4:], "identity": identity,
                      "serial": serial, "name": read("product") or card.name})
    return cards


def identity_matches(want: str, card: dict) -> bool:
    base, _, serial = want.partition(":")
    # partition once above only separates 0xVID; split the optional third field.
    parts = want.split(":", 2)
    return card["identity"] == ":".join(parts[:2]) and (
        len(parts) == 2 or card["serial"].lower() == parts[2].lower())


def installed_conf(prefix: str) -> dict[str, str]:
    path = Path(os.environ.get("OMDRC_CONF", f"{prefix}/etc/open-media-drc/omdrc.conf"))
    values = {}
    if path.is_file():
        for line in path.read_text(errors="replace").splitlines():
            match = re.match(r"^(AUDIO_USER|AUDIO_HOME|OMDRC_SITE_DIR)=(.*)$", line.strip())
            if match:
                values[match.group(1)] = match.group(2).strip("'\"")
    return values


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def allowed_site_root(value: str) -> Path:
    root = Path(value).resolve()
    prefix = os.environ.get("PREFIX", "/usr/local")
    configured = installed_conf(prefix).get("OMDRC_SITE_DIR")
    allowed = {Path(f"{prefix}/etc/open-media-drc").resolve()}
    if configured:
        allowed.add(Path(configured).resolve())
    if root not in allowed:
        raise ValueError(f"site root is not the configured open-media-drc site: {root}")
    return root


def safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe bundle path: {value!r}")
    return path


def filter_publish(staged_value: str, site_value: str) -> None:
    staged = Path(staged_value).resolve()
    site = allowed_site_root(site_value)
    manifests = [p for p in staged.glob("filters/*/provenance/*.json")
                 if not p.name.endswith(".source.json")]
    if len(manifests) != 1:
        raise ValueError("staged publication must contain exactly one runtime manifest")
    manifest_path = manifests[0]
    manifest = json.loads(manifest_path.read_text())
    geometry = manifest.get("geometry", "")
    design = manifest.get("design_id", manifest.get("variant", ""))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", geometry or "") or not \
       re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", design or ""):
        raise ValueError("manifest has an unsafe geometry or design ID")
    geometry_root = Path("filters") / geometry
    expected: dict[Path, str] = {}
    for item in manifest["source"]["artifacts"].values():
        expected[geometry_root / safe_relative(item["bundle_path"])] = item["sha256"]
    measurements = manifest["source"].get("measurements", {})
    if measurements.get("bundle_path"):
        expected[geometry_root / safe_relative(measurements["bundle_path"])] = measurements["sha256"]
    analysis = manifest["analysis"]
    expected[geometry_root / safe_relative(analysis["path"])] = analysis["sha256"]
    for runtime in manifest["runtime"]["rates"].values():
        expected[safe_relative(runtime["config"])] = runtime["config_sha256"]
        for channel in runtime["channels"].values():
            expected[geometry_root / safe_relative(channel["path"])] = channel["sha256"]
    live_manifest = site / geometry_root / "provenance" / f"{design}.json"
    try:
        live_data = json.loads(live_manifest.read_text())
    except (OSError, json.JSONDecodeError):
        live_data = {}
    if live_data.get("bundle_id") == manifest.get("bundle_id") and all(
            (site / relative).is_file() and
            digest(site / relative) == wanted for relative, wanted in expected.items()):
        print("UNCHANGED: this verified bundle is already installed; nothing was written.")
        return
    source_recipe = geometry_root / "provenance" / f"{design}.source.json"
    if (staged / source_recipe).is_file():
        expected[source_recipe] = digest(staged / source_recipe)
    manifest_relative = geometry_root / "provenance" / f"{design}.json"
    expected[manifest_relative] = digest(manifest_path)
    for relative, wanted in expected.items():
        source = staged / relative
        if not source.is_file() or source.is_symlink() or digest(source) != wanted:
            raise RuntimeError(f"staged file failed hash verification: {relative}")
        destination = site / relative
        if destination.exists() and digest(destination) != wanted:
            raise RuntimeError(f"design already exists with different bytes: {relative}")
    # Manifest last: readers never observe a claim before all claimed bytes exist.
    ordered = [item for item in expected if item != manifest_relative] + [manifest_relative]
    for relative in ordered:
        source, destination = staged / relative, site / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file() and digest(destination) == expected[relative]:
            continue
        fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        os.close(fd)
        try:
            shutil.copyfile(source, temporary)
            os.chmod(temporary, 0o644)
            os.replace(temporary, destination)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        print(f"PUBLISHED: {relative}")
    print(f"VERIFIED: installed {geometry}@{design} into {site}")


def filter_remove(selector: str, site_value: str, script_value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*@[A-Za-z0-9][A-Za-z0-9._+-]*", selector):
        raise ValueError("invalid design selector")
    site = allowed_site_root(site_value)
    script = Path(script_value).resolve()
    if script.name != "remove_filter_design.py" or not script.is_file():
        raise ValueError("invalid filter removal script")
    run([sys.executable, str(script), selector, "--site-root", str(site),
         "--no-commit", "--yes", "--live"])


def linux_apply(dac: str, timeout: int, restart: bool = True) -> None:
    matches = [card for card in linux_usb_cards() if identity_matches(dac, card)]
    if len(matches) != 1:
        raise RuntimeError(f"configured DAC identity resolves to {len(matches)} attached ALSA cards")
    selected = matches[0]
    prefix = os.environ.get("PREFIX", "/usr/local")
    conf = installed_conf(prefix)
    user = conf.get("AUDIO_USER")
    if not user:
        raise RuntimeError("AUDIO_USER is missing from omdrc.conf")
    account = pwd.getpwnam(user)
    home = Path(conf.get("AUDIO_HOME") or account.pw_dir)
    defaults = home / ".config/BruteFIR/brutefir_defaults.conf"
    text = defaults.read_text()
    replacement = rf'\1"hw:{selected["number"]},0";\2'
    changed, count = MANAGED.subn(replacement, text)
    if count != 1:
        raise RuntimeError(
            f"{defaults} must contain exactly one '# omdrc-managed-dac' output device")
    atomic_text(defaults, changed, owner=(account.pw_uid, account.pw_gid))
    role_conf = Path(f"{prefix}/etc/open-media-drc/audio-roles.conf")
    atomic_text(role_conf, f'OMDRC_AUDIO_DAC="{dac}"\n')
    state = Path("/run/omdrc/audio.roles")
    atomic_text(state, f"dac_unit={selected['number']}\n"
                       f"dac_desc={selected['name']}\n"
                       f"dac_id={dac}\n"
                       "capture_unit=\ncapture_desc=\ncapture_id=\n")
    if not restart:
        print(f"RESOLVED: {dac} -> ALSA card {selected['number']}")
        return
    run(["/usr/bin/systemctl", "restart", "drc-usb-audio.service"])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = run(["/usr/bin/systemctl", "is-active", "drc-usb-audio.service"],
                     check=False)
        if status.stdout.strip() == "active":
            print(f"VERIFIED: {dac} -> ALSA card {selected['number']} ")
            return
        time.sleep(1)
    raise RuntimeError("saved DAC selection, but the DRC service did not become active")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--dac", required=True)
    apply.add_argument("--capture", default="")
    apply.add_argument("--timeout", type=int, default=120)
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--timeout", type=int, default=120)
    publish = sub.add_parser("filter-publish")
    publish.add_argument("--staged", required=True)
    publish.add_argument("--site-root", required=True)
    remove = sub.add_parser("filter-remove")
    remove.add_argument("--selector", required=True)
    remove.add_argument("--site-root", required=True)
    remove.add_argument("--script", required=True)
    args = parser.parse_args()
    if args.command == "filter-publish":
        filter_publish(args.staged, args.site_root)
        return 0
    if args.command == "filter-remove":
        filter_remove(args.selector, args.site_root, args.script)
        return 0
    if args.command == "reconcile":
        if platform.system() != "Linux":
            raise RuntimeError("the reconcile helper is Linux-only")
        prefix = os.environ.get("PREFIX", "/usr/local")
        path = Path(f"{prefix}/etc/open-media-drc/audio-roles.conf")
        if not path.is_file():
            print(f"NOTICE: {path} does not exist; keeping the existing ALSA DAC device")
            return 0
        match = re.search(r'^OMDRC_AUDIO_DAC="([^"]+)"$', path.read_text(), re.M)
        if not match:
            raise RuntimeError(f"no configured DAC in {path}")
        linux_apply(parse_identity(match.group(1)), args.timeout, restart=False)
        return 0
    dac = parse_identity(args.dac)
    capture = parse_identity(args.capture, optional=True)
    if dac == capture:
        raise ValueError("DAC and capture identities must differ")
    if platform.system() == "FreeBSD":
        freebsd_apply(dac, capture, args.timeout)
    elif platform.system() == "Linux":
        if capture:
            raise ValueError("Linux capture selection is not operational")
        linux_apply(dac, args.timeout)
    else:
        raise RuntimeError(f"unsupported operating system: {platform.system()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
