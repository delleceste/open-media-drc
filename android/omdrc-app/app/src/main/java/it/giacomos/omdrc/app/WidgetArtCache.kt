package it.giacomos.omdrc.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import java.io.File

/**
 * Per-appWidgetId cover-art cache, on disk rather than in WidgetPrefs'
 * SharedPreferences (unbounded image bytes don't belong there), so the TINY
 * layout's cover art survives a transient outage the same way the rest of
 * the cached snapshot does, and persists across process death between
 * refreshes.
 */
object WidgetArtCache {
    private fun file(context: Context, appWidgetId: Int) = File(context.cacheDir, "widget_art_$appWidgetId.img")

    /** null clears the cache - used when the box reports no art for the
     *  current track, as distinct from "fetch failed", which the caller
     *  simply skips calling this for, leaving the last-good art in place. */
    fun save(context: Context, appWidgetId: Int, bytes: ByteArray?) {
        val f = file(context, appWidgetId)
        if (bytes == null) {
            f.delete()
            return
        }
        f.writeBytes(bytes)
    }

    fun exists(context: Context, appWidgetId: Int): Boolean = file(context, appWidgetId).exists()

    fun load(context: Context, appWidgetId: Int): Bitmap? {
        val f = file(context, appWidgetId)
        if (!f.exists()) return null
        return BitmapFactory.decodeFile(f.path)
    }

    fun delete(context: Context, appWidgetId: Int) {
        file(context, appWidgetId).delete()
    }
}
