package com.omdrc.widget

private val LEADING_STATE_TAG = Regex("""^\[(playing|paused|stopped)\]\s*""", RegexOption.IGNORE_CASE)

/**
 * Both playback status sources follow the "[state] Artist - Title" /
 * "Artist - Title" convention (qobuzconnect2mpd's status-file line1, and
 * MPD's own default tag format via mpc) - this keeps only the title,
 * dropping the artist/composer portion before the first " - ", per
 * request. Falls back to showing the raw string unchanged if it doesn't
 * match that shape (nothing to strip).
 */
object TrackTitle {
    fun titleOnly(raw: String?): String? {
        val cleaned = raw?.trim()?.replace(LEADING_STATE_TAG, "")?.trim()
        if (cleaned.isNullOrEmpty()) return null
        val separator = cleaned.indexOf(" - ")
        return if (separator >= 0) cleaned.substring(separator + 3).trim() else cleaned
    }

    fun titleAndAlbum(raw: String?, title: String?, album: String?): String? {
        val cleanTitle = title?.trim()?.takeIf { it.isNotEmpty() } ?: titleOnly(raw)
        val cleanAlbum = album?.trim()?.takeIf { it.isNotEmpty() && it != cleanTitle }
        return listOfNotNull(cleanTitle, cleanAlbum).takeIf { it.isNotEmpty() }?.joinToString(" · ")
    }
}
