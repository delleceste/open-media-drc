package com.omdrc.widget

import android.Manifest
import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.LinearLayout
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import androidx.activity.ComponentActivity
import androidx.activity.addCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsControllerCompat
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
    private lateinit var loadingRing: LoadingRingView
    private lateinit var swipeRefresh: SwipeRefreshLayout

    /** The kiosk scrolls inside its own page, not the WebView: it tells us (AppBridge)
     *  whether that page is scrolled down, so swipe-down scrolls it back up instead
     *  of reloading. */
    private var pageScrolled = false
    private var filePathCallback: ValueCallback<Array<Uri>>? = null

    /** What the page last asked for over [AppBridge]: true only while the
     *  kiosk's "Now playing" page is on screen. */
    private var pageWantsScreenOn = false

    /** The page in the WebView could not be loaded (box off, wrong address). */
    private var loadFailed = false
    private lateinit var connError: ConnectionErrorPanel

    /** The dashboard URL last asked for: what "Retry" loads again. */
    private var lastUrl: String? = null

    /** Set by [load], cleared by the onPageStarted it causes: while the error panel
     *  is up only a load the app asked for may take it down. */
    private var loadRequested = false
    private lateinit var settingsButton: View

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
    // Microphone for the meter-delay calibration: asked for only when it is started.
    private var pendingMic: Pair<Int, Int>? = null
    private val micPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        val p = pendingMic; pendingMic = null
        if (granted && p != null) recordMic(p.first, p.second)
        else sendMicResult("{\"ok\":false,\"error\":\"microphone permission denied\"}")
    }

    private fun sendMicResult(json: String) {
        runOnUiThread { webView.evaluateJavascript("window.K && K.onMicEnvelope && K.onMicEnvelope($json)", null) }
    }

    private fun recordMic(durationMs: Int, stepMs: Int) {
        Thread {
            val json = try {
                val r = MicEnvelope(durationMs, stepMs).record()
                val o = org.json.JSONObject()
                o.put("ok", true); o.put("t0", r.t0WallMs); o.put("step", r.stepMs); o.put("source", r.source)
                val a = org.json.JSONArray()
                for (v in r.db) a.put(Math.round(v * 10) / 10.0)
                o.put("db", a)
                o.toString()
            } catch (e: Exception) {
                org.json.JSONObject().put("ok", false).put("error", e.message ?: "microphone error").toString()
            }
            sendMicResult(json)
        }.start()
    }

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
            // System bars while they are showing, plus the camera cut-out either way
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout(),
            )
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }

        applySystemBars()

        webView = findViewById(R.id.web_view)
        loadingRing = findViewById(R.id.loading_ring)
        swipeRefresh = findViewById(R.id.swipe_refresh)
        swipeRefresh.setColorSchemeColors(0xFF58A6FF.toInt(), 0xFF3FB950.toInt(), 0xFFD8C23A.toInt())
        swipeRefresh.setProgressBackgroundColorSchemeColor(0xFF161B22.toInt())
        swipeRefresh.setOnChildScrollUpCallback { _, _ -> webView.scrollY > 0 || pageScrolled }
        swipeRefresh.setOnRefreshListener {
            swipeRefresh.isRefreshing = false        // the big ring shows the reload instead
            if (loadFailed) loadDashboard() else webView.reload()
        }

        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true

        webView.addJavascriptInterface(AppBridge(), "OmdrcApp")

        connError = ConnectionErrorPanel(
            findViewById(R.id.conn_error),
            AppPrefs.DEFAULT_PORT,
            onRetry = { lastUrl?.let { load(it) } ?: loadDashboard() },
            onConnect = { host, port -> useServer(host, port) },
        )
        // Edge-to-edge: the window does not shrink for the keyboard, so the error card
        // pads itself above it (the root is already padded by the system bars).
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.conn_error)) { view, insets ->
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime()).bottom
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars()).bottom
            view.setPadding(0, 0, 0, maxOf(0, ime - bars))
            connError.revealField()
            insets
        }

        webView.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView?, url: String?, favicon: android.graphics.Bitmap?) {
                // A new document knows nothing about the old one's request:
                // drop the screen-on flag until the kiosk page asks again.
                pageWantsScreenOn = false
                applyKeepScreenOn()
                pageScrolled = false
                val requested = loadRequested
                loadRequested = false
                if (connError.isShown) {
                    if (!requested) return
                    connError.connecting()      // the panel stays up until this load's outcome
                } else {
                    loadingRing.start()
                }
                loadFailed = false
                updateSettingsButton()
            }

            override fun onReceivedError(
                view: WebView?,
                request: android.webkit.WebResourceRequest?,
                error: android.webkit.WebResourceError?,
            ) {
                // Only the page itself: without this the gear stays hidden and a
                // wrong server address could not be corrected.
                if (request?.isForMainFrame == true) {
                    val description = error?.description?.toString()
                    showLoadError(ConnectionErrorPanel.reasonFor(error?.errorCode ?: 0, description), description)
                }
            }

            // The server answered, but with its own failure (panel restarting behind a
            // proxy, a crash): same panel rather than a bare server error page.
            override fun onReceivedHttpError(
                view: WebView?,
                request: android.webkit.WebResourceRequest?,
                errorResponse: android.webkit.WebResourceResponse?,
            ) {
                val code = errorResponse?.statusCode ?: return
                if (request?.isForMainFrame == true && code >= 500) {
                    showLoadError(ConnectionErrorPanel.Reason.HTTP, httpStatus = code)
                }
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                if (loadFailed) return
                loadingRing.finish()
                webView.visibility = View.VISIBLE
                connError.hide()
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                if (!loadFailed) loadingRing.setProgress(newProgress)
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

        settingsButton = findViewById(R.id.settings_button)
        settingsButton.setOnClickListener { showSettings() }
        updateSettingsButton()

        loadDashboard()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        // Dialogs and the transient swipe-in bars drop immersive mode: put it back.
        if (hasFocus) applySystemBars()
    }

    /** Fullscreen like a browser's fullscreen mode: hide the status and
     *  navigation bars; swiping from an edge shows them briefly. */
    private fun applySystemBars() {
        val controller = WindowCompat.getInsetsController(window, window.decorView)
        if (AppPrefs.hideSystemBars(this)) {
            controller.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            controller.hide(WindowInsetsCompat.Type.systemBars())
        } else {
            controller.show(WindowInsetsCompat.Type.systemBars())
        }
    }

    override fun onStart() {
        super.onStart()
        connError.resume()
        // Resets LiveStatusService's idle clock every time the dashboard
        // becomes visible again, not just on first open.
        AppPrefs.touchForeground(this)
    }

    override fun onStop() {
        super.onStop()
        connError.pause()
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
                load(AppPrefs.dashboardUrl(this, newHost, newPort))
                startLiveUpdates(newHost, newPort)
            }
            return
        }
        load(AppPrefs.dashboardUrl(this, host, port))
        startLiveUpdates(host, port)
    }

    private fun load(url: String) {
        lastUrl = url
        loadRequested = true
        webView.loadUrl(url)
    }

    /** The page could not be loaded: hide the WebView (and with it the engine's own
     *  error page) behind [ConnectionErrorPanel]. */
    private fun showLoadError(reason: ConnectionErrorPanel.Reason, description: String? = null, httpStatus: Int = 0) {
        loadFailed = true
        updateSettingsButton()
        loadingRing.hide()
        webView.visibility = View.INVISIBLE
        val address = lastUrl?.let {
            val uri = Uri.parse(it)
            "${uri.host ?: ""}:${if (uri.port > 0) uri.port else AppPrefs.defaultPort(this)}"
        } ?: ""
        connError.show(reason, address, description, httpStatus)
    }

    private fun changeServer() {
        promptForHost { newHost, newPort -> useServer(newHost, newPort) }
    }

    /** A new server address, from the settings dialog or the error screen's field:
     *  remembered, loaded, and the live status service re-pointed at it. */
    private fun useServer(host: String, port: Int) {
        AppPrefs.setDefault(this, host, port)
        load(AppPrefs.dashboardUrl(this, host, port))
        startLiveUpdates(host, port)
    }

    /** The screen stays on only while the kiosk view's "Now playing" page is
     *  showing (and the setting allows it).  Every other page, the full web
     *  page, and a page that is loading all let the phone sleep normally. */
    private fun applyKeepScreenOn() {
        val on = pageWantsScreenOn && AppPrefs.keepScreenOn(this) &&
            AppPrefs.viewMode(this) == AppPrefs.VIEW_KIOSK
        if (on) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    /** What the kiosk page can ask of the app.  Called from a WebView
     *  thread, so everything hops to the UI thread. */
    private inner class AppBridge {
        @JavascriptInterface
        fun setPageWantsScreenOn(on: Boolean) {
            runOnUiThread { pageWantsScreenOn = on; applyKeepScreenOn() }
        }

        @JavascriptInterface
        fun openSettings() {
            runOnUiThread { showSettings() }
        }

        /** The user's "keep the screen on while Now playing is shown" choice. */
        @JavascriptInterface
        fun keepScreenOn(): Boolean = AppPrefs.keepScreenOn(this@MainActivity)

        /** The kiosk's top-right button: false lets Android switch the display off on its own timeout. */
        @JavascriptInterface
        fun setKeepScreenOn(on: Boolean) {
            runOnUiThread { AppPrefs.setKeepScreenOn(this@MainActivity, on); applyKeepScreenOn() }
        }

        /** The kiosk's current page is scrolled down (true) or at its top (false). */
        @JavascriptInterface
        fun setPageScrolled(scrolled: Boolean) {
            runOnUiThread { pageScrolled = scrolled }
        }

        /** Record the microphone for [durationMs] and reply through K.onMicEnvelope(). */
        @JavascriptInterface
        fun startMicEnvelope(durationMs: Int, stepMs: Int) {
            val d = durationMs.coerceIn(2000, 30000)
            val s = stepMs.coerceIn(5, 50)
            runOnUiThread {
                if (ContextCompat.checkSelfPermission(this@MainActivity, Manifest.permission.RECORD_AUDIO)
                    == PackageManager.PERMISSION_GRANTED) recordMic(d, s)
                else { pendingMic = d to s; micPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO) }
            }
        }

        @JavascriptInterface
        fun micAvailable(): Boolean = packageManager.hasSystemFeature(PackageManager.FEATURE_MICROPHONE)

        @JavascriptInterface
        fun apiVersion(): Int = 4
    }

    /** The gear button: which view to show, the screen-on rule, and the
     *  server address.  Choosing a view reloads the page in it. */
    private fun showSettings() {
        val kiosk = AppPrefs.viewMode(this) == AppPrefs.VIEW_KIOSK
        val keepOn = AppPrefs.keepScreenOn(this)
        val hideBars = AppPrefs.hideSystemBars(this)
        val items = arrayOf(
            (if (kiosk) "● " else "○ ") + getString(R.string.settings_view_kiosk),
            (if (!kiosk) "● " else "○ ") + getString(R.string.settings_view_web),
            (if (keepOn) "☑ " else "☐ ") + getString(R.string.settings_keep_on),
            (if (hideBars) "☑ " else "☐ ") + getString(R.string.settings_hide_bars),
            getString(R.string.settings_change_server),
        )
        AlertDialog.Builder(this)
            .setTitle(R.string.settings_title)
            .setItems(items) { _, which ->
                when (which) {
                    0 -> switchView(AppPrefs.VIEW_KIOSK)
                    1 -> switchView(AppPrefs.VIEW_WEB)
                    2 -> { AppPrefs.setKeepScreenOn(this, !keepOn); applyKeepScreenOn() }
                    3 -> { AppPrefs.setHideSystemBars(this, !hideBars); applySystemBars() }
                    4 -> changeServer()
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun switchView(mode: String) {
        if (AppPrefs.viewMode(this) == mode) return
        AppPrefs.setViewMode(this, mode)
        updateSettingsButton()
        loadDashboard()
    }

    /** In the kiosk view the settings live on the kiosk's own Config page ("App
     *  settings"), so the gear only shows for the full web page - which has no
     *  such page - and whenever the page could not be loaded at all. */
    private fun updateSettingsButton() {
        val show = AppPrefs.viewMode(this) == AppPrefs.VIEW_WEB || loadFailed
        settingsButton.visibility = if (show) View.VISIBLE else View.GONE
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
