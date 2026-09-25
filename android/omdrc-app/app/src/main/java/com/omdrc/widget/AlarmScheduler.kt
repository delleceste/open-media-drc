package com.omdrc.widget

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import java.util.concurrent.TimeUnit

/**
 * Drives the "glance" refresh cadence (~90s) via a self-rescheduling
 * inexact alarm - deliberately the *plain* set(), not
 * setAndAllowWhileIdle()/setExactAndAllowWhileIdle(). Those "AllowWhileIdle"
 * variants exist specifically to bypass Doze, and in exchange the platform
 * hard-throttles them to roughly once per 9 minutes per app - a fixed OS
 * floor, not something tunable from here. Plain set() has no such floor: it
 * fires close to on-schedule whenever the device isn't deep in Doze (i.e.
 * whenever a glance at the widget is actually likely), needs no API 31+
 * SCHEDULE_EXACT_ALARM permission, and simply gets deferred while the phone
 * is idle with the screen off for a while - which is fine, since
 * WidgetRefreshWorker's 15-min periodic job is the resilience fallback for
 * that case, on top of the refresh triggered when MainActivity stops (see
 * MainActivity.onStop) and the manual-refresh tap. Alarms don't survive
 * reboot at all, hence re-arming from OmdrcWidgetProvider's own
 * BOOT_COMPLETED handling.
 */
object AlarmScheduler {
    private const val REQUEST_CODE = 1001
    val INTERVAL_MILLIS = TimeUnit.SECONDS.toMillis(90)

    fun scheduleNext(context: Context) {
        val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val triggerAt = System.currentTimeMillis() + INTERVAL_MILLIS
        alarmManager.set(AlarmManager.RTC_WAKEUP, triggerAt, tickIntent(context))
    }

    fun cancel(context: Context) {
        val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        alarmManager.cancel(tickIntent(context))
    }

    private fun tickIntent(context: Context): PendingIntent {
        val intent = Intent(context, OmdrcWidgetProvider::class.java).apply {
            action = OmdrcWidgetProvider.ACTION_ALARM_TICK
        }
        return PendingIntent.getBroadcast(
            context, REQUEST_CODE, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }
}
