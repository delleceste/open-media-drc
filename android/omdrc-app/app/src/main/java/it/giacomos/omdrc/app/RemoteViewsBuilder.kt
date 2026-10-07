package it.giacomos.omdrc.app

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.graphics.RadialGradient
import android.graphics.Rect
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.Typeface
import android.os.Build
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
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

private const val ICON_SURFACE_PX = 256

// The Pixel launcher's label face.
private const val PIXEL_LABEL_FONT = "google-sans-text"

private const val PIXEL_LAUNCHER = "com.google.android.apps.nexuslauncher"

// Font families offered for the label, besides the launcher's and the
// system default; only the ones this device has are listed.
private val LABEL_FONT_CANDIDATES = listOf(
    "google-sans-text", "google-sans", "sans-serif", "sans-serif-medium",
    "sans-serif-condensed", "roboto-flex", "serif", "monospace",
    "barlow", "karla", "lato", "rubik", "fraunces", "lustria",
)

// Pixel launcher on a Pixel 9, in dp: cell width (as the widget reports
// it), icon diameter, icon bottom to the label's cap line, label cap
// height, measured off screenshots. Small, medium, large and extra-large
// icon grids, by cell width - the cell's height doesn't order them
// (medium's cell is the shortest).
private val PIXEL_GRIDS = arrayOf(
    floatArrayOf(62f, 52.6f, 8.4f, 8.76f),
    floatArrayOf(81.1f, 59.4f, 13.5f, 10.1f),
    floatArrayOf(112f, 83f, 14.9f, 12.2f),
    floatArrayOf(175.8f, 144.1f, 22.7f, 17.5f),
)

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

    /** The 1x1 tile, made to look like the launcher icon + its label: no
     *  room for title/subtitle text, so it gets its own render path rather
     *  than squeezing into renderConfigured()'s text-driven states. */
    private fun buildTiny(
        context: Context,
        appWidgetId: Int,
        host: String?,
        port: Int,
        snapshot: WidgetSnapshot?,
    ): RemoteViews {
        val views = RemoteViews(context.packageName, R.layout.widget_tiny)

        layoutTiny(context, views, appWidgetId)

        // A null song means an empty queue (nothing loaded to play/pause),
        // not just "currently stopped". Out of reach (the phone away from
        // home), the album last seen loaded most likely still is, so its
        // cover stays; the play/pause control and DRC badge don't, as they
        // would claim a state the tile can't know.
        val reachable = snapshot?.reachable == true
        val hasQueue = snapshot?.mpd?.song != null
        val drcOn = reachable && snapshot?.drc?.running == true
        val art = if (hasQueue) WidgetArtCache.load(context, appWidgetId) else null
        views.setImageViewBitmap(R.id.cover_art, iconSurface(context, art, drcOn))

        if (reachable && hasQueue) {
            val playing = snapshot?.mpd?.state == "playing"
            views.setImageViewResource(R.id.play_pause_icon, if (playing) R.drawable.ic_pause else R.drawable.ic_play)
            views.setViewVisibility(R.id.play_pause_icon, View.VISIBLE)
            views.setOnClickPendingIntent(R.id.play_pause_icon, togglePlayPauseIntent(context, appWidgetId))
        } else {
            views.setViewVisibility(R.id.play_pause_icon, View.GONE)
        }

        views.setOnClickPendingIntent(R.id.widget_root, openDashboardIntent(context, appWidgetId, host, port))

        return views
    }

    /** Column [column] of PIXEL_GRIDS, linearly interpolated (and
     *  extrapolated past either end) at cell width [width] dp. */
    private fun pixelMetric(width: Float, column: Int): Float {
        val i = (1 until PIXEL_GRIDS.size - 1).firstOrNull { width < PIXEL_GRIDS[it][0] }
            ?: (PIXEL_GRIDS.size - 1)
        val a = PIXEL_GRIDS[i - 1]
        val b = PIXEL_GRIDS[i]
        return a[column] + (b[column] - a[column]) * (width - a[0]) / (b[0] - a[0])
    }

    private fun isPixelLauncher(context: Context): Boolean {
        val home = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
        val resolved = context.packageManager.resolveActivity(home, PackageManager.MATCH_DEFAULT_ONLY)
        return resolved?.activityInfo?.packageName == PIXEL_LAUNCHER
    }

    /** The launcher's label font family, as far as it can be told: no
     *  launcher reports it, but the Pixel launcher's is known, and most
     *  others use the system default (null). */
    fun launcherFontFamily(context: Context): String? =
        if (isPixelLauncher(context)) PIXEL_LABEL_FONT else null

    /** LABEL_FONT_CANDIDATES this device has: an unknown family resolves
     *  to the default typeface. */
    fun availableLabelFonts(): List<String> = LABEL_FONT_CANDIDATES.filter {
        it == "sans-serif" || Typeface.create(it, Typeface.NORMAL) != Typeface.DEFAULT
    }

    private fun labelTypeface(context: Context, tuning: TileTuning): Typeface {
        val family = tuning.fontFamily ?: launcherFontFamily(context) ?: return Typeface.DEFAULT
        return Typeface.create(family, Typeface.NORMAL)
    }

    /** Sizes the tile like the launcher's own icons in the same cell. A
     *  launcher reports a widget cell as big as an icon cell and centers
     *  icon + label in it. No API exposes the launcher's icon size, so on
     *  the Pixel launcher icon, gap and label are interpolated between its
     *  measured grids (PIXEL_GRIDS), and anywhere else they're plain
     *  proportions of the cell - close to a launcher icon, if not exact.
     *  The user's TileTuning, set by eye in WidgetConfigureActivity, then
     *  scales and nudges the result. The label is drawn into a bitmap, so
     *  it can take any font and its capitals land exactly at the intended
     *  height. Before Android 12 RemoteViews can't size views at runtime:
     *  the layout's fixed, centered icon applies, with a default label. */
    private fun layoutTiny(context: Context, views: RemoteViews, appWidgetId: Int) {
        val tuning = WidgetPrefs.loadTileTuning(context, appWidgetId)
        views.setInt(R.id.widget_root, "setBackgroundResource", if (tuning.outline) R.drawable.widget_outline else 0)
        val typeface = labelTypeface(context, tuning)
        val options = AppWidgetManager.getInstance(context).getAppWidgetOptions(appWidgetId)
        val width = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 0).toFloat()
        val height = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT, 0).toFloat()
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S || width <= 0f || height <= 0f) {
            val label = labelBitmap(context, typeface, PIXEL_GRIDS[0][3] * tuning.labelScale, 0f)
            views.setImageViewBitmap(R.id.omdrc_label, label.bitmap)
            return
        }
        val icon: Float
        val gap: Float
        val capHeight: Float
        if (isPixelLauncher(context)) {
            icon = pixelMetric(width, 1)
            gap = pixelMetric(width, 2)
            capHeight = pixelMetric(width, 3)
        } else {
            // The Pixel grids' typical proportions: icon ~75% of the cell
            // width, gap ~18% and label caps ~14% of the icon.
            val fit = minOf(0.75f * width, height / 1.45f)
            icon = fit
            gap = 0.18f * fit
            capHeight = 0.14f * fit
        }
        val tunedIcon = icon * tuning.iconScale
        val tunedCap = capHeight * tuning.labelScale
        // Never more than the cell holds.
        val scale = minOf(1f, width / tunedIcon, height / (tunedIcon + gap + tunedCap))
        val iconDp = (tunedIcon * scale).coerceAtLeast(16f)
        val gapDp = (gap * scale).coerceAtLeast(1f)
        val capDp = (tunedCap * scale).coerceAtLeast(4f)

        val label = labelBitmap(context, typeface, capDp, width)
        val top = ((height - (iconDp + gapDp + capDp)) / 2f + tuning.offsetDp)
            .coerceIn(0f, (height - iconDp).coerceAtLeast(0f))
        Log.d("OmdrcWidget", "layoutTiny($appWidgetId): cell ${width}x$height dp -> icon $iconDp dp, caps $capDp dp, top $top dp, $tuning")

        val dip = TypedValue.COMPLEX_UNIT_DIP
        views.setInt(R.id.tiny_column, "setGravity", Gravity.TOP or Gravity.CENTER_HORIZONTAL)
        views.setViewLayoutWidth(R.id.icon_surface, iconDp, dip)
        views.setViewLayoutHeight(R.id.icon_surface, iconDp, dip)
        views.setViewLayoutMargin(R.id.icon_surface, RemoteViews.MARGIN_TOP, top, dip)
        views.setImageViewBitmap(R.id.omdrc_label, label.bitmap)
        views.setViewLayoutMargin(R.id.omdrc_label, RemoteViews.MARGIN_TOP, gapDp - label.capTopDp, dip)
        val chip = 0.3f * iconDp
        views.setViewLayoutWidth(R.id.play_pause_icon, chip, dip)
        views.setViewLayoutHeight(R.id.play_pause_icon, chip, dip)
    }

    private class Label(val bitmap: Bitmap, val capTopDp: Float)

    /** The "OMDRC" label in [typeface], sized so its capitals are [capDp]
     *  tall - in dp, not sp: the launcher's labels follow the grid, not the
     *  font scale setting - and centered in [widthDp] (at least the text's
     *  own width). capTopDp is how far below the bitmap's top the cap line
     *  sits, to place it a given gap under the icon. */
    private fun labelBitmap(context: Context, typeface: Typeface, capDp: Float, widthDp: Float): Label {
        val density = context.resources.displayMetrics.density
        val text = context.getString(R.string.app_name)
        val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.SUBPIXEL_TEXT_FLAG).apply {
            this.typeface = typeface
            color = 0xFFFFFFFF.toInt()
            textAlign = Paint.Align.CENTER
            textSize = 100f
        }
        val bounds = Rect().also { paint.getTextBounds(text, 0, text.length, it) }
        paint.textSize = 100f * capDp * density / bounds.height()
        val metrics = paint.fontMetrics
        val baseline = -metrics.top
        val capTop = baseline + bounds.top * paint.textSize / 100f
        val width = maxOf(widthDp * density, paint.measureText(text) + 2f).roundToInt().coerceAtLeast(1)
        val height = (metrics.bottom - metrics.top).roundToInt().coerceAtLeast(1)
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        bitmap.density = context.resources.displayMetrics.densityDpi
        Canvas(bitmap).drawText(text, width / 2f, baseline, paint)
        return Label(bitmap, capTop / density)
    }

    /** The launcher icon's circle, drawn as one square bitmap so it stays a
     *  true circle whatever the cell's aspect: the static icon (the
     *  ic_launcher_background gradient + ic_launcher_foreground mark) when
     *  there's no art, the cover filling that same circle when there is.
     *  The DRC badge is painted in too, so it stays in the circle's own
     *  top-right corner rather than the cell's. */
    private fun iconSurface(context: Context, art: Bitmap?, drcOn: Boolean): Bitmap {
        val size = ICON_SURFACE_PX
        val content = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(content)
        if (art != null) {
            val side = minOf(art.width, art.height)
            val left = (art.width - side) / 2
            val top = (art.height - side) / 2
            canvas.drawBitmap(
                art, Rect(left, top, left + side, top + side), Rect(0, 0, size, size),
                Paint(Paint.FILTER_BITMAP_FLAG),
            )
        } else {
            // Same gradient as ic_launcher_background (artwork/draw-app-icon.py).
            val paint = Paint().apply {
                shader = RadialGradient(
                    size * 0.5f, size * 0.45f, size * 0.7f,
                    0xFF1B2028.toInt(), 0xFF07090C.toInt(), Shader.TileMode.CLAMP,
                )
            }
            canvas.drawRect(0f, 0f, size.toFloat(), size.toFloat(), paint)
            // An adaptive icon's mask only shows the central 72 of its 108dp
            // layer, so the launcher draws the mark 1.5x what a plain
            // 108->size scale would - match that.
            val inset = size / 4
            context.getDrawable(R.drawable.ic_launcher_foreground)?.let {
                it.setBounds(-inset, -inset, size + inset, size + inset)
                it.draw(canvas)
            }
        }
        val surface = circularCrop(content)
        if (drcOn) drawDrcBadge(context, surface)
        return surface
    }

    private fun drawDrcBadge(context: Context, surface: Bitmap) {
        val size = surface.width.toFloat()
        val canvas = Canvas(surface)
        val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = 0xFFFFFFFF.toInt()
            textSize = size * 0.13f
            typeface = Typeface.DEFAULT_BOLD
        }
        val text = context.getString(R.string.drc_badge)
        val padX = size * 0.04f
        val padY = size * 0.02f
        val width = textPaint.measureText(text) + 2 * padX
        val height = textPaint.textSize + 2 * padY
        val box = RectF(size - width, 0f, size, height)
        val bgPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xCC000000.toInt() }
        canvas.drawRoundRect(box, size * 0.04f, size * 0.04f, bgPaint)
        canvas.drawText(text, box.left + padX, box.bottom - padY - textPaint.descent(), textPaint)
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
