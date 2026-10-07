package it.giacomos.omdrc.app.data

import org.json.JSONObject

/** Parsed GET /now: what the box is playing - the music, or the film the
 *  video remote is showing - the DRC in use, and the token that names
 *  this state, to ask the box to answer only once it changes. */
data class NowState(
    val token: String,
    val music: Music,
    val drc: Drc,
    val video: Video?,
) {
    data class Music(
        val state: String,
        val title: String,
        val artist: String,
        val album: String,
        val line1: String,
        val edition: String,
        val art: String,
        val rate: Int?,
        val bits: Int?,
        val renderer: String,
    )

    data class Drc(
        val running: Boolean,
        val geometry: String?,
        val description: String?,
        val attenuationDb: Double?,
        val headroomSafe: Boolean?,
    )

    data class Video(
        val title: String,
        val year: String,
        val director: String,
        val runtime: String,
        val genre: String,
        val rating: String,
        val paused: Boolean,
        val art: String,
    )

    companion object {
        fun parse(json: JSONObject): NowState {
            val music = json.optJSONObject("music") ?: JSONObject()
            val drc = json.optJSONObject("drc") ?: JSONObject()
            val video = json.optJSONObject("video")
            return NowState(
                token = json.optString("token"),
                music = Music(
                    state = music.optString("state", "stop"),
                    title = music.optString("title"),
                    artist = music.optString("artist"),
                    album = music.optString("album"),
                    line1 = music.optString("line1"),
                    edition = music.optString("edition"),
                    art = music.optString("art"),
                    rate = music.optIntOrNull("rate"),
                    bits = music.optIntOrNull("bits"),
                    renderer = music.optString("renderer"),
                ),
                drc = Drc(
                    running = drc.optBoolean("running", false),
                    geometry = drc.optStringOrNull("geometry"),
                    description = drc.optStringOrNull("description") ?: drc.optStringOrNull("design_id"),
                    attenuationDb = if (drc.isNull("effective_attenuation_db")) null
                    else drc.optDouble("effective_attenuation_db"),
                    headroomSafe = if (drc.isNull("headroom_safe")) null else drc.optBoolean("headroom_safe"),
                ),
                video = video?.let {
                    Video(
                        title = it.optString("title"),
                        year = it.optString("year"),
                        director = it.optString("director"),
                        runtime = it.optString("runtime"),
                        genre = it.optString("genre"),
                        rating = it.optString("rating"),
                        paused = it.optBoolean("paused", false),
                        art = it.optString("art"),
                    )
                },
            )
        }
    }
}
