package com.omdrc.widget.data

/**
 * Everything a widget instance needs to render itself, plus enough metadata
 * (reachable / fetchedAtMillis) to show an honest "stale" state instead of
 * silently displaying old data as if it were fresh.
 */
data class WidgetSnapshot(
    val drc: DrcStatus?,
    val mpd: MpdStatus?,
    val rti: RtiStatus?,
    val peak: PeakStatus?,
    val fetchedAtMillis: Long,
    val reachable: Boolean,
)
