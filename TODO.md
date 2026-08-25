# TODO

Ideas worth doing, with enough context to pick up cold. Not a roadmap — no
ordering or commitment implied.

## Make geometry selection a runtime operation

Filters are keyed by geometry (`$SITE_DIR/configs/<GEOMETRY>/` and
`filters/<GEOMETRY>/`, see `drc.sh:39,699`), but the geometry itself is only
settable by editing `omdrc.conf` (`drc.sh:37`). That is the right storage model
— a filter set describes a *room*, so it belongs to shared site data rather
than to a user or a machine — but it makes *switching* rooms heavier than it
needs to be.

The case that exposes it: a laptop carried between rooms. It gains no new user
and no new machine, only a new geometry, so the existing per-site storage is
already correct; only the selection mechanism is awkward.

Two increments, either useful alone:

1. **An `omdrc geometry <name>` verb.** Selection is state, not configuration —
   which geometry is active belongs next to `last_arg` in `$STATE_DIR`
   (`drc.sh:118`), not in `omdrc.conf`. `omdrc.conf`'s `GEOMETRY` then becomes
   the default for a system that never switches, with the state file winning
   when set. Needs a `geometry` case in the verb dispatch, listing of the
   available geometries (the directories under `$SITE_DIR/configs/`), and a
   reload of the active chain when it changes.

2. **Auto-select from the connected DAC.** In practice the DAC often changes
   with the room, so the USB vendor/product id is a usable proxy for location.
   A `dac-id → geometry` mapping in `omdrc.conf` would let the chain pick its
   own correction on hotplug. The hook already exists: `omdrc-sndlink.conf`
   runs `libexec/omdrc-hotplug` on every `pcm` attach/detach, and it already
   resolves the card identity (vendor/product/serial) through
   `omdrc_audio`, so the id is in hand at exactly the moment the decision
   needs making.

Worth keeping the two separate: (1) is a small, self-contained ergonomic win;
(2) is the interesting one but depends on (1) existing first.
