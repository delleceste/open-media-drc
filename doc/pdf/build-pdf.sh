#!/usr/bin/env sh
# build-pdf.sh [version] — regenerate every tracked PDF in doc/:
#   doc/open-media-drc-manual.pdf          <- doc/pdf/open-media-drc-manual.md
#                                             + the graphviz diagrams
#   doc/FILTER_PROVENANCE_AND_RESPONSE.pdf <- doc/FILTER_PROVENANCE_AND_RESPONSE.md
#
# Requirements: pandoc, pdflatex (texlive), graphviz (dot).
#
# Version stamp: the title page carries the project release the manual
# documents. Pass it explicitly when building FOR a release that is about
# to be tagged (the tag does not exist yet at build time):
#     ./build-pdf.sh v0.90.0
# With no argument, the version is derived from the nearest v* tag
# (git describe --match 'v*'); bare-number tags (1.x) are the FILTER
# release series and are deliberately excluded.
#
# The master document (open-media-drc-manual.md) is a *synthesis* of the
# repository's Markdown docs — when those change, update the relevant
# section of the master document too, then re-run this script.
# See doc/pdf/README.md for the section -> source-doc mapping.

set -eu
cd "$(dirname "$0")"

mkdir -p build

# 1. Render every graphviz diagram to PDF (vector, scales cleanly in LaTeX).
for f in diagrams/*.dot; do
    out="build/$(basename "${f%.dot}").pdf"
    echo "dot: $f -> $out"
    dot -Tpdf "$f" -o "$out"
done

# 2. Resolve the version stamp (arg > git describe on v* tags > fallback).
VERSION="${1:-$(git describe --tags --match 'v*' --dirty --always 2>/dev/null || true)}"
[ -n "$VERSION" ] || VERSION="unreleased"
DATE="$(date +%Y-%m-%d)"

# 3. Pandoc -> PDF. pdflatex is used, so the master doc must stay
#    (mostly) ASCII: no box-drawing chars, use -> instead of arrows.
OUT="../open-media-drc-manual.pdf"
echo "pandoc: open-media-drc-manual.md -> doc/open-media-drc-manual.pdf ($VERSION, $DATE)"
pandoc open-media-drc-manual.md \
    --from markdown+smart \
    --pdf-engine=pdflatex \
    --toc --toc-depth=3 \
    --number-sections \
    --highlight-style=tango \
    -V documentclass=report \
    -M date="$VERSION ($DATE)" \
    -o "$OUT"

# 4. The standalone provenance document.  It is not part of the manual (it is a
#    specification, not a synthesis), but it is tracked as a PDF too, so build
#    it here rather than by hand -- that is how it silently went stale before.
#    --shift-heading-level-by=-1 turns its single H1 into the title page instead
#    of a duplicated section 1.
PROV="../FILTER_PROVENANCE_AND_RESPONSE.pdf"
echo "pandoc: FILTER_PROVENANCE_AND_RESPONSE.md -> doc/FILTER_PROVENANCE_AND_RESPONSE.pdf"
pandoc ../FILTER_PROVENANCE_AND_RESPONSE.md \
    --from markdown+smart \
    --pdf-engine=pdflatex \
    --shift-heading-level-by=-1 \
    --toc --toc-depth=3 \
    --number-sections \
    --highlight-style=tango \
    -V documentclass=article \
    -M title="Filter provenance and predicted-response strategy" \
    -M date="$VERSION ($DATE)" \
    -o "$PROV"

echo "OK: $(cd .. && pwd)/open-media-drc-manual.pdf"
echo "OK: $(cd .. && pwd)/FILTER_PROVENANCE_AND_RESPONSE.pdf"
