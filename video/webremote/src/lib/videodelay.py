"""The DRC audio path's own delay, for mpv's --audio-delay (video/lib/drc-audio.sh).

Room correction (brutefir) adds two delays that mpv must compensate by delaying
the video instead: the active filter's own group delay (its FIR impulse's peak
tap) and one brutefir partition (filter_length / rate). Both depend on which
filter is actually loaded right now -- a different filter or rate changes the
number -- so this reads it fresh from the running brutefir rather than using a
single hardcoded constant for every filter. Full derivation of what the two
terms mean: ../../../AV-SYNC-DELAY.md.

We assume the filter does not change mid-playback (room correction does not
flip filters while a movie is running), so one computation per app start/launch
is enough; see drc-audio.sh and app.py's startup use of this module.

Dependency-free (no numpy): `array` handles the common little-endian cases in
bulk, `struct` is the fallback for the rare big-endian one.
"""
import array
import re
import struct
import subprocess
from pathlib import Path

_RATE_RE = re.compile(r"sampling_rate:\s*(\d+)")
_LEN_RE = re.compile(r"filter_length:\s*(\d+)")
# filter_length usually lives here, not in the per-rate conf -- see
# _brutefir_partition_size in omdrc-ctrl/src/app.py, which this mirrors.
_DEFAULTS_CONF = Path("~/.config/BruteFIR/brutefir_defaults.conf").expanduser()
_COEFF_RE = re.compile(r'coeff\s+"[^"]*"\s*\{([^}]*)\}', re.S)
_FILENAME_RE = re.compile(r'filename:\s*"([^"]*)"')
_FORMAT_RE = re.compile(r'format:\s*"?(\w+)"?')

# array.array typecodes assume the host is little-endian (true of every target
# this project runs on); struct covers the big-endian formats explicitly.
_ARRAY_CODE = {"FLOAT64_LE": "d", "FLOAT32_LE": "f", "S32_LE": "i", "S16_LE": "h"}
_STRUCT_FMT = {"FLOAT64_BE": ">d", "FLOAT32_BE": ">f", "S32_BE": ">i", "S16_BE": ">h"}


def _active_conf_path() -> str | None:
    """The .conf of the currently-running brutefir, from its process args."""
    try:
        out = subprocess.run(["ps", "-ax", "-o", "args="], capture_output=True,
                              text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split()
        if not parts:
            continue
        prog = parts[0]
        if prog != "brutefir" and not prog.endswith("/brutefir"):
            continue
        for part in parts[1:]:
            if part.endswith(".conf"):
                return part
    return None


def _peak_index(data: bytes, fmt: str) -> int | None:
    """The index of the largest-magnitude sample -- the FIR impulse's peak tap."""
    code = _ARRAY_CODE.get(fmt)
    if code:
        samples = array.array(code)
        usable = len(data) - (len(data) % samples.itemsize)
        samples.frombytes(data[:usable])
    else:
        samples = [v for (v,) in struct.iter_unpack(_STRUCT_FMT.get(fmt, "<d"), data)]
    best_i, best_v = -1, -1.0
    for i, v in enumerate(samples):
        av = abs(v)
        if av > best_v:
            best_v, best_i = av, i
    return best_i if best_i >= 0 else None


def compute_seconds() -> float | None:
    """Group delay + one brutefir partition, in seconds; None if not computable
    (brutefir not running, or its config can't be parsed)."""
    conf_path = _active_conf_path()
    if not conf_path:
        return None
    try:
        text = Path(conf_path).read_text()
    except OSError:
        return None
    rate_m = _RATE_RE.search(text)
    if not rate_m:
        return None
    len_m = _LEN_RE.search(text)
    if not len_m:
        try:
            len_m = _LEN_RE.search(_DEFAULTS_CONF.read_text())
        except OSError:
            len_m = None
    if not len_m:
        return None
    rate, length = int(rate_m.group(1)), int(len_m.group(1))
    if rate <= 0 or length <= 0:
        return None
    partition_delay = length / rate

    coeff_m = _COEFF_RE.search(text)
    if not coeff_m:
        return partition_delay                            # no filter: nothing more to add
    body = coeff_m.group(1)
    fn_m, fmt_m = _FILENAME_RE.search(body), _FORMAT_RE.search(body)
    if not fn_m or fn_m.group(1) == "dirac pulse":
        return partition_delay                             # identity filter: no group delay
    fmt = (fmt_m.group(1) if fmt_m else "FLOAT64_LE").upper()
    fn = fn_m.group(1)
    if not Path(fn).is_absolute():
        fn = str(Path(conf_path).parent / fn)
    try:
        data = Path(fn).read_bytes()
    except OSError:
        return partition_delay
    peak = _peak_index(data, fmt)
    if peak is None:
        return partition_delay
    return peak / rate + partition_delay


if __name__ == "__main__":
    import sys
    value = compute_seconds()
    if value is None:
        print("could not determine the active filter's delay", file=sys.stderr)
        sys.exit(1)
    print(f"{value:.4f}")
