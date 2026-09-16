package com.omdrc.widget

import android.annotation.SuppressLint
import android.app.AlertDialog
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ProgressBar
import androidx.activity.ComponentActivity
import androidx.activity.addCallback
import androidx.activity.result.contract.ActivityResultContracts
import com.omdrc.widget.work.WidgetRefreshWorker

/**
 * Opens the dashboard in an in-app WebView, single task/instance reused on
 * every launch - the whole point of this activity is to fix the "Add to
 * Home screen" shortcut's annoyance of piling up a new Chrome tab (with the
 * address bar visible) on every tap, which plain HTTP can't avoid via
 * Chrome's own install path since that requires HTTPS.
 */
class MainActivity : ComponentActivity() {

    private lateinit var webView: WebView
    private lateinit var progressBar: ProgressBar
    private var filePathCallback: ValueCallback<Array<Uri>>? = null

    private val fileChooserLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        val values = WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data)
        filePathCallback?.onReceiveValue(values)
        filePathCallback = null
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.web_view)
        progressBar = findViewById(R.id.progress_bar)

        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true

        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                progressBar.visibility = View.GONE
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                progressBar.visibility = if (newProgress in 1..99) View.VISIBLE else View.GONE
                progressBar.progress = newProgress
            }

            override fun onShowFileChooser(
                view: WebView?,
                callback: ValueCallback<Array<Uri>>?,
                params: FileChooserParams?,
            ): Boolean {
                filePathCallback?.onReceiveValue(null)
                filePathCallback = callback
                val intent = params?.createIntent() ?: return false
                return try {
                    fileChooserLauncher.launch(intent)
                    true
                } catch (e: Exception) {
                    filePathCallback = null
                    false
                }
            }
        }

        onBackPressedDispatcher.addCallback(this) {
            if (webView.canGoBack()) webView.goBack() else finish()
        }

        loadDashboard()
    }

    override fun onStop() {
        super.onStop()
        // Leaving the dashboard is exactly when you might just have changed
        // something (enabled DRC, switched geometry, ...) and exactly when
        // you're about to glance at the widget next - refresh every
        // configured widget instance right away rather than waiting for the
        // next alarm tick.
        WidgetRefreshWorker.enqueueOneTime(this, null)
    }

    private fun loadDashboard() {
        val host = intent.getStringExtra(EXTRA_HOST) ?: AppPrefs.defaultHost(this)
        val port = intent.getIntExtra(EXTRA_PORT, AppPrefs.defaultPort(this))
        if (host == null) {
            promptForHost { newHost, newPort ->
                AppPrefs.setDefault(this, newHost, newPort)
                webView.loadUrl("http://$newHost:$newPort/")
            }
            return
        }
        webView.loadUrl("http://$host:$port/")
    }

    private fun promptForHost(onSaved: (String, Int) -> Unit) {
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 24, 48, 0)
        }
        val hostInput = EditText(this).apply { hint = getString(R.string.hint_host) }
        val portInput = EditText(this).apply {
            hint = getString(R.string.hint_port)
            setText(AppPrefs.DEFAULT_PORT.toString())
        }
        container.addView(hostInput)
        container.addView(portInput)

        AlertDialog.Builder(this)
            .setTitle(R.string.configure_widget_title)
            .setView(container)
            .setCancelable(false)
            .setPositiveButton(R.string.save) { _, _ ->
                val host = hostInput.text.toString().trim()
                val port = portInput.text.toString().trim().toIntOrNull() ?: AppPrefs.DEFAULT_PORT
                if (host.isNotEmpty()) onSaved(host, port) else finish()
            }
            .show()
    }

    companion object {
        const val EXTRA_HOST = "com.omdrc.widget.EXTRA_HOST"
        const val EXTRA_PORT = "com.omdrc.widget.EXTRA_PORT"
    }
}
