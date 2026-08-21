# doc/pdf — the PDF manual build

`open-media-drc-manual.md` is a **synthesis** of the repository's Markdown
documentation, rendered to `doc/open-media-drc-manual.pdf` by
`./build-pdf.sh` (pandoc + pdflatex + graphviz).

## Regenerating

```sh
doc/pdf/build-pdf.sh              # -> doc/open-media-drc-manual.pdf
doc/pdf/build-pdf.sh v0.90.0      # build FOR a release about to be tagged
```

Requirements: `pandoc`, a TeX Live with `pdflatex`, `graphviz` (`dot`).

## Versioning

The title page carries the **project release** the manual documents.
Project/software releases use **`v*` annotated tags** (`v0.90.0`, ...);
the pre-existing bare-number tags (`1.1.0`..`1.5.4`) are the **filter**
release series and are deliberately excluded from the version stamp
(`git describe --match 'v*'`). When cutting a release, build the PDF with
the version as argument (the tag does not exist yet at build time), commit
the PDF, then create the annotated `v*` tag on that commit.

## Structure of the manual (fixed, by request)

1. **Introduction** — what the stack is, design principles, chain diagram,
   repository map.
2. **Components** — every component in signal order; explicitly notes that
   **MPD is packaged as `musicpd` on FreeBSD** (`mpd` on Linux).
3. **Installation** — deps, build order, Linux vs FreeBSD differences
   (systemd/udev vs rc.d/devd, copy-vs-symlink rule, MPD drop-in caveat).
4. **Usage** — drc.sh verbs/options/state, MPD outputs, filters/configs
   layout, filter-generation workflow, browser-nodrc.
5. **Filter provenance and verification** — the three repositories and
   the site-data split (`OMDRC_SITE_DATA_DIRS` / `OMDRC_SITE_ROOT`),
   geometry/design/variant, the bundle layout, what is hashed and how
   `bundle_id` is derived, the design scripts, the publication
   transaction, and verification at install time and at runtime.
6. **Tools** — omdrc-ctrl (web UI, spectrum analyzer, filter response),
   video (launchers + webremote), glitch detection, bit-perfect
   verification, scripts/.
7. **CD input** — `omdrc-cdin`, the S/PDIF capture bridge: why the lead
   is the only number that matters, the state machine and the two device
   tenancies, the transport simulator, the web card, and the ESI U24 XL
   configuration it depends on (unit order, input selector, `rec.vchans`).
8. **FreeBSD peculiarities** — one-place summary of everything
   FreeBSD-specific, including known bugs and the port plan.
9. **Appendix A** — detailed description of every FreeBSD patch (uaudio,
   virtual_oss/cuse, Kodi): what it does, root cause, how to apply/build/
   install/verify/revert.
10. **Appendix B** — the FreeBSD port plan (from `doc/FREEBSD-PORT-PLAN.md`).
11. **Appendix C** — bit-perfect test assets and the cross-OS byte-comparison
   procedure (from `tests/README.md`, `scripts/README.md`).
12. **Appendix D** — mapping of manual sections to the source `.md` files,
    plus the update procedure for this manual.

## Updating after documentation changes

The master document does **not** transclude the source files — it
summarizes them. When a source `.md` changes, update the corresponding
section of `open-media-drc-manual.md` (Appendix D in the manual is the
section -> source mapping) and re-run `build-pdf.sh`.

Constraints to respect when editing:

- **pdflatex only**: keep the master document ASCII — no box-drawing
  characters, use `->` not arrows, `---` for em-dashes (pandoc smart), no
  `≥ ≈ ± × µ`.
- Diagrams are **graphviz** sources in `diagrams/*.dot`, rendered to vector
  PDFs in `build/` by the script and referenced as
  `![caption](build/<name>.pdf){width=NN%}`. Add new diagrams the same way.
- The measurement plots are referenced from `doc/` directly
  (`../current.amplitude.png` etc.).
- `build/` is regenerable output; only the `.md`, `.dot` and the script are
  source.
