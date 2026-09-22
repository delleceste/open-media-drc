package com.omdrc.widget

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.omdrc.widget.data.OmdrcClient
import com.omdrc.widget.data.WidgetSnapshot
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private const val TAG = "OmdrcWidget"

/**
 * Optional, explicitly user-started: a foreground service that polls the
 * box continuously (matching the web dashboard's own ~2s cadence) and
 * pushes live updates to every widget configured for the same host/port.
 * Android requires a foreground service to show a persistent, non-
 * dismissable notification the entire time it's alive; the "Stop" action on
 * that notification ends it early, same pattern as a music player's
 * playback notification (or the Qobuz app's own service). It also stops
 * itself automatically after IDLE_TIMEOUT_MILLIS with the dashboard not
 * reopened (see AppPrefs.touchForeground/lastForegroundMillis), so leaving
 * it running by accident doesn't poll forever. Starting it is a deliberate
 * trade of "live" for "a permanent notification and continuous network
 * polling," made once when the dashboard is opened, not something hidden
 * from the user.
 */
class LiveStatusService : Service() {

    private var job: Job? = null
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            Log.d(TAG, "LiveStatusService: stop requested")
            stopSelf()
            return START_NOT_STICKY
        }

        val host = intent?.getStringExtra(MainActivity.EXTRA_HOST) ?: AppPrefs.defaultHost(this)
        val port = intent?.getIntExtra(MainActivity.EXTRA_PORT, AppPrefs.defaultPort(this))
            ?: AppPrefs.defaultPort(this)
        if (host == null) {
            stopSelf()
            return START_NOT_STICKY
        }

        StatusNotifier.ensureChannels(this)
        startForegroundCompat(buildNotification(host, port, null))

        job?.cancel()
        job = scope.launch { pollLoop(host, port) }
        return START_STICKY
    }

    private suspend fun pollLoop(host: String, port: Int) {
        while (true) {
            val idleMillis = System.currentTimeMillis() - AppPrefs.lastForegroundMillis(this)
            if (idleMillis > IDLE_TIMEOUT_MILLIS) {
                Log.d(TAG, "LiveStatusService: stopping after ${idleMillis / 60000} min with the app not reopened")
                stopSelf()
                return
            }
            try {
                val snapshot = OmdrcClient.fetchSnapshot(host, port)
                pushToWidgets(host, port, snapshot)
                startForegroundCompat(buildNotification(host, port, snapshot))
            } catch (e: Exception) {
                Log.w(TAG, "LiveStatusService poll failed: ${e.message}")
            }
            delay(POLL_INTERVAL_MS)
        }
    }

    /** Only updates widgets configured for this exact host/port - a
     *  different widget pointed at a different box must not be overwritten
     *  with this service's data. */
    private fun pushToWidgets(host: String, port: Int, snapshot: WidgetSnapshot) {
        for (id in RefreshEngine.activeWidgetIds(this)) {
            val hostPort = WidgetPrefs.load(this, id) ?: continue
            if (hostPort.first != host || hostPort.second != port) continue
            WidgetPrefs.saveSnapshot(this, id, snapshot)
            RefreshEngine.updateViews(this, id, host, port)
        }
    }

    private fun buildNotification(host: String, port: Int, snapshot: WidgetSnapshot?): Notification {
        val (title, collapsed) = if (snapshot != null) {
            StatusNotifier.collapsedText(this, snapshot)
        } else {
            getString(R.string.live_updates_connecting) to "…"
        }
        val expanded = snapshot?.let { StatusNotifier.expandedText(it) } ?: collapsed

        val contentIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
                putExtra(MainActivity.EXTRA_HOST, host)
                putExtra(MainActivity.EXTRA_PORT, port)
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val stopIntent = PendingIntent.getService(
            this, 0,
            Intent(this, LiveStatusService::class.java).setAction(ACTION_STOP),
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
            .setStyle(NotificationCompat.BigTextStyle().bigText(expanded))
            .setContentIntent(contentIntent)
            .addAction(R.drawable.ic_notification, getString(R.string.live_levels), levelsIntent)
            .addAction(R.drawable.ic_notification, getString(R.string.live_updates_stop), stopIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun startForegroundCompat(notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            @Suppress("DEPRECATION")
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        job?.cancel()
        scope.cancel()
    }

    companion object {
        private const val NOTIFICATION_ID = 3
        private const val ACTION_STOP = "com.omdrc.widget.ACTION_STOP_LIVE"
        private const val POLL_INTERVAL_MS = 2000L
        private const val IDLE_TIMEOUT_MILLIS = 10 * 60 * 1000L

        fun start(context: Context, host: String, port: Int) {
            val intent = Intent(context, LiveStatusService::class.java).apply {
                putExtra(MainActivity.EXTRA_HOST, host)
                putExtra(MainActivity.EXTRA_PORT, port)
            }
            ContextCompat.startForegroundService(context, intent)
        }
    }
}
