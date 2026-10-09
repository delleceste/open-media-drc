# Now playing

The first page of the kiosk and of the Android app. It shows what is playing
and how it sounds, all of it live: level meters, the dynamic range of the last
minutes, channel balance, the cover, and one line saying which room
correction is in the path. Nothing on this page is computed unless it is on
screen.

[← Back to the README](../../README.md)

## Six level displays

Choose one from the top bar's **View** menu. A **double tap** on the meters
steps to the next display (wrapping round, never to *Level off*).

| | | |
|---|---|---|
| <img src="../screenshots/now-needles.webp" alt="VU needles"> | <img src="../screenshots/now-bars.webp" alt="LED bars"> | <img src="../screenshots/now-bars-spectrum.webp" alt="Bars and spectrum"> |
| **Needles**: a pair of VU meters with peak (PK) and RMS readouts per channel | **Bars**: LED-style bars with peak-hold markers on a dBFS scale | **Bars + spectrum**: the bars over a band spectrum analyzer |
| <img src="../screenshots/now-lr-spectrum.webp" alt="Separate left/right spectrum"> | <img src="../screenshots/now-circular.webp" alt="Circular spectrum"> | <img src="../screenshots/now-bars-circular.webp" alt="Bars and circular spectrum"> |
| **Bars + separate L/R spectrum**: one analyzer per channel | **Circular spectrum**: the bands as concentric rings, left and right levels at the centre | **Bars + circular spectrum** |

- **Level off** hides the meters and leaves the cover, DR and balance.
- **One choice per orientation.** Landscape and upright each remember their own display, so turning the phone switches between the two at once.
- The circular spectrum's **number of rings** can be set.
- In landscape, the dividers between the meters and the panels beside them **can be dragged**. A double tap on a divider resets it.

The meters read MPD's FIFO output, so they show exactly what MPD plays.
[Calibration](../pdf/open-media-drc-manual.md#meter-timing-and-the-calibration-page-secmeter-calibration)
lines them up with what you hear when the DAC adds latency.

## Dynamic range, live

Under the meters, **DR** shows the dynamic range of what you have just heard.
It is worked out in 3-second blocks over a rolling window: tap the window chip
to choose 1, 5, 15, 30, 60 or 90 minutes. The coloured bar beneath is the DR
history: one block per slice of the window, its height and colour showing how
compressed that slice was (red compressed, green dynamic), with its value
printed on it. **Per song** restarts the window at each track change;
**Continuous** keeps it rolling across tracks. See
[Dynamic range](dynamic-range.md).

## Channel balance

The power average of left against right on a ±6 dB scale. Loud passages count
for more than quiet ones, because powers are added before the logarithm. The
averaging window goes from 0.3 s (the latest frame) to 60 s. **Bar** or
**Split** changes how it is drawn. A steady offset is a room, speaker or
cartridge question worth looking at; a wandering one is the recording.

## The track

- **Cover**: upright, it takes a third of the screen. ⛶ opens it full screen.
- **Seek ring**: a first touch on the cover brings up a thin ring (12 o'clock is the start of the track). A slide that starts *on* the ring seeks, so a stray touch never moves the music.
- **Format line**: the album, label, year and the stream's resolution (24/96…).
- **Album details**: the booklet, performers and engineers, release data, Wikipedia links and review searches. You can also mark the album as awarded or rate it there.
- **♡ Favorites** files the album into a [Library folder](qobuz.md#favourites-folders-qobuz-doesnt-have).
- **Research music** opens the [AI listening guide](ai.md#the-listening-guide).
- **Transport**: `<<` and `>>` seek within the track. The first tap shows the ring, each further tap steps 5 s, and quicker tapping steps further. Then previous, play/pause, next.
- **DRC line**: whether room correction is on, and which filter set, design and rate it uses. A tap opens the [DRC page](room-correction.md).

<p align="center"><img src="../screenshots/album-details.webp" width="40%" alt="Album details"></p>

## Screen

The Android app keeps the screen on only while Now is showing *and* music is
playing. On the 7" kiosk screen a screen saver takes over after a while. The
top bar slides in on a tap on empty space and hides again by itself.

More: [the kiosk and the Android app](../pdf/open-media-drc-manual.md#the-kiosk-and-the-android-app-seckiosk)
in the manual.
