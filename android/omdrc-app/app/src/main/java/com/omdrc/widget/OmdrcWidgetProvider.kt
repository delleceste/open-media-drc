package com.omdrc.widget

import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.Log
import com.omdrc.widget.work.WidgetRefreshWorker

private const val TAG = "OmdrcWidget"

/**
 * Single receiver for everything widget-related: the standard
 * AppWidgetProvider lifecycle (dispatched by the superclass's own
 * onReceive into onUpdate/onDeleted/onEnabled/onDisabled), plus two custom
 * explicit-intent actions (manual refresh tap, alarm tick) and
 * BOOT_COMPLETED, all handled directly in the onReceive override below.
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
                }
            }
            ACTION_ALARM_TICK -> {
                WidgetRefreshWorker.enqueueOneTime(context, null)
                if (RefreshEngine.activeWidgetIds(context).isNotEmpty()) {
                    AlarmScheduler.scheduleNext(context)
                }
            }
            Intent.ACTION_BOOT_COMPLETED -> {
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
        const val ACTION_MANUAL_REFRESH = "com.omdrc.widget.ACTION_MANUAL_REFRESH"
        const val ACTION_ALARM_TICK = "com.omdrc.widget.ACTION_ALARM_TICK"
    }
}
