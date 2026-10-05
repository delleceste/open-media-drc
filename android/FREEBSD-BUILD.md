# Building the Android app on FreeBSD

This procedure was used on FreeBSD 15.1 to build `android/omdrc-app` and run
Android lint. The Android SDK tools and Android Gradle Plugin expect Linux, so
the build uses FreeBSD's Linux compatibility layer and a Linux JDK. The SDK,
JDK, and Gradle live in a persistent toolchain directory,
`~/devel/android-toolchain`, so they survive reboots and `/tmp` cleanup:

```sh
TC=$HOME/devel/android-toolchain
mkdir -p $TC
```

Downloaded archives stay in `/tmp`; they can be deleted after unpacking.

## Prerequisites

- FreeBSD's `linux_base-rl9` package and Linux binary compatibility enabled.
- `curl`, `unzip`, and `tar`.
- `android-tools` for the native FreeBSD `adb` command.

The project's Android Gradle Plugin is 9.4.0. It needs Gradle 9.6 or newer;
the checked-in wrapper properties currently name Gradle 8.9, and the wrapper
JAR is not checked in. Use a suitable standalone Gradle distribution until
the project wrapper is updated.

## Download the tools

The command-line SDK archive below was the Linux download listed by
[Android Developers](https://developer.android.com/studio) on 2026-10-02.
Check that page for a newer archive and checksum before repeating this later.

```sh
curl -fL -o /tmp/omdrc-commandlinetools.zip \
  https://dl.google.com/android/repository/commandlinetools-linux-15859902_latest.zip
sha256 -q /tmp/omdrc-commandlinetools.zip
# Expected: 4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583
unzip -q /tmp/omdrc-commandlinetools.zip -d $TC/sdk
mv $TC/sdk/cmdline-tools $TC/sdk/latest
mkdir $TC/sdk/cmdline-tools
mv $TC/sdk/latest $TC/sdk/cmdline-tools/
```

Download the Linux JDK 17 and Gradle 9.6.0:

```sh
curl -fL -o /tmp/omdrc-linux-jdk17.tar.gz \
  https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse
tar -xzf /tmp/omdrc-linux-jdk17.tar.gz -C $TC
curl -fL -o /tmp/omdrc-gradle-9.6-bin.zip \
  https://services.gradle.org/distributions/gradle-9.6.0-bin.zip
unzip -q /tmp/omdrc-gradle-9.6-bin.zip -d $TC
```

Set `JAVA_HOME` to the directory extracted by the JDK archive. For this run
it was `$TC/jdk-17.0.20.1+1`; the patch version can change.

## Install the SDK packages and build

`REPO_OS_OVERRIDE=linux` makes the SDK manager offer Linux build tools on
FreeBSD. Accept the Android SDK license when prompted.

```sh
JAVA_HOME=$TC/jdk-17.0.20.1+1 REPO_OS_OVERRIDE=linux \
  $TC/sdk/cmdline-tools/latest/bin/sdkmanager \
  --sdk_root=$TC/sdk 'platforms;android-35' 'build-tools;35.0.0'

cd android/omdrc-app
JAVA_HOME=$TC/jdk-17.0.20.1+1 ANDROID_HOME=$TC/sdk \
  REPO_OS_OVERRIDE=linux $TC/gradle-9.6.0/bin/gradle \
  :app:assembleDebug :app:lint --no-daemon
```

The APK is `app/build/outputs/apk/debug/app-debug.apk`. Gradle may also
install newer SDK build tools and platform tools automatically. Using FreeBSD's
native JDK fails because the Android Gradle Plugin does not recognize
`FreeBSD`; changing `os.name` on the FreeBSD JVM fails inside the JDK. The
Linux JDK avoids both problems.

The app now uses Android application id `it.giacomos.omdrc.app`; the older
`com.omdrc.widget` installation is a separate app. Back up its private data
if you need to migrate settings into the new package.

## ADB on FreeBSD

On this host, the Pixel appeared in `sudo usbconfig list` but the USB device
nodes were owned by `root:operator` with mode `0600`, so ordinary `adb devices`
listed no device. A separate root ADB server found it without changing USB
permissions:

```sh
sudo adb -P 5038 devices -l
sudo adb -P 5038 install -r \
  android/omdrc-app/app/build/outputs/apk/debug/app-debug.apk
```

Unlock the phone and approve its USB debugging prompt if ADB says
`unauthorized`. If installation reports `INSTALL_FAILED_UPDATE_INCOMPATIBLE`,
the installed package was signed with another key. Debug builds use a local
`~/.android/debug.keystore`; a build from another computer may have a different
one. Find the original signing key for an in-place upgrade, or back up the old
app's private data before uninstalling it. The package rename above avoids a
signature conflict but creates a new, initially empty app installation.
