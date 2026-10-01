# Meter timing by network and audio configuration

The Android app saves a separate display delay for each connected Wi-Fi SSID,
plus one `Wired` profile for Ethernet. Profiles remain on the device, including
when the controller's IP address changes. Both the kiosk and full panel use them.
Each network has separate profiles for DRC off and each DRC configuration (rate,
config, filter files and their modification times, partition size, and source).
A new combination starts at 0 ms and shows `Not calibrated`. Old network-only
profiles are preserved but are not applied to a newly identified configuration.

In Config → Meter timing, tap **Identify Wi-Fi** and grant precise location
permission. Android also needs Location enabled to reveal the Wi-Fi name. The
app reads only the connection name; it does not request geographic coordinates.
If Android hides the name, or in an ordinary browser, use **Set network name**.
Enter the Wi-Fi name, or `wired` for Ethernet. Manual names apply only when
automatic identification is unavailable; change them yourself when switching
networks. An empty name restores automatic detection. Ordinary browser profiles
are stored per browser origin; the Android app uses native device storage.

Microphone calibration in the updated app requires an identified or manually
named network. Existing unscoped delays remain available in unidentified browsers
and older app versions; they are not copied into newly identified profiles.

Each saved delay records the server's effective margin (the portion actually
subtracted from the audio chain delay). The active wait is the saved delay plus
the current effective margin minus the saved margin, bounded to 0–3000 ms.
For example, a saved 100 ms wait becomes 140 ms if frames leave 40 ms earlier.
Settings refresh every five seconds and at the start and end of calibration;
a margin adjustment made by this screen is applied immediately.

Calibration results are discarded if the network, audio configuration, or requested
server margin changes while measuring. Changes in effective margin as playback
starts and stops do not invalidate a run. Automatic calibration history resets
when the context changes.

The kiosk schedules frames against their server send timestamps and the lowest
transit time observed on the connection. Network jitter uses up the saved wait
instead of adding to it. Frames over 1.2 seconds behind are excluded from display
and calibration; five consecutive late frames replace the connection and cancel
old pending draws. Recovery reapplies the saved profile automatically. A permanent
change in network latency can still require recalibration.

The click detector uses the meter silence floor instead of a fixed -50 dBFS
threshold, so attenuated clicks can be measured. The calibration log retains
detector diagnostics even when a configuration change invalidates the result.
