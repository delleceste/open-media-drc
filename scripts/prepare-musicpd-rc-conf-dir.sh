#!/bin/sh
# Convert rc.conf.d/musicpd from FreeBSD's single-file form to its directory
# form, without changing the user's settings.  rc.subr supports both layouts,
# but omdrc_audio needs a separate post-start fragment in the directory form.

set -eu

target=${1:-}
if [ -z "$target" ]; then
    echo "usage: $0 /path/to/etc/rc.conf.d/musicpd" >&2
    exit 64
fi

if [ -L "$target" ]; then
    echo "refusing to migrate symlink: $target" >&2
    exit 1
fi

if [ -e "$target" ] && [ ! -f "$target" ] && [ ! -d "$target" ]; then
    echo "not a regular file or directory: $target" >&2
    exit 1
fi

if [ -f "$target" ]; then
    parent=${target%/*}
    [ "$parent" != "$target" ] || parent=.
    mkdir -p "$parent"

    # Keep both renames on the same filesystem.  If the second rename fails,
    # restore the original file at its original path before returning failure.
    temporary="${target}.omdrc-migrate.$$"
    if [ -e "$temporary" ]; then
        echo "temporary migration path already exists: $temporary" >&2
        exit 1
    fi
    mkdir -m 755 "$temporary"
    if ! mv "$target" "$temporary/00-local.conf"; then
        rmdir "$temporary" 2>/dev/null || true
        exit 1
    fi
    if ! mv "$temporary" "$target"; then
        mv "$temporary/00-local.conf" "$target" 2>/dev/null || true
        rmdir "$temporary" 2>/dev/null || true
        echo "could not convert $target to directory form" >&2
        exit 1
    fi
    echo "migrated $target to directory form; preserved settings in 00-local.conf"
fi

mkdir -p "$target"
