package com.omdrc.widget

import android.app.Activity
import android.appwidget.AppWidgetManager
import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import com.omdrc.widget.work.WidgetRefreshWorker

class WidgetConfigureActivity : Activity() {

    private var appWidgetId = AppWidgetManager.INVALID_APPWIDGET_ID

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Standard widget-config boilerplate: if the user backs out without
        // saving, the host must see RESULT_CANCELED or it won't remove the
        // half-added widget.
        setResult(RESULT_CANCELED)
        setContentView(R.layout.activity_configure)

        appWidgetId = intent?.extras?.getInt(
            AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID,
        ) ?: AppWidgetManager.INVALID_APPWIDGET_ID

        if (appWidgetId == AppWidgetManager.INVALID_APPWIDGET_ID) {
            finish()
            return
        }

        val hostInput = findViewById<EditText>(R.id.host_input)
        val portInput = findViewById<EditText>(R.id.port_input)

        val existing = WidgetPrefs.load(this, appWidgetId)
        hostInput.setText(existing?.first ?: AppPrefs.defaultHost(this) ?: "")
        portInput.setText((existing?.second ?: AppPrefs.defaultPort(this)).toString())

        findViewById<Button>(R.id.save_button).setOnClickListener {
            val host = hostInput.text.toString().trim()
            val port = portInput.text.toString().trim().toIntOrNull()
            if (host.isEmpty() || port == null || port !in 1..65535) {
                Toast.makeText(this, "Enter a valid host and port", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            save(host, port)
        }
    }

    private fun save(host: String, port: Int) {
        WidgetPrefs.save(this, appWidgetId, host, port)
        AppPrefs.setDefault(this, host, port)

        // Paint from cache immediately (there is none yet, so this shows
        // the "loading" state) and kick the real fetch right away so the
        // widget has live data within a couple seconds of being added,
        // rather than waiting for the first alarm tick.
        RefreshEngine.renderCached(this, appWidgetId)
        WidgetRefreshWorker.enqueueOneTime(this, appWidgetId)

        val resultValue = Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId)
        setResult(RESULT_OK, resultValue)
        finish()
    }
}
