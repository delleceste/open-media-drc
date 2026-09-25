package com.omdrc.widget.data

import org.json.JSONObject

/** Parsed shape of GET /qconnect/services - which of the two
 *  mutually-exclusive renderers (qobuzconnect2mpd, upmpdcli) is currently
 *  feeding MPD. nowPlaying is filled in separately (from GET
 *  /qconnect/status's "line1", only when qobuzconnect2mpd is active) - it's
 *  qobuzconnect2mpd's own self-reported track title, which is what the web
 *  dashboard's Renderer card shows, and unlike MPD's own tag data it
 *  doesn't fall back to the raw stream URL when MPD has no metadata for the
 *  local proxy stream. */
data class RendererStatus(
    val qobuzconnect2mpd: Boolean,
    val upmpdcli: Boolean,
    val nowPlaying: String? = null,
) {
    val label: String?
        get() = when {
            qobuzconnect2mpd -> "qobuzconnect2mpd"
            upmpdcli -> "upmpdcli"
            else -> null
        }

    companion object {
        fun parse(json: JSONObject): RendererStatus = RendererStatus(
            qobuzconnect2mpd = json.optBoolean("qobuzconnect2mpd", false),
            upmpdcli = json.optBoolean("upmpdcli", false),
        )
    }
}
