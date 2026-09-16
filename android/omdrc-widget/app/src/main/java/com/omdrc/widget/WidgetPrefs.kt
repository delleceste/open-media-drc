package com.omdrc.widget

import android.content.Context
import com.omdrc.widget.data.DrcStatus
import com.omdrc.widget.data.MpdStatus
import com.omdrc.widget.data.PeakStatus
import com.omdrc.widget.data.RendererStatus
import com.omdrc.widget.data.RtiStatus
import com.omdrc.widget.data.WidgetSnapshot

/**
 * Per-appWidgetId configuration + last-good snapshot cache. Each widget
 * instance gets its own SharedPreferences file so multiple widgets can (in
 * principle) point at different boxes, and so onDeleted can cleanly drop a
 * single instance's state without touching the others.
 */
object WidgetPrefs {
    private fun fileName(appWidgetId: Int) = "omdrc_widget_$appWidgetId"

    fun save(context: Context, appWidgetId: Int, host: String, port: Int) {
        prefs(context, appWidgetId).edit()
            .putString("host", host)
            .putInt("port", port)
            .apply()
    }

    fun load(context: Context, appWidgetId: Int): Pair<String, Int>? {
        val p = prefs(context, appWidgetId)
        val host = p.getString("host", null) ?: return null
        return host to p.getInt("port", AppPrefs.DEFAULT_PORT)
    }

    fun saveSnapshot(context: Context, appWidgetId: Int, snapshot: WidgetSnapshot) {
        val editor = prefs(context, appWidgetId).edit()
            .putLong("fetched_at", snapshot.fetchedAtMillis)
            .putBoolean("reachable", snapshot.reachable)
        // Only overwrite the last-good fields when the fetch actually
        // succeeded, so a transient outage doesn't erase the last real
        // status - the "reachable" flag above still gets updated every
        // time, which is what drives the staleness/unreachable display.
        if (snapshot.reachable) {
            snapshot.drc?.let { drc ->
                editor.putBoolean("drc_ok", drc.ok)
                editor.putBoolean("drc_running", drc.running)
                editor.putString("geometry", drc.geometry)
                editor.putString("design_id", drc.designId)
                editor.putString("description", drc.description)
                drc.effectiveAttenuationDb?.let { editor.putFloat("atten_db", it.toFloat()) }
                editor.putString("atten_source", drc.effectiveAttenuationSource)
                drc.headroomSafe?.let { editor.putBoolean("headroom_safe", it) }
            }
            snapshot.mpd?.let { mpd ->
                editor.putString("mpd_state", mpd.state)
                editor.putString("mpd_song", mpd.song)
                editor.putString("mpd_title", mpd.title)
                editor.putString("mpd_album", mpd.album)
                mpd.sampleRate?.let { editor.putInt("sample_rate", it) }
                mpd.bitDepth?.let { editor.putInt("bit_depth", it) }
                mpd.brutefirRate?.let { editor.putInt("brutefir_rate", it) }
            }
            snapshot.renderer?.let { renderer ->
                editor.putBoolean("qobuzconnect2mpd", renderer.qobuzconnect2mpd)
                editor.putBoolean("upmpdcli", renderer.upmpdcli)
                editor.putString("now_playing", renderer.nowPlaying)
            }
            // rti/peak are null (not just absent) whenever DRC isn't
            // running - clear any previously-cached figures so an old
            // reading doesn't linger under a now-stale "DRC ON" label.
            editor.remove("rti_available").remove("rti_value")
            editor.remove("peak_available").remove("peak_db").remove("peak_clipped")
            snapshot.rti?.let { rti ->
                editor.putBoolean("rti_available", rti.available)
                rti.rti?.let { editor.putFloat("rti_value", it.toFloat()) }
            }
            snapshot.peak?.let { peak ->
                editor.putBoolean("peak_available", peak.available)
                peak.peakDb?.let { editor.putFloat("peak_db", it.toFloat()) }
                editor.putBoolean("peak_clipped", peak.clipped)
            }
        }
        editor.apply()
    }

    fun loadSnapshot(context: Context, appWidgetId: Int): WidgetSnapshot? {
        val p = prefs(context, appWidgetId)
        if (!p.contains("fetched_at")) return null
        val drc = if (p.contains("drc_ok")) DrcStatus(
            ok = p.getBoolean("drc_ok", false),
            running = p.getBoolean("drc_running", false),
            geometry = p.getString("geometry", null),
            designId = p.getString("design_id", null),
            description = p.getString("description", null),
            effectiveAttenuationDb = if (p.contains("atten_db")) p.getFloat("atten_db", 0f).toDouble() else null,
            effectiveAttenuationSource = p.getString("atten_source", null),
            headroomSafe = if (p.contains("headroom_safe")) p.getBoolean("headroom_safe", false) else null,
            error = null,
        ) else null
        val mpd = if (p.contains("mpd_state")) MpdStatus(
            ok = true,
            state = p.getString("mpd_state", null),
            song = p.getString("mpd_song", null),
            title = p.getString("mpd_title", null),
            album = p.getString("mpd_album", null),
            sampleRate = if (p.contains("sample_rate")) p.getInt("sample_rate", 0) else null,
            bitDepth = if (p.contains("bit_depth")) p.getInt("bit_depth", 0) else null,
            brutefirRate = if (p.contains("brutefir_rate")) p.getInt("brutefir_rate", 0) else null,
        ) else null
        val rti = if (p.contains("rti_available")) RtiStatus(
            available = p.getBoolean("rti_available", false),
            rti = if (p.contains("rti_value")) p.getFloat("rti_value", 0f).toDouble() else null,
        ) else null
        val peak = if (p.contains("peak_available")) PeakStatus(
            available = p.getBoolean("peak_available", false),
            peakDb = if (p.contains("peak_db")) p.getFloat("peak_db", 0f).toDouble() else null,
            clipped = p.getBoolean("peak_clipped", false),
        ) else null
        val renderer = if (p.contains("qobuzconnect2mpd") || p.contains("upmpdcli")) RendererStatus(
            qobuzconnect2mpd = p.getBoolean("qobuzconnect2mpd", false),
            upmpdcli = p.getBoolean("upmpdcli", false),
            nowPlaying = p.getString("now_playing", null),
        ) else null
        return WidgetSnapshot(
            drc = drc,
            mpd = mpd,
            rti = rti,
            peak = peak,
            renderer = renderer,
            fetchedAtMillis = p.getLong("fetched_at", 0L),
            reachable = p.getBoolean("reachable", false),
        )
    }

    fun delete(context: Context, appWidgetId: Int) {
        context.deleteSharedPreferences(fileName(appWidgetId))
    }

    private fun prefs(context: Context, appWidgetId: Int) =
        context.getSharedPreferences(fileName(appWidgetId), Context.MODE_PRIVATE)
}
