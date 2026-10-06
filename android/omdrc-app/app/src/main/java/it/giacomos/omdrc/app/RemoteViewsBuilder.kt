package it.giacomos.omdrc.app

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.graphics.Rect
import android.view.View
import android.widget.RemoteViews
import it.giacomos.omdrc.app.data.MpdStatus
import it.giacomos.omdrc.app.data.WidgetSnapshot
import java.util.concurrent.TimeUnit
import kotlin.math.roundToInt

enum class WidgetSize { TINY, SMALL, LARGE }

// Offsets request codes for the extra PendingIntents below away from the
// ones keyed on appWidgetId alone (manualRefreshIntent, openDashboardIntent)
// so they never collide for the same widget instance.
private const val TOGGLE_REQUEST_CODE_OFFSET = 1_000_000

/**
 * Pure(ish) rendering: takes a snapshot (possibly null/stale/unreachable)
 * and produces the RemoteViews to show, without touching the network
 * itself. Kept separate from the provider/worker so the state->UI mapping
 * is easy to reason about on its own.
 */
object RemoteViewsBuilder {

    fun build(
        context: Context,
        appWidgetId: Int,
        host: String?,
        port: Int,
        snapshot: WidgetSnapshot?,
        size: WidgetSize,
        checking: Boolean = false,
    ): RemoteViews {
        if (size == WidgetSize.TINY) {
            return buildTiny(context, appWidgetId, host, port, snapshot)
        }
        val layout = if (size == WidgetSize.LARGE) R.layout.widget_large else R.layout.widget_small
        val views = RemoteViews(context.packageName, layout)

        if (host == null) {
            views.setImageViewResource(R.id.status_icon, R.drawable.ic_status_off)
            views.setTextViewText(R.id.title_text, context.getString(R.string.status_unconfigured))
            views.setTextViewText(R.id.subtitle_text, "")
        } else {
            renderConfigured(context, views, snapshot)
        }

        if (size == WidgetSize.LARGE) {
            views.setTextViewText(R.id.mpd_text, mpdLine(context, snapshot))
            renderChainLine(views, snapshot)
            renderMetersLine(views, snapshot)
        }

        // Overrides the subtitle after normal rendering so a manual-refresh
        // tap is visibly acknowledged even when the fetch it kicks off ends
        // up reporting a status identical to what was already on screen
        // (e.g. the box is still mid filter-set switch, which can take up
        // to ~2 minutes server-side) - otherwise a working tap and a dead
        // one look exactly the same.
        if (checking && host != null) {
            views.setTextViewText(R.id.subtitle_text, "Checking…")
        }

        views.setOnClickPendingIntent(R.id.widget_root, openDashboardIntent(context, appWidgetId, host, port))
        views.setOnClickPendingIntent(R.id.refresh_button, manualRefreshIntent(context, appWidgetId))

        return views
    }

    /** The 1x1 cover-art tile: no room for title/subtitle text, so it gets
     *  its own render path entirely rather than squeezing into
     *  renderConfigured()'s text-driven states. Unconfigured/unreachable
     *  just falls back to the placeholder art + whatever play/pause icon
     *  the last cached state implies - there's no text room to say so. */
    private fun buildTiny(
        context: Context,
        appWidgetId: Int,
        host: String?,
        port: Int,
        snapshot: WidgetSnapshot?,
    ): RemoteViews {
        val views = RemoteViews(context.packageName, R.layout.widget_tiny)

        val art = WidgetArtCache.load(context, appWidgetId)
        if (art != null) {
            views.setImageViewBitmap(R.id.cover_art, circularCrop(art))
        } else {
            views.setImageViewResource(R.id.cover_art, R.drawable.ic_cover_placeholder)
        }

        val playing = snapshot?.mpd?.state == "playing"
        views.setImageViewResource(R.id.play_pause_icon, if (playing) R.drawable.ic_pause else R.drawable.ic_play)

        val drcOn = snapshot?.drc?.running == true
        views.setViewVisibility(R.id.drc_badge, if (drcOn) View.VISIBLE else View.GONE)

        views.setOnClickPendingIntent(R.id.widget_root, openDashboardIntent(context, appWidgetId, host, port))
        views.setOnClickPendingIntent(R.id.play_pause_icon, togglePlayPauseIntent(context, appWidgetId))

        return views
    }

    /** RemoteViews/AppWidgetHostView has no clipToOutline support reliable
     *  enough across launchers to round an ImageView on its own, so the
     *  circle is baked into the bitmap itself before handing it over. */
    private fun circularCrop(bitmap: Bitmap): Bitmap {
        val size = minOf(bitmap.width, bitmap.height)
        val output = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(output)
        val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        canvas.drawCircle(size / 2f, size / 2f, size / 2f, paint)
        paint.xfermode = PorterDuffXfermode(PorterDuff.Mode.SRC_IN)
        val left = (bitmap.width - size) / 2
        val top = (bitmap.height - size) / 2
        canvas.drawBitmap(bitmap, Rect(left, top, left + size, top + size), Rect(0, 0, size, size), paint)
        return output
    }

