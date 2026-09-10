#!/bin/sh
#
# grub-reboot-to-freebsd.sh  --  Linux/GRUB (BIOS): switch the default OS to
#                                FreeBSD and reboot into it.
#
# Semantics: STICKY, not one-shot.  The chosen OS becomes the default and
# stays the default until something changes it again — either this script, or
# picking a different entry at the GRUB menu (see below).  Rebooting FreeBSD
# repeatedly keeps landing in FreeBSD; that is the intent.
#
# Coming back is symmetric and needs no script on the FreeBSD side: pick
# "Arch Linux" at the GRUB menu once, and Linux becomes the default again.
# That works because every menuentry calls `savedefault`, so an entry chosen
# from the menu is written back as the new default.
#
# One-time setup required (verify with -c):
#   * /etc/default/grub must contain:        GRUB_DEFAULT=saved
#   * /etc/default/grub should contain:      GRUB_SAVEDEFAULT=true
#     (this is what makes a manual menu pick stick, i.e. the return path)
#   * regenerate config:    sudo grub-mkconfig -o /boot/grub/grub.cfg
#
# Usage:
#   grub-reboot-to-freebsd.sh [label]   label defaults to "FreeBSD";
#                                        matched as a substring of the GRUB title.
#   grub-reboot-to-freebsd.sh -l        list GRUB menu entries and exit.
#   grub-reboot-to-freebsd.sh -c        check setup + show current default, exit.
#   grub-reboot-to-freebsd.sh -n [label]  set the default but do NOT reboot.
#
# History: this used `grub-reboot` (one-shot, reverting to the previous default
# on the next boot).  That contradicted the intended behaviour — after
# deliberately switching to FreeBSD, the next reboot silently went back to
# Linux — so it now uses `grub-set-default`.
#
set -eu

GRUBCFG=/boot/grub/grub.cfg
GRUBENV=/boot/grub/grubenv
LABEL="FreeBSD"
REBOOT=1

list_entries() {
    grep -E "^[[:space:]]*menuentry " "$GRUBCFG" \
        | sed -E "s/^[[:space:]]*menuentry ['\"]([^'\"]+)['\"].*/\1/"
}

current_default() {
    sed -n 's/^saved_entry=//p' "$GRUBENV" 2>/dev/null
}

check_setup() {
    rc=0
    if grep -qE '^[[:space:]]*GRUB_DEFAULT=saved' /etc/default/grub 2>/dev/null; then
        echo "ok      GRUB_DEFAULT=saved"
    else
        echo "PROBLEM /etc/default/grub does not set GRUB_DEFAULT=saved;" >&2
        echo "        the saved default is ignored until you set it." >&2
        rc=1
    fi
    if grep -qE '^[[:space:]]*GRUB_SAVEDEFAULT=true' /etc/default/grub 2>/dev/null; then
        echo "ok      GRUB_SAVEDEFAULT=true  (a manual menu pick sticks)"
    else
        echo "note    GRUB_SAVEDEFAULT is not true: choosing an entry at the" >&2
        echo "        menu will NOT become the new default, so returning from" >&2
        echo "        FreeBSD to Linux would not stick." >&2
    fi
    if [ "$(grep -c 'savedefault' "$GRUBCFG" 2>/dev/null || echo 0)" -gt 0 ]; then
        echo "ok      menuentries call savedefault"
    else
        echo "PROBLEM no menuentry calls savedefault; regenerate grub.cfg." >&2
        rc=1
    fi
    echo "current default: $(current_default)"
    return $rc
}

while [ $# -gt 0 ]; do
    case "$1" in
        -l) list_entries; exit 0 ;;
        -c) check_setup; exit $? ;;
        -n) REBOOT=0; shift ;;
        --) shift; break ;;
        -*) echo "unknown option: $1" >&2; exit 2 ;;
        *)  LABEL="$1"; shift ;;
    esac
done

[ "$(id -u)" -eq 0 ] || { echo "Must run as root." >&2; exit 1; }

if ! grep -qE '^[[:space:]]*GRUB_DEFAULT=saved' /etc/default/grub 2>/dev/null; then
    echo "ERROR: /etc/default/grub does not set GRUB_DEFAULT=saved;" >&2
    echo "       the default written here would be ignored (see header)." >&2
    exit 1
fi

# Resolve the exact GRUB menuentry title matching the label.
title=$(list_entries | grep -iF -- "$LABEL" | head -n1)
if [ -z "${title:-}" ]; then
    echo "No GRUB entry matching '$LABEL'. Entries found:" >&2
    list_entries >&2
    exit 1
fi

grub-set-default "$title"          # NB: some distros name this 'grub2-set-default'

# Trust the write rather than assume it: a silent failure here would send the
# machine back into the OS it is already running.
saved=$(current_default)
if [ "$saved" != "$title" ]; then
    echo "ERROR: grub-set-default did not take: saved_entry='$saved'," >&2
    echo "       expected '$title'.  Not rebooting." >&2
    exit 1
fi

echo "Default OS -> '$title'  (stays the default until changed again)"

[ "$REBOOT" -eq 1 ] || exit 0

echo "Rebooting in 3s (Ctrl-C to abort)..."
sleep 3
systemctl reboot
