package it.giacomos.omdrc.app

import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import android.util.Log
import it.giacomos.omdrc.app.data.ArtFetch
import it.giacomos.omdrc.app.data.OmdrcClient

private const val TAG = "OmdrcWidget"

// A wide widget at least this tall (about two launcher rows) is the media
// widget; shorter, the text-only LARGE one.
private const val MEDIA_MIN_HEIGHT_DP = 200

/** Shared fetch + persist + render logic, reused by the worker, the alarm
 *  receiver path and onUpdate's immediate cached render. */
object RefreshEngine {

    /** The box a widget shows: the one the dashboard last used on the
     *  current network, so the widget follows the phone between boxes the
     *  way the dashboard does, else the one set up for the widget. */
    fun serverFor(context: Context, appWidgetId: Int): Pair<String, Int>? =
        ServerNetwork.currentServer(context) ?: WidgetPrefs.load(context, appWidgetId)

    suspend fun refreshWidget(context: Context, appWidgetId: Int) {
        val hostPort = serverFor(context, appWidgetId)
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
        // transient outage, nor on a failed art fetch. Only the box saying
        // there's no art for the current track clears it.
        if (snapshot.reachable) {
            saveArt(context, appWidgetId, OmdrcClient.fetchArt(hostPort.first, hostPort.second))
        }
        updateViews(context, appWidgetId, hostPort.first, hostPort.second)
    }

    fun saveArt(context: Context, appWidgetId: Int, art: ArtFetch) {
        when (art) {
            is ArtFetch.Found -> WidgetArtCache.save(context, appWidgetId, art.bytes)
            ArtFetch.None -> WidgetArtCache.save(context, appWidgetId, null)
            ArtFetch.Failed -> Log.w(TAG, "saveArt($appWidgetId): art fetch failed, keeping cached art")
        }
    }

    /** Play/pause toggle from the TINY widget's icon tap. There's no
     *  server-side "toggle" action, so the desired action is derived from
     *  the last cached MPD state; refreshWidget() right after is what
     *  reconciles the icon with whatever the box actually did (the POST
     *  could fail, or another client could race it). */
    suspend fun togglePlayPause(context: Context, appWidgetId: Int) {
        val hostPort = serverFor(context, appWidgetId)
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

    /** prev/next from the media widget. */
    suspend fun transport(context: Context, appWidgetId: Int, action: String) {
        val hostPort = serverFor(context, appWidgetId) ?: return
        Log.d(TAG, "transport($appWidgetId): sending $action")
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
        val hostPort = serverFor(context, appWidgetId)
        updateViews(context, appWidgetId, hostPort?.first, hostPort?.second ?: AppPrefs.DEFAULT_PORT)
    }

    /** Immediate, network-free visual acknowledgement that a manual refresh
     *  tap registered, so a fetch that completes with an unchanged status
     *  (e.g. the box hasn't finished switching filter sets yet) doesn't look
     *  indistinguishable from the tap having done nothing at all. */
    fun showChecking(context: Context, appWidgetId: Int) {
        Log.d(TAG, "showChecking($appWidgetId)")
        val manager = AppWidgetManager.getInstance(context)
        val hostPort = serverFor(context, appWidgetId)
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

    fun sizeFor(manager: AppWidgetManager, appWidgetId: Int): WidgetSize {
        val options = manager.getAppWidgetOptions(appWidgetId)
        // Portrait size: the narrowest width and the tallest height.
        val width = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 0)
        val height = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT, 0)
        return when {
            // A single cell is narrower than tall on the launcher's grids
            // (79x127dp at 5 columns, 128x174dp at 3), a 2x1 strip wider -
            // a fixed dp threshold misfires as the grid's cells grow.
            width < 70 || width < height -> WidgetSize.TINY
            width >= 250 && height >= MEDIA_MIN_HEIGHT_DP -> WidgetSize.MEDIA
            width >= 250 -> WidgetSize.LARGE
            else -> WidgetSize.SMALL
        }
    }
}
