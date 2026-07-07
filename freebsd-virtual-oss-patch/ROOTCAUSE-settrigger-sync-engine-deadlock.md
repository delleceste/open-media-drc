# Root cause of the virtual_oss "155% CPU + frozen chain" livelock: SETTRIGGER vs the synchronized-loopback engine wait

**Captured live 2026-07-06** on the wedged instance (pid 4733, `-r 44100 -i 8
-C 2 -c 2 -b 32 -s 200ms -f /dev/null -a 0 -d dsp.play -L dsp.loop`), with
`procstat -kk` + gdb attach (debug symbols from the base build). This is the
**steady-state livelock** that precedes (and feeds) the teardown wedge analysed
in `ROOTCAUSE-cuse_client_open-refleak.md` — it is what makes brutefir "hammer"
the device and what makes every stop from this state a reboot risk.

**Fix:** [`virtual_oss-settrigger-sync-deadlock.patch`](virtual_oss-settrigger-sync-deadlock.patch)
(userland, `usr.sbin/virtual_oss`) — built + installed 2026-07-06, pending
post-reboot validation.

## Symptom

Minutes-to-seconds after a DRC chain (re)start, playback freezes: MPD's
position stops advancing, brutefir starves (`dsp0` underruns, DAC shows
pause), and `virtual_oss` burns ~150–200% CPU while both clients sit
unkillable-looking in `cuse-cli` waits. Stopping anything from this state
walks straight into the cuse teardown minefield (bug 296291).

## Captured stacks (the whole story in three lines)

```
virtual_oss main/DSP thread:
  vclient_read_linear (pvc=0x3a92e9e00000, total=17640)  virtual_oss.c:100  ← atomic_wait()
  virtual_oss_process                                    virtual_oss.c:593  ← section (4): loopback TX

brutefir main thread:      select() → cuse_client_poll → cuse_client_receive_command_locked (never answered usefully)
musicpd output thread:     poll()   → cuse_client_poll → cuse_client_receive_command_locked
```

And the deadlocked client's state, read via gdb from the live process:

```
pvc->profile->synchronized = 1     (it is the -L dsp.loop client = brutefir)
pvc->rx_enabled = 1
pvc->tx_enabled = 0                ← !! the engine sleeps in code guarded by tx_enabled != 0
pvc->tx_samples = 0                (client never wrote a byte — it is open O_RDONLY)
pvc->tx_ring[1] = { len_write = 0 }
```

`procstat -f` confirms brutefir holds `/dev/dsp.loop` **read-only** (`r-------`).

## The bug pair (`usr.sbin/virtual_oss`)

### 1. `SNDCTL_DSP_SETTRIGGER` ignores the open mode (`main.c`)

```c
case SNDCTL_DSP_SETTRIGGER:
    if (data.val & PCM_ENABLE_INPUT)  pvc->rx_enabled = 1; ...
    if (data.val & PCM_ENABLE_OUTPUT) pvc->tx_enabled = 1; ...   /* no fflags check! */
```

A capture-only client that sets `PCM_ENABLE_OUTPUT` (brutefir's OSS dai
triggers both directions with one call — harmless on kernel pcm, which honours
the fd's open mode) flips `tx_enabled = 1` on an fd that **can never write**.

### 2. The synchronized wait loops never re-check the trigger state, and the trigger/halt handlers never wake them (`virtual_oss.c` + `main.c`)

For a `-L` (synchronized) loopback client the DSP engine's block loop does, in
section (4):

```c
if (pvc->tx_enabled == 0) continue;            /* checked ONCE, outside the wait */
vclient_read_linear(pvc, &pvc->tx_ring[0], ...):
    while (1) {
        ... read ring ...
        if (!synchronized || sync_wakeup || total == 0) break;
        atomic_wait();                          /* re-checks NOTHING else */
    }
```

So the engine parked itself waiting for play data from brutefir. brutefir then
cleared the bogus output trigger (`SETTRIGGER(INPUT)` / `HALT_OUTPUT` — the
live state shows `tx_enabled` back at 0), but those ioctl paths **do not call
`atomic_wakeup()`**, and even when other broadcasts arrive, the loop's break
condition **never looks at `tx_enabled`** — the premise of the wait is gone
and nobody re-evaluates it. The engine sleeps forever.

The arming window (engine samples `tx_enabled == 1` between brutefir's two
ioctls) is microseconds wide against a 200 ms block cadence — hence
**intermittent**: most runs miss it, one in N runs parks the engine.

## Why the whole system then melts

With the engine parked mid-block:

- No capture data is produced → brutefir's `select()` poll never becomes
  readable; the cuse poll answer/re-arm cycle between the kernel and the
  worker threads becomes a hot loop → the observed 150–200% CPU.
- MPD's `dsp.play` ring never drains → its output thread blocks in `poll()`
  forever.
- Both clients are now exactly the "clients still attached, hammering the
  server" population that makes teardown hit the `cuse_client_open()` ref-leak
  (`ROOTCAUSE-cuse_client_open-refleak.md`) → unkillable `D<E` server, pinned
  `cuse.ko`, reboot.

## The fix (all in `usr.sbin/virtual_oss`, upstreamable)

1. **`SETTRIGGER` honours the open mode** (`fflags & CUSE_FFLAG_READ/WRITE`,
   which cuse passes to every ioctl): `PCM_ENABLE_OUTPUT` on a read-only fd is
   now a no-op, matching kernel pcm semantics. Removes this arming vector.
2. **Trigger/halt changes wake the engine**: `SETTRIGGER`,
   `SNDCTL_DSP_HALT_OUTPUT`, `SNDCTL_DSP_HALT_INPUT` now `atomic_wakeup()`
   after flipping `rx_enabled`/`tx_enabled`.
3. **The sync wait loops re-check their premise**: `vclient_read_linear()`
   breaks when `tx_enabled == 0`, `vclient_write_linear()` breaks when
   `rx_enabled == 0` — a client disabling a direction can no longer strand the
   engine.
4. **Never sync-wait for a client that has never written**: new
   `pvc->tx_written` latch (set on first successful `write()`);
   `vclient_read_linear()` won't sleep for a synchronized client with
   `tx_written == 0`. Covers the remaining case of a writable-but-silent
   client arming the output trigger.

(1)–(3) close the observed deadlock and its mirror image on the rx side; (4)
is defence in depth. A synchronized *player* still paces the engine exactly as
before once it has written its first block.

## Validation plan (after reboot)

1. Restart the DRC chain (`drc.sh 44100`), play through rate changes and
   `off/on` cycles — the freeze historically fires within seconds intermittently,
   so loop start/stop ~20×.
2. Watch `top -H` for virtual_oss CPU: should stay near-idle; no `cuse-cli`
   pile-ups in `ps -o stat,mwchan`.
3. The teardown stress-repro (`repro-deadlock.sh`) should get *less* dangerous
   (clients no longer wedge mid-poll), but the kernel `cuse_client_open()`
   ref-leak remains latent and still needs the kernel fix for full safety.
