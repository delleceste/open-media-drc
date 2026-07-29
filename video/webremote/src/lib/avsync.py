"""A/V sync fine-tune: a small trim on top of mpv's baseline audio-delay.

`mpv-idle.sh` sets `--audio-delay` once at startup to the DRC audio-path latency
(filter group delay + brutefir partition — full derivation in
`../../../AV-SYNC-DELAY.md`). That number is exact for the two terms it covers,
but the remaining virtual_oss / snd-aloop buffering is runtime-dependent and is
"confirm by eye" territory. So the remote offers a trim on top of it:

    audio-delay = base + trim          (mpv sign: + delays the AUDIO)

`base` is whatever mpv was launched with and `trim` is ours, clamped to ±range
and persisted so a tuning survives a restart of mpv *or* of this app. Keeping the
two apart across those restarts is the whole subtlety here, and mpv's `pid`
property is what settles it — the store records which mpv the trim was last
pushed to:

  * same pid  -> our trim is already live, so `base = audio-delay - trim`;
  * new pid   -> a fresh mpv at its launch delay, so `base = audio-delay` and the
                 trim is re-applied to it.

Caveats: a change made outside this app (mpv's own audio-delay keybindings) is
not seen here and is overwritten by the next trim we send; and a stale stored pid
that happens to be reused by a *different* mpv would mis-read the baseline once
(harmless — the next trim from the UI re-anchors it).
"""
import json
import os
import threading

from . import mpvipc

_UNSET = object()

_lock = threading.Lock()
_pid = None            # pid of the mpv our trim was last pushed to
_base = 0.0            # that mpv's launch --audio-delay, in seconds
_trim = None           # our offset; None until loaded from the store
_base_known = False    # False = _base still has to be worked out from a live mpv


def _load(store: str) -> None:
    """Read the persisted trim (and the mpv it was applied to) once."""
    global _trim, _pid
    if _trim is not None:
        return
    try:
        with open(store, encoding="utf-8") as f:
            data = json.load(f)
        _trim = float(data["trim"])
        _pid = data.get("pid")
    except (OSError, ValueError, TypeError, KeyError):
        _trim, _pid = 0.0, None


def _save(store: str) -> None:
    try:
        os.makedirs(os.path.dirname(store), exist_ok=True)
        tmp = store + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"trim": _trim, "pid": _pid}, f)
        os.replace(tmp, store)
    except OSError:
        pass          # a lost tuning is not worth failing the request over


def _apply(sock: str) -> None:
    """Push base+trim to mpv, flashing its OSD so it is visible on the TV.

    The value goes as a *string*: mpv's `set` command parses its argument as
    option text and rejects a JSON number ("invalid parameter").
    """
    mpvipc.command(sock, ["osd-msg", "set", "audio-delay", f"{_base + _trim:.4f}"])


def _rebase(sock: str, store: str) -> bool:
    """Work out the baseline for the live mpv. Returns True when mpv answered."""
    global _pid, _base, _base_known
    delay = mpvipc.get_property(sock, "audio-delay", _UNSET)
    if delay is _UNSET:
        return False
    pid = mpvipc.get_property(sock, "pid")
    if pid == _pid and _base_known:
        return True
    if pid == _pid and pid is not None:
        _base, _base_known = float(delay) - _trim, True   # our trim is already live
        return True
    _pid, _base, _base_known = pid, float(delay), True    # fresh mpv at its launch delay
    if _trim:
        _apply(sock)
    _save(store)
    return True


def state(sock: str, store: str, rng: float, step: float) -> dict:
    """Current trim and the bounds the UI should offer."""
    with _lock:
        _load(store)
        try:
            running = _rebase(sock, store)
        except (mpvipc.MpvError, mpvipc.MpvNotRunning):
            running = False
        return {"running": running, "base": round(_base, 4) if running else None,
                "trim": _trim, "delay": round(_base + _trim, 4) if running else None,
                "range": rng, "step": step}


def set_trim(sock: str, store: str, rng: float, step: float, value: float) -> dict:
    """Clamp `value` to ±rng, send base+trim to mpv, persist. Raises on IPC errors."""
    global _trim
    with _lock:
        _load(store)
        prev = _trim
        try:
            if not _rebase(sock, store):
                raise mpvipc.MpvNotRunning("mpv is not running")
            _trim = round(max(-rng, min(rng, float(value))), 4)
            _apply(sock)
        except Exception:
            _trim = prev
            raise
        _save(store)
        return {"running": True, "base": round(_base, 4), "trim": _trim,
                "delay": round(_base + _trim, 4), "range": rng, "step": step}
