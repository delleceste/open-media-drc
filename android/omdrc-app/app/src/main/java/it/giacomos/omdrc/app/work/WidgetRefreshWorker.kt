package it.giacomos.omdrc.app.work

import android.appwidget.AppWidgetManager
import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import android.util.Log
import it.giacomos.omdrc.app.AppPrefs
import it.giacomos.omdrc.app.RefreshEngine
import it.giacomos.omdrc.app.StatusNotifier
import it.giacomos.omdrc.app.WidgetPrefs
import java.util.concurrent.TimeUnit

private const val TAG = "OmdrcWidget"

/**
 * The one place actual fetch-and-render work happens. Used both as the
 * WorkManager periodic fallback (15 min, the system's own enforced floor)
 * and as a one-shot job enqueued by the AlarmManager tick / manual-refresh
 * tap / initial onUpdate - so there is exactly one code path for "go fetch
 * status and update a widget", regardless of what triggered it.
 */
class WidgetRefreshWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val targetId = inputData.getInt(KEY_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID)
        val notify = inputData.getBoolean(KEY_NOTIFY, false)
        val togglePlayPause = inputData.getBoolean(KEY_TOGGLE_PLAY_PAUSE, false)
        Log.d(TAG, "doWork start, targetId=$targetId, notify=$notify, togglePlayPause=$togglePlayPause")
        val transport = inputData.getString(KEY_TRANSPORT)
        if (transport != null && targetId != AppWidgetManager.INVALID_APPWIDGET_ID) {
            RefreshEngine.transport(applicationContext, targetId, transport)
            return Result.success()
        }
        if (togglePlayPause && targetId != AppWidgetManager.INVALID_APPWIDGET_ID) {
            RefreshEngine.togglePlayPause(applicationContext, targetId)
            return Result.success()
        }
        if (targetId != AppWidgetManager.INVALID_APPWIDGET_ID) {
            RefreshEngine.refreshWidget(applicationContext, targetId)
            // Only ever posted for a manual, user-triggered refresh of one
            // specific widget - never for the background alarm/periodic
            // path or a bulk "refresh all", so it never becomes passive
            // noise (see StatusNotifier).
            if (notify) {
                val hostPort = WidgetPrefs.load(applicationContext, targetId)
                val snapshot = WidgetPrefs.loadSnapshot(applicationContext, targetId)
                if (hostPort != null && snapshot != null) {
                    StatusNotifier.show(applicationContext, hostPort.first, hostPort.second, snapshot)
                }
            }
        } else if (AppPrefs.closed(applicationContext)) {
            // Closed from the notification: no background refresh until
            // the app is opened again; a widget's own taps still go.
            Log.d(TAG, "doWork: app closed, skipping background refresh")
        } else {
            RefreshEngine.refreshAll(applicationContext)
        }
        Log.d(TAG, "doWork done, targetId=$targetId")
        return Result.success()
    }

    companion object {
        private const val KEY_APPWIDGET_ID = "appWidgetId"
        private const val KEY_NOTIFY = "notify"
        private const val KEY_TOGGLE_PLAY_PAUSE = "togglePlayPause"
        private const val KEY_TRANSPORT = "transport"
        private const val PERIODIC_WORK_NAME = "omdrc_periodic_refresh"
        private val NETWORK_CONSTRAINTS = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()

        /** appWidgetId == null means "refresh every configured widget instance".
         *  notify only applies when a specific appWidgetId is given. */
        fun enqueueOneTime(context: Context, appWidgetId: Int?, notify: Boolean = false) {
            val data = workDataOf(
                KEY_APPWIDGET_ID to (appWidgetId ?: AppWidgetManager.INVALID_APPWIDGET_ID),
                KEY_NOTIFY to notify,
            )
            val request = OneTimeWorkRequestBuilder<WidgetRefreshWorker>()
                .setInputData(data)
                .setConstraints(NETWORK_CONSTRAINTS)
                // User-triggered (tap, alarm tick, activity stop) - ask
                // WorkManager to run it with priority rather than queued
                // behind its normal scheduling, falling back to ordinary
                // work if the expedited quota is exhausted.
                .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
                .build()
            val uniqueName = "omdrc_oneoff_${appWidgetId ?: "all"}"
            Log.d(TAG, "enqueueOneTime: $uniqueName notify=$notify")
            WorkManager.getInstance(context).enqueueUniqueWork(uniqueName, ExistingWorkPolicy.REPLACE, request)
        }

        /** TINY widget's play/pause icon tap. */
        fun enqueueTogglePlayPause(context: Context, appWidgetId: Int) {
            val data = workDataOf(
                KEY_APPWIDGET_ID to appWidgetId,
                KEY_TOGGLE_PLAY_PAUSE to true,
            )
            val request = OneTimeWorkRequestBuilder<WidgetRefreshWorker>()
                .setInputData(data)
                .setConstraints(NETWORK_CONSTRAINTS)
                .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
                .build()
            val uniqueName = "omdrc_toggle_$appWidgetId"
            Log.d(TAG, "enqueueTogglePlayPause: $uniqueName")
            WorkManager.getInstance(context).enqueueUniqueWork(uniqueName, ExistingWorkPolicy.REPLACE, request)
        }

        /** The media widget's previous/next buttons. */
        fun enqueueTransport(context: Context, appWidgetId: Int, action: String) {
            val request = OneTimeWorkRequestBuilder<WidgetRefreshWorker>()
                .setInputData(workDataOf(KEY_APPWIDGET_ID to appWidgetId, KEY_TRANSPORT to action))
                .setConstraints(NETWORK_CONSTRAINTS)
                .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
                .build()
            WorkManager.getInstance(context)
                .enqueueUniqueWork("omdrc_transport_$appWidgetId", ExistingWorkPolicy.REPLACE, request)
        }

        fun enqueuePeriodic(context: Context) {
            val request = PeriodicWorkRequestBuilder<WidgetRefreshWorker>(15, TimeUnit.MINUTES)
                .setConstraints(NETWORK_CONSTRAINTS)
                .build()
            WorkManager.getInstance(context)
                .enqueueUniquePeriodicWork(PERIODIC_WORK_NAME, ExistingPeriodicWorkPolicy.KEEP, request)
        }

        fun cancelPeriodic(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(PERIODIC_WORK_NAME)
        }
    }
}
