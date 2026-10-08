package it.giacomos.omdrc.app

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Rect
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import it.giacomos.omdrc.app.data.ArtFetch
import it.giacomos.omdrc.app.data.NowFetch
import it.giacomos.omdrc.app.data.NowState
import it.giacomos.omdrc.app.data.OmdrcClient
import it.giacomos.omdrc.app.data.WidgetSnapshot
import it.giacomos.omdrc.app.work.WidgetRefreshWorker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.util.Locale

private const val TAG = "OmdrcWidget"

/**
 * A foreground service that keeps the widgets and its own notification up to
 * date with the box, in one of two modes:
 *
 *  - Live updates (default): started when the dashboard is opened, it polls
 *    every 2s - the web dashboard's own cadence, so the large widget's meters
 *    move - and stops itself after IDLE_TIMEOUT_MILLIS with the dashboard not
 *    reopened, so it never polls forever by accident.
 *  - Instant updates (AppPrefs.instantUpdates): it stays on and keeps one
 *    request waiting on the box's /now, which answers as soon as the track,
 *    the playback state, the DRC or the film changes - an idle connection,
 *    not polling. A box without /now is polled every few seconds instead.
 *
 * Either way the notification shows what's playing - the cover or the
 * film's poster, the track - and expanded, the format and the DRC in use;
 * the status bar shows the app's note, the launcher icon's "d", and the
 * notification carries the same note on its picture (top right in the
 * shade, where Android now puts the app's icon on the left instead).
 * Android requires a foreground service to show it the whole time; its Close
 * action stops the service and every other background request of the app
 * until the app is opened again (see AppPrefs.closed).
 *
 * In both modes the service goes away by itself once there is nothing to
 * show - nothing playing for STOPPED_GRACE_MS, paused for PAUSE_TIMEOUT_MS,
 * the box unreachable for UNREACHABLE_GRACE_MS - but never while the app is
 * on screen. Nothing is left running then: it comes back only when the app
 * is opened, or a widget's refresh icon is tapped with instant updates on,
 * since Android does not let an app start it from the background.
 */
class LiveStatusService : Service() {

    private var job: Job? = null
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    // What the notification and the widgets were last given.
    private var musicArtKey: String? = null
    private var musicArt: Bitmap? = null
    private var videoArtKey: String? = null
    private var videoArt: Bitmap? = null

