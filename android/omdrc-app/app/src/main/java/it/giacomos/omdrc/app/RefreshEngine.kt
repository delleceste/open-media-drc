package it.giacomos.omdrc.app

import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import android.util.Log
import it.giacomos.omdrc.app.data.OmdrcClient

private const val TAG = "OmdrcWidget"

/** Shared fetch + persist + render logic, reused by the worker, the alarm
 *  receiver path and onUpdate's immediate cached render. */
object RefreshEngine {

    suspend fun refreshWidget(context: Context, appWidgetId: Int) {
        val hostPort = WidgetPrefs.load(context, appWidgetId)
        if (hostPort == null) {
            Log.w(TAG, "refreshWidget($appWidgetId): no host/port configured, skipping")
            return
        }
        Log.d(TAG, "refreshWidget($appWidgetId): fetching from ${hostPort.first}:${hostPort.second}")
        val snapshot = OmdrcClient.fetchSnapshot(hostPort.first, hostPort.second)
        Log.d(TAG, "refreshWidget($appWidgetId): reachable=${snapshot.reachable} running=${snapshot.drc?.running}")
        WidgetPrefs.saveSnapshot(context, appWidgetId, snapshot)
        // Only touched while actually reachable - same reasoning as
        // WidgetPrefs.saveSnapshot not overwriting the last-good status on a
        // transient outage. A null here (no art for the current track) does
        // still clear it, since that's a real "no art" answer, not a fetch
        // failure.
        if (snapshot.reachable) {
            val art = OmdrcClient.fetchArt(hostPort.first, hostPort.second)
            WidgetArtCache.save(context, appWidgetId, art)
        }
        updateViews(context, appWidgetId, hostPort.first, hostPort.second)
    }

    /** Play/pause toggle from the TINY widget's icon tap. There's no
     *  server-side "toggle" action, so the desired action is derived from
     *  the last cached MPD state; refreshWidget() right after is what
     *  reconciles the icon with whatever the box actually did (the POST
     *  could fail, or another client could race it). */
    suspend fun togglePlayPause(context: Context, appWidgetId: Int) {
        val hostPort = WidgetPrefs.load(context, appWidgetId)
        if (hostPort == null) {
            Log.w(TAG, "togglePlayPause($appWidgetId): no host/port configured, skipping")
            return
        }
        val cached = WidgetPrefs.loadSnapshot(context, appWidgetId)
        val action = if (cached?.mpd?.state == "playing") "pause" else "play"
        Log.d(TAG, "togglePlayPause($appWidgetId): sending $action")
        OmdrcClient.sendTransport(hostPort.first, hostPort.second, action)
        refreshWidget(context, appWidgetId)
    }

    suspend fun refreshAll(context: Context) {
        val ids = activeWidgetIds(context)
        Log.d(TAG, "refreshAll: ${ids.size} widget(s)")
        for (id in ids) {
            refreshWidget(context, id)
        }
    }

    /** Renders whatever is already cached, with no network call - used for
     *  an instant first paint while the real fetch is still in flight. */
    fun renderCached(context: Context, appWidgetId: Int) {
        val hostPort = WidgetPrefs.load(context, appWidgetId)
        updateViews(context, appWidgetId, hostPort?.first, hostPort?.second ?: AppPrefs.DEFAULT_PORT)
    }

    /** Immediate, network-free visual acknowledgement that a manual refresh
     *  tap registered, so a fetch that completes with an unchanged status
     *  (e.g. the box hasn't finished switching filter sets yet) doesn't look
     *  indistinguishable from the tap having done nothing at all. */
    fun showChecking(context: Context, appWidgetId: Int) {
        Log.d(TAG, "showChecking($appWidgetId)")
        val manager = AppWidgetManager.getInstance(context)
        val hostPort = WidgetPrefs.load(context, appWidgetId)
        val snapshot = WidgetPrefs.loadSnapshot(context, appWidgetId)
        val size = sizeFor(manager, appWidgetId)
        val views = RemoteViewsBuilder.build(
            context, appWidgetId, hostPort?.first, hostPort?.second ?: AppPrefs.DEFAULT_PORT,
            snapshot, size, checking = true,
        )
        manager.updateAppWidget(appWidgetId, views)
    }

    fun updateViews(context: Context, appWidgetId: Int, host: String?, port: Int) {
        val manager = AppWidgetManager.getInstance(context)
        val snapshot = WidgetPrefs.loadSnapshot(context, appWidgetId)
        val size = sizeFor(manager, appWidgetId)
        val views = RemoteViewsBuilder.build(context, appWidgetId, host, port, snapshot, size)
        manager.updateAppWidget(appWidgetId, views)
        Log.d(TAG, "updateViews($appWidgetId): pushed to AppWidgetManager")
    }

    fun activeWidgetIds(context: Context): IntArray {
        val manager = AppWidgetManager.getInstance(context)
        return manager.getAppWidgetIds(ComponentName(context, OmdrcWidgetProvider::class.java))
    }

    private fun sizeFor(manager: AppWidgetManager, appWidgetId: Int): WidgetSize {
        val options = manager.getAppWidgetOptions(appWidgetId)
        val minWidth = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 0)
        return when {
            minWidth >= 250 -> WidgetSize.LARGE
            // Below the 2-cell width (110dp) but still resized down from it -
            // the 1-cell cover-art tile, not the text-strip SMALL layout.
            minWidth < 70 -> WidgetSize.TINY
            else -> WidgetSize.SMALL
        }
    }
}
