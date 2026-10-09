package it.giacomos.omdrc.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.pm.ActivityInfo
import android.content.pm.PackageManager
import android.database.ContentObserver
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import androidx.activity.ComponentActivity
import androidx.activity.addCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.drawToBitmap
import androidx.core.view.updateLayoutParams
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.core.view.WindowInsetsCompat
import it.giacomos.omdrc.app.work.WidgetRefreshWorker

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
    /** The kiosk holds the screen on for a calibration (setHoldScreenOn). */
    private var pageHoldsScreenOn = false
    /** The kiosk reports its active page; orientation comes from Android itself. */
    private var nowPage = false

    /** The page in the WebView could not be loaded (box off, wrong address). */
    private var loadFailed = false
    private lateinit var connError: ConnectionErrorPanel

    /** The kiosk said its first page is painted (AppBridge.pageReady): the splash
     *  goes once the phone is also in the orientation that page asked for. */
    private var pageReady = false
    private val finishSplash = Runnable {
        if (pageReady && !loadFailed) {
            val host = findViewById<EditText>(R.id.loading_host)
            if (host.hasFocus()) {
                (getSystemService(INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager)
                    .hideSoftInputFromWindow(host.windowToken, 0)
                host.clearFocus()
            }
            loadingRing.finish()
            endLastPage()
        }
    }
    private val splashFallback = Runnable { pageReady = true; finishSplash.run() }

    /** A cold start shows the kiosk's last screen (LastPage) instead of the splash;
     *  the splash comes up over it only if the live page is slow to paint. */
    private lateinit var lastPage: ImageView
    private var showingLastPage = false
    private var firstLoad = true
    private val ringLater = Runnable { if (showingLastPage && !pageReady && !loadFailed) loadingRing.start() }

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

    // At most one recording at a time; it is cancelled when the page asks, when the app
    // leaves the screen, and by a watchdog, so the microphone can never stay open.
    @Volatile private var micRun: MicEnvelope? = null

    private fun stopMic() { micRun?.cancel() }

    override fun onDestroy() { stopMic(); unregisterReceiver(closeReceiver); super.onDestroy() }

    private fun recordMic(durationMs: Int, stepMs: Int) {
        stopMic()
        val mic = MicEnvelope(durationMs, stepMs)
        micRun = mic
        val watchdog = android.os.Handler(android.os.Looper.getMainLooper())
        val kill = Runnable { mic.cancel() }
        watchdog.postDelayed(kill, durationMs + 4000L)
        Thread {
            val json = try {
                val r = mic.record()
                val o = org.json.JSONObject()
                o.put("ok", true); o.put("t0", r.t0WallMs); o.put("step", r.stepMs); o.put("source", r.source)
                val a = org.json.JSONArray()
                for (v in r.db) a.put(Math.round(v * 10) / 10.0)
                o.put("db", a)
                o.toString()
            } catch (e: Exception) {
                org.json.JSONObject().put("ok", false).put("error", e.message ?: "microphone error").toString()
            }
            watchdog.removeCallbacks(kill)
            if (micRun === mic) micRun = null
            sendMicResult(json)
        }.start()
    }

    // The Wi-Fi name is read when the app starts or resumes, every 5 minutes while it is
    // on screen, and when the user presses "Identify Wi-Fi" (reading it is a location access, and Android shows its location
    // indicator for it).  It is then remembered against the network's fingerprint
    // (gateway address + subnet, readable without location), so later runs and app
    // restarts recognise the network without asking Android for the name again.
    // The page's once-a-second timingNetwork() never touches WifiManager.
    private val netPrefs get() = ServerNetwork.names(this)

    private var currentServerNetwork: String? = null
    private var loadedServerNetwork: String? = null
    private var observedNetwork: android.net.Network? = null
    private var networkWatching = false
    private var foreground = false
    private var connectionFailures = 0
    private var configurationOffered = false
    private var serverDialog: AlertDialog? = null
    private val networkHandler = android.os.Handler(android.os.Looper.getMainLooper())
    private val networkChanged = Runnable {
        if (!foreground) return@Runnable
        val cm = getSystemService(android.net.ConnectivityManager::class.java)
        val network = cm.activeNetwork
        val key = serverNetworkKey()
        if (network == observedNetwork && key == currentServerNetwork) return@Runnable
        observedNetwork = network
        currentServerNetwork = key
        connectionFailures = 0
        configurationOffered = false
        serverDialog?.dismiss()
        if (network != null) {
            val saved = key?.let { AppPrefs.networkServer(this, it) }
            if (saved != null) connectServer(saved.first, saved.second) else loadDashboard()
        }
    }
    private val networkCallback = object : android.net.ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: android.net.Network) = queueNetworkChange()
        override fun onLost(network: android.net.Network) = queueNetworkChange()
        override fun onLinkPropertiesChanged(network: android.net.Network, properties: android.net.LinkProperties) = queueNetworkChange()
        override fun onCapabilitiesChanged(network: android.net.Network, capabilities: android.net.NetworkCapabilities) = queueNetworkChange()
    }

    private fun queueNetworkChange() {
        networkHandler.removeCallbacks(networkChanged)
        networkHandler.postDelayed(networkChanged, 800)
    }

    private fun serverNetworkKey(): String? {
        val cm = getSystemService(android.net.ConnectivityManager::class.java)
        val caps = cm.activeNetwork?.let { cm.getNetworkCapabilities(it) }
        // Refresh only on network events / foreground entry, never during polling.
        if (caps?.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI) == true &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
            == PackageManager.PERMISSION_GRANTED) identifyWifiNow(quiet = true)
        return ServerNetwork.currentKey(this)
    }

    private fun rememberServer() {
        if (loadedServerNetwork == null || loadedServerNetwork != serverNetworkKey()) return
        val uri = lastUrl?.let { Uri.parse(it) } ?: return
        val host = uri.host ?: return
        AppPrefs.setNetworkServer(this, loadedServerNetwork!!, host,
            if (uri.port > 0) uri.port else AppPrefs.DEFAULT_PORT)
    }

    private val networkPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { grants -> if (grants.values.any { it }) identifyWifiNow() }

    @Suppress("DEPRECATION")
    private fun identifyWifiNow(quiet: Boolean = false) {
        try {
            val cm = getSystemService(android.net.ConnectivityManager::class.java)
            val net = cm.activeNetwork
            val caps = cm.getNetworkCapabilities(net)
            val ssid = if (caps != null && caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI))
                applicationContext.getSystemService(android.net.wifi.WifiManager::class.java)
                    .connectionInfo.ssid?.removeSurrounding("\"") else null
            val fp = ServerNetwork.fingerprint(cm, net)
            if (!ssid.isNullOrBlank() && ssid != android.net.wifi.WifiManager.UNKNOWN_SSID && fp != null) {
                netPrefs.edit().putString(fp, ssid).apply()
                if (!quiet) android.widget.Toast.makeText(this, "Wi-Fi remembered: $ssid", android.widget.Toast.LENGTH_SHORT).show()
            } else {
                if (!quiet) android.widget.Toast.makeText(this, "Wi-Fi name unavailable (is Location on?)", android.widget.Toast.LENGTH_LONG).show()
            }
        } catch (_: SecurityException) {
            if (!quiet) android.widget.Toast.makeText(this, "Wi-Fi name unavailable (location permission)", android.widget.Toast.LENGTH_LONG).show()
        }
    }

    private fun timingNetwork(): String {
        val out = org.json.JSONObject()
        val cm = getSystemService(android.net.ConnectivityManager::class.java)
        val active = cm.activeNetwork
        val caps = cm.getNetworkCapabilities(active)
        when {
            caps == null -> out.put("key", "offline").put("label", "Disconnected")
            caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_ETHERNET) ->
                out.put("key", "wired").put("label", "Wired")
            caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI) -> {
                val ssid = ServerNetwork.fingerprint(cm, active)?.let { netPrefs.getString(it, null) }
                if (ssid != null) out.put("key", "wifi:$ssid").put("label", ssid)
                else out.put("key", "unknown").put("label", "Wi-Fi not identified")
            }
            else -> out.put("key", "unknown").put("label", "Network unidentified")
        }
        return out.toString()
    }

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { }

    /** The phone's own auto-rotate setting.  On, the app follows the phone the way
     *  every other app does, and the kiosk adapts to whichever way it is held (Now:
     *  needles in landscape, bars upright); off, the rotate button selects
     *  one orientation for every page. */
    private fun autoRotate(): Boolean =
        Settings.System.getInt(contentResolver, Settings.System.ACCELEROMETER_ROTATION, 0) == 1

    private fun isPortrait(): Boolean =
        resources.configuration.orientation == android.content.res.Configuration.ORIENTATION_PORTRAIT

    /** The orientation to ask for: the phone's with auto-rotate, else the user's choice
     *  from the last run: landscape at first. */
    private fun rememberedOrientation(): Int = when {
        autoRotate() -> ActivityInfo.SCREEN_ORIENTATION_USER
        AppPrefs.lastPortrait(this) -> ActivityInfo.SCREEN_ORIENTATION_SENSOR_PORTRAIT
        else -> ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
    }

    /** Auto-rotate switched on or off while the app is open: follow at once, and let
     *  the kiosk show or hide its rotate button. */
    private val autoRotateObserver = object : ContentObserver(Handler(Looper.getMainLooper())) {
        override fun onChange(selfChange: Boolean) {
            requestedOrientation = rememberedOrientation()
            webView.evaluateJavascript("window.K && K.onAutoRotate && K.onAutoRotate()", null)
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    // The live notification's Close: leave, so the page stops its requests too.
    private val closeReceiver = object : android.content.BroadcastReceiver() {
        override fun onReceive(context: android.content.Context, intent: android.content.Intent) = finishAndRemoveTask()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        LiveStatusService.reopenApp(this)
        ContextCompat.registerReceiver(this, closeReceiver,
            android.content.IntentFilter(LiveStatusService.ACTION_CLOSE_APP), ContextCompat.RECEIVER_NOT_EXPORTED)
        requestedOrientation = rememberedOrientation()
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
            // and the keyboard while it is up: the page shrinks above it rather than
            // under it (the Qobuz search box sits at the bottom of Now, upright)
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime()).bottom
            // The kiosk runs on under the navigation bar, as Android 15 recommends: its
            // bottom strips carry their colour to the screen's edge and keep their content
            // above the bar (CSS env(safe-area-inset-bottom), which the WebView reports).
            // The app's own panels over it stay above the bar.
            val underBar = AppPrefs.viewMode(this) == AppPrefs.VIEW_KIOSK && ime == 0
            view.setPadding(bars.left, bars.top, bars.right, if (underBar) 0 else maxOf(bars.bottom, ime))
            val panelBottom = if (underBar) bars.bottom else 0
            for (id in intArrayOf(R.id.loading_ring, R.id.conn_error)) {
                val panel = view.findViewById<View>(id)
                panel.updateLayoutParams<ViewGroup.MarginLayoutParams> { bottomMargin = panelBottom }
            }
            insets
        }

        applySystemBars()

        val timingDebug = (applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0
        WebView.setWebContentsDebuggingEnabled(timingDebug)
        webView = findViewById(R.id.web_view)
        loadingRing = findViewById(R.id.loading_ring)
        val loadingHost = findViewById<EditText>(R.id.loading_host)
        val reconnect = {
            val host = loadingHost.text.toString().trim()
            if (host.isEmpty() || host.any { it.isWhitespace() || it == ':' || it == '/' }) {
                loadingHost.error = getString(R.string.conn_error_bad_address)
            } else {
                (getSystemService(INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager)
                    .hideSoftInputFromWindow(loadingHost.windowToken, 0)
                loadingHost.clearFocus()
                useServer(host, AppPrefs.defaultPort(this))
            }
        }
        findViewById<View>(R.id.loading_connect).setOnClickListener { reconnect() }
        loadingHost.setOnEditorActionListener { _, action, _ ->
            if (action == android.view.inputmethod.EditorInfo.IME_ACTION_GO) { reconnect(); true } else false
        }
        lastPage = findViewById(R.id.last_page)
        swipeRefresh = findViewById(R.id.swipe_refresh)
        swipeRefresh.setColorSchemeColors(0xFF58A6FF.toInt(), 0xFF3FB950.toInt(), 0xFFD8C23A.toInt())
        swipeRefresh.setProgressBackgroundColorSchemeColor(ContextCompat.getColor(this, R.color.kiosk_surface))
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
        // The root is padded by the keyboard (above), so the error card is already above
        // it; only the address field is scrolled back into sight.
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.conn_error)) { _, insets ->
            connError.revealField()
            insets
        }

        webView.webViewClient = object : WebViewClient() {
            // A link that leaves the box (an "open on dr.loudness-war.info" in the DR
            // versions frame, say) goes to the phone's browser: shown in place, it
            // replaced the kiosk, and Back loaded the kiosk again behind the splash.
            override fun shouldOverrideUrlLoading(
                view: WebView?,
                request: android.webkit.WebResourceRequest?,
            ): Boolean {
                val uri = request?.url ?: return false
                if (!request.isForMainFrame || lastUrl == null || isCurrentServer(uri)) return false
                if (uri.scheme != "https" && uri.scheme != "http") return false
                try {
                    startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, uri))
                } catch (e: android.content.ActivityNotFoundException) {
                    android.widget.Toast.makeText(this@MainActivity, "Nothing on this phone opens $uri", android.widget.Toast.LENGTH_LONG).show()
                }
                return true
            }

            override fun onPageStarted(view: WebView?, url: String?, favicon: android.graphics.Bitmap?) {
                if (url == null || !isCurrentServer(Uri.parse(url))) return
                // A new document knows nothing about the old one's request:
                // drop the screen-on flag until the kiosk page asks again.
                pageWantsScreenOn = false
                pageHoldsScreenOn = false
                nowPage = false
                applyKeepScreenOn()
                applySystemBars()
                pageScrolled = false
                // and back to the last orientation asked for until a page asks otherwise
                requestedOrientation = rememberedOrientation()
                val requested = loadRequested
                loadRequested = false
                if (connError.isShown) {
                    if (!requested) return
                    connError.connecting()      // the panel stays up until this load's outcome
                } else if (!showingLastPage) {
                    loadingRing.start()
                }
                pageReady = false
                loadingRing.removeCallbacks(splashFallback)
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
                if (request?.isForMainFrame == true && isCurrentServer(request.url)) {
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
                if (request?.isForMainFrame == true && isCurrentServer(request.url) && code >= 500) {
                    showLoadError(ConnectionErrorPanel.Reason.HTTP, httpStatus = code)
                }
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                if (loadFailed || url == null || !isCurrentServer(Uri.parse(url))) return
                webView.visibility = View.VISIBLE
                connError.hide()
                connectionFailures = 0
                configurationOffered = false
                serverDialog?.dismiss()
                rememberServer()
                // The kiosk builds its pages after the document has loaded and says when
                // the first one is painted; the full web page (or a kiosk too old to say)
                // is shown as soon as it has loaded, or after a few seconds at most.
                loadingRing.setProgress(100)
                if (AppPrefs.viewMode(this@MainActivity) != AppPrefs.VIEW_KIOSK) splashFallback.run()
                else loadingRing.postDelayed(splashFallback, 5000)
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onConsoleMessage(message: android.webkit.ConsoleMessage): Boolean {
                if (timingDebug && message.message().startsWith("OMDRC_TIMING ")) {
                    android.util.Log.i("OMDRCTiming", message.message().removePrefix("OMDRC_TIMING "))
                    return true
                }
                return super.onConsoleMessage(message)
            }

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
            val fallback = {
                // Back leaves the app like Home does when the kiosk has no view to close.
                if (webView.canGoBack()) webView.goBack() else moveTaskToBack(true)
            }
            if (AppPrefs.viewMode(this@MainActivity) == AppPrefs.VIEW_KIOSK && !loadFailed) {
                webView.evaluateJavascript("window.K && K.handleAndroidBack ? K.handleAndroidBack() : false") { result ->
                    if (result != "true") fallback()
                }
            } else fallback()
        }

        settingsButton = findViewById(R.id.settings_button)
        settingsButton.setOnClickListener { showSettings() }
        updateSettingsButton()

        loadDashboard()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        // Dialogs and transient swipe-in bars can drop immersive mode.
        if (hasFocus) applySystemBars()
    }

    /** Now in landscape uses the full screen; every other view keeps system bars. */
    private fun applySystemBars() {
        val controller = WindowCompat.getInsetsController(window, window.decorView)
        if (AppPrefs.viewMode(this) == AppPrefs.VIEW_KIOSK && nowPage && !isPortrait()) {
            controller.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            controller.hide(WindowInsetsCompat.Type.systemBars())
        } else {
            controller.show(WindowInsetsCompat.Type.systemBars())
        }
    }

    override fun onStart() {
        super.onStart()
        LiveStatusService.reopenApp(this)
        contentResolver.registerContentObserver(
            Settings.System.getUriFor(Settings.System.ACCELEROMETER_ROTATION), false, autoRotateObserver)
        requestedOrientation = rememberedOrientation()      // the setting may have changed meanwhile
        foreground = true
        if (!networkWatching) {
            getSystemService(android.net.ConnectivityManager::class.java)
                .registerDefaultNetworkCallback(networkCallback, networkHandler)
            networkWatching = true
        }
        networkChanged.run()
        if (serverDialog == null) connError.resume()
        offerNetworkConfiguration()
        // Resets LiveStatusService's idle clock every time the dashboard
        // becomes visible again, not just on first open.
        AppPrefs.touchForeground(this)
        // It stops itself when nothing plays: opening the app brings it back.
        LiveStatusService.appVisible = true
        AppPrefs.defaultHost(this)?.let { LiveStatusService.ensureRunning(this, it, AppPrefs.defaultPort(this)) }
    }

    // The Wi-Fi name is read at startup / resume and then every 5 minutes while the app
    // is on screen (only when location is already permitted) - never more often.
    private val wifiHandler = android.os.Handler(android.os.Looper.getMainLooper())
    private val wifiCheck = object : Runnable {
        override fun run() {
            if (ContextCompat.checkSelfPermission(this@MainActivity, Manifest.permission.ACCESS_FINE_LOCATION)
                == PackageManager.PERMISSION_GRANTED) identifyWifiNow(quiet = true)
            wifiHandler.postDelayed(this, 5 * 60_000L)
        }
    }

    override fun onResume() {
        super.onResume()
        wifiHandler.removeCallbacks(wifiCheck)
        wifiHandler.post(wifiCheck)
    }

    override fun onPause() {
        super.onPause()
        wifiHandler.removeCallbacks(wifiCheck)
        // The screen as it is left, for the next cold start (see LastPage).  Here and
        // not in onStop: the page is still drawn, so the picture is not a blank one.
        val url = lastUrl
        if (url != null && pageReady && !loadFailed && !showingLastPage &&
            AppPrefs.viewMode(this) == AppPrefs.VIEW_KIOSK && webView.visibility == View.VISIBLE) {
            val portrait = resources.configuration.orientation == android.content.res.Configuration.ORIENTATION_PORTRAIT
            LastPage.save(this, webView, url, portrait)
        }
    }

    override fun onStop() {
        foreground = false
        LiveStatusService.appVisible = false
        networkHandler.removeCallbacks(networkChanged)
        if (networkWatching) {
            getSystemService(android.net.ConnectivityManager::class.java).unregisterNetworkCallback(networkCallback)
            networkWatching = false
        }
        stopMic()
        super.onStop()
        contentResolver.unregisterContentObserver(autoRotateObserver)
        connError.pause()
        // Leaving the dashboard is exactly when you might just have changed
        // something (enabled DRC, switched geometry, ...) and exactly when
        // you're about to glance at the widget next - refresh every
        // configured widget instance right away rather than waiting for the
        // next alarm tick.
        WidgetRefreshWorker.enqueueOneTime(this, null)
    }

    private fun loadDashboard() {
        currentServerNetwork = serverNetworkKey()
        observedNetwork = getSystemService(android.net.ConnectivityManager::class.java).activeNetwork
        val saved = currentServerNetwork?.let { AppPrefs.networkServer(this, it) }
        val host = saved?.first ?: intent.getStringExtra(EXTRA_HOST) ?: AppPrefs.defaultHost(this)
        val port = saved?.second ?: intent.getIntExtra(EXTRA_PORT, AppPrefs.defaultPort(this))
        if (host == null) {
            promptForHost { newHost, newPort -> useServer(newHost, newPort) }
            return
        }
        connectServer(host, port)
    }

    private fun load(url: String) {
        webView.stopLoading()
        loadingRing.removeCallbacks(splashFallback)
        loadingRing.removeCallbacks(finishSplash)
        loadingRing.removeCallbacks(ringLater)
        pageReady = false
        findViewById<EditText>(R.id.loading_host).setText(Uri.parse(url).host ?: "")
        loadedServerNetwork = currentServerNetwork
        lastUrl = url
        loadRequested = true
        if (connError.isShown) connError.connecting()
        if (!connError.isShown) {
            // the first load of a fresh start: the last screen at once, if there is one
            // of this page in this orientation; else the splash at once, not once the
            // request is answered
            if (firstLoad && showLastPage(url)) loadingRing.postDelayed(ringLater, 1200)
            else loadingRing.start()
        }
        firstLoad = false
        // No pull-to-reload in the kiosk: it reloads itself when the box has new code
        // (main.js, reloadQuietly below) and otherwise has nothing to reload for.  The
        // full web page keeps it: it has no other way.
        swipeRefresh.isEnabled = AppPrefs.viewMode(this) != AppPrefs.VIEW_KIOSK
        webView.loadUrl(url)
    }

    /** The page could not be loaded: hide the WebView (and with it the engine's own
     *  error page) behind [ConnectionErrorPanel]. */
    private fun isCurrentServer(uri: Uri): Boolean {
        val current = lastUrl?.let { Uri.parse(it) } ?: return false
        return uri.scheme == current.scheme && uri.host == current.host && uri.port == current.port
    }

    private fun showLoadError(reason: ConnectionErrorPanel.Reason, description: String? = null, httpStatus: Int = 0) {
        loadFailed = true
        updateSettingsButton()
        loadingRing.hide()
        endLastPage(at = true)
        webView.visibility = View.INVISIBLE
        val address = lastUrl?.let {
            val uri = Uri.parse(it)
            "${uri.host ?: ""}:${if (uri.port > 0) uri.port else AppPrefs.defaultPort(this)}"
        } ?: ""
        connError.show(reason, address, description, httpStatus)
        connectionFailures++
        offerNetworkConfiguration()
    }

    private fun offerNetworkConfiguration() {
        if (!foreground || connectionFailures < 3 || configurationOffered || serverDialog != null) return
        // One offer per failure episode; dismissing it keeps automatic retries available.
        configurationOffered = true
        changeServer()
    }

    private fun changeServer() {
        promptForHost { newHost, newPort -> useServer(newHost, newPort) }
    }

    /** A new server address, from the settings dialog or the error screen's field:
     *  remembered, loaded, and the live status service re-pointed at it. */
    private fun useServer(host: String, port: Int) {
        currentServerNetwork = serverNetworkKey()
        currentServerNetwork?.let { AppPrefs.setNetworkServer(this, it, host, port) }
        connectionFailures = 0
        configurationOffered = false
        connectServer(host, port)
    }

    private fun connectServer(host: String, port: Int) {
        AppPrefs.setDefault(this, host, port)
        load(AppPrefs.dashboardUrl(this, host, port))
        startLiveUpdates(host, port)
    }

    /** The screen stays on only while the kiosk view's "Now playing" page is
     *  showing (and the setting allows it).  Every other page, the full web
     *  page, and a page that is loading all let the phone sleep normally. */
    private fun applyKeepScreenOn() {
        // a calibration holds it on whatever the setting: it must not sleep mid-run
        val on = pageHoldsScreenOn || pageWantsScreenOn && AppPrefs.keepScreenOn(this) &&
            AppPrefs.viewMode(this) == AppPrefs.VIEW_KIOSK
        if (on) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    /** What the kiosk page can ask of the app.  Called from a WebView
     *  thread, so everything hops to the UI thread. */
    /** The last screen of [url], if there is one for the orientation the app starts in. */
    private fun showLastPage(url: String): Boolean {
        if (AppPrefs.viewMode(this) != AppPrefs.VIEW_KIOSK) return false
        val portrait = if (autoRotate()) isPortrait() else AppPrefs.lastPortrait(this)
        val bitmap = LastPage.load(this, url, portrait) ?: return false
        lastPage.setImageBitmap(bitmap)
        lastPage.animate().cancel()
        lastPage.alpha = 1f
        lastPage.visibility = View.VISIBLE
        showingLastPage = true
        return true
    }

    /** The live page is there (or failed): the picture goes, fading unless [at] once. */
    private fun endLastPage(at: Boolean = false) {
        loadingRing.removeCallbacks(ringLater)
        if (!showingLastPage) return
        showingLastPage = false
        val done = { lastPage.visibility = View.GONE; lastPage.setImageDrawable(null) }
        if (at) done() else lastPage.animate().alpha(0f).setDuration(220).withEndAction(done).start()
    }

    /** The splash stays over the page until the kiosk is painted and the phone has
     *  turned the way the page wants, so neither the half-built page nor the turn is seen. */
    private fun maybeEndSplash() {
        if (!pageReady || loadFailed) return
        val portrait = resources.configuration.orientation == android.content.res.Configuration.ORIENTATION_PORTRAIT
        val wantPortrait = requestedOrientation == ActivityInfo.SCREEN_ORIENTATION_SENSOR_PORTRAIT
        if (requestedOrientation == ActivityInfo.SCREEN_ORIENTATION_USER) {   // auto-rotate: nothing to wait for
            loadingRing.removeCallbacks(splashFallback)
            loadingRing.postDelayed(finishSplash, 200)
            return
        }
        if (portrait != wantPortrait) return            // onConfigurationChanged calls again
        loadingRing.removeCallbacks(splashFallback)
        loadingRing.postDelayed(finishSplash, 200)   // the page re-lays out after a turn
    }

    override fun onConfigurationChanged(newConfig: android.content.res.Configuration) {
        super.onConfigurationChanged(newConfig)
        // turned by hand: the next start begins this way up (and shows this way's last screen)
        if (autoRotate()) AppPrefs.setLastPortrait(this, isPortrait())
        applySystemBars()
        maybeEndSplash()
    }

    private inner class AppBridge {
        @JavascriptInterface
        fun timingNetwork(): String = this@MainActivity.timingNetwork()

        @JavascriptInterface
        fun identifyTimingNetwork() {
            runOnUiThread {
                if (ContextCompat.checkSelfPermission(this@MainActivity, Manifest.permission.ACCESS_FINE_LOCATION)
                    == PackageManager.PERMISSION_GRANTED) identifyWifiNow()
                else networkPermissionLauncher.launch(arrayOf(
                    Manifest.permission.ACCESS_COARSE_LOCATION, Manifest.permission.ACCESS_FINE_LOCATION))
            }
        }

        // Native storage survives a change of the box's IP / WebView origin.
        @JavascriptInterface
        fun timingProfiles(): String = getSharedPreferences("meter-timing", MODE_PRIVATE)
            .getString("profiles", "{}") ?: "{}"

        @JavascriptInterface
        fun saveTimingProfiles(json: String) {
            if (json.length <= 100000) getSharedPreferences("meter-timing", MODE_PRIVATE)
                .edit().putString("profiles", json).apply()
        }

        /** The kiosk's first page is on screen (main.js boot). */
        @JavascriptInterface
        fun pageReady() {
            runOnUiThread { pageReady = true; maybeEndSplash() }
        }

        @JavascriptInterface
        fun setPageWantsScreenOn(on: Boolean) {
            runOnUiThread { pageWantsScreenOn = on; applyKeepScreenOn() }
        }

        /** The meter-timing sheet is open (a calibration may run): screen on,
         *  regardless of the "keep the screen on" setting, which is about Now playing. */
        @JavascriptInterface
        fun setHoldScreenOn(on: Boolean) {
            runOnUiThread { pageHoldsScreenOn = on; applyKeepScreenOn() }
        }

        @JavascriptInterface
        fun openSettings() {
            runOnUiThread { showSettings() }
        }

        /** The kiosk found new code on the box: reload under a picture of the screen as it
         *  is, so nothing is seen but the new page replacing it (the ring only if that
         *  takes more than about a second, as on a cold start). */
        @JavascriptInterface
        fun reloadQuietly() {
            runOnUiThread {
                if (loadFailed || connError.isShown) return@runOnUiThread
                try {
                    lastPage.setImageBitmap(webView.drawToBitmap())
                    lastPage.animate().cancel()
                    lastPage.alpha = 1f
                    lastPage.visibility = View.VISIBLE
                    showingLastPage = true
                    loadingRing.postDelayed(ringLater, 1200)
                } catch (e: IllegalStateException) {}      // not laid out: an ordinary reload
                webView.reload()
            }
        }

        /** A link outside the box (an album's booklet PDF, the album on Qobuz): to
         *  whatever Android opens it with, since the WebView shows no PDF. Back returns here. */
        @JavascriptInterface
        fun openExternal(url: String) {
            val uri = android.net.Uri.parse(url)
            if (uri.scheme != "https" && uri.scheme != "http") return
            runOnUiThread {
                try {
                    startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, uri))
                } catch (e: android.content.ActivityNotFoundException) {
                    android.widget.Toast.makeText(this@MainActivity, "Nothing on this phone opens $url", android.widget.Toast.LENGTH_LONG).show()
                }
            }
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

        /** Keep system bars visible except on the kiosk's Now page in landscape. */
        @JavascriptInterface
        fun setNowPage(now: Boolean) {
            runOnUiThread { nowPage = now; applySystemBars() }
        }

        /** Page navigation preserves the user's app-wide orientation. */
        @JavascriptInterface
        fun setPageOrientation(orientation: String) {
            runOnUiThread { requestedOrientation = rememberedOrientation() }
        }

        @JavascriptInterface
        fun forcedOrientation(): String =
            if (AppPrefs.lastPortrait(this@MainActivity)) "portrait" else "landscape"

        /** Only the rotate button changes the forced orientation. */
        @JavascriptInterface
        fun setUserOrientation(orientation: String) {
            if (orientation != "portrait" && orientation != "landscape") return
            runOnUiThread {
                if (!autoRotate()) {
                    AppPrefs.setLastPortrait(this@MainActivity, orientation == "portrait")
                }
                requestedOrientation = rememberedOrientation()
            }
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

        /** Release the microphone at once (the page gave up waiting, or the run ended). */
        @JavascriptInterface
        fun stopMicEnvelope() { stopMic() }

        @JavascriptInterface
        fun micAvailable(): Boolean = packageManager.hasSystemFeature(PackageManager.FEATURE_MICROPHONE)

        /** The phone rotates by itself (auto-rotate on): the kiosk hides its rotate button. */
        @JavascriptInterface
        fun autoRotate(): Boolean = this@MainActivity.autoRotate()

        @JavascriptInterface
        fun apiVersion(): Int = 8
    }

    /** The gear button: which view to show, the screen-on rule, and the
     *  server address.  Choosing a view reloads the page in it. */
    private fun showSettings() {
        val kiosk = AppPrefs.viewMode(this) == AppPrefs.VIEW_KIOSK
        val keepOn = AppPrefs.keepScreenOn(this)
        val instant = AppPrefs.instantUpdates(this)
        val items = arrayOf(
            (if (kiosk) "● " else "○ ") + getString(R.string.settings_view_kiosk),
            (if (!kiosk) "● " else "○ ") + getString(R.string.settings_view_web),
            (if (keepOn) "☑ " else "☐ ") + getString(R.string.settings_keep_on),
            getString(R.string.settings_change_server),
            (if (instant) "☑ " else "☐ ") + getString(R.string.settings_instant_updates),
        )
        AlertDialog.Builder(this)
            .setTitle(R.string.settings_title)
            .setItems(items) { _, which ->
                when (which) {
                    0 -> switchView(AppPrefs.VIEW_KIOSK)
                    1 -> switchView(AppPrefs.VIEW_WEB)
                    2 -> { AppPrefs.setKeepScreenOn(this, !keepOn); applyKeepScreenOn() }
                    3 -> changeServer()
                    4 -> setInstantUpdates(!instant)
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    /** Restarts the live service in the mode chosen: instant updates keep
     *  it (and its notification) on, waiting on the box for changes. */
    private fun setInstantUpdates(on: Boolean) {
        AppPrefs.setInstantUpdates(this, on)
        val host = AppPrefs.defaultHost(this) ?: return
        startLiveUpdates(host, AppPrefs.defaultPort(this))
        android.widget.Toast.makeText(
            this, if (on) R.string.instant_updates_on else R.string.instant_updates_off,
            android.widget.Toast.LENGTH_LONG,
        ).show()
    }

    private fun switchView(mode: String) {
        if (AppPrefs.viewMode(this) == mode) return
        AppPrefs.setViewMode(this, mode)
        nowPage = false
        ViewCompat.requestApplyInsets(findViewById(R.id.root_container))   // the kiosk runs under the navigation bar
        applySystemBars()
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
        if (serverDialog != null) return
        connError.pause()
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
        serverDialog = builder.create().also { dialog ->
            dialog.setOnDismissListener {
                serverDialog = null
                if (foreground) connError.resume()
            }
            dialog.show()
        }
    }

    companion object {
        const val EXTRA_HOST = "it.giacomos.omdrc.app.EXTRA_HOST"
        const val EXTRA_PORT = "it.giacomos.omdrc.app.EXTRA_PORT"
    }
}
