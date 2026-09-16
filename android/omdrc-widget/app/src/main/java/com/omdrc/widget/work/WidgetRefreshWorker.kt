package com.omdrc.widget.work

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
import com.omdrc.widget.RefreshEngine
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
        Log.d(TAG, "doWork start, targetId=$targetId")
        if (targetId != AppWidgetManager.INVALID_APPWIDGET_ID) {
            RefreshEngine.refreshWidget(applicationContext, targetId)
        } else {
            RefreshEngine.refreshAll(applicationContext)
        }
        Log.d(TAG, "doWork done, targetId=$targetId")
        return Result.success()
    }

    companion object {
        private const val KEY_APPWIDGET_ID = "appWidgetId"
        private const val PERIODIC_WORK_NAME = "omdrc_periodic_refresh"
        private val NETWORK_CONSTRAINTS = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()

        /** appWidgetId == null means "refresh every configured widget instance". */
        fun enqueueOneTime(context: Context, appWidgetId: Int?) {
            val data = workDataOf(KEY_APPWIDGET_ID to (appWidgetId ?: AppWidgetManager.INVALID_APPWIDGET_ID))
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
            Log.d(TAG, "enqueueOneTime: $uniqueName")
            WorkManager.getInstance(context).enqueueUniqueWork(uniqueName, ExistingWorkPolicy.REPLACE, request)
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
