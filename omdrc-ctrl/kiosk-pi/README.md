# Raspberry Pi kiosk: switching the display off

The kiosk page (`http://<box>:9090/k/`) is served by the DRC box, and a web page
cannot switch a monitor off. So on the Pi that shows the kiosk, a tiny helper,
`omdrc-display-helper`, listens on `127.0.0.1:9097` and does it when the page
asks. With the helper running, the top-right button of the kiosk becomes **⏻**:

1. Tap **⏻**. The page goes black and stops its live streams (nothing is
   computed on the box), and the helper switches the screen off.
2. Tap the screen anywhere. The screen comes back on and the page resumes.

Without the helper, or in any other browser, the button stays the usual
☀/☾ keep-awake toggle.

Everything here runs on the **Pi**, not on the DRC box. It needs only
`python3` (preinstalled on Raspberry Pi OS).

## 1. Install the helper (as the user that runs the kiosk browser)

From a checkout of this repository on the Pi, or copy the three files over:

```sh
install -Dm755 omdrc-ctrl/kiosk-pi/omdrc-display-helper ~/.local/bin/omdrc-display-helper
install -Dm644 omdrc-ctrl/kiosk-pi/omdrc-display-helper.service \
        ~/.config/systemd/user/omdrc-display-helper.service
systemctl --user daemon-reload
systemctl --user enable --now omdrc-display-helper
# start it at boot even before you log in (for an auto-login kiosk this is optional):
sudo loginctl enable-linger "$USER"
```

Check it:

```sh
curl -s localhost:9097/status        # {"ok": true, "method": "backlight", "state": "on"}
curl -s -X POST localhost:9097/display/off; sleep 3; curl -s -X POST localhost:9097/display/on
journalctl --user -u omdrc-display-helper -n 20
```

## 2. Let it switch the screen (depends on the display)

`"method"` in the status shows what the helper picked. It tries, in order:

| Method | For | What it needs |
|---|---|---|
| `backlight` | the official DSI touch display | write access to `/sys/class/backlight/*/bl_power` (udev rule below) |
| `wlopm` | HDMI/DSI under Wayland (labwc, the Raspberry Pi OS default) | `sudo apt install wlopm` |
| `xset` | X11 sessions | `x11-xserver-utils` (usually installed) |
| `vcgencmd` | older firmware display stack | preinstalled |

For the **DSI touch display**, let the `video` group switch the backlight:

```sh
sudo install -m644 omdrc-ctrl/kiosk-pi/99-omdrc-backlight.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=backlight
groups | grep -q video || sudo usermod -aG video "$USER"   # then log out and in
```

To force a method (or change the port), uncomment the `Environment=` lines in
`~/.config/systemd/user/omdrc-display-helper.service`, then
`systemctl --user daemon-reload && systemctl --user restart omdrc-display-helper`.

The touch panel keeps working while the screen is off with `backlight` and
`wlopm`, so the tap that wakes the page also switches the screen back on.

## 3. Open the kiosk with the helper enabled

Point the kiosk browser at the page with `?display=` once. The kiosk remembers
it, so later reloads keep using the helper:

```sh
chromium --kiosk --noerrdialogs --disable-infobars --touch-events=enabled \
         --overscroll-history-navigation=0 \
         --disable-features=LocalNetworkAccessChecks,BlockInsecurePrivateNetworkRequests,PrivateNetworkAccessRespectPreflightResults \
         "http://<box>:9090/k/?display=127.0.0.1:9097"
```

The `--disable-features` line matters. The page comes from the box over plain
http and calls `127.0.0.1` on the Pi, and recent Chromium blocks, or asks
permission for, such "local network" requests. The helper already answers
Chromium's preflight (`Access-Control-Allow-Private-Network`), but on current
Chromium the flag is the dependable way. `?display=off` forgets the helper again.

Also switch off the desktop's own screen blanking (Raspberry Pi OS:
`sudo raspi-config` → Display Options → Screen Blanking → No), so the screen goes
off only when you ask.

## Security

The helper listens on `127.0.0.1` only and accepts three requests: status, off
and on. Anything that can already run on the Pi could switch its screen, and
nothing more.
