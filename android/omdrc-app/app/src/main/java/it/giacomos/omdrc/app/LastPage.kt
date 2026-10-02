package it.giacomos.omdrc.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.view.View
import androidx.core.view.drawToBitmap
import java.io.File

/**
 * The kiosk's last screen, kept as a picture so a cold start (the system had
 * dropped the app, or it was swiped away) shows the page at once instead of
 * the splash: the live page takes its place as soon as it is painted.
 *
 * One picture per orientation, since the app starts in the orientation last
 * asked for; each remembers the dashboard URL it was taken of, so another
 * server's page is never shown.  In the cache directory: losing it only means
 * the splash again.
 */
object LastPage {
    private const val PREFS = "last_page"

    private fun file(context: Context, portrait: Boolean) =
        File(context.cacheDir, if (portrait) "last-page-portrait.jpg" else "last-page-landscape.jpg")

    private fun key(portrait: Boolean) = if (portrait) "url_portrait" else "url_landscape"

    /** Take the picture now (UI thread), write it in the background. */
    fun save(context: Context, view: View, url: String, portrait: Boolean) {
        if (view.width <= 0 || view.height <= 0) return
        val bitmap = try { view.drawToBitmap() } catch (e: Exception) { return }
        val app = context.applicationContext
        Thread {
            try {
                val target = file(app, portrait)
                val tmp = File(target.path + ".tmp")
                tmp.outputStream().use { bitmap.compress(Bitmap.CompressFormat.JPEG, 85, it) }
                if (tmp.renameTo(target)) {
                    app.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                        .putString(key(portrait), url).apply()
                }
            } catch (_: Exception) {
            } finally {
                bitmap.recycle()
            }
        }.start()
    }

    /** The picture of [url] in that orientation, or null. */
    fun load(context: Context, url: String, portrait: Boolean): Bitmap? {
        val saved = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(key(portrait), null)
        if (saved != url) return null
        val f = file(context, portrait)
        if (!f.isFile) return null
        return try { BitmapFactory.decodeFile(f.path) } catch (_: Exception) { null }
    }
}
