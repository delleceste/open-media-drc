package com.omdrc.widget

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
    const val DEFAULT_PORT = 9090

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

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
