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
# The snd-aloop options line whose timer_source has to follow the selected DAC,
# so the loopback is clocked by the DAC rather than by a free-running hrtimer.
# See etc/modprobe.d/omdrc-snd-aloop.conf for why that matters.
MANAGED_ALOOP = re.compile(
    r'^(\s*options\s+snd-aloop\b[^\n]*?)timer_source="[^"]*"([^\n]*'
    r'#\s*omdrc-managed-aloop-timer\s*)$', re.M)
ALOOP_MODPROBE = "/etc/modprobe.d/omdrc-snd-aloop.conf"


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


def canonical_hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def bundle_identity(manifest: dict) -> dict:
    if manifest.get("schema") != 2:
        raise ValueError(f"unsupported bundle schema: {manifest.get('schema')!r}")
    return {
        "schema": manifest["schema"],
        "geometry": manifest["geometry"],
        "variant": manifest["variant"],
        "design_id": manifest.get("design_id", manifest["variant"]),
        "description": manifest["description"],
        "source_provenance_sha256": canonical_hash(manifest["source"]),
        "source_artifacts": {
            role: item["sha256"] for role, item in manifest["source"]["artifacts"].items()
        },
        "runtime": {
            rate: {
                "config": item["config"],
                "config_sha256": item["config_sha256"],
                "format": item["format"],
                "attenuation_db": item["attenuation_db"],
                "channels": {
                    channel: data["sha256"]
                    for channel, data in item["channels"].items()
                },
            }
            for rate, item in manifest["runtime"]["rates"].items()
        },
        "analysis_sha256": manifest["analysis"]["sha256"],
    }


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
    if manifest_path.is_symlink():
        raise ValueError("staged runtime manifest must be a regular file")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("verification", {}).get("status") != "verified":
        raise ValueError("staged manifest status is not verified")
    if canonical_hash(bundle_identity(manifest)) != manifest.get("bundle_id"):
        raise ValueError("staged manifest bundle ID does not match its content")
    geometry = manifest.get("geometry", "")
    design = manifest.get("design_id", manifest.get("variant", ""))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", geometry or "") or not \
       re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", design or ""):
        raise ValueError("manifest has an unsafe geometry or design ID")
    geometry_root = Path("filters") / geometry
    expected: dict[Path, str] = {}

    def expect(relative: Path, wanted: str) -> None:
        previous = expected.setdefault(relative, wanted)
        if previous != wanted:
            raise ValueError(f"conflicting hashes for staged bundle path: {relative}")

    def staged_source(relative: Path) -> Path:
        source = (staged / relative).resolve()
        try:
            source.relative_to(staged)
        except ValueError as error:
            raise ValueError(f"staged bundle path escapes staging: {relative}") from error
        if not source.is_file() or source.is_symlink():
            raise RuntimeError(f"staged bundle path is not a regular file: {relative}")
        return source

    for item in manifest["source"]["artifacts"].values():
        expect(geometry_root / safe_relative(item["bundle_path"]), item["sha256"])
    measurements = manifest["source"].get("measurements", {})
    if measurements.get("bundle_path"):
        expect(geometry_root / safe_relative(measurements["bundle_path"]),
               measurements["sha256"])
    analysis = manifest["analysis"]
    expect(geometry_root / safe_relative(analysis["path"]), analysis["sha256"])
    for runtime in manifest["runtime"]["rates"].values():
        expect(safe_relative(runtime["config"]), runtime["config_sha256"])
        for channel in runtime["channels"].values():
            expect(geometry_root / safe_relative(channel["path"]), channel["sha256"])
    live_manifest = site / geometry_root / "provenance" / f"{design}.json"
    try:
        live_data = json.loads(live_manifest.read_text())
    except (OSError, json.JSONDecodeError):
        live_data = {}
    if live_data.get("bundle_id") == manifest.get("bundle_id") and all(
            (site / relative).is_file() and not (site / relative).is_symlink() and
            digest(site / relative) == wanted for relative, wanted in expected.items()):
        print("UNCHANGED: this verified bundle is already installed; nothing was written.")
        return
    source_recipe = geometry_root / "provenance" / f"{design}.source.json"
    if (staged / source_recipe).is_file() and not (staged / source_recipe).is_symlink():
        expect(source_recipe, digest(staged_source(source_recipe)))
    manifest_relative = geometry_root / "provenance" / f"{design}.json"
    expect(manifest_relative, digest(manifest_path))
    for relative, wanted in expected.items():
        source = staged_source(relative)
        if digest(source) != wanted:
            raise RuntimeError(f"staged file failed hash verification: {relative}")
        destination = site / relative
        if destination.exists() and (destination.is_symlink() or
                                     not destination.is_file() or
                                     digest(destination) != wanted):
            raise RuntimeError(f"design already exists with different bytes: {relative}")
    derived_configs: list[tuple[Path, str]] = []
    for runtime in manifest["runtime"]["rates"].values():
        template_relative = safe_relative(runtime["config"])
        if template_relative.name.endswith(".conf.in"):
            template = (staged / template_relative).read_text(encoding="utf-8")
            if "@REPO_DIR@" not in template:
                raise RuntimeError(f"config template has no @REPO_DIR@: {template_relative}")
            rendered_relative = template_relative.with_name(
                template_relative.name[:-len(".in")])
            derived_configs.append(
                (rendered_relative, template.replace("@REPO_DIR@", str(site))))

    def publish_one(relative: Path) -> None:
        source, destination = staged_source(relative), site / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file() and digest(destination) == expected[relative]:
            return
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

    # Templates and bytes first, deterministic runnable configs second, and the
    # manifest last: readers never observe a claim before its derived runtime
    # is complete.
    for relative in expected:
        if relative != manifest_relative:
            publish_one(relative)
    for relative, rendered in derived_configs:
        destination = site / relative
        if not destination.is_file() or destination.read_text(encoding="utf-8") != rendered:
            atomic_text(destination, rendered)
            print(f"RENDERED: {relative}")
    publish_one(manifest_relative)
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


