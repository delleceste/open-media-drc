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

enum class WidgetSize { TINY, SMALL, LARGE, MEDIA, COMPACT }

// Offsets request codes for the extra PendingIntents below away from the
// ones keyed on appWidgetId alone (manualRefreshIntent, openDashboardIntent)
// so they never collide for the same widget instance.
private const val TOGGLE_REQUEST_CODE_OFFSET = 1_000_000

private const val TRANSPORT_REQUEST_CODE_OFFSET = 2_000_000

private const val ICON_SURFACE_PX = 256

// The Pixel launcher's label face.
private const val PIXEL_LABEL_FONT = "google-sans-text"

// Clear space kept between the play/pause chip and the label's text.
private const val CHIP_TEXT_MARGIN_PX = 3f

// Below this the strip over the 1x1 tile's icon isn't drawn at all.
private const val MIN_STRIP_CAP_DP = 4f

// The strip's backdrop around its text, in cap heights.
private const val STRIP_PAD_X = 0.45f
private const val STRIP_PAD_Y = 0.3f

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
        if (size == WidgetSize.COMPACT) {
            return buildCompact(context, appWidgetId, host, port, snapshot)
        }
        if (size == WidgetSize.MEDIA) {
            return buildMedia(context, appWidgetId, host, port, snapshot, checking)
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

    /** The tall media widget: cover, track, progress, DRC and transport. */
    private fun buildMedia(
        context: Context,
        appWidgetId: Int,
        host: String?,
        port: Int,
        snapshot: WidgetSnapshot?,
        checking: Boolean,
    ): RemoteViews {
        val views = RemoteViews(context.packageName, R.layout.widget_media)
        views.setOnClickPendingIntent(R.id.widget_root, openDashboardIntent(context, appWidgetId, host, port))
        views.setOnClickPendingIntent(R.id.refresh_button, manualRefreshIntent(context, appWidgetId))
        views.setOnClickPendingIntent(R.id.media_prev, transportIntent(context, appWidgetId, "prev"))
        views.setOnClickPendingIntent(R.id.media_next, transportIntent(context, appWidgetId, "next"))
        views.setOnClickPendingIntent(R.id.media_play_pause, togglePlayPauseIntent(context, appWidgetId))

        val reachable = host != null && snapshot?.reachable == true
        val mpd = snapshot?.mpd
        val hasQueue = reachable && mpd?.song != null
        val playing = hasQueue && mpd?.state == "playing"
        
        // Status line: the DRC state (icon), or why there is nothing to show.
        val drc = snapshot?.drc
        val drcOn = reachable && drc?.running == true
        views.setImageViewResource(
            R.id.status_icon,
            when {
                !drcOn -> R.drawable.ic_status_off
                drc?.headroomSafe == true -> R.drawable.ic_status_safe
                drc?.headroomSafe == false -> R.drawable.ic_status_unsafe
                else -> R.drawable.ic_status_off
            },
        )
        views.setTextViewText(
            R.id.title_text,
            when {
                host == null -> context.getString(R.string.status_unconfigured)
                snapshot == null -> "…"
                !snapshot.reachable -> context.getString(R.string.status_unreachable) +
                    " · " + staleness(snapshot.fetchedAtMillis)
                checking -> "Checking…"
                drcOn -> context.getString(R.string.status_drc_on)
                else -> context.getString(R.string.status_drc_off)
            },
        )

        // Out of reach, the last cover and track stay, as on the tile; the
        // transport and progress don't, as they'd claim a live state.
        val cached = snapshot?.mpd
        val cachedQueue = cached?.song != null
        val player = snapshot?.player
        val renderer = snapshot?.renderer
        val title = TrackTitle.titleAndAlbum(
            renderer?.nowPlaying ?: cached?.displaySong, cached?.title, null,
        )
        views.setTextViewText(
            R.id.media_title,
            if (cachedQueue) title ?: cached?.title ?: "" else context.getString(R.string.mpd_unknown),
        )
        views.setTextViewText(R.id.media_artist, if (cachedQueue) player?.artist ?: "" else "")
        views.setTextViewText(
            R.id.media_edition,
            if (cachedQueue) listOfNotNull(cached?.album, player?.edition).joinToString(" · ") else "",
        )

        val art = if (cachedQueue) WidgetArtCache.load(context, appWidgetId) else null
        views.setImageViewBitmap(R.id.media_cover, roundedCover(context, art))

        // Fit the cover and the text to the size it was given: the cover
        // is what's left of the height after the times and the controls,
        // at most 45% of the width; a narrow widget drops the secondary
        // text lines instead of squeezing them into a sliver.
        val options = AppWidgetManager.getInstance(context).getAppWidgetOptions(appWidgetId)
        val width = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 0).toFloat()
        val height = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT, 0).toFloat()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && width > 0f && height > 0f) {
            // Text grows with the widget (up to 1.6x its size at 2x2).
            val grow = minOf(width / 218f, height / 202f).coerceIn(1f, 1.6f)
            val cover = minOf(height - 20f - 36f - 40f, 0.42f * (width - 24f)).coerceAtLeast(40f)
            val dip = TypedValue.COMPLEX_UNIT_DIP
            views.setViewLayoutWidth(R.id.media_cover, cover, dip)
            views.setViewLayoutHeight(R.id.media_cover, cover, dip)
            views.setTextViewTextSize(R.id.media_title, TypedValue.COMPLEX_UNIT_SP, 13f * grow)
            views.setTextViewTextSize(R.id.media_artist, TypedValue.COMPLEX_UNIT_SP, 12f * grow)
            views.setTextViewTextSize(R.id.media_edition, TypedValue.COMPLEX_UNIT_SP, 11f * grow)
            views.setTextViewTextSize(R.id.media_drc, TypedValue.COMPLEX_UNIT_SP, 11f * grow)
            views.setTextViewTextSize(R.id.media_format, TypedValue.COMPLEX_UNIT_SP, 11f * grow)
        }

        // The source rate, to the right of the controls.
        views.setTextViewText(
            R.id.media_format,
            if (hasQueue) formatShort(mpd?.sampleRate, mpd?.bitDepth) ?: "" else "",
        )
        views.setTextColor(R.id.media_format, formatColor(context, mpd?.sampleRate, mpd?.bitDepth))
        // A tall widget has the room: one DRC figure per line.
        val tall = (AppWidgetManager.getInstance(context).getAppWidgetOptions(appWidgetId)
            .getInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT, 0)) >= 260
        val drcLine = mediaDrcLine(context, snapshot, drcOn, if (tall) "\n" else " · ")
        views.setTextViewText(R.id.media_drc, drcLine)
        views.setViewVisibility(R.id.media_drc, if (drcLine.isEmpty()) View.GONE else View.VISIBLE)
        views.setImageViewResource(R.id.media_play_pause, if (playing) R.drawable.ic_pause else R.drawable.ic_play)
        return views
    }

    /** The short media widget: cover with the play/pause chip, title, artist. */
    private fun buildCompact(
        context: Context,
        appWidgetId: Int,
        host: String?,
        port: Int,
        snapshot: WidgetSnapshot?,
    ): RemoteViews {
        val views = RemoteViews(context.packageName, R.layout.widget_media_compact)
        views.setOnClickPendingIntent(R.id.widget_root, openDashboardIntent(context, appWidgetId, host, port))
        val reachable = host != null && snapshot?.reachable == true
        val mpd = snapshot?.mpd
        val cachedQueue = mpd?.song != null
        val hasQueue = reachable && cachedQueue
        val renderer = snapshot?.renderer
        val title = TrackTitle.titleAndAlbum(renderer?.nowPlaying ?: mpd?.displaySong, mpd?.title, null)
        views.setTextViewText(
            R.id.media_title,
            when {
                host == null -> context.getString(R.string.status_unconfigured)
                cachedQueue -> title ?: mpd?.title ?: ""
                snapshot?.reachable == false -> context.getString(R.string.status_unreachable)
                else -> context.getString(R.string.mpd_unknown)
            },
        )
        views.setTextViewText(R.id.media_artist, if (cachedQueue) snapshot?.player?.artist ?: "" else "")
        val art = if (cachedQueue) WidgetArtCache.load(context, appWidgetId) else null
        views.setImageViewBitmap(R.id.media_cover, roundedCover(context, art))
        if (hasQueue) {
            val playing = mpd?.state == "playing"
            views.setImageViewResource(R.id.media_play_pause, if (playing) R.drawable.ic_pause else R.drawable.ic_play)
            views.setViewVisibility(R.id.media_play_pause, View.VISIBLE)
            views.setOnClickPendingIntent(R.id.media_play_pause, togglePlayPauseIntent(context, appWidgetId))
        } else {
            views.setViewVisibility(R.id.media_play_pause, View.GONE)
        }
        val options = AppWidgetManager.getInstance(context).getAppWidgetOptions(appWidgetId)
        val width = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 0).toFloat()
        val height = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT, 0).toFloat()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && width > 0f && height > 0f) {
            val cover = minOf(height - 16f, 0.45f * (width - 16f)).coerceAtLeast(32f)
            val dip = TypedValue.COMPLEX_UNIT_DIP
            views.setViewLayoutWidth(R.id.media_cover_box, cover, dip)
            views.setViewLayoutHeight(R.id.media_cover_box, cover, dip)
        }
        // Wide enough: previous / next beside the title.
        val wide = width >= 250f && hasQueue
        val skip = if (wide) View.VISIBLE else View.GONE
        views.setViewVisibility(R.id.media_prev, skip)
        views.setViewVisibility(R.id.media_next, skip)
        if (wide) {
            views.setOnClickPendingIntent(R.id.media_prev, transportIntent(context, appWidgetId, "prev"))
            views.setOnClickPendingIntent(R.id.media_next, transportIntent(context, appWidgetId, "next"))
        }
        return views
    }

    /** "96/24 → 96 kHz · 1.2 dB · RTI 80% · Peak -3.1 dB" style DRC detail. */
    private fun mediaDrcLine(context: Context, snapshot: WidgetSnapshot?, drcOn: Boolean, separator: String = " · "): String {
        val drc = snapshot?.drc
        if (!drcOn || drc == null) return ""
        val parts = mutableListOf<String>()
        parts.add(drc.description ?: drc.designId ?: drc.geometry ?: "")
        drc.effectiveAttenuationDb?.let { parts.add("%.1f dB".format(java.util.Locale.ROOT, it)) }
        val rti = snapshot.rti
        if (rti != null && rti.available && rti.rti != null) parts.add("RTI ${(rti.rti * 100).roundToInt()}%")
        val peak = snapshot.peak
        if (peak != null && peak.available) {
            parts.add("Peak " + (peak.peakDb?.let { "%.1f dB".format(java.util.Locale.ROOT, it) } ?: "−∞ dB") +
                if (peak.clipped) " ⚠" else "")
        }
        return parts.filter { it.isNotEmpty() }.joinToString(separator)
    }

    /** The cover as a rounded square, or the app's mark when there's none. */
    private fun roundedCover(context: Context, art: Bitmap?): Bitmap {
        val square = iconSurface(context, art, circle = false)
        val out = Bitmap.createBitmap(square.width, square.height, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(out)
        val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        val r = square.width * 0.08f
        canvas.drawRoundRect(0f, 0f, square.width.toFloat(), square.height.toFloat(), r, r, paint)
        paint.xfermode = PorterDuffXfermode(PorterDuff.Mode.SRC_IN)
        canvas.drawBitmap(square, 0f, 0f, paint)
        return out
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

        // A null song means an empty queue (nothing loaded to play/pause),
        // not just "currently stopped". Out of reach (the phone away from
        // home), the album last seen loaded most likely still is, so its
        // cover stays; the play/pause control and the strip above the icon
        // don't, as they would claim a state the tile can't know.
        val reachable = snapshot?.reachable == true
        val hasQueue = snapshot?.mpd?.song != null
        val drcOn = reachable && snapshot?.drc?.running == true
        val mpd = if (reachable && hasQueue) snapshot?.mpd else null
        val rate = mpd?.let { formatShort(it.sampleRate, it.bitDepth) } ?: ""
        val rateColor = mpd?.let { formatColor(context, it.sampleRate, it.bitDepth) } ?: 0xFFFFFFFF.toInt()
        val drcLabel = if (drcOn) context.getString(R.string.drc_badge) else ""

        layoutTiny(context, views, appWidgetId, drcLabel, rate, rateColor)

        val art = if (hasQueue) WidgetArtCache.load(context, appWidgetId) else null
        views.setImageViewBitmap(R.id.cover_art, iconSurface(context, art))

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

    /** "96/24", "44.1/16": the strip's short source format. */
    private fun formatShort(rate: Int?, bits: Int?): String? {
        if (rate == null || rate <= 0) return null
        val khz = if (rate % 1000 == 0) "${rate / 1000}" else "%.1f".format(java.util.Locale.ROOT, rate / 1000.0)
        return khz + if (bits != null && bits > 0) "/$bits" else ""
    }

    /** The format figure's color, the same rule and colors the kiosk page's
     *  track strip uses (kiosk/static/widgets/track.js K.formatColor):
     *  24-bit/176kHz+ in the app's existing "safe" green, 24-bit/88kHz+ (hi-
     *  res) in its existing accent blue, anything else plain white. */
    private fun formatColor(context: Context, rate: Int?, bits: Int?): Int {
        if (rate == null || bits == null || bits < 24) return 0xFFFFFFFF.toInt()
        return when {
            rate >= 176000 -> context.getColor(R.color.widget_fmt_green)
            rate >= 88000 -> context.getColor(R.color.widget_fmt_hires)
            else -> 0xFFFFFFFF.toInt()
        }
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
    private fun layoutTiny(
        context: Context,
        views: RemoteViews,
        appWidgetId: Int,
        drcLabel: String,
        rateValue: String,
        rateColor: Int,
    ) {
        val tuning = WidgetPrefs.loadTileTuning(context, appWidgetId)
        views.setInt(R.id.widget_root, "setBackgroundResource", if (tuning.outline) R.drawable.widget_outline else 0)
        val typeface = labelTypeface(context, tuning)
        val options = AppWidgetManager.getInstance(context).getAppWidgetOptions(appWidgetId)
        val width = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 0).toFloat()
        val height = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT, 0).toFloat()
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S || width <= 0f || height <= 0f) {
            val label = labelBitmap(context, typeface, PIXEL_GRIDS[0][3] * tuning.labelScale, 0f)
            views.setImageViewBitmap(R.id.omdrc_label, label.bitmap)
            views.setViewVisibility(R.id.tiny_strip_drc, View.GONE)
            views.setViewVisibility(R.id.tiny_strip_rate, View.GONE)
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
        // The play/pause chip straddles the icon's bottom-right edge, its
        // center 92% across and 98% down the icon's square, kept inside the
        // cell - and never over the label's text: where the two overlap
        // across, the chip rises to end CHIP_TEXT_MARGIN_PX above the text's
        // ink bounds.
        // It's centered in the cell by gravity, so the margins are its
        // offset from the cell's center.
        val chip = 0.42f * iconDp
        val dx = minOf(0.42f * iconDp, (width - chip) / 2f)
        var cy = minOf(top + 0.98f * iconDp, height - chip / 2f)
        val labelTop = top + iconDp + gapDp - label.capTopDp
        val margin = CHIP_TEXT_MARGIN_PX / context.resources.displayMetrics.density
        val text = RectF(label.text).apply {
            offset(width / 2f, labelTop)
            inset(-margin, -margin)
        }
        val chipLeft = width / 2f + dx - chip / 2f
        if (chipLeft < text.right && chipLeft + chip > text.left) {
            cy = minOf(cy, text.top - chip / 2f)
        }
        val dy = cy - height / 2f
        Log.d("OmdrcWidget", "layoutTiny($appWidgetId): text ink $text, chip ${RectF(chipLeft, cy - chip / 2f, chipLeft + chip, cy + chip / 2f)} dp")
        views.setViewLayoutWidth(R.id.play_pause_icon, chip, dip)
        views.setViewLayoutHeight(R.id.play_pause_icon, chip, dip)
        views.setViewLayoutMargin(R.id.play_pause_icon, RemoteViews.MARGIN_LEFT, dx, dip)
        views.setViewLayoutMargin(R.id.play_pause_icon, RemoteViews.MARGIN_TOP, maxOf(dy, 0f), dip)
        views.setViewLayoutMargin(R.id.play_pause_icon, RemoteViews.MARGIN_BOTTOM, maxOf(-dy, 0f), dip)
        val pad = (0.18f * chip * context.resources.displayMetrics.density).roundToInt()
        views.setViewPadding(R.id.play_pause_icon, pad, pad, pad, pad)

        // Two independent badges above the icon, each on its own backdrop
        // (the play/pause chip's color): "DRC" in green, aligned with the
        // icon's left edge, and the source format on the right, colored by
        // formatColor, aligned with its right edge (over the chip). Their
        // capitals are at most 80% of the label's, and smaller still where
        // the space over the icon is short; their backdrops end
        // CHIP_TEXT_MARGIN_PX above the icon, and start as far below the
        // cell's top.
        val stripCap = minOf(0.8f * capDp, (top - 2f * margin) / (1f + 2f * STRIP_PAD_Y))
        val sideMargin = (width - iconDp) / 2f
        placeBadge(
            views, R.id.tiny_strip_drc, context, typeface, stripCap, drcLabel,
            context.getColor(R.color.widget_fmt_green), top, margin, sideMargin, dip,
        )
        placeBadge(
            views, R.id.tiny_strip_rate, context, typeface, stripCap, rateValue,
            rateColor, top, margin, sideMargin, dip,
        )
        Log.d(
            "OmdrcWidget",
            "layoutTiny($appWidgetId): DRC \"$drcLabel\", rate \"$rateValue\" cap $stripCap dp, " +
                "color ${Integer.toHexString(rateColor)}",
        )
    }

    /** One badge (DRC or the source format) above the icon: hidden when
     *  [text] is empty or there's no room ([capDp] too small), else sized
     *  and colored by [stripBitmap] and placed [sideMargin] in from the
     *  cell's edge on [viewId]'s own side (its XML layout_gravity: start for
     *  the DRC badge, end for the format one), its bottom
     *  CHIP_TEXT_MARGIN_PX above the icon. */
    @androidx.annotation.RequiresApi(Build.VERSION_CODES.S)
    private fun placeBadge(
        views: RemoteViews,
        viewId: Int,
        context: Context,
        typeface: Typeface,
        capDp: Float,
        text: String,
        color: Int,
        top: Float,
        margin: Float,
        sideMargin: Float,
        dip: Int,
    ) {
        if (text.isEmpty() || capDp < MIN_STRIP_CAP_DP) {
            views.setViewVisibility(viewId, View.GONE)
            return
        }
        val badge = stripBitmap(context, typeface, capDp, text, color)
        views.setImageViewBitmap(viewId, badge.bitmap)
        views.setViewVisibility(viewId, View.VISIBLE)
        val badgeTop = top - margin - badge.text.bottom
        // The launcher rounds a widget's corners and clips what's drawn
        // there: keep the badge's outer top corner inside that curve.
        val radius = cornerRadiusDp(context)
        val rise = radius - badgeTop.coerceIn(0f, radius)
        val inset = radius - kotlin.math.sqrt((radius * radius - rise * rise).coerceAtLeast(0f))
        val side = maxOf(sideMargin, inset)
        views.setViewLayoutMargin(viewId, RemoteViews.MARGIN_TOP, badgeTop, dip)
        views.setViewLayoutMargin(viewId, RemoteViews.MARGIN_START, side, dip)
        views.setViewLayoutMargin(viewId, RemoteViews.MARGIN_END, side, dip)
    }

    /** The radius, in dp, the launcher rounds a widget's corners with. */
    private fun cornerRadiusDp(context: Context): Float {
        val res = context.resources
        val id = res.getIdentifier("system_app_widget_background_radius", "dimen", "android")
        val dp = if (id != 0) res.getDimension(id) / res.displayMetrics.density else 0f
        return maxOf(dp, 24f)
    }

    /** text: the text's ink bounds in dp, across from the bitmap's center
     *  and down from its top (stripBitmap: the whole bitmap, backdrop
     *  included). */
    private class Label(val bitmap: Bitmap, val capTopDp: Float, val text: RectF)

    /** The "OMDRC" label in [typeface], sized so its capitals are [capDp]
     *  tall - in dp, not sp: the launcher's labels follow the grid, not the
     *  font scale setting - and centered in [widthDp] (at least the text's
     *  own width). capTopDp is how far below the bitmap's top the cap line
     *  sits, to place it a given gap under the icon; text its ink bounds,
     *  from the font's own glyph metrics. */
    private fun labelBitmap(
        context: Context,
        typeface: Typeface,
        capDp: Float,
        widthDp: Float,
        text: String = context.getString(R.string.app_name),
    ): Label {
        val density = context.resources.displayMetrics.density
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
        val ink = Rect().also { paint.getTextBounds(text, 0, text.length, it) }
        // getTextBounds is relative to the drawing origin: the baseline,
        // and with Align.CENTER the left of the text still - recenter it.
        val half = paint.measureText(text) / 2f
        val inkDp = RectF(
            (ink.left - half) / density, (baseline + ink.top) / density,
            (ink.right - half) / density, (baseline + ink.bottom) / density,
        )
        return Label(bitmap, capTop / density, inkDp)
    }

    /** One of the 1x1 tile's badges above the icon (DRC, or the source
     *  format): [text] in [color] on its own rounded backdrop - the same
     *  color as the play/pause chip's - sized to fit it, with [capDp]-tall
     *  capitals. The bitmap edges are the backdrop's own, padded by
     *  STRIP_PAD_X/Y of the cap height beyond the text, which is centered
     *  on its own drawn ink, not the font's full ascent/descent - digits
     *  and "DRC" have no descenders, so that reserved space below the
     *  baseline would otherwise push the text toward the top of the pill. */
    private fun stripBitmap(context: Context, typeface: Typeface, capDp: Float, text: String, color: Int): Label {
        val density = context.resources.displayMetrics.density
        val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.SUBPIXEL_TEXT_FLAG).apply {
            this.typeface = typeface
            this.color = color
            textAlign = Paint.Align.LEFT
            textSize = 100f
        }
        // "DRC" as the cap-height reference regardless of which text is
        // shown, so the two badges' text size doesn't jump between states.
        val refBounds = Rect().also { paint.getTextBounds("DRC", 0, 3, it) }
        paint.textSize = 100f * capDp * density / refBounds.height()
        val capPx = capDp * density
        // A dark shadow so a lighter color (the hi-res blue especially)
        // still reads against whatever the backdrop shows through it - its
        // own translucency, or a bright cover behind it.
        paint.setShadowLayer(0.12f * capPx, 0f, 0.05f * capPx, context.getColor(R.color.widget_strip_text_shadow))

        val textWidth = paint.measureText(text)
        val padX = STRIP_PAD_X * capPx
        val padY = STRIP_PAD_Y * capPx
        val ink = Rect().also { paint.getTextBounds(text, 0, text.length, it) }

        val width = (textWidth + 2f * padX).roundToInt().coerceAtLeast(1)
        val height = (ink.bottom - ink.top + 2f * padY).roundToInt().coerceAtLeast(1)
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        bitmap.density = context.resources.displayMetrics.densityDpi
        val canvas = Canvas(bitmap)
        val radius = 0.5f * capPx
        canvas.drawRoundRect(
            0f, 0f, width.toFloat(), height.toFloat(), radius, radius,
            Paint(Paint.ANTI_ALIAS_FLAG).apply { this.color = context.getColor(R.color.widget_chip_backdrop) },
        )
        canvas.drawText(text, padX, padY - ink.top, paint)
        return Label(bitmap, 0f, RectF(0f, 0f, width / density, height / density))
    }

    /** The launcher icon's circle, drawn as one square bitmap so it stays a
     *  true circle whatever the cell's aspect: the static icon (the
     *  ic_launcher_background gradient + ic_launcher_foreground mark) when
     *  there's no art, the cover filling that same circle when there is. */
    private fun iconSurface(context: Context, art: Bitmap?, circle: Boolean = true): Bitmap {
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
        return if (circle) circularCrop(content) else content
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

    private fun transportIntent(context: Context, appWidgetId: Int, action: String): PendingIntent {
        val intent = Intent(context, OmdrcWidgetProvider::class.java).apply {
            this.action = OmdrcWidgetProvider.ACTION_TRANSPORT
            putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId)
            putExtra(OmdrcWidgetProvider.EXTRA_TRANSPORT_ACTION, action)
        }
        val code = TRANSPORT_REQUEST_CODE_OFFSET + 2 * appWidgetId + if (action == "next") 1 else 0
        return PendingIntent.getBroadcast(
            context, code, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
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
