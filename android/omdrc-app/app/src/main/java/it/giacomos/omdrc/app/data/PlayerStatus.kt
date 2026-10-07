package it.giacomos.omdrc.app.data

import org.json.JSONObject

/** Parsed GET /k/api/player: the playing song's artist and edition, and
 *  where in it and in the queue MPD is. elapsed/duration are seconds. */
data class PlayerStatus(
    val elapsed: Double?,
    val duration: Double?,
    val pos: Int?,
    val length: Int,
    val artist: String?,
    val edition: String?,
) {
    companion object {
        fun parse(json: JSONObject): PlayerStatus? {
            if (!json.optBoolean("ok", false)) return null
            val label = json.optStringOrNull("label")
            val year = json.optStringOrNull("date")?.take(4)
            return PlayerStatus(
                elapsed = json.optDoubleOrNull("elapsed"),
                duration = json.optDoubleOrNull("duration"),
                pos = json.optIntOrNull("pos"),
                length = json.optInt("length", 0),
                artist = json.optStringOrNull("artist"),
                edition = listOfNotNull(label, year).joinToString(" · ").ifEmpty { null },
            )
        }
    }
}
