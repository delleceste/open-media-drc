package com.omdrc.widget.data

import org.json.JSONObject

/** Parsed shape of GET /mpd/info - independent of DRC on/off state.
 *  sampleRate/bitDepth are the source stream's own rate/depth (from MPD's
 *  "audio" line); brutefirRate is what BruteFIR is actually outputting,
 *  present only while DRC is running - together they're the
 *  "source -> output" bitrate chain shown on the widget. */
data class MpdStatus(
    val ok: Boolean,
    val state: String?,
    val song: String?,
    val title: String?,
    val album: String?,
    val sampleRate: Int?,
    val bitDepth: Int?,
    val brutefirRate: Int?,
) {
    /** MPD falls back to printing the raw stream URI as its "current song"
     *  line when it has no tag metadata for the stream - seen with
     *  qobuzconnect2mpd's local proxy (http://127.0.0.1:<port>/qobuz-direct/
     *  ...). Not meaningful as a displayed title, so it's filtered out here
     *  rather than shown verbatim. */
    val displaySong: String?
        get() = song?.takeUnless { it.startsWith("http://") || it.startsWith("https://") }

    companion object {
        fun parse(json: JSONObject): MpdStatus = MpdStatus(
            ok = json.optBoolean("ok", false),
            state = json.optStringOrNull("state"),
            song = json.optStringOrNull("song"),
            title = json.optStringOrNull("title"),
            album = json.optStringOrNull("album"),
            sampleRate = json.optIntOrNull("sample_rate"),
            bitDepth = json.optIntOrNull("bit_depth"),
            brutefirRate = json.optIntOrNull("brutefir_rate"),
        )
    }
}
