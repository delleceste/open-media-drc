#!/usr/bin/env sh
# build-pdf.sh [version] — regenerate every tracked PDF in doc/:
#   doc/open-media-drc-manual.pdf          <- doc/pdf/open-media-drc-manual.md
#                                             + the graphviz diagrams
#   doc/FILTER_PROVENANCE_AND_RESPONSE.pdf <- doc/FILTER_PROVENANCE_AND_RESPONSE.md
#
# Requirements: pandoc and graphviz (dot).  PDF backend, in preference order:
# pdflatex; or headless Chromium (keeps the build practical on small FreeBSD
# root filesystems where TeX Live alone needs several GiB).
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

# 1. Render every graphviz diagram to both vector formats.  LaTeX embeds PDF;
# Chromium embeds SVG.  Both are generated from the same .dot source.
for f in diagrams/*.dot; do
    out="build/$(basename "${f%.dot}").pdf"
    echo "dot: $f -> $out"
    dot -Tpdf "$f" -o "$out"
    svg="build/$(basename "${f%.dot}").svg"
    echo "dot: $f -> $svg"
    dot -Tsvg "$f" -o "$svg"
done

# 2. Resolve the version stamp (arg > git describe on v* tags > fallback).
VERSION="${1:-$(git describe --tags --match 'v*' --dirty --always 2>/dev/null || true)}"
[ -n "$VERSION" ] || VERSION="unreleased"
DATE="$(date +%Y-%m-%d)"

# Build one document with pdflatex when available, otherwise with Pandoc HTML
# and installed headless Chromium.  The HTML path substitutes only generated
# Graphviz image extensions and TeX page-break commands in a temporary source.
build_document()
{
    src=$1 out=$2 class=$3
    shift 3
    if command -v pdflatex >/dev/null 2>&1; then
        pandoc "$src" \
            --from markdown+smart \
            --pdf-engine=pdflatex \
            --toc --toc-depth=3 \
            --number-sections \
            --syntax-highlighting=tango \
            -V "documentclass=$class" \
            "$@" -o "$out"
        return
    fi

    chromium=$(command -v chromium 2>/dev/null || true)
    [ -n "$chromium" ] || chromium=$(command -v chrome 2>/dev/null || true)
    [ -n "$chromium" ] || [ ! -x /usr/local/bin/chromium ] ||
        chromium=/usr/local/bin/chromium
    [ -n "$chromium" ] || [ ! -x /usr/local/bin/chrome ] ||
        chromium=/usr/local/bin/chrome
    [ -n "$chromium" ] || {
        echo "error: need pdflatex or chromium for PDF output" >&2
        return 1
    }
    tmp="build/$(basename "${src%.md}").chromium.md"
    html="build/$(basename "${src%.md}").html"
    sed -e 's|build/\([^)]*\)\.pdf|build/\1.svg|g' \
        -e 's|^\\newpage$|<div class="page-break"></div>|' \
        "$src" > "$tmp"
    pandoc "$tmp" \
        --from markdown+smart+raw_html \
        --standalone --embed-resources \
        --toc --toc-depth=3 \
        --number-sections \
        --syntax-highlighting=tango \
        --css chromium-pdf.css \
        "$@" -o "$html"
    abs_html="$(cd "$(dirname "$html")" && pwd)/$(basename "$html")"
    abs_out="$(cd "$(dirname "$out")" && pwd)/$(basename "$out")"
    "$chromium" --headless --no-sandbox --disable-gpu \
        --allow-file-access-from-files --no-pdf-header-footer \
        --print-to-pdf="$abs_out" "file://$abs_html"
}

# 3. Pandoc -> PDF.
OUT="../open-media-drc-manual.pdf"
echo "pandoc: open-media-drc-manual.md -> doc/open-media-drc-manual.pdf ($VERSION, $DATE)"
build_document open-media-drc-manual.md "$OUT" report \
    -M date="$VERSION ($DATE)"

# 4. The standalone provenance document.  It is not part of the manual (it is a
#    specification, not a synthesis), but it is tracked as a PDF too, so build
#    it here rather than by hand -- that is how it silently went stale before.
#    --shift-heading-level-by=-1 turns its single H1 into the title page instead
#    of a duplicated section 1.
PROV="../FILTER_PROVENANCE_AND_RESPONSE.pdf"
echo "pandoc: FILTER_PROVENANCE_AND_RESPONSE.md -> doc/FILTER_PROVENANCE_AND_RESPONSE.pdf"
build_document ../FILTER_PROVENANCE_AND_RESPONSE.md "$PROV" article \
    --shift-heading-level-by=-1 \
    -M title="Filter provenance, deployment, and response strategy" \
    -M date="$VERSION ($DATE)"

echo "OK: $(cd .. && pwd)/open-media-drc-manual.pdf"
echo "OK: $(cd .. && pwd)/FILTER_PROVENANCE_AND_RESPONSE.pdf"
