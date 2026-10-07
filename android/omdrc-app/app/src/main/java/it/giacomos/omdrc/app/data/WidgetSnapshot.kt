package it.giacomos.omdrc.app.data

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
    val renderer: RendererStatus?,
    val fetchedAtMillis: Long,
    val reachable: Boolean,
    val player: PlayerStatus? = null,
    /** The box's link to the current cover, which changes only with the
     *  cover: "" for none, null when the box doesn't say (the cover is then
     *  fetched every time). Not persisted. */
    val artPath: String? = null,
)
