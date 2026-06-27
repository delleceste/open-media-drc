#!/bin/sh
#
# repro-deadlock-aggressive.sh — more forceful trigger for the virtual_oss/cuse
# teardown deadlock, used if the ordered repro-deadlock.sh does not hit it.
#
# Difference: it kills the SERVER (virtual_oss) while the clients (brutefir on
# /dev/dsp.loop, MPD on /dev/d