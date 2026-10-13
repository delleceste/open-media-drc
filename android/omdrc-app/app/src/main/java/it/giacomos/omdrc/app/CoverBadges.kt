package it.giacomos.omdrc.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Rect
import android.graphics.RectF
import android.graphics.Typeface
import java.util.Locale

/**
 * The notification's cover with the widget's badges drawn on it: the DRC
 * in use with its output rate ("DRC 192", only while the DRC runs), the
 * source format ("96/24", colored as on the widget) and the stored DR of
 * the track ("DR11"), or of its album ("DR12 album"), with "≈" when it is
 * not final (see drLabel).
 *
 * Two layouts, for the two ways Android shows the picture:
 *  - MEDIA: the media player stretches the art across a wide card and
 *    crops a square's top and bottom quarters, with its own app icon and
 *    output chip at the card's top corners and its title, buttons and seek
 *    bar below. The badges go in a row at the top center of what stays in
 *    view, which the player leaves free.
 *  - ICON: the plain notification's small square thumbnail, shown whole:
 *    one row along its bottom, large enough to read at that size.
 */
object CoverBadges {
    enum class Layout { MEDIA, ICON }

    private const val SIZE_PX = 512
    /** The widest badge row in the media player, as a share of the cover. */
    private const val MEDIA_ROW = 0.6f

    fun draw(
        context: Context, art: Bitmap?, layout: Layout,
        drcRate: Int?, drcOn: Boolean, rate: Int?, bits: Int?, dr: String?,
    ): Bitmap? {
        val badges = listOfNotNull(
            if (drcOn) (context.getString(R.string.drc_badge) + (drcRate?.let { " " + khz(it) } ?: "")) to
                context.getColor(R.color.widget_fmt_green) else null,
            RemoteViewsBuilder.formatShort(rate, bits)?.let { it to RemoteViewsBuilder.formatColor(context, rate, bits) },
            dr?.let { it to 0xFFFFFFFF.toInt() },
        )
        if (badges.isEmpty()) return art
        val out = Bitmap.createBitmap(SIZE_PX, SIZE_PX, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(out)
        if (art != null) {
            // Center-crop the cover into the square.
            val side = minOf(art.width, art.height)
            val src = Rect((art.width - side) / 2, (art.height - side) / 2, (art.width + side) / 2, (art.height + side) / 2)
            canvas.drawBitmap(art, src, Rect(0, 0, SIZE_PX, SIZE_PX), Paint(Paint.FILTER_BITMAP_FLAG))
        } else {
            canvas.drawColor(0xFF202124.toInt())
        }

        val cap = SIZE_PX * if (layout == Layout.MEDIA) 0.045f else 0.11f
        val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.SUBPIXEL_TEXT_FLAG).apply {
            typeface = Typeface.create("sans-serif", Typeface.BOLD)
            textSize = 100f
        }
        val ref = Rect().also { paint.getTextBounds("DRC", 0, 3, it) }
        paint.textSize = 100f * cap / ref.height()
        paint.setShadowLayer(0.12f * cap, 0f, 0.05f * cap, context.getColor(R.color.widget_strip_text_shadow))
        val padX = 0.45f * cap
        val padY = 0.4f * cap
        val gap = gap(cap)
        val widths = badges.map { paint.measureText(it.first) + 2 * padX }
        val height = cap + 2 * padY
        val backdrop = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0x99000000.toInt() }
        if (layout == Layout.MEDIA) {
            // Rows no wider than the gap between the player's app icon and
            // its output chip; what does not fit goes on a row below, still
            // above the title.
            val rows = mutableListOf(mutableListOf<Int>())
            var used = 0f
            for (i in badges.indices) {
                val need = widths[i] + if (rows.last().isEmpty()) 0f else gap
                if (rows.last().isNotEmpty() && used + need > SIZE_PX * MEDIA_ROW) {
                    rows += mutableListOf<Int>()
                    used = 0f
                }
                used += widths[i] + if (rows.last().isEmpty()) 0f else gap
                rows.last() += i
            }
            for ((r, row) in rows.withIndex()) {
                val total = row.sumOf { widths[it].toDouble() }.toFloat() + gap * (row.size - 1)
                drawRow(canvas, paint, backdrop, badges, widths, row, (SIZE_PX - total) / 2f,
                    SIZE_PX * 0.29f + r * (height + gap), height, padX, padY, cap, 1f)
            }
        } else {
            var total = widths.sum() + gap * (badges.size - 1)
            // Too wide for the thumbnail: shrink the row to fit, edge to edge.
            val room = SIZE_PX * 0.94f
            val scale = if (total > room) room / total else 1f
            if (scale < 1f) {
                paint.textSize *= scale
                total = room
            }
            drawRow(canvas, paint, backdrop, badges, widths, badges.indices.toList(), (SIZE_PX - total) / 2f,
                SIZE_PX - height * scale - SIZE_PX * 0.04f, height, padX, padY, cap, scale)
        }
        return out
    }

    private fun drawRow(
        canvas: Canvas, paint: Paint, backdrop: Paint, badges: List<Pair<String, Int>>, widths: List<Float>,
        row: List<Int>, left: Float, top: Float, height: Float, padX: Float, padY: Float, cap: Float, scale: Float,
    ) {
        var x = left
        for (i in row) {
            val w = widths[i] * scale
            val h = height * scale
            canvas.drawRoundRect(RectF(x, top, x + w, top + h), 0.5f * cap * scale, 0.5f * cap * scale, backdrop)
            paint.color = badges[i].second
            canvas.drawText(badges[i].first, x + padX * scale, top + (padY + cap) * scale, paint)
            x += w + gap(cap) * scale
        }
    }

    private fun gap(cap: Float) = 0.5f * cap

    /** The track's figure, "DR11" - "≈DR11" from a partial listen - else
     *  the album's, "DR12 album" - "≈DR12 album" for an estimate from some
     *  of its tracks - else null. */
    fun drLabel(track: Int?, trackExact: Boolean, album: Int?, albumExact: Boolean): String? = when {
        track != null -> (if (trackExact) "" else "≈") + "DR$track"
        album != null -> (if (albumExact) "" else "≈") + "DR$album album"
        else -> null
    }

    /** 192000 -> "192", 44100 -> "44.1". */
    private fun khz(rate: Int): String =
        if (rate % 1000 == 0) "${rate / 1000}" else String.format(Locale.ROOT, "%.1f", rate / 1000.0)
}
