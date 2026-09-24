# doc/pdf — the PDF manual build

`open-media-drc-manual.md` is a **synthesis** of the repository's Markdown
documentation, rendered to `doc/open-media-drc-manual.pdf` by
`./build-pdf.sh` (pandoc + graphviz, with pdflatex or headless Chromium).

## Regenerating

```sh
doc/pdf/build-pdf.sh              # -> doc/open-media-drc-manual.pdf
doc/pdf/build-pdf.sh v0.90.0      # build FOR a release about to be tagged
```

Requirements: `pandoc`, `graphviz` (`dot`), and either a TeX Live with
`pdflatex` or headless Chromium. The Chromium fallback exists for small
FreeBSD root filesystems where TeX Live's multi-gigabyte dependency is
unreasonable; both paths consume the same Markdown and Graphviz sources.

## Versioning

The title page carries the **project release** the manual documents.
Project/software releases use **`v*` annotated tags** (`v0.90.0`, ...);
the pre-existing bare-number tags (`1.1.0`..`1.5.4`) are the **filter**
release series and are deliberately excluded from the version stamp
(`git describe --match 'v*'`). When cutting a release, build the PDF with
the version as argument (the tag does not exist yet at build time), commit
the PDF, then create the annotated `v*` tag on that commit.

## Structure of the manual (fixed, by request)

The manual is split **by operating system** so a reader can skip the other
OS's material entirely. Part I never depends on Part II or III and only
*points* to them.

**Part I --- Common** (OS-neutral)

1. **Introduction** --- what the stack is, design principles, chain diagram,
   how to read the manual, repository map (with an OS column).
2. **Components** --- every component in signal order, with a per-OS pointer
   column; notes that MPD is `mpd` on Linux and `musicpd` on FreeBSD.
3. **Installation: the common build** --- build tools, upmpdcli/BruteFIR/CMake
   steps, `host.cmake`, BruteFIR defaults requirement, renderer runtime state.
4. **Usage** --- drc.sh verbs and state, reconcile, MPD outputs, filters/configs
   layout, filter-generation workflow, the No DRC browser launchers, helper
   scripts.
5. **Filter provenance and verification** --- repositories, bundle, hashing,
   the design scripts, publication/removal, deployment, live installs.
6. **The web panel** --- omdrc-ctrl, spectrum analyzer, configuration page,
   known-device policy.
7. **Bit-perfect verification** --- the `/bitperfect` page and its
   implementation.
8. **Dynamic range** --- DR versions, pressing identification, *Measure DR*.
9. **CD input** --- concept, exclusive-source rule, ESI U24 XL facts, web card.

**Part II --- Linux**: 10 installation and lifecycle (packages, /etc files,
MPD drop-in, udev/systemd hotplug, `snd-aloop`, audio roles, browser ALSA,
the panel on Linux); 11 CD input with `alsaloop`.

**Part III --- FreeBSD**: 12 installation (packages, rc.conf, network);
13 services, device roles and lifecycle (rc.d/devd, locks); 14 OSS audio
stack, panel, glitch detection, bit-perfect on FreeBSD, browsers; 15 video;
16 CD input with `omdrc-cdin` (incl. the ESI traps); 17 known issues;
18 kernel/userland patches; 19 port plan and image.

**Appendices** --- A: bit-perfect test assets and cross-OS comparison;
B: source-document index (split Common / Linux / FreeBSD) and the update
procedure; C: glossary (each term tagged with its OS and section references).

The rule when editing: an OS-specific fact goes in Part II or III, never
inline in Part I.

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
