package com.omdrc.widget.data

import org.json.JSONObject

/** Parsed shape of GET /mpd/info - independent of DRC on/off state.
 *  pathStatusText is the same plain-language audio-path verdict the web
 *  dashboard shows (_path_status() in app.py) - e.g. "Bit-perfect
 *  passthrough", "Full-resolution DRC · no resampling", "Resampling
 *  active" - already written for exactly this "what's the chain doing"
 *  summary, so it's reused as-is rather than re-deriving it from the rates. */
data class MpdStatus(
    val ok: Boolean,
    val state: String?,
    val song: String?,
    val pathStatusText: String?,
) {
    companion object {
        fun parse(json: JSONObject): MpdStatus = MpdStatus(
            ok = json.optBoolean("ok", false),
            state = json.optStringOrNull("state"),
            song = json.optStringOrNull("song"),
            pathStatusText = json.optJSONObject("path_status")?.optStringOrNull("text"),
        )
    }
}