    // What the box was last seen doing, and since when: the idle stop.
    private var activity = Activity.UNKNOWN
    private var activitySince = 0L
    private var unreachableSince: Long? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_CLOSE) {
            Log.d(TAG, "LiveStatusService: close requested")
            closeApp(this)
            stopSelf()
            return START_NOT_STICKY
        }

        val host = intent?.getStringExtra(MainActivity.EXTRA_HOST) ?: AppPrefs.defaultHost(this)
        val port = intent?.getIntExtra(MainActivity.EXTRA_PORT, AppPrefs.defaultPort(this))
            ?: AppPrefs.defaultPort(this)
        if (host == null || AppPrefs.closed(this)) {
            stopSelf()
            return START_NOT_STICKY
        }

        StatusNotifier.ensureChannels(this)
        startForegroundCompat(buildNotification(host, port, null, null))

        job?.cancel()
        startedFor = host to port
        musicArtKey = null
        videoArtKey = null
        activity = Activity.UNKNOWN
        unreachableSince = null
        job = scope.launch {
            if (AppPrefs.instantUpdates(this@LiveStatusService)) instantLoop(host, port) else pollLoop(host, port)
        }
        return START_STICKY
    }

    /** The box on the network the phone is on now, else the one started
     *  with: instant updates outlive a move between home and office. */
    private fun server(host: String, port: Int): Pair<String, Int> =
        ServerNetwork.currentServer(this) ?: (host to port)

    private suspend fun pollLoop(host: String, port: Int) {
        var nowSupported = true
        while (true) {
            val idleMillis = System.currentTimeMillis() - AppPrefs.lastForegroundMillis(this)
            if (idleMillis > IDLE_TIMEOUT_MILLIS) {
                Log.d(TAG, "LiveStatusService: stopping after ${idleMillis / 60000} min with the app not reopened")
                stopSelf()
                return
            }
            try {
                val snapshot = OmdrcClient.fetchSnapshot(host, port)
                val now = if (nowSupported && snapshot.reachable) {
                    when (val fetched = OmdrcClient.fetchNow(host, port, null, 0)) {
                        is NowFetch.Found -> fetched.now
                        NowFetch.Unsupported -> { nowSupported = false; null }
                        NowFetch.Failed -> null
                    }
                } else {
                    null
                }
                show(host, port, snapshot, now)
            } catch (e: Exception) {
                Log.w(TAG, "LiveStatusService poll failed: ${e.message}")
                observe(false, null, null)
            }
            if (stopIfIdle()) return
            delay(POLL_INTERVAL_MS)
        }
    }

    private suspend fun instantLoop(startHost: String, startPort: Int) {
        var token: String? = null
        var server = server(startHost, startPort)
        while (true) {
            val current = server(startHost, startPort)
            if (current != server) {
                server = current
                token = null
            }
            val (host, port) = server
            try {
                when (val fetched = OmdrcClient.fetchNow(host, port, token, INSTANT_WAIT_S)) {
                    is NowFetch.Found -> {
                        observe(true, fetched.now, null)
                        if (fetched.now.token != token) {
                            token = fetched.now.token
                            Log.d(TAG, "LiveStatusService: state changed on $host:$port")
                            show(host, port, OmdrcClient.fetchSnapshot(host, port), fetched.now)
                        }
                    }
                    NowFetch.Unsupported -> {
                        // An older panel: poll it, more gently than live updates.
                        show(host, port, OmdrcClient.fetchSnapshot(host, port), null)
                        if (stopIfIdle()) return
                        delay(FALLBACK_POLL_MS)
                    }
                    NowFetch.Failed -> {
                        token = null
                        show(host, port, OmdrcClient.fetchSnapshot(host, port), null)
                        if (stopIfIdle()) return
                        delay(RETRY_MS)
                    }
                }
            } catch (e: Exception) {
                Log.w(TAG, "LiveStatusService instant update failed: ${e.message}")
                observe(false, null, null)
                if (stopIfIdle()) return
                delay(RETRY_MS)
            }
            if (stopIfIdle()) return
        }
    }

    /** Notes whether the box answered and what it is doing. A box without
     *  /now is judged by MPD's state alone; one that says neither never
     *  counts as idle. */
    private fun observe(reachable: Boolean, now: NowState?, snapshot: WidgetSnapshot?) {
        val t = SystemClock.elapsedRealtime()
        if (!reachable) {
            if (unreachableSince == null) unreachableSince = t
            return
        }
        unreachableSince = null
        val state = when {
            now?.video != null -> if (now.video.paused) "pause" else "play"
            now != null -> now.music.state
            snapshot?.mpd?.ok == true -> snapshot.mpd.state
            else -> null
        }
        val seen = when (state) {
            "play" -> Activity.PLAYING
            "pause" -> Activity.PAUSED
            null -> Activity.UNKNOWN
            else -> Activity.IDLE
        }
        if (seen != activity) {
            activity = seen
            activitySince = t
        }
    }

    /** Stops the service, and with it the notification, once there has been
     *  nothing to show for long enough; true if it did. */
    private fun stopIfIdle(): Boolean {
        if (appVisible) return false
        val t = SystemClock.elapsedRealtime()
        val reason = when {
            unreachableSince?.let { t - it >= UNREACHABLE_GRACE_MS } == true -> "box unreachable"
            activity == Activity.IDLE && t - activitySince >= STOPPED_GRACE_MS -> "nothing playing"
            activity == Activity.PAUSED && t - activitySince >= PAUSE_TIMEOUT_MS -> "paused too long"
            else -> return false
        }
        Log.d(TAG, "LiveStatusService: stopping, $reason")
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
        return true
    }

    /** Fetches whatever art changed, then updates widgets and notification. */
    private suspend fun show(host: String, port: Int, snapshot: WidgetSnapshot, now: NowState?) {
        var widgetArt: ArtFetch? = null
        if (now != null && now.music.art != musicArtKey) {
            val art = if (now.music.art.isEmpty()) ArtFetch.None
            else OmdrcClient.fetchImage(host, port, now.music.art)
            if (art !is ArtFetch.Failed) {
                musicArtKey = now.music.art
                musicArt = (art as? ArtFetch.Found)?.let { decodeIcon(it.bytes) }?.let { withNote(it) }
                widgetArt = art
            }
        }
        val videoKey = now?.video?.art.orEmpty()
        if (now?.video != null && videoKey != videoArtKey) {
            val art = if (videoKey.isEmpty()) ArtFetch.None else OmdrcClient.fetchImage(host, port, videoKey)
            if (art !is ArtFetch.Failed) {
                videoArtKey = videoKey
                videoArt = (art as? ArtFetch.Found)?.let { decodeIcon(it.bytes) }?.let { withNote(it) }
            }
        }
        observe(snapshot.reachable || now != null, now, snapshot)
        pushToWidgets(host, port, snapshot, widgetArt)
        startForegroundCompat(buildNotification(host, port, snapshot, now))
    }

    /** Only updates widgets showing this exact host/port - a different
     *  widget pointed at a different box must not be overwritten with this
     *  service's data. */
    private fun pushToWidgets(host: String, port: Int, snapshot: WidgetSnapshot, art: ArtFetch?) {
        for (id in RefreshEngine.activeWidgetIds(this)) {
            val hostPort = RefreshEngine.serverFor(this, id) ?: continue
            if (hostPort.first != host || hostPort.second != port) continue
            WidgetPrefs.saveSnapshot(this, id, snapshot)
            art?.let { RefreshEngine.saveArt(this, id, it) }
            RefreshEngine.updateViews(this, id, host, port)
        }
    }

    private fun buildNotification(host: String, port: Int, snapshot: WidgetSnapshot?, now: NowState?): Notification {
        val title: String
        val collapsed: String
        val expanded: String
        var icon: Bitmap? = null
        when {
            now?.video != null -> {
                val video = now.video
                title = video.title + if (video.year.isNotEmpty()) " (${video.year})" else ""
                collapsed = listOf(
                    getString(if (video.paused) R.string.now_film_paused else R.string.now_film_playing),
                    video.director,
                ).filter { it.isNotEmpty() }.joinToString(" · ")
                expanded = listOf(
                    collapsed,
                    listOf(video.runtime, video.genre).filter { it.isNotEmpty() }.joinToString(" · "),
                    if (video.rating.isNotEmpty()) "IMDb ${video.rating}" else "",
                    drcLine(now.drc),
                ).filter { it.isNotEmpty() }.joinToString("\n")
                icon = videoArt
            }
            now != null -> {
                val music = now.music
                val playing = music.state == "play" || music.state == "pause"
                title = music.title.ifEmpty { music.line1 }.ifEmpty { getString(R.string.now_nothing_playing) }
                val artistAlbum = listOf(music.artist, music.album).filter { it.isNotEmpty() }.joinToString(" — ")
                collapsed = listOf(stateLabel(music.state), artistAlbum).filter { it.isNotEmpty() }.joinToString(" · ")
                expanded = listOf(
                    collapsed,
                    music.edition,
                    listOf(if (playing) formatLine(music.rate, music.bits) else "", music.renderer)
                        .filter { it.isNotEmpty() }.joinToString(" · "),
                    drcLine(now.drc),
                ).filter { it.isNotEmpty() }.joinToString("\n")
                icon = if (music.title.isNotEmpty() || music.line1.isNotEmpty()) musicArt else null
            }
            snapshot != null -> {
                val (t, c) = StatusNotifier.collapsedText(this, snapshot)
                title = t
                collapsed = c
                expanded = StatusNotifier.expandedText(snapshot)
            }
            else -> {
                title = getString(R.string.live_updates_connecting)
                collapsed = "…"
                expanded = collapsed
            }
        }

        val contentIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
                putExtra(MainActivity.EXTRA_HOST, host)
                putExtra(MainActivity.EXTRA_PORT, port)
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val closeIntent = PendingIntent.getService(
            this, 0,
            Intent(this, LiveStatusService::class.java).setAction(ACTION_CLOSE),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val levelsIntent = PendingIntent.getActivity(
            this, 2,
            Intent(this, LevelsActivity::class.java).apply {
                putExtra(MainActivity.EXTRA_HOST, host)
                putExtra(MainActivity.EXTRA_PORT, port)
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        return NotificationCompat.Builder(this, StatusNotifier.LIVE_CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(collapsed)
            .setLargeIcon(icon ?: noteAlone)
            .setStyle(NotificationCompat.BigTextStyle().bigText(expanded))
            .setContentIntent(contentIntent)
            .addAction(R.drawable.ic_notification, getString(R.string.live_levels), levelsIntent)
            .addAction(R.drawable.ic_notification, getString(R.string.live_close_app), closeIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .build()
    }

    private fun stateLabel(state: String): String = getString(
        when (state) {
            "play" -> R.string.now_playing
            "pause" -> R.string.now_paused
            else -> R.string.now_stopped
        },
    )

    /** "DRC: multipos · FDW 6 cycles · −8.0 dB · headroom safe", or "DRC off". */
    private fun drcLine(drc: NowState.Drc): String {
        if (!drc.running) return getString(R.string.now_drc_off)
        val parts = listOfNotNull(
            drc.geometry,
            drc.description,
            drc.attenuationDb?.let { String.format(Locale.ROOT, "−%.1f dB", it) },
            when (drc.headroomSafe) {
                true -> getString(R.string.now_headroom_safe)
                false -> getString(R.string.now_headroom_unsafe)
                null -> null
            },
        ).filter { it.isNotEmpty() }
        return getString(R.string.now_drc_on, parts.joinToString(" · "))
    }

    /** With no picture, the note alone takes its place. */
    private val noteAlone: Bitmap by lazy { withNote(null) }

    /** The picture for the notification's large icon: the cover cut square,
     *  with the status bar's note - white, on a dark disc - in its corner, so
     *  the shade's entry shows which app that status-bar icon belongs to.
     *  Without a cover, the note's disc fills it. */
    private fun withNote(art: Bitmap?): Bitmap {
        val out = Bitmap.createBitmap(ICON_PX, ICON_PX, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(out)
        if (art != null) {
            val side = minOf(art.width, art.height)
            canvas.drawBitmap(
                art,
                Rect((art.width - side) / 2, (art.height - side) / 2, (art.width + side) / 2, (art.height + side) / 2),
                Rect(0, 0, ICON_PX, ICON_PX),
                Paint(Paint.FILTER_BITMAP_FLAG),
            )
        }
        val disc = if (art != null) ICON_PX * NOTE_BADGE else ICON_PX.toFloat()
        val cx = ICON_PX - disc / 2
        val cy = if (art != null) disc / 2 else ICON_PX / 2f
        canvas.drawCircle(cx, cy, disc / 2, Paint(Paint.ANTI_ALIAS_FLAG).apply { color = NOTE_DISC })
        val note = ContextCompat.getDrawable(this, R.drawable.ic_notification)!!.mutate()
        note.setTint(Color.WHITE)
        val half = (disc * 0.36f).toInt()
        note.setBounds((cx - half).toInt(), (cy - half).toInt(), (cx + half).toInt(), (cy + half).toInt())
        note.draw(canvas)
        return out
    }

    /** A cover or poster scaled for the notification's large icon. */
    private fun decodeIcon(bytes: ByteArray): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        var sample = 1
        while (minOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= ICON_PX) sample *= 2
        return BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
    }

    private fun startForegroundCompat(notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            @Suppress("DEPRECATION")
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    /** Android 15 allows a dataSync service six hours a day: past them it
     *  must stop, and comes back when the app is next opened. */
    override fun onTimeout(startId: Int, fgsType: Int) {
        Log.d(TAG, "LiveStatusService: Android's daily time limit reached, stopping")
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        super.onDestroy()
        startedFor = null
        job?.cancel()
        scope.cancel()
    }

    private enum class Activity { PLAYING, PAUSED, IDLE, UNKNOWN }

    companion object {
        private const val NOTIFICATION_ID = 3
        private const val ACTION_CLOSE = "it.giacomos.omdrc.app.ACTION_CLOSE"
        const val ACTION_CLOSE_APP = "it.giacomos.omdrc.app.ACTION_CLOSE_APP"
        private const val POLL_INTERVAL_MS = 2000L
        private const val IDLE_TIMEOUT_MILLIS = 10 * 60 * 1000L
        private const val INSTANT_WAIT_S = 25
        private const val FALLBACK_POLL_MS = 5000L
        private const val RETRY_MS = 15000L
        private const val ICON_PX = 256
        private const val NOTE_BADGE = 0.42f            // of the picture's side
        private const val NOTE_DISC = 0xD9101418.toInt()
        private const val STOPPED_GRACE_MS = 30 * 1000L
        private const val PAUSE_TIMEOUT_MS = 15 * 60 * 1000L
        private const val UNREACHABLE_GRACE_MS = 60 * 1000L

        /** The app is on screen: the service never stops itself meanwhile,
         *  so the notification doesn't vanish under the user's eyes. */
        @Volatile
        var appVisible = false

        /** The box the service was last started for, until it stops. */
        @Volatile
        private var startedFor: Pair<String, Int>? = null

        /** Starts the service unless it already runs for this box. */
        fun ensureRunning(context: Context, host: String, port: Int) {
            if (startedFor == host to port) return
            start(context, host, port)
        }

        fun start(context: Context, host: String, port: Int) {
            val intent = Intent(context, LiveStatusService::class.java).apply {
                putExtra(MainActivity.EXTRA_HOST, host)
                putExtra(MainActivity.EXTRA_PORT, port)
            }
            ContextCompat.startForegroundService(context, intent)
            startedFor = host to port
        }

        /** "96 kHz / 24 bit", "44.1 kHz / 16 bit". */
        fun formatLine(rate: Int?, bits: Int?): String {
            if (rate == null) return ""
            val khz = if (rate % 1000 == 0) "${rate / 1000}" else String.format(Locale.ROOT, "%.1f", rate / 1000.0)
            return "$khz kHz" + if (bits != null) " / $bits bit" else ""
        }

        /** The notification's Close: the dashboard (and its page's
         *  requests) goes, the widgets' timers stop, and nothing reaches the
         *  network again until the app is opened. */
        fun closeApp(context: Context) {
            AppPrefs.setClosed(context, true)
            AlarmScheduler.cancel(context)
            WidgetRefreshWorker.cancelPeriodic(context)
            context.sendBroadcast(Intent(ACTION_CLOSE_APP).setPackage(context.packageName))
        }

        /** Opening the app undoes closeApp. */
        fun reopenApp(context: Context) {
            if (!AppPrefs.closed(context)) return
            AppPrefs.setClosed(context, false)
            if (RefreshEngine.activeWidgetIds(context).isNotEmpty()) {
                AlarmScheduler.scheduleNext(context)
                WidgetRefreshWorker.enqueuePeriodic(context)
            }
        }
    }
}