def linux_resolve(identity: str, role: str) -> dict:
    matches = [card for card in linux_usb_cards() if identity_matches(identity, card)]
    if len(matches) != 1:
        raise RuntimeError(
            f"configured {role} identity resolves to {len(matches)} attached ALSA cards")
    return matches[0]


def linux_aloop_timer(card: str) -> None:
    """Point snd-aloop's timer_source at the DAC card, if the file is there.

    Best-effort on purpose.  The loopback module is loaded at boot, so this
    cannot take effect now anyway, and a box that never installed the
    modprobe.d file has simply chosen the hrtimer default — neither is a reason
    to fail an otherwise successful DAC selection."""
    path = Path(ALOOP_MODPROBE)
    if not path.is_file():
        print(f"NOTICE: {path} does not exist; snd-aloop keeps its default timer source")
        return
    text = path.read_text()
    changed, count = MANAGED_ALOOP.subn(rf'\1timer_source="hw:{card},0,0"\2', text)
    if count != 1:
        print(f"NOTICE: no '# omdrc-managed-aloop-timer' options line in {path}; "
              "leaving the loopback timer source alone")
        return
    if changed == text:
        return
    atomic_text(path, changed)
    print(f"NOTICE: snd-aloop timer_source set to hw:{card},0,0 — "
          "takes effect on the next boot")


def linux_apply(dac: str, timeout: int, restart: bool = True,
                capture: str = "") -> None:
    selected = linux_resolve(dac, "DAC")
    chosen_capture = linux_resolve(capture, "capture") if capture else None
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
    linux_aloop_timer(selected["number"])
    role_conf = Path(f"{prefix}/etc/open-media-drc/audio-roles.conf")
    atomic_text(role_conf, f'OMDRC_AUDIO_DAC="{dac}"\n'
                           f'OMDRC_AUDIO_CAPTURE="{capture}"\n')
    # /run/omdrc/audio.roles is what the CD bridge reads to find the capture
    # interface (scripts/omdrc-cdin-alsaloop), so the capture half has to be
    # published here for the same reason the DAC half is: nothing else on Linux
    # turns a USB identity into a stable ALSA card number.
    state = Path("/run/omdrc/audio.roles")
    atomic_text(state, f"dac_unit={selected['number']}\n"
                       f"dac_desc={selected['name']}\n"
                       f"dac_id={dac}\n"
                       f"capture_unit={chosen_capture['number'] if chosen_capture else ''}\n"
                       f"capture_desc={chosen_capture['name'] if chosen_capture else ''}\n"
                       f"capture_id={capture}\n")
    if not restart:
        print(f"RESOLVED: {dac} -> ALSA card {selected['number']}"
              + (f", capture {capture} -> ALSA card {chosen_capture['number']}"
                 if chosen_capture else ""))
        return
    run(["/usr/bin/systemctl", "restart", "drc-usb-audio.service"])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = run(["/usr/bin/systemctl", "is-active", "drc-usb-audio.service"],
                     check=False)
        if status.stdout.strip() == "active":
            print(f"VERIFIED: {dac} -> ALSA card {selected['number']}")
            if chosen_capture:
                print(f"VERIFIED: capture {capture} -> ALSA card "
                      f"{chosen_capture['number']} ({chosen_capture['name']})")
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
        saved = path.read_text()
        match = re.search(r'^OMDRC_AUDIO_DAC="([^"]+)"$', saved, re.M)
        if not match:
            raise RuntimeError(f"no configured DAC in {path}")
        capture_match = re.search(r'^OMDRC_AUDIO_CAPTURE="([^"]*)"$', saved, re.M)
        capture = parse_identity(capture_match.group(1), optional=True) \
            if capture_match else ""
        # A capture card that is not plugged in must not take the DAC down with
        # it: the box still plays music, it just has no CD input this boot.
        if capture and not [c for c in linux_usb_cards() if identity_matches(capture, c)]:
            print(f"NOTICE: configured capture interface {capture} is not attached; "
                  "publishing the DAC role only")
            capture = ""
        linux_apply(parse_identity(match.group(1)), args.timeout, restart=False,
                    capture=capture)
        return 0
    dac = parse_identity(args.dac)
    capture = parse_identity(args.capture, optional=True)
    if dac == capture:
        raise ValueError("DAC and capture identities must differ")
    if platform.system() == "FreeBSD":
        freebsd_apply(dac, capture, args.timeout)
    elif platform.system() == "Linux":
        linux_apply(dac, args.timeout, capture=capture)
    else:
        raise RuntimeError(f"unsupported operating system: {platform.system()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
