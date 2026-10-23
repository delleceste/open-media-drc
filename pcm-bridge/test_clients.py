#!/usr/bin/env python3
"""Real MPD and BruteFIR tests on isolated nodes; never opens a hardware DAC."""
import argparse
import errno
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import time
import wave

parser = argparse.ArgumentParser()
parser.add_argument("build", type=Path)
args = parser.parse_args()
build = args.build.resolve()
repo = Path(__file__).resolve().parent.parent


def stop(proc):
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)


@contextmanager
def process(command, directory, env=None):
    with tempfile.TemporaryFile(mode="w+") as log:
        proc = subprocess.Popen(command, cwd=directory, stdout=log, stderr=log,
                                start_new_session=True, env=env)
        try:
            yield proc
        finally:
            stop(proc)
            log.seek(0)
            print(log.read(), end="")


def wait_for(predicate, proc, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        if proc.poll() is not None:
            raise RuntimeError(f"process exited: {proc.returncode}")
        time.sleep(.02)
    raise TimeoutError("client readiness/output timed out")


@contextmanager
def bridge(directory, label):
    names = [f"dsp.omdrcclient_{os.getpid()}_{label}_{x}" for x in ("play", "loop")]
    paths = [Path("/dev") / x for x in names]
    assert not any(p.exists() for p in paths)
    with process([str(build / "omdrc-pcm-bridge"), "--rate", "48000",
                  "--play", names[0], "--capture", names[1]], directory) as proc:
        wait_for(lambda: all(p.exists() for p in paths), proc)
        yield paths
    assert proc.returncode == 0
    assert not any(p.exists() for p in paths)


def command(stream, text):
    stream.write((text + "\n").encode())
    lines = []
    while True:
        line = stream.readline().decode().strip()
        if line == "OK":
            return lines
        if not line or line.startswith("ACK"):
            raise RuntimeError(f"MPD {text}: {line}")
        lines.append(line)


# Exact S32 values with headroom and low eight bits clear, suitable for the
# independent identity-convolution control as well as direct MPD comparison.
reference = b"".join(struct.pack("<ii", ((i * 137) % 65536 - 32768) * 256,
                                ((i * 251 + 17) % 65536 - 32768) * 256)
                     for i in range(32768))
with tempfile.TemporaryDirectory(prefix="omdrc-pcm-clients-") as tmp:
    root = Path(tmp)
    mpd = shutil.which("musicpd") or shutil.which("mpd")
    bf = shutil.which("brutefir")
    if not mpd or not bf:
        raise RuntimeError("installed MPD and BruteFIR required")
    with wave.open(str(root / "reference.wav"), "wb") as wav:
        wav.setparams((2, 4, 48000, 0, "NONE", "not compressed"))
        wav.writeframes(reference + bytes(32768 * 8))
        # Trailing guard silence keeps the marked region before MPD EOS/reset.
        # EOS lifecycle is a separate production gate, not hidden by this test.
    with bridge(root, "mpd") as (play, capture):
        config = root / "mpd.conf"
        config.write_text(f'''music_directory "{root}"
playlist_directory "{root}"
db_file "{root}/database"
bind_to_address "{root}/mpd.sock"
log_file "{root}/mpd.log"
audio_output {{
 type "oss"
 name "isolated byte test"
 device "{play}"
 format "48000:32:2"
 mixer_type "none"
}}
''')
        fd = os.open(capture, os.O_RDONLY | os.O_NONBLOCK)
        try:
            with process([mpd, "--no-daemon", "--stderr", str(config)], root) as proc:
                wait_for(lambda: (root / "mpd.sock").exists(), proc)
                with socket.socket(socket.AF_UNIX) as sock:
                    sock.settimeout(10)
                    sock.connect(str(root / "mpd.sock"))
                    with sock.makefile("rwb", buffering=0) as stream:
                        assert stream.readline().startswith(b"OK MPD")
                        command(stream, "update")
                        wait_for(lambda: not any(x.startswith("updating_db:") for x in command(stream, "status")), proc)
                        command(stream, 'add "reference.wav"')
                        command(stream, "play")
                        received = bytearray()
                        def capture_done():
                            try:
                                data = os.read(fd, 65536)
                            except BlockingIOError:
                                return False
                            except OSError as exc:
                                if exc.errno == errno.ENXIO and len(received) >= len(reference):
                                    return True  # MPD ended the session.
                                raise
                            received.extend(data)
                            return len(received) >= len(reference) + 32768 * 8
                        wait_for(capture_done, proc)
                        assert bytes(received[:len(reference)]) == reference, "MPD S32 output differs"
                        print("real MPD → bridge: exact 262144-byte comparison passed")
        finally:
            os.close(fd)
    with bridge(root, "bf") as (play, capture):
        defaults = (repo / "etc/open-media-drc/brutefir_defaults.conf").read_text()
        defaults = defaults.replace('filter_length: 8192,64;', 'filter_length: 1024,1;')
        defaults = defaults.replace('lock_memory: true;', 'lock_memory: false;')
        defaults = defaults.replace('show_progress: true;', 'show_progress: false;')
        # Isolate BruteFIR defaults and its convolver cache in this temporary tree.
        defaults = defaults.replace('"$XDG_CONFIG_HOME/BruteFIR/brutefir_convolver"', f'"{root}/convolver"')
        config = root / "bf.conf"
        (root / "xdg/BruteFIR").mkdir(parents=True)
        (root / "xdg/BruteFIR/brutefir_defaults.conf").write_text(defaults)
        config.write_text(f'''
sampling_rate: 48000;
coeff "identity" {{ filename: "dirac pulse"; }};
input "left_in", "right_in" {{ device: "oss" {{ device: "{capture}"; }}; }};
output "left_out", "right_out" {{ device: "file" {{ path: "{root}/output.raw"; }}; }};
filter "left" {{ from_inputs: "left_in"; to_outputs: "left_out"; coeff: "identity"; }};
filter "right" {{ from_inputs: "right_in"; to_outputs: "right_out"; coeff: "identity"; }};
''')
        fd = os.open(play, os.O_WRONLY | os.O_NONBLOCK)
        try:
            with process([bf, str(config)], root, env={**os.environ, "XDG_CONFIG_HOME": str(root / "xdg")}) as proc:
                payload = reference + bytes(8192 * 8)
                sent = 0
                def send_done():
                    global sent
                    try:
                        sent += os.write(fd, payload[sent:sent + 32768])
                    except BlockingIOError:
                        pass
                    return sent == len(payload)
                wait_for(send_done, proc)
                output = root / "output.raw"
                wait_for(lambda: output.exists() and output.stat().st_size >= len(reference) + 8192, proc)
                actual = output.read_bytes()
                offset = actual.find(reference)
                assert offset >= 0 and offset % 8 == 0, "identity BruteFIR output differs"
                print(f"bridge → real BruteFIR identity → file: exact reference found at byte {offset}")
        finally:
            os.close(fd)
print("real-client tests passed; no DAC was opened")
