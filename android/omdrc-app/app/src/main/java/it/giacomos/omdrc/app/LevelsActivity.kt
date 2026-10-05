package it.giacomos.omdrc.app

import android.app.PictureInPictureParams
import android.content.res.Configuration
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.util.Rational
import android.view.WindowManager
import androidx.activity.ComponentActivity
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.atomic.AtomicReference

/**
 * A tiny native client for omdrcctrl's VU-only SSE stream.  It deliberately
 * does not use the dashboard WebView: keeping this activity alive in PiP is
 * what gives Android a real, observable lifetime for the fast meter stream.
 */
class LevelsActivity : ComponentActivity() {
    private lateinit var meterView: LevelsView
    @Volatile private var running = false
    @Volatile private var connection: HttpURLConnection? = null
    private var streamThread: Thread? = null
    private var streamHost: String? = null
    private var streamPort: Int = AppPrefs.DEFAULT_PORT

    /** The newest levels waiting for the UI thread; at most one post is in flight. */
    private class Levels(val left: Double, val right: Double,
                         val leftPeak: Double, val rightPeak: Double, val due: Long)
    private val pending = AtomicReference<Levels?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.statusBarColor = Color.BLACK
        window.navigationBarColor = Color.BLACK
        meterView = LevelsView(this)
        setContentView(meterView)

        val host = intent.getStringExtra(MainActivity.EXTRA_HOST) ?: AppPrefs.defaultHost(this)
        val port = intent.getIntExtra(MainActivity.EXTRA_PORT, AppPrefs.defaultPort(this))
        if (host == null) {
            finish()
            return
        }
        streamHost = host
        streamPort = port

        // The notification tap is an explicit user gesture, so it may enter
        // PiP immediately. Posting waits until the activity has a window.
        meterView.post { enterMeterPictureInPicture() }
    }

    override fun onStart() {
        super.onStart()
        streamHost?.let { startStream(it, streamPort) }
    }

    override fun onStop() {
        stopStream()
        super.onStop()
    }

    private fun enterMeterPictureInPicture() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O || isInPictureInPictureMode) return
        val params = PictureInPictureParams.Builder()
            .setAspectRatio(Rational(16, 7))
            .build()
        enterPictureInPictureMode(params)
    }

    override fun onUserLeaveHint() {
        super.onUserLeaveHint()
        enterMeterPictureInPicture()
    }

    override fun onPictureInPictureModeChanged(
        isInPictureInPictureMode: Boolean,
        newConfig: Configuration,
    ) {
        super.onPictureInPictureModeChanged(isInPictureInPictureMode, newConfig)
        meterView.compact = isInPictureInPictureMode
    }

    private fun startStream(host: String, port: Int) {
        if (streamThread != null) return
        running = true
        val worker = Thread({
            while (running && Thread.currentThread() === streamThread) {
                try {
                    readStream(host, port)
                } catch (_: Exception) {
                    if (running && Thread.currentThread() === streamThread) {
                        meterView.post { meterView.setDisconnected() }
                    }
                } finally {
                    connection?.disconnect()
                    connection = null
                }
                if (running && Thread.currentThread() === streamThread) {
                    try {
                        Thread.sleep(1500)
                    } catch (_: InterruptedException) {
                        break
                    }
                }
            }
        }, "omdrc-levels-sse")
        streamThread = worker
        worker.apply {
            isDaemon = true
            start()
        }
    }

    private fun stopStream() {
        running = false
        streamThread = null
        connection?.disconnect()
        connection = null
    }

    private fun readStream(host: String, port: Int) {
        val http = URL("http://$host:$port/spectrum/stream?mode=vu")
            .openConnection() as HttpURLConnection
        connection = http
        http.connectTimeout = 4000
        http.readTimeout = 0
        http.requestMethod = "GET"
        http.setRequestProperty("Accept", "text/event-stream")
        http.connect()
        if (http.responseCode !in 200..299) error("HTTP ${http.responseCode}")

        // Late frames are dropped, never drawn late (the kiosk's policy, core.js):
        // each frame says when the box produced it (`published`) and sent it
        // (`sent`).  The lowest arrival - sent on this connection is the network at
        // its best plus the clocks' offset (relaxed by 100 ppm against drift), so
        // published + that floor is when the frame is due.  One past due by more
        // than STALE_MS - a network backlog, or a UI thread that fell behind - is
        // dropped here and again on the UI thread, and only the newest frame is
        // ever queued there, so nothing piles up.
        var best = Double.MAX_VALUE       // fractional, so the 100 ppm relaxation adds up
        var bestAt = 0L
        BufferedReader(InputStreamReader(http.inputStream)).use { reader ->
            while (running) {
                val line = reader.readLine() ?: break
                if (!line.startsWith("data:")) continue
                val frame = JSONObject(line.substring(5).trim())
                val vu = frame.optJSONObject("vu") ?: continue
                val now = System.currentTimeMillis()
                var due = now
                if (frame.has("sent")) {
                    val sent = frame.optLong("sent")
                    val relaxed = if (bestAt == 0L) Double.MAX_VALUE
                                  else best + (now - bestAt) * DRIFT
                    best = minOf((now - sent).toDouble(), relaxed)
                    bestAt = now
                    due = frame.optLong("published", sent) + best.toLong()
                }
                val live = frame.optBoolean("ok") && frame.optString("state") == "running"
                if (live && now > due + STALE_MS) continue
                val levels = Levels(
                    vu.optDouble("left_rms", LevelsView.FLOOR_DB),
                    vu.optDouble("right_rms", LevelsView.FLOOR_DB),
                    vu.optDouble("left_peak", LevelsView.FLOOR_DB),
                    vu.optDouble("right_peak", LevelsView.FLOOR_DB),
                    if (live) due else Long.MAX_VALUE - STALE_MS)
                if (pending.getAndSet(levels) == null) meterView.post { drawPending() }
            }
        }
    }

    private fun drawPending() {
        val levels = pending.getAndSet(null) ?: return
        if (System.currentTimeMillis() > levels.due + STALE_MS) return
        meterView.setLevels(levels.left, levels.right, levels.leftPeak, levels.rightPeak)
    }

    override fun onDestroy() {
        stopStream()
        meterView.stop()
        super.onDestroy()
    }

    private companion object {
        const val STALE_MS = 100L
        const val DRIFT = 1e-4
    }
}
