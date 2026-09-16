package com.omdrc.widget

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.omdrc.widget.data.WidgetSnapshot
import kotlin.math.roundToInt

/**
 * A one-shot, dismissable status snapshot - deliberately NOT an ongoing/
 * foreground-style notification (setOngoing(false), the default), so it can
 * be swiped away immediately. Posted only on explicit user interaction
 * (opening the app, tapping the widget's refresh icon), never from the
 * background alarm/periodic refresh, so it never reappears as passive
 * noise. Collapsed view is a one-line summary; expanding it (swipe/tap)
 * reveals the full detail via BigTextStyle.
 */
object StatusNotifier {
    private const val CHANNEL_ID = "drc_status"
    const val LIVE_CHANNEL_ID = "drc_live_status"
    private const val NOTIFICATION_ID = 1

    fun show(context: Context, host: String, port: Int, snapshot: WidgetSnapshot) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        ensureChannels(context)

        val (title, collapsed) = collapsedText(context, snapshot)
        val expanded = expandedText(snapshot)

        val contentIntent = PendingIntent.getActivity(
            context, 0,
            Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
                putExtra(MainActivity.EXTRA_HOST, host)
                putExtra(MainActivity.EXTRA_PORT, port)
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val levelsIntent = PendingIntent.getActivity(
            context, 2,
            Intent(context, LevelsActivity::class.java).apply {
                putExtra(MainActivity.EXTRA_HOST, host)
                putExtra(MainActivity.EXTRA_PORT, port)
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(collapsed)
            .setStyle(NotificationCompat.BigTextStyle().bigText(expanded))
            .setContentIntent(contentIntent)
            .addAction(R.drawable.ic_notification, context.getString(R.string.live_levels), levelsIntent)
            .setAutoCancel(true)
            .setOngoing(false)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()

        NotificationManagerCompat.from(context).notify(NOTIFICATION_ID, notification)
    }

    /** Two channels so the always-on live-updates notification (from
     *  LiveStatusService) can be muted/configured independently of this
     *  one-shot dismissable one - different enough purposes (a permanent
     *  ongoing status vs. an occasional glance) that they shouldn't share
     *  settings. */
    fun ensureChannels(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                context.getString(R.string.notification_channel_name),
                NotificationManager.IMPORTANCE_LOW,
            ).apply { description = context.getString(R.string.notification_channel_description) },
        )
        manager.createNotificationChannel(
            NotificationChannel(
                LIVE_CHANNEL_ID,
                context.getString(R.string.notification_live_channel_name),
                NotificationManager.IMPORTANCE_LOW,
            ).apply { description = context.getString(R.string.notification_live_channel_description) },
        )
    }

    internal fun collapsedText(context: Context, snapshot: WidgetSnapshot): Pair<String, String> {
        if (!snapshot.reachable) {
            return context.getString(R.string.status_unreachable) to ""
        }
        val drc = snapshot.drc
        if (drc == null || !drc.running) {
            return context.getString(R.string.status_drc_off) to (drc?.error ?: "")
        }
        val label = listOfNotNull(drc.geometry, drc.designId).joinToString(" · ")
        val title = context.getString(R.string.status_drc_on) + if (label.isNotEmpty()) " · $label" else ""
        val safe = when (drc.headroomSafe) {
            true -> "safe"
            false -> "UNSAFE"
            null -> ""
        }
        return title to safe
    }

    internal fun expandedText(snapshot: WidgetSnapshot): String {
        val lines = mutableListOf<String>()
        val drc = snapshot.drc
        when {
            !snapshot.reachable -> lines += "Box unreachable"
            drc == null || !drc.running -> {
                lines += "DRC is off"
                drc?.error?.let { lines += it }
            }
            else -> {
                drc.geometry?.let { lines += "Geometry: $it" }
                drc.designId?.let { lines += "Design: $it" }
                drc.headroomSafe?.let { lines += "Headroom: " + if (it) "safe" else "UNSAFE" }
                snapshot.rti?.takeIf { it.available }?.rti?.let {
                    lines += "RTI: ${(it * 100).roundToInt()}%"
                }
                snapshot.peak?.takeIf { it.available }?.let { peak ->
                    val peakText = peak.peakDb?.let { "%.1f dB".format(it) } ?: "−∞ dB"
                    lines += "Peak: $peakText" + if (peak.clipped) " (clipped)" else ""
                }
            }
        }
        snapshot.renderer?.label?.let { lines += "Renderer: $it" }
        ChainFormat.bitrateChain(snapshot.mpd, drc?.running == true)?.let { lines += "Chain: $it" }
        snapshot.mpd?.let { mpd ->
            val song = TrackTitle.titleOnly(snapshot.renderer?.nowPlaying ?: mpd.displaySong)
            when (mpd.state) {
                "playing" -> lines += "Playing" + (song?.let { ": $it" } ?: "")
                "paused" -> lines += "Paused" + (song?.let { ": $it" } ?: "")
                "stopped" -> lines += "MPD stopped"
            }
        }
        return lines.joinToString("\n")
    }
}
