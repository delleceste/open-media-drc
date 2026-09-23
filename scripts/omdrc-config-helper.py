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
MANAGED_MPD = re.compile(
    r'^(\s*device\s+)"[^"]+"(\s*#\s*omdrc-managed-mpd-dac\s*)$', re.M)
# The snd-aloop options line whose timer_source has to follow the selected DAC,
# so the loopback is clocked by the DAC rather than by a free-running hrtimer.
# See etc/modprobe.d/omdrc-snd-aloop.conf for why that matters.
MANAGED_ALOOP = re.compile(
    r'^(\s*options\s+snd-aloop\b[^\n]*?)timer_source="[^"]*"([^\n]*'
    r'#\s*omdrc-managed-aloop-timer\s*)$', re.M)
ALOOP_MODPROBE = "/etc/modprobe.d/omdrc-snd-aloop.conf"
ALOOP_TIMER_PARAM = "/sys/module/snd_aloop/parameters/timer_source"
SYSTEMCTL = "/usr/bin/systemctl"
MODPROBE = "/usr/bin/modprobe"


def run(argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print("$ " + " ".join(repr(item) if " " in item else item for item in argv), flush=True)
    # A reconciler may daemonize BruteFIR, which inherits stdout.  Capturing
    # through PIPE makes subprocess.run wait for that daemon to close the pipe
    # even after the command itself is done.  A regular file has no such EOF
    # dependency on descendants.
    with tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8") as output:
        result = subprocess.run(argv, text=True, stdout=output,
                                stderr=subprocess.STDOUT)
        # Reopen instead of seeking the writer: daemon descendants may still
        # hold it and must not share our read position.
        with open(output.name, encoding="utf-8", errors="replace") as reader:
            result.stdout = reader.read()
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


# How many cards each role remembers.  Enough for every DAC a box travels
# between; bounded so a long history of experiments does not grow forever.
REMEMBERED = 8


def remember(saved: str, chosen: str, drop: tuple[str, ...] = ()) -> str:
    """The role's new card list: `chosen` first, then what was saved before.

    A role is a comma-separated list, most recently applied first, and the
    reconciler takes the first entry that is attached (audio_pick in
    omdrc_audio, linux_pick here).  So applying the office DAC does not forget
    the home one: moving back is a plug-in, not another Apply.  `drop` removes
    identities outright -- the other role's card, and capture cards the user
    saw attached and chose to disable.  Hand-written description entries
    ("ESI U24XL") are kept as they are; only USB ids are compared."""
    gone = {item.lower() for item in (chosen, *drop) if item}
    kept = [chosen] if chosen else []
    for item in saved.split(","):
        item = item.strip()
        if item and item.lower() not in gone and item not in kept:
            kept.append(item)
    return ",".join(kept[:REMEMBERED])


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


def freebsd_apply(dac: str, capture: str, timeout: int,
                  forget_capture: tuple[str, ...] = ()) -> None:
    saved = {}
    for key in ("omdrc_audio_dac", "omdrc_audio_capture"):
        # Unset is exit 1 with the complaint on (merged) stdout: that is "".
        result = run(["/usr/sbin/sysrc", "-n", key], check=False)
        saved[key] = result.stdout.strip() if result.returncode == 0 else ""
    dacs = remember(saved["omdrc_audio_dac"], dac, (capture,))
    captures = remember(saved["omdrc_audio_capture"], capture, (dac, *forget_capture))
    run(["/usr/sbin/sysrc", f"omdrc_audio_dac={dacs}"])
    run(["/usr/sbin/sysrc", f"omdrc_audio_capture={captures}"])
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


def linux_pick(saved: str, role: str, exclude: str = "") -> str:
    """The attached card a remembered role list resolves to, or "".

    The known-device policy of the manual (sec:known-dac-policy), as
    audio_pick applies it on FreeBSD: the first entry, the explicit
    selection, wins when attached; otherwise exactly one attached card from
    the rest of the list takes the role; two or more is refused, because
    choosing between known cards is the operator's job.  Linux roles hold USB
    ids only; anything else in the list is skipped."""
    cards = linux_usb_cards()
    hits = []
    for position, item in enumerate(saved.split(",")):
        item = item.strip().lower()
        if not IDENTITY.fullmatch(item) or item == exclude:
            continue
        matches = [card for card in cards if identity_matches(item, card)]
        if len(matches) > 1:
            raise RuntimeError(f"configured {role} identity {item} matches "
                               f"{len(matches)} attached ALSA cards")
        if matches and position == 0:
            return item
        if matches:
            hits.append(item)
    if len(hits) > 1:
        raise RuntimeError(
            f"several known {role} cards are attached ({', '.join(hits)}) and the "
            "selected one is not; choose one on /configuration")
    return hits[0] if hits else ""


def linux_pick_capture(saved: str) -> str:
    """linux_pick for the capture role, which is never worth an error.

    The CD input is optional: the interface may be at home, unplugged for
    want of a USB port, or just not needed today.  Whatever stops it from
    resolving -- absent, or ambiguous -- the DAC role and the chain must still
    come up, so this reports and carries on without it."""
    try:
        capture = linux_pick(saved, "capture")
    except RuntimeError as error:
        print(f"NOTICE: {error}; continuing without the capture interface")
        return ""
    if saved and not capture:
        print(f"NOTICE: no configured capture interface ({saved}) is attached; "
              "continuing without it")
    return capture


def linux_saved_roles(prefix: str) -> dict[str, str]:
    path = Path(f"{prefix}/etc/open-media-drc/audio-roles.conf")
    saved = path.read_text() if path.is_file() else ""
    roles = {}
    for role in ("dac", "capture"):
        match = re.search(rf'^OMDRC_AUDIO_{role.upper()}="([^"]*)"$', saved, re.M)
        roles[role] = match.group(1) if match else ""
    return roles


def linux_resolve(identity: str, role: str) -> dict:
    matches = [card for card in linux_usb_cards() if identity_matches(identity, card)]
    if len(matches) != 1:
        raise RuntimeError(
            f"configured {role} identity resolves to {len(matches)} attached ALSA cards")
    return matches[0]


def linux_alsa_card_id(card: dict) -> str:
    """Resolve the kernel's ALSA ID after selecting hardware by USB identity."""
    card_id = Path(f"/proc/asound/card{card['number']}/id").read_text().strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", card_id) or card_id.isdigit():
        raise RuntimeError(f"invalid ALSA card ID: {card_id!r}")
    return card_id


def linux_browser_alsa(prefix: str, dac: dict | None,
                       capture: dict | None) -> None:
    """Publish desktop routes from the same USB roles as MPD and BruteFIR.

    The template's presence opts an installed Linux system into this feature.
    Use an unavailable named card when no DAC is selected/attached: falling back
    to card zero could send audio to an unrelated output.
    """
    template = Path(prefix) / "share/open-media-drc/asoundrc.linux.conf.in"
    if not template.is_file():
        return
    dac_id = linux_alsa_card_id(dac) if dac else "OMDRCNoDAC"
    capture_pcm = (f"hw:CARD={linux_alsa_card_id(capture)},DEV=0"
                   if capture else "null")
    rendered = template.read_text().replace("@_browser_dac@", dac_id).replace(
        "@_browser_capture_pcm@", capture_pcm)
    destination = Path(prefix) / "etc/open-media-drc/browser-alsa.conf"
    if not destination.is_file() or destination.read_text() != rendered:
        atomic_text(destination, rendered)
    print(f"BROWSER ALSA: playback={dac_id}, capture={capture_pcm}")


def linux_browser_alsa_refresh() -> None:
    """Installation-time refresh without touching the DRC lifecycle."""
    prefix = os.environ.get("PREFIX", "/usr/local")
    saved = linux_saved_roles(prefix)
    selected = {}
    capture = linux_pick_capture(saved["capture"])
    for role, identity in (("capture", capture),
                           ("dac", linux_pick(saved["dac"], "DAC", capture))):
        selected[role] = linux_resolve(identity, role) if identity else None
    if selected["dac"] is None:
        print("NOTICE: browser output is unavailable; select an attached DAC on /configuration")
    linux_browser_alsa(prefix, selected["dac"], selected["capture"])


def linux_aloop_timer(card: str) -> bool:
    """Point snd-aloop's timer_source at the DAC card, if the file is there.

    Return whether this installation manages the timer source.  A box that
    never installed the modprobe.d file has chosen the hrtimer default, which
    is not a reason to fail an otherwise successful DAC selection."""
    path = Path(ALOOP_MODPROBE)
    if not path.is_file():
        print(f"NOTICE: {path} does not exist; snd-aloop keeps its default timer source")
        return False
    text = path.read_text()
    changed, count = MANAGED_ALOOP.subn(rf'\1timer_source="hw:{card},0,0"\2', text)
    if count != 1:
        print(f"NOTICE: no '# omdrc-managed-aloop-timer' options line in {path}; "
              "leaving the loopback timer source alone")
        return False
    if changed != text:
        atomic_text(path, changed)
        print(f"NOTICE: snd-aloop timer_source set to hw:{card},0,0")
    return True


def linux_reload_aloop_timer(card: str) -> bool:
    """Apply a managed snd-aloop timer change to the running module.

    snd-aloop copies timer_source at module load; rewriting modprobe.d alone
    leaves the old physical clock active until reboot.  Stop the DRC service
    first so its ExecStop releases MPD, BruteFIR and the loopback in the safe
    order, reload only when the live value differs, and tell the caller whether
    an already-active chain must be restored after all role files are written.
    """
    wanted = f"hw:{card},0,0"
    parameter = Path(ALOOP_TIMER_PARAM)

    def live_timer() -> str:
        # The sysfs parameter is an array, rendered as
        # "hw:C,D,S,(null),(null),...".  The commas inside the first PCM timer
        # name are therefore not array separators we can split on directly.
        match = re.match(r"^(hw:[^,]+,[^,]+,[^,]+|[^,]*)",
                         parameter.read_text(errors="replace").strip())
        return match.group(1) if match else ""

    if parameter.is_file():
        current = live_timer()
        if current == wanted:
            return False

    status = run([SYSTEMCTL, "is-active", "drc-usb-audio.service"], check=False)
    was_active = status.stdout.strip() == "active"
    if was_active:
        run([SYSTEMCTL, "stop", "drc-usb-audio.service"])
    if parameter.is_file():
        run([MODPROBE, "-r", "snd-aloop"])
    run([MODPROBE, "snd-aloop"])

    if not parameter.is_file():
        raise RuntimeError("snd-aloop loaded without publishing timer_source")
    loaded = live_timer()
    if loaded != wanted:
        raise RuntimeError(f"snd-aloop timer_source is {loaded!r}, wanted {wanted!r}")
    print(f"VERIFIED: live snd-aloop timer_source={wanted}")
    return was_active


def linux_apply(dac: str, timeout: int, restart: bool = True,
                capture: str = "", dacs: str = "", captures: str = "") -> None:
    """Point everything at `dac` and `capture`, and save the role lists.

    `dacs`/`captures` are the remembered lists to store (see remember); they
    default to just the cards being applied."""
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
    if count == 0:
        # The defaults file is hand-edited and no install ever overwrites it, so
        # a copy predating the marker keeps rejecting every DAC selection until
        # someone says how to fix it.  Say it here rather than in a doc.
        raise RuntimeError(
            f"{defaults} has no '# omdrc-managed-dac' output device.\n"
            "       That marker names the line this helper rewrites; a defaults "
            "file written before it existed needs it added once.\n"
            "       `make user-install` does it, or by hand — inside the "
            "output { device: \"alsa\" { … } } block, end the device line with:\n"
            '           device: "hw:0,0"; # omdrc-managed-dac')
    if count > 1:
        raise RuntimeError(
            f"{defaults} carries '# omdrc-managed-dac' on {count} device lines; "
            "exactly one output device may be managed")
    linux_browser_alsa(prefix, selected, chosen_capture)
    atomic_text(defaults, changed, owner=(account.pw_uid, account.pw_gid))
    # MPD's direct/no-DRC output must follow the same physical role. Card
    # numbers change when USB interfaces enumerate in a different order.
    mpd_conf = Path(prefix) / "etc/open-media-drc/mpd.conf"
    mpd_dac_moved = False
    if mpd_conf.is_file():
        mpd_text = mpd_conf.read_text()
        mpd_changed, mpd_count = MANAGED_MPD.subn(
            rf'\1"hw:{selected["number"]},0"\2', mpd_text)
        if mpd_count != 1:
            raise RuntimeError(
                f"{mpd_conf} has {mpd_count} '# omdrc-managed-mpd-dac' device lines; "
                "exactly one OKTO-DAC output must be managed")
        mpd_dac_moved = mpd_changed != mpd_text
        atomic_text(mpd_conf, mpd_changed)
    manages_aloop = linux_aloop_timer(selected["number"])
    role_conf = Path(f"{prefix}/etc/open-media-drc/audio-roles.conf")
    atomic_text(role_conf, f'OMDRC_AUDIO_DAC="{dacs or dac}"\n'
                           f'OMDRC_AUDIO_CAPTURE="{captures or capture}"\n')
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
                       f"capture_id={capture}\n"
                       f"dac_wanted={dacs or dac}\n"
                       f"capture_wanted={captures or capture}\n")
    # MPD reads audio_output only at startup, so rewriting the device line
    # above changes nothing for the daemon that is already running: it keeps
    # playing to whatever card it resolved when it started.  That stays
    # invisible for as long as DRC is on — MPD only writes to the loopback
    # then, and brutefir opens the DAC from its own defaults file — and
    # surfaces solely as "No DRC plays nothing", because the direct output is
    # the one line that moved.  Seen exactly so on this box: mpd started at
    # 19:46:02 holding hw:0,0 and this helper rewrote the file to hw:2,0 at
    # 19:47:28, leaving the direct output pointed at the wrong USB interface
    # (the measurement card) for hours, with every other symptom absent.
    #
    # Restart only when the line actually moved, and before drc-usb-audio's
    # `omdrc restore` below re-selects MPD's outputs, so that restore is
    # talking to the daemon that will still be there afterwards.  Non-fatal:
    # a box whose MPD is not systemd-managed must still finish reconciling.
    if mpd_dac_moved:
        result = run([SYSTEMCTL, "restart", "mpd.service"], check=False)
        if result.returncode == 0:
            print(f"RESTARTED: mpd.service (OKTO-DAC -> hw:{selected['number']},0)")
        else:
            print(f"WARNING: mpd.conf now says hw:{selected['number']},0 but "
                  "mpd.service could not be restarted; MPD keeps its old "
                  "device until it is restarted by hand "
                  "(No DRC will play to the wrong card)")

    restore_active = (linux_reload_aloop_timer(selected["number"])
                      if manages_aloop else False)
    restart = restart or restore_active
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
    # Attached capture cards the user chose to disable: forgotten, so they do
    # not take the role back on the next plug-in.  Absent ones stay remembered.
    apply.add_argument("--forget-capture", action="append", default=[])
    apply.add_argument("--timeout", type=int, default=120)
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--timeout", type=int, default=120)
    sub.add_parser("browser-alsa-refresh")
    publish = sub.add_parser("filter-publish")
    publish.add_argument("--staged", required=True)
    publish.add_argument("--site-root", required=True)
    remove = sub.add_parser("filter-remove")
    remove.add_argument("--selector", required=True)
    remove.add_argument("--site-root", required=True)
    remove.add_argument("--script", required=True)
    args = parser.parse_args()
    if args.command == "browser-alsa-refresh":
        if platform.system() != "Linux":
            raise RuntimeError("browser-alsa-refresh is Linux-only")
        linux_browser_alsa_refresh()
        return 0
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
        saved = linux_saved_roles(prefix)
        if not saved["dac"]:
            raise RuntimeError(f"no configured DAC in {path}")
        # A capture card that is not plugged in must not take the DAC down with
        # it: the box still plays music, it just has no CD input this boot.
        capture = linux_pick_capture(saved["capture"])
        dac = linux_pick(saved["dac"], "DAC", capture)
        if not dac:
            raise RuntimeError(f"none of the configured DACs ({saved['dac']}) "
                               "is attached")
        linux_apply(dac, args.timeout, restart=False, capture=capture,
                    dacs=saved["dac"], captures=saved["capture"])
        return 0
    dac = parse_identity(args.dac)
    capture = parse_identity(args.capture, optional=True)
    if dac == capture:
        raise ValueError("DAC and capture identities must differ")
    forget = tuple(parse_identity(item) for item in args.forget_capture)
    if platform.system() == "FreeBSD":
        freebsd_apply(dac, capture, args.timeout, forget)
    elif platform.system() == "Linux":
        prefix = os.environ.get("PREFIX", "/usr/local")
        saved = linux_saved_roles(prefix)
        linux_apply(dac, args.timeout, capture=capture,
                    dacs=remember(saved["dac"], dac, (capture,)),
                    captures=remember(saved["capture"], capture, (dac, *forget)))
    else:
        raise RuntimeError(f"unsupported operating system: {platform.system()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
