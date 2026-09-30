# Meter timing by network

The Android app saves a separate display delay for each connected Wi-Fi SSID,
plus one `Wired` profile for Ethernet. Profiles remain on the device, including
when the controller's IP address changes. Both the kiosk and full panel use them.
A new identified network starts at 0 ms and shows `Not calibrated`.

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

Calibration results are discarded if the network or effective margin changes
while measuring. Automatic calibration's recent-result history resets when the
network or margin changes. Recalibrate if timing still varies on a busy network.
