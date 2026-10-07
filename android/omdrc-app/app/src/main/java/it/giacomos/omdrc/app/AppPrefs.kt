package it.giacomos.omdrc.app

import android.content.Context

/**
 * App-wide default host/port, used to pre-fill new widget configurations and
 * as the destination when the app is launched directly from the launcher
 * icon (not via a specific widget tap, which instead carries its own
 * per-widget host/port as intent extras).
 */
object AppPrefs {
    private const val PREFS_NAME = "omdrc_app_prefs"
    private const val KEY_HOST = "default_host"
    private const val KEY_PORT = "default_port"
    private const val KEY_LAST_FOREGROUND = "last_foreground_millis"
    private const val KEY_VIEW = "view_mode"
    private const val KEY_KEEP_ON = "keep_screen_on"
    private const val KEY_PORTRAIT = "last_portrait"
    private const val KEY_INSTANT = "instant_updates"
    private const val KEY_CLOSED = "closed"
    const val DEFAULT_PORT = 9090

    /** The small-screen kiosk UI served by omdrcctrl at /k/ (the default). */
    const val VIEW_KIOSK = "kiosk"
    /** The full desktop web page at /. */
    const val VIEW_WEB = "web"

    fun defaultHost(context: Context): String? =
        prefs(context).getString(KEY_HOST, null)

    fun defaultPort(context: Context): Int =
        prefs(context).getInt(KEY_PORT, DEFAULT_PORT)

    fun setDefault(context: Context, host: String, port: Int) {
        prefs(context).edit()
            .putString(KEY_HOST, host)
            .putInt(KEY_PORT, port)
            .apply()
    }

    /** Last selected or successfully reached server on this network. */
    fun networkServer(context: Context, network: String): Pair<String, Int>? {
        val host = prefs(context).getString("network_host:$network", null) ?: return null
        return host to prefs(context).getInt("network_port:$network", DEFAULT_PORT)
    }

    fun setNetworkServer(context: Context, network: String, host: String, port: Int) {
        prefs(context).edit().putString("network_host:$network", host)
            .putInt("network_port:$network", port).apply()
    }

    fun viewMode(context: Context): String =
        if (prefs(context).getString(KEY_VIEW, VIEW_KIOSK) == VIEW_WEB) VIEW_WEB else VIEW_KIOSK

    /** Keep the display on while the kiosk's "Now playing" page is showing. */
    fun keepScreenOn(context: Context): Boolean =
        prefs(context).getBoolean(KEY_KEEP_ON, true)

    fun setKeepScreenOn(context: Context, on: Boolean) {
        prefs(context).edit().putBoolean(KEY_KEEP_ON, on).apply()
    }

    /** The orientation the user selected for every kiosk page: the app
     *  starts in it next time, instead of landscape and then turning. */
    fun lastPortrait(context: Context): Boolean =
        prefs(context).getBoolean(KEY_PORTRAIT, false)

    fun setLastPortrait(context: Context, portrait: Boolean) {
        prefs(context).edit().putBoolean(KEY_PORTRAIT, portrait).apply()
    }

    /** Instant updates: LiveStatusService stays on (its notification with
     *  it) and waits on the box's /now for changes, instead of polling
     *  only while the dashboard was used in the last minutes. */
    fun instantUpdates(context: Context): Boolean =
        prefs(context).getBoolean(KEY_INSTANT, false)

    fun setInstantUpdates(context: Context, on: Boolean) {
        prefs(context).edit().putBoolean(KEY_INSTANT, on).apply()
    }

    /** Set by the notification's Close: no background traffic at all - no
     *  live service, no widget refresh timers - until the app is opened
     *  again. A widget's own taps still work. */
    fun closed(context: Context): Boolean =
        prefs(context).getBoolean(KEY_CLOSED, false)

    fun setClosed(context: Context, closed: Boolean) {
        prefs(context).edit().putBoolean(KEY_CLOSED, closed).apply()
    }

    fun setViewMode(context: Context, mode: String) {
        prefs(context).edit().putString(KEY_VIEW, if (mode == VIEW_WEB) VIEW_WEB else VIEW_KIOSK).apply()
    }

    /** The page to open for [host]:[port] in the chosen view. */
    fun dashboardUrl(context: Context, host: String, port: Int): String =
        "http://$host:$port" + if (viewMode(context) == VIEW_WEB) "/" else "/k/"

    /** Marks "the dashboard was just visible" - read by LiveStatusService
     *  to auto-stop itself after a period with the app not reopened, rather
     *  than polling forever once you've walked away. */
    fun touchForeground(context: Context) {
        prefs(context).edit().putLong(KEY_LAST_FOREGROUND, System.currentTimeMillis()).apply()
    }

    fun lastForegroundMillis(context: Context): Long =
        prefs(context).getLong(KEY_LAST_FOREGROUND, 0L)

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
