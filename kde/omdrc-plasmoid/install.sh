#!/bin/sh
# Install the OMDRC Monitor Plasma widget for the current user, or upgrade it
# in place when it is already installed.
set -eu

package="$(cd "$(dirname "$0")" && pwd)/package"
id=org.omdrc.monitor

if kpackagetool6 --type Plasma/Applet --show "$id" >/dev/null 2>&1; then
    kpackagetool6 --type Plasma/Applet --upgrade "$package"
    echo "Upgraded $id. Restart Plasma for running widgets to load it."
else
    kpackagetool6 --type Plasma/Applet --install "$package"
    echo "Installed $id: add \"OMDRC Monitor\" from Add Widgets..."
fi