    private fun togglePlayPauseIntent(context: Context, appWidgetId: Int): PendingIntent {
        val intent = Intent(context, OmdrcWidgetProvider::class.java).apply {
            action = OmdrcWidgetProvider.ACTION_TOGGLE_PLAY_PAUSE
            putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId)
        }
        return PendingIntent.getBroadcast(
            context, TOGGLE_REQUEST_CODE_OFFSET + appWidgetId, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun renderConfigured(
        context: Context,
        views: RemoteViews,
        snapshot: WidgetSnapshot?,
    ) {
        if (snapshot == null) {
            views.setImageViewResource(R.id.status_icon, R.drawable.ic_status_off)
            views.setTextViewText(R.id.title_text, "…")
            views.setTextViewText(R.id.subtitle_text, "")
            return
        }

        if (!snapshot.reachable) {
            views.setImageViewResource(R.id.status_icon, R.drawable.ic_status_off)
            views.setTextViewText(R.id.title_text, context.getString(R.string.status_unreachable))
            // No point repeating the host/port here - that's config, not
            // status, and the user already knows what they typed in.
            val hasLastKnown = snapshot.drc != null
            views.setTextViewText(
                R.id.subtitle_text,
                if (hasLastKnown) "Last seen ${staleness(snapshot.fetchedAtMillis)}" else "",
            )
            return
        }

        val drc = snapshot.drc
        // running is the authoritative on/off flag - it's present even in
        // the {ok:false, running:true, error:...} "BruteFIR up but headroom
        // analysis failed" shape, which should still read as "on" with the
        // error surfaced, not as "off".
        if (drc == null || !drc.running) {
            views.setImageViewResource(R.id.status_icon, R.drawable.ic_status_off)
            views.setTextViewText(R.id.title_text, context.getString(R.string.status_drc_off))
            views.setTextViewText(R.id.subtitle_text, drc?.error ?: mpdStateLabel(snapshot.mpd))
            return
        }

        // Deliberately just "DRC ON" - geometry/design/attenuation are
        // config detail, not at-a-glance status; the icon already carries
        // safe/unsafe.
        views.setTextViewText(R.id.title_text, context.getString(R.string.status_drc_on))

        val icon = when (drc.headroomSafe) {
            true -> R.drawable.ic_status_safe
            false -> R.drawable.ic_status_unsafe
            null -> R.drawable.ic_status_off
        }
        views.setImageViewResource(R.id.status_icon, icon)

        views.setTextViewText(R.id.subtitle_text, mpdStateLabel(snapshot.mpd))
    }

    private fun mpdStateLabel(mpd: MpdStatus?): String = when (mpd?.state) {
        "playing" -> "Playing"
        "paused" -> "Paused"
        "stopped" -> "Stopped"
        else -> ""
    }

    /** Which renderer is feeding MPD, plus the song when one's playing -
     *  the geometry/attenuation detail that used to live here moved out per
     *  request; this is "what's actually happening" instead. */
    private fun mpdLine(context: Context, snapshot: WidgetSnapshot?): CharSequence {
        val renderer = snapshot?.renderer
        val mpd = snapshot?.mpd
        // Prefer the renderer's own self-reported title (qobuzconnect2mpd's
        // "line1", same as the dashboard's Renderer card) over MPD's tag
        // data, which falls back to the raw stream URL for a local proxy
        // stream MPD has no metadata for.
        val song = if (mpd?.state == "playing") TrackTitle.titleAndAlbum(
            renderer?.nowPlaying ?: mpd.displaySong,
            mpd.title,
            mpd.album,
        ) else null
        val parts = listOfNotNull(renderer?.label, song)
        return if (parts.isEmpty()) context.getString(R.string.mpd_unknown) else parts.joinToString(" · ")
    }

    /** "24/96000 → 96000 Hz" source->output bitrate chain - only shown
     *  while DRC is actually running, per request. */
    private fun renderChainLine(views: RemoteViews, snapshot: WidgetSnapshot?) {
        val text = ChainFormat.bitrateChain(snapshot?.mpd, snapshot?.drc?.running == true)
        if (text.isNullOrEmpty()) {
            views.setViewVisibility(R.id.chain_text, View.GONE)
        } else {
            views.setTextViewText(R.id.chain_text, text)
            views.setViewVisibility(R.id.chain_text, View.VISIBLE)
        }
    }

    private fun renderMetersLine(views: RemoteViews, snapshot: WidgetSnapshot?) {
        val parts = mutableListOf<String>()
        val rti = snapshot?.rti
        if (rti != null && rti.available && rti.rti != null) {
            parts.add("RTI ${(rti.rti * 100).roundToInt()}%")
        }
        val peak = snapshot?.peak
        if (peak != null && peak.available) {
            val peakText = peak.peakDb?.let { "%.1f dB".format(it) } ?: "−∞ dB"
            parts.add("Peak $peakText" + if (peak.clipped) " ⚠" else "")
        }
        if (parts.isEmpty()) {
            views.setViewVisibility(R.id.meters_text, View.GONE)
        } else {
            views.setTextViewText(R.id.meters_text, parts.joinToString(" · "))
            views.setViewVisibility(R.id.meters_text, View.VISIBLE)
        }
    }

    private fun staleness(fetchedAtMillis: Long): String {
        val minutes = TimeUnit.MILLISECONDS.toMinutes(System.currentTimeMillis() - fetchedAtMillis)
        return when {
            minutes < 1 -> "just now"
            minutes < 60 -> "${minutes}m ago"
            else -> "${minutes / 60}h ago"
        }
    }

    private fun openDashboardIntent(context: Context, appWidgetId: Int, host: String?, port: Int): PendingIntent {
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            if (host != null) {
                putExtra(MainActivity.EXTRA_HOST, host)
                putExtra(MainActivity.EXTRA_PORT, port)
            }
        }
        return PendingIntent.getActivity(
            context, appWidgetId, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun manualRefreshIntent(context: Context, appWidgetId: Int): PendingIntent {
        val intent = Intent(context, OmdrcWidgetProvider::class.java).apply {
            action = OmdrcWidgetProvider.ACTION_MANUAL_REFRESH
            putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId)
        }
        return PendingIntent.getBroadcast(
            context, appWidgetId, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }
}
