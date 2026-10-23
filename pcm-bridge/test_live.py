#!/usr/bin/env python3
"""Explicit opt-in live CUSE test; never opens the DAC or production nodes."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

parser = argparse.ArgumentParser()
parser.add_argument("build", type=Path)
parser.add_argument("--daemon", type=Path, help="test an installed daemon with build-tree clients")
args = parser.parse_args()
build = args.build.resolve()
daemon = args.daemon.resolve() if args.daemon else build / "omdrc-pcm-bridge"
for iteration in range(5):
    suffix = f"{os.getpid()}_{iteration}"
    names = [f"dsp.omdrcbridge_test_{suffix}_{kind}" for kind in ("play", "loop")]
    paths = [Path("/dev") / name for name in names]
    assert not any(p.exists() for p in paths)
    with tempfile.TemporaryFile(mode="w+") as log:
        server = subprocess.Popen([str(daemon), "--rate", "48000",
                                   "--play", names[0], "--capture", names[1]], stderr=log)
        child = None
        try:
            for _ in range(100):
                if all(p.exists() for p in paths):
                    break
                if server.poll() is not None:
                    raise RuntimeError("bridge exited before readiness")
                time.sleep(.02)
            else:
                raise RuntimeError("device creation timed out")
            if iteration < 3:
                subprocess.run([str(build / "pcm-oss-test"), *map(str, paths)], check=True, timeout=30)
            else:
                mode = "block-read" if iteration == 3 else "block-write"
                child = subprocess.Popen([str(build / "pcm-oss-test"), *map(str, paths), mode],
                                         stdout=subprocess.PIPE, text=True)
                assert child.stdout.readline().strip() == "blocking client ready"
                time.sleep(.1)
                assert child.poll() is None, "client should be blocked until server shutdown"
            server.send_signal(signal.SIGUSR1)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
                raise RuntimeError("bridge teardown timed out")
            finally:
                log.seek(0)
                print(log.read(), end="")
        if child:
            output, _ = child.communicate(timeout=5)
            print(output, end="")
            assert child.returncode == 0
        assert server.returncode == 0, server.returncode
        assert not any(p.exists() for p in paths), "CUSE devices leaked"
print("live CUSE: three start/test/stop cycles plus active reader/writer shutdown passed")
