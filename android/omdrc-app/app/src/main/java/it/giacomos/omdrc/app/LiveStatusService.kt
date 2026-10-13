package it.giacomos.omdrc.app

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.support.v4.media.MediaMetadataCompat
import android.support.v4.media.session.MediaSessionCompat
import android.support.v4.media.session.PlaybackStateCompat
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
 *    move. After IDLE_TIMEOUT_MILLIS with the dashboard not reopened it
 *    stops polling and carries on as instant updates do, so it never polls
 *    forever by accident, yet the notification (and its Pause) stays while
 *    something plays.
 *  - Instant updates (AppPrefs.instantUpdates): it stays on and keeps one
 *    request waiting on the box's /now, which answers as soon as the track,
 *    the playback state, the DRC or the film changes - an idle connection,
 *    not polling. A box without /now is polled every few seconds instead.
 *
 * Either way the notification shows what's playing - the cover or the
 * film's poster, the track - and expanded, the format and the DRC in use;
 * the status bar shows the app's note, the launcher icon's "d", with a
 * small play, pause or stop glyph in its top-left corner (in the shade,
 * Android shows the launcher icon itself instead).
 * With the media notification on (the default, AppPrefs.mediaNotification)
 * it is the box's player in Android's media controls, backed by a
 * MediaSession: play/pause, previous/next and seeking for music, play/pause
 * for a film, and Close. Off, it is a plain notification offering Play or
 * Pause, Levels and Close. Swiping either away is the same as Close.
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
    // The art with its badges, redrawn only when what it shows changes.
    private var badgedKey: Any? = null
    private var badged: Bitmap? = null

    // What the box was last seen doing, and since when: the idle stop.
    private var activity = Activity.UNKNOWN
    private var activitySince = 0L
    private var unreachableSince: Long? = null

    /** The box's player as Android's media controls see it; null with the
     *  media notification off. Created and released on the main thread. */
    private var session: MediaSessionCompat? = null

    /** Where the session's buttons send their commands: the box and what it
     *  plays, as last shown. */
    private data class Target(val host: String, val port: Int, val video: Boolean, val music: Boolean)
    @Volatile
    private var target: Target? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_CLOSE) {
            Log.d(TAG, "LiveStatusService: close requested")
            close()
            return START_NOT_STICKY
        }
        if (intent?.action == ACTION_TRANSPORT) {
            transport(intent)
            return START_STICKY
        }

        val host = intent?.getStringExtra(MainActivity.EXTRA_HOST) ?: AppPrefs.defaultHost(this)
        val port = intent?.getIntExtra(MainActivity.EXTRA_PORT, AppPrefs.defaultPort(this))
            ?: AppPrefs.defaultPort(this)
        if (host == null || AppPrefs.closed(this)) {
            stopSelf()
            return START_NOT_STICKY
        }

        StatusNotifier.ensureChannels(this)
        if (AppPrefs.mediaNotification(this)) openSession() else releaseSession()
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
                Log.d(TAG, "LiveStatusService: ${idleMillis / 60000} min with the app not reopened, waiting on /now instead of polling")
                instantLoop(host, port)
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

    /** Close, or the notification swiped away: everything stops until the
     *  app is opened again. */
    private fun close() {
        closeApp(this)
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun openSession() {
        if (session != null) return
        session = MediaSessionCompat(this, "omdrc").apply {
            setSessionActivity(dashboardIntent(AppPrefs.defaultHost(this@LiveStatusService), AppPrefs.defaultPort(this@LiveStatusService)))
            setCallback(object : MediaSessionCompat.Callback() {
                override fun onPlay() = send("play")
                override fun onPause() = send("pause")
                override fun onSkipToNext() = send("next")
                override fun onSkipToPrevious() = send("prev")
                override fun onSeekTo(pos: Long) = send("seek", pos / 1000.0)
                override fun onCustomAction(action: String?, extras: android.os.Bundle?) {
                    when (action) {
                        CUSTOM_CLOSE -> close()
                        CUSTOM_STOP -> send("stop")
                    }
                }
            }, Handler(Looper.getMainLooper()))
        }
    }

    private fun releaseSession() {
        session?.release()
        session = null
    }

    /** A media-controls button: a film takes only play, pause and stop. */
    private fun send(action: String, seconds: Double? = null) {
        val to = target ?: return
        if (to.video && action !in VIDEO_ACTIONS) return
        if (!to.video && !to.music) return
        Log.d(TAG, "LiveStatusService: $action from the media controls")
        scope.launch {
            OmdrcClient.sendTransport(to.host, to.port, action, to.video, seconds)
            if (action == "stop") closeAfterStop()
        }
    }

    /** Stop from the notification: once the box has stopped, the app closes
     *  after STOP_CLOSE_MS rather than the idle stop's STOPPED_GRACE_MS -
     *  unless something plays again meanwhile. */
    private suspend fun closeAfterStop() {
        delay(STOP_CLOSE_MS)
        if (activity == Activity.PLAYING) return
        Log.d(TAG, "LiveStatusService: closing after a stop from the notification")
        Handler(Looper.getMainLooper()).post { close() }
    }

    /** A notification button (Play/Pause, previous, next): the box is told,
     *  and the update it causes repaints the notification - at once with
     *  instant updates, on the next poll with live updates. A film's goes
     *  to the box's video remote instead of MPD. */
    private fun transport(intent: Intent) {
        val host = intent.getStringExtra(MainActivity.EXTRA_HOST)
        val action = intent.getStringExtra(EXTRA_TRANSPORT_ACTION)
        val running = job?.isActive == true
        if (host == null || action == null) {
            if (!running) stopSelf()
            return
        }
        val port = intent.getIntExtra(MainActivity.EXTRA_PORT, AppPrefs.defaultPort(this))
        val video = intent.getBooleanExtra(EXTRA_TRANSPORT_VIDEO, false)
        Log.d(TAG, "LiveStatusService: $action${if (video) " (film)" else ""} requested from the notification")
        scope.launch {
            OmdrcClient.sendTransport(host, port, action, video)
            // Started by the tap alone (the service had been killed): no
            // loop to show the result, so nothing left to do.
            if (!running) stopSelf()
            else if (action == "stop") closeAfterStop()
        }
    }

    /** "pause" while music or a film plays, "play" while it is paused, else
     *  null. */
    private fun playPauseAction(snapshot: WidgetSnapshot?, now: NowState?): String? {
        val state = when {
            now?.video != null -> if (now.video.paused) "pause" else "play"
            now != null -> now.music.state
            else -> when (snapshot?.mpd?.state) {
                "playing" -> "play"
                "paused" -> "pause"
                else -> null
            }
        }
        return when (state) {
            "play" -> "pause"
            "pause" -> "play"
            else -> null
        }
    }

    /** Fetches whatever art changed, then updates widgets and notification. */
    private suspend fun show(host: String, port: Int, snapshot: WidgetSnapshot, now: NowState?) {
        var widgetArt: ArtFetch? = null
        if (now != null && now.music.art != musicArtKey) {
            val art = if (now.music.art.isEmpty()) ArtFetch.None
            else OmdrcClient.fetchImage(host, port, now.music.art)
            if (art !is ArtFetch.Failed) {
                musicArtKey = now.music.art
                musicArt = (art as? ArtFetch.Found)?.let { decodeIcon(it.bytes) }
                widgetArt = art
            }
        }
        val videoKey = now?.video?.art.orEmpty()
        if (now?.video != null && videoKey != videoArtKey) {
            val art = if (videoKey.isEmpty()) ArtFetch.None else OmdrcClient.fetchImage(host, port, videoKey)
            if (art !is ArtFetch.Failed) {
                videoArtKey = videoKey
                videoArt = (art as? ArtFetch.Found)?.let { decodeIcon(it.bytes) }
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
        var artist: String
        var album = ""
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
                artist = collapsed
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
                artist = music.artist.ifEmpty { collapsed }
                album = music.album
            }
            snapshot != null -> {
                val (t, c) = StatusNotifier.collapsedText(this, snapshot)
                title = t
                collapsed = c
                expanded = StatusNotifier.expandedText(snapshot)
                artist = collapsed
            }
            else -> {
                title = getString(R.string.live_updates_connecting)
                collapsed = "…"
                expanded = collapsed
                artist = ""
            }
        }

        val contentIntent = dashboardIntent(host, port)
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

        val playPause = playPauseAction(snapshot, now)
        val video = now?.video != null
        val music = !video && playPause != null
        target = Target(host, port, video, music)
        if (now != null && (icon != null || playPause != null)) {
            icon = badgedArt(icon, now, video, if (session != null) CoverBadges.Layout.MEDIA else CoverBadges.Layout.ICON)
        }

        val builder = NotificationCompat.Builder(this, StatusNotifier.LIVE_CHANNEL_ID)
            .setSmallIcon(
                when (activity) {
                    Activity.PLAYING -> R.drawable.ic_notification_play
                    Activity.PAUSED -> R.drawable.ic_notification_pause
                    Activity.IDLE -> R.drawable.ic_notification_stop
                    Activity.UNKNOWN -> R.drawable.ic_notification
                },
            )
            .setContentTitle(title)
            .setContentText(collapsed)
            .setLargeIcon(icon)
            .setContentIntent(contentIntent)
            // Swiping it away closes the app, as Close does.
            .setOngoing(false)
            .setDeleteIntent(closeIntent)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)

        val session = session
        if (session != null) {
            updateSession(session, title, artist, album, icon, now, playPause, music)
            val compact = mutableListOf<Int>()
            var count = 0
            if (music) {
                builder.addAction(R.drawable.ic_skip_previous, getString(R.string.live_previous),
                    transportIntent(host, port, "prev", false, 3))
                compact += count++
            }
            playPause?.let { action ->
                builder.addAction(
                    if (action == "pause") R.drawable.ic_pause else R.drawable.ic_play,
                    getString(if (action == "pause") R.string.live_pause else R.string.live_play),
                    transportIntent(host, port, action, video, 1),
                )
                compact += count++
            }
            if (music) {
                builder.addAction(R.drawable.ic_skip_next, getString(R.string.live_next),
                    transportIntent(host, port, "next", false, 4))
                compact += count++
            }
            if (playPause != null) {
                builder.addAction(R.drawable.ic_stop, getString(R.string.live_stop),
                    transportIntent(host, port, "stop", video, 5))
            }
            builder.addAction(R.drawable.ic_close, getString(R.string.live_close_app), closeIntent)
            return builder
                .setStyle(
                    androidx.media.app.NotificationCompat.MediaStyle()
                        .setMediaSession(session.sessionToken)
                        .setShowActionsInCompactView(*compact.toIntArray()),
                )
                .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
                .build()
        }

        builder.setStyle(NotificationCompat.BigTextStyle().bigText(expanded))
        playPause?.let { action ->
            val playPauseIntent = transportIntent(host, port, action, video, 1)
            if (action == "pause") {
                builder.addAction(R.drawable.ic_pause, getString(R.string.live_pause), playPauseIntent)
            } else {
                builder.addAction(R.drawable.ic_play, getString(R.string.live_play), playPauseIntent)
            }
            builder.addAction(R.drawable.ic_stop, getString(R.string.live_stop),
                transportIntent(host, port, "stop", video, 5))
        }
        return builder
            .addAction(R.drawable.ic_notification, getString(R.string.live_levels), levelsIntent)
            .addAction(R.drawable.ic_notification, getString(R.string.live_close_app), closeIntent)
            .build()
    }

    /** The cover with the DRC, the format and the stored DR on it. */
    private fun badgedArt(art: Bitmap?, now: NowState, video: Boolean, layout: CoverBadges.Layout): Bitmap? {
        val m = now.music
        val drcOn = now.drc.running
        val rate = if (video) null else m.rate
        val bits = if (video) null else m.bits
        val dr = if (video) null else CoverBadges.drLabel(m.drTrack, m.drTrackExact)
        val key = listOf(System.identityHashCode(art), layout, drcOn, now.drc.rate, rate, bits, dr)
        if (key != badgedKey) {
            badged = CoverBadges.draw(this, art, layout, now.drc.rate, drcOn, rate, bits, dr)
            badgedKey = key
        }
        return badged
    }

    /** What Android's media controls show and offer: the track or film, its
     *  cover, where the track is (Android moves the bar on by itself between
     *  updates), and the buttons that make sense for it. */
    private fun updateSession(
        session: MediaSessionCompat, title: String, artist: String, album: String, art: Bitmap?,
        now: NowState?, playPause: String?, music: Boolean,
    ) {
        val duration = now?.music?.duration?.takeIf { music && it > 0 }
        session.setMetadata(
            MediaMetadataCompat.Builder()
                .putString(MediaMetadataCompat.METADATA_KEY_TITLE, title)
                .putString(MediaMetadataCompat.METADATA_KEY_ARTIST, artist)
                .putString(MediaMetadataCompat.METADATA_KEY_ALBUM, album)
                .apply {
                    art?.let { putBitmap(MediaMetadataCompat.METADATA_KEY_ALBUM_ART, it) }
                    duration?.let { putLong(MediaMetadataCompat.METADATA_KEY_DURATION, (it * 1000).toLong()) }
                }
                .build(),
        )
        val state = when (playPause) {
            "pause" -> PlaybackStateCompat.STATE_PLAYING
            "play" -> PlaybackStateCompat.STATE_PAUSED
            else -> if (now == null) PlaybackStateCompat.STATE_NONE else PlaybackStateCompat.STATE_STOPPED
        }
        var actions = 0L
        if (playPause != null) {
            actions = actions or PlaybackStateCompat.ACTION_PLAY or PlaybackStateCompat.ACTION_PAUSE or
                PlaybackStateCompat.ACTION_PLAY_PAUSE
        }
        if (music) {
            actions = actions or PlaybackStateCompat.ACTION_SKIP_TO_NEXT or PlaybackStateCompat.ACTION_SKIP_TO_PREVIOUS
            if (duration != null) actions = actions or PlaybackStateCompat.ACTION_SEEK_TO
        }
        val position = now?.music?.elapsed?.takeIf { music }?.let { (it * 1000).toLong() }
            ?: PlaybackStateCompat.PLAYBACK_POSITION_UNKNOWN
        session.setPlaybackState(
            PlaybackStateCompat.Builder()
                .setActions(actions)
                .setState(state, position, if (state == PlaybackStateCompat.STATE_PLAYING) 1f else 0f,
                    SystemClock.elapsedRealtime())
                .apply {
                    if (playPause != null) {
                        addCustomAction(
                            PlaybackStateCompat.CustomAction.Builder(
                                CUSTOM_STOP, getString(R.string.live_stop), R.drawable.ic_stop,
                            ).build(),
                        )
                    }
                }
                .addCustomAction(
                    PlaybackStateCompat.CustomAction.Builder(
                        CUSTOM_CLOSE, getString(R.string.live_close_app), R.drawable.ic_close,
                    ).build(),
                )
                .build(),
        )
        session.isActive = true
    }

    private fun dashboardIntent(host: String?, port: Int): PendingIntent = PendingIntent.getActivity(
        this, 0,
        Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            host?.let { putExtra(MainActivity.EXTRA_HOST, it) }
            putExtra(MainActivity.EXTRA_PORT, port)
        },
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    private fun transportIntent(host: String, port: Int, action: String, video: Boolean, requestCode: Int) =
        PendingIntent.getService(
            this, requestCode,
            Intent(this, LiveStatusService::class.java).apply {
                setAction(ACTION_TRANSPORT)
                putExtra(MainActivity.EXTRA_HOST, host)
                putExtra(MainActivity.EXTRA_PORT, port)
                putExtra(EXTRA_TRANSPORT_ACTION, action)
                putExtra(EXTRA_TRANSPORT_VIDEO, video)
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

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
            val type = if (session != null) ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK
            else ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            startForeground(NOTIFICATION_ID, notification, type)
        } else {
            @Suppress("DEPRECATION")
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    /** Android 15 allows a dataSync service six hours a day (a mediaPlayback
     *  one, with the media notification on, has no limit): past them it
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
        releaseSession()
    }

    private enum class Activity { PLAYING, PAUSED, IDLE, UNKNOWN }

    companion object {
        private const val NOTIFICATION_ID = 3
        private const val ACTION_CLOSE = "it.giacomos.omdrc.app.ACTION_CLOSE"
        const val ACTION_CLOSE_APP = "it.giacomos.omdrc.app.ACTION_CLOSE_APP"
        private const val ACTION_TRANSPORT = "it.giacomos.omdrc.app.ACTION_LIVE_TRANSPORT"
        private const val CUSTOM_CLOSE = "close"
        private const val CUSTOM_STOP = "stop"
        private val VIDEO_ACTIONS = setOf("play", "pause", "stop")
        private const val STOP_CLOSE_MS = 5 * 1000L
        private const val EXTRA_TRANSPORT_ACTION = "transportAction"
        private const val EXTRA_TRANSPORT_VIDEO = "transportVideo"
        private const val POLL_INTERVAL_MS = 2000L
        private const val IDLE_TIMEOUT_MILLIS = 10 * 60 * 1000L
        private const val INSTANT_WAIT_S = 290   // the box holds a request up to 300 s
        private const val FALLBACK_POLL_MS = 5000L
        private const val RETRY_MS = 15000L
        private const val ICON_PX = 256
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
