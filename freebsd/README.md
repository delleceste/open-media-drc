# FreeBSD port skeleton (DRAFT — not yet submitted)

`audio/open-media-drc/` is a ports-tree-shaped draft of the port described in
`doc/FREEBSD-PORT-PLAN.md`.  It consumes a tagged GitHub release of this repo
(`git archive` — the `.gitattributes` export-ignore rules strip room-specific
filters, kernel patches and journals from the tarball) and installs via the
top-level `Makefile` (`make install DESTDIR=… PREFIX=…`).

Status / blockers before submission:

1. **Phase 0 of the plan**: the BruteFIR fork delta must land in
   `audio/brutefir` (or become its own port) — `RUN_DEPENDS` currently points
   at stock `audio/brutefir`, which is not what the stack is tested against.
   Same for the virtual_oss SETTRIGGER fix (upstream to hselasky/virtual_oss).
2. A real release tag matching `DISTVERSION` must exist on GitHub.
3. Untested: needs `portlint -AC`, `portclippy`, `poudriere testport`, and a
   `pkg check -s` after a service cycle on a FreeBSD box.
4. The `CTRL` option (omdrc-ctrl web UI) is implemented and on by default: it
   adds the Flask/Markdown/numpy run dependencies, the `omdrcctrl` rc script,
   `share/omdrc-ctrl/` and an `@sample` `commands.conf`.  It is installed by
   the top-level `Makefile`'s separate `install-ctrl` target, so the core
   install stays free of any Python dependency.  Still untested under
   poudriere (see 3).  The UPNP front-end and the video webremote remain
   deferred until the core port passes testing.

Note on layouts: the port installs omdrc-ctrl to `share/omdrc-ctrl/` with its
config as `etc/open-media-drc/commands.conf.sample`, and bakes no build-host
path into it.  Run-from-repo is untouched by this and keeps using the CMake
flow (`lib/omdrcctrl`, `etc/omdrcctrl`) — the two installation modes coexist
and neither is aware of the other.

To try it on the FreeBSD box: copy `audio/open-media-drc` into a ports tree
checkout, `make makesum`, then `make stage && make check-plist`.

Run-from-repo mode is unaffected by any of this: `./install.sh` remains the
setup path for a checkout, and drc.sh auto-detects which mode it is in by the
presence of `config.env` beside it.
