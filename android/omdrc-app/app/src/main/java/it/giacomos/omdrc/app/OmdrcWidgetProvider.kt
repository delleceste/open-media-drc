package it.giacomos.omdrc.app

import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.Log
import it.giacomos.omdrc.app.work.WidgetRefreshWorker

private const val TAG = "OmdrcWidget"

/**
 * Single receiver for everything widget-related: the standard
 * AppWidgetProvider lifecycle (dispatched by the superclass's own
 * onReceive into onUpdate/onDeleted/onEnabled/onDisabled), plus custom
 * explicit-intent actions (manual refresh tap, play/pause tap, alarm tick)
 * and BOOT_COMPLETED, all handled directly in the onReceive override below.
 */
class OmdrcWidgetProvider : AppWidgetProvider() {

    override fun onReceive(context: Context, intent: Intent) {
        Log.d(TAG, "onReceive: action=${intent.action}")
        when (intent.action) {
            ACTION_MANUAL_REFRESH -> {
                val id = intent.getIntExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID)
                Log.d(TAG, "manual refresh requested for widget $id")
                if (id != AppWidgetManager.INVALID_APPWIDGET_ID) {
                    RefreshEngine.showChecking(context, id)
                    WidgetRefreshWorker.enqueueOneTime(context, id, notify = true)
                    resumeInstantUpdates(context, id)
                }
            }
            ACTION_TOGGLE_PLAY_PAUSE -> {
                val id = intent.getIntExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID)
                Log.d(TAG, "play/pause toggle requested for widget $id")
                if (id != AppWidgetManager.INVALID_APPWIDGET_ID) {
                    WidgetRefreshWorker.enqueueTogglePlayPause(context, id)
                }
            }
            ACTION_TRANSPORT -> {
                val id = intent.getIntExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID)
                val action = intent.getStringExtra(EXTRA_TRANSPORT_ACTION)
                if (id != AppWidgetManager.INVALID_APPWIDGET_ID && action != null) {
                    WidgetRefreshWorker.enqueueTransport(context, id, action)
                }
            }
            ACTION_ALARM_TICK -> {
                // Closed from the notification: the timer stops here, and
                // opening the app starts it again.
                if (AppPrefs.closed(context)) return super.onReceive(context, intent)
                WidgetRefreshWorker.enqueueOneTime(context, null)
                if (RefreshEngine.activeWidgetIds(context).isNotEmpty()) {
                    AlarmScheduler.scheduleNext(context)
                }
            }
            Intent.ACTION_BOOT_COMPLETED -> {
                if (AppPrefs.closed(context)) return super.onReceive(context, intent)
                // AlarmManager alarms don't survive reboot (unlike
                // WorkManager's own periodic jobs, which reschedule
                // themselves automatically) - re-arm here.
                if (RefreshEngine.activeWidgetIds(context).isNotEmpty()) {
                    AlarmScheduler.scheduleNext(context)
                }
            }
        }
        super.onReceive(context, intent)
    }

    /** With instant updates on, a refresh tap also brings back the status
     *  notification, which stops itself when nothing plays. A tap on the
     *  widget is one of the few moments Android lets a background app start
     *  a foreground service; should it refuse, the app's next opening will. */
    private fun resumeInstantUpdates(context: Context, id: Int) {
        if (!AppPrefs.instantUpdates(context) || AppPrefs.closed(context)) return
        val (host, port) = RefreshEngine.serverFor(context, id) ?: return
        try {
            LiveStatusService.ensureRunning(context, host, port)
        } catch (e: IllegalStateException) {
            Log.w(TAG, "status notification not resumed: ${e.message}")
        }
    }

    override fun onUpdate(context: Context, appWidgetManager: AppWidgetManager, appWidgetIds: IntArray) {
        for (id in appWidgetIds) {
            // Paint immediately from cache (instant, no network), then kick
            // a real fetch so the widget doesn't sit blank/stale while the
            // first request is in flight (e.g. right after being added, or
            // after a device reboot).
            RefreshEngine.renderCached(context, id)
            WidgetRefreshWorker.enqueueOneTime(context, id)
        }
    }

    override fun onAppWidgetOptionsChanged(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetId: Int,
        newOptions: Bundle,
    ) {
        // A resize never needs a fresh network fetch - just re-render the
        // already-cached snapshot at the new size.
        RefreshEngine.renderCached(context, appWidgetId)
    }

    override fun onDeleted(context: Context, appWidgetIds: IntArray) {
        for (id in appWidgetIds) {
            WidgetPrefs.delete(context, id)
            WidgetArtCache.delete(context, id)
        }
    }

    override fun onEnabled(context: Context) {
        AlarmScheduler.scheduleNext(context)
        WidgetRefreshWorker.enqueuePeriodic(context)
    }

    override fun onDisabled(context: Context) {
        AlarmScheduler.cancel(context)
        WidgetRefreshWorker.cancelPeriodic(context)
    }

    companion object {
        const val ACTION_MANUAL_REFRESH = "it.giacomos.omdrc.app.ACTION_MANUAL_REFRESH"
        const val ACTION_ALARM_TICK = "it.giacomos.omdrc.app.ACTION_ALARM_TICK"
        const val ACTION_TRANSPORT = "it.giacomos.omdrc.app.ACTION_TRANSPORT"
        const val EXTRA_TRANSPORT_ACTION = "transportAction"
        const val ACTION_TOGGLE_PLAY_PAUSE = "it.giacomos.omdrc.app.ACTION_TOGGLE_PLAY_PAUSE"
    }
}
