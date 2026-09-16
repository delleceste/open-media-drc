package com.omdrc.widget

import android.Manifest
import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
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
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
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

    // Result ignored either way: LiveStatusService starts regardless (a
    // foreground service doesn't need the permission to run, only to show
    // its notification), so a denial here just means that notification
    // stays hidden until the user grants it some other way.
    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // targetSdk 35 draws edge-to-edge by default, so without this the
        // page's own top strip (its header bar, including the mascot) ends
        // up underneath the status bar. Pad the root by the system bar
        // insets instead of opting back out of edge-to-edge (Android 15+
        // no longer lets an app targeting API 35 do that).
        val root = findViewById<View>(R.id.root_container)
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }

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

        findViewById<View>(R.id.settings_button).setOnClickListener {
            promptForHost { newHost, newPort ->
                AppPrefs.setDefault(this, newHost, newPort)
                webView.loadUrl("http://$newHost:$newPort/")
                startLiveUpdates(newHost, newPort)
            }
        }

        loadDashboard()
    }

    override fun onStart() {
        super.onStart()
        // Resets LiveStatusService's idle clock every time the dashboard
        // becomes visible again, not just on first open.
        AppPrefs.touchForeground(this)
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
                startLiveUpdates(newHost, newPort)
            }
            return
        }
        webView.loadUrl("http://$host:$port/")
        startLiveUpdates(host, port)
    }

    /** Explicit, visible trade (permanent notification + continuous polling
     *  for a genuinely live widget) made every time the dashboard is opened
     *  - see LiveStatusService. It keeps running after you leave the app
     *  until its notification's Stop action is tapped; opening the app
     *  again just re-points it at the current host/port if that's changed.
     *  Superseded the old one-shot dismissable notify-on-open (that
     *  notification is now only posted from the widget's manual-refresh
     *  tap, where no live service is necessarily running yet). */
    private fun startLiveUpdates(host: String, port: Int) {
        // Must happen before the service starts, synchronously - the
        // service reads this on its very first poll tick to decide whether
        // it's been idle too long, and onStart() alone would run too late
        // (after onCreate has already called this).
        AppPrefs.touchForeground(this)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        LiveStatusService.start(this, host, port)
    }

    /** Doubles as first-run setup (no host configured yet - not cancelable,
     *  backing out finishes the activity since there's nothing to show) and
     *  as the "change server address" flow from the settings button
     *  (pre-filled with the current value, cancelable, leaving everything
     *  unchanged if dismissed). */
    private fun promptForHost(onSaved: (String, Int) -> Unit) {
        val alreadyConfigured = AppPrefs.defaultHost(this) != null
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 24, 48, 0)
        }
        val hostInput = EditText(this).apply {
            hint = getString(R.string.hint_host)
            setText(AppPrefs.defaultHost(this@MainActivity) ?: "")
        }
        val portInput = EditText(this).apply {
            hint = getString(R.string.hint_port)
            setText(AppPrefs.defaultPort(this@MainActivity).toString())
        }
        container.addView(hostInput)
        container.addView(portInput)

        val builder = AlertDialog.Builder(this)
            .setTitle(R.string.configure_widget_title)
            .setView(container)
            .setCancelable(alreadyConfigured)
            .setPositiveButton(R.string.save) { _, _ ->
                val host = hostInput.text.toString().trim()
                val port = portInput.text.toString().trim().toIntOrNull() ?: AppPrefs.DEFAULT_PORT
                when {
                    host.isNotEmpty() -> onSaved(host, port)
                    !alreadyConfigured -> finish()
                    // else: cleared the field while editing an existing
                    // config - drop the dialog, change nothing.
                }
            }
        if (alreadyConfigured) builder.setNegativeButton(android.R.string.cancel, null)
        builder.show()
    }

    companion object {
        const val EXTRA_HOST = "com.omdrc.widget.EXTRA_HOST"
        const val EXTRA_PORT = "com.omdrc.widget.EXTRA_PORT"
    }
}
