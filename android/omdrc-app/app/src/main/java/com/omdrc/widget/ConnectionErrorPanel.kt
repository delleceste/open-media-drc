package com.omdrc.widget

import android.animation.ObjectAnimator
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Rect
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.TextView

/**
 * The screen shown when the page cannot be loaded, in place of the WebView's own
 * "Web page not available": a card in the kiosk's palette saying which address
 * failed and why, the address in an editable field with "Connect" (after moving to
 * another network the box usually has another IP), and "Retry now".  While it is up
 * it retries by itself after 5, 10, 20 and then every 30 s (a box that is still
 * booting comes back without a tap) - except while the address is being edited -
 * and it stays up through a retry until that retry either fails again or finishes
 * loading.
 */
class ConnectionErrorPanel(
    private val root: View,
    private val defaultPort: Int,
    private val onRetry: () -> Unit,
    private val onConnect: (host: String, port: Int) -> Unit,
) {
    enum class Reason { LOOKUP, REFUSED, TIMEOUT, HTTP, OTHER }

    private val res = root.resources
    private val detail: TextView = root.findViewById(R.id.conn_error_detail)
    private val hint: TextView = root.findViewById(R.id.conn_error_hint)
    private val status: TextView = root.findViewById(R.id.conn_error_status)
    private val field: EditText = root.findViewById(R.id.conn_error_host)
    private val handler = Handler(Looper.getMainLooper())

    private val pulse = ObjectAnimator.ofFloat(root.findViewById<View>(R.id.conn_error_led), View.ALPHA, 1f, 0.25f).apply {
        duration = 900; repeatMode = ValueAnimator.REVERSE; repeatCount = ValueAnimator.INFINITE
    }

    private var attempt = 0
    private var secondsLeft = 0
    private var connecting = false
    private var paused = false

    val isShown: Boolean get() = root.visibility == View.VISIBLE

    private val tick = object : Runnable {
        override fun run() {
            if (secondsLeft <= 0) { retryNow(); return }
            status.text = res.getString(R.string.conn_error_retrying_in, secondsLeft)
            secondsLeft--
            handler.postDelayed(this, 1000)
        }
    }

    init {
        root.findViewById<View>(R.id.conn_error_retry).setOnClickListener { endEditing(); retryNow() }
        root.findViewById<View>(R.id.conn_error_connect).setOnClickListener { connect() }
        field.setOnEditorActionListener { _, action, _ ->
            if (action == EditorInfo.IME_ACTION_GO || action == EditorInfo.IME_ACTION_DONE) { connect(); true } else false
        }
        // No automatic retry under the user's fingers: it would only fail again at the old address.
        field.setOnFocusChangeListener { _, focused ->
            if (focused) { handler.removeCallbacks(tick); status.setText(R.string.conn_error_editing) }
            else if (isShown && !connecting && !paused) scheduleRetry(again = true)
        }
    }

    /** The keyboard came up or went: keep the field in view above it. */
    fun revealField() {
        if (field.hasFocus()) field.post { field.requestRectangleOnScreen(Rect(0, 0, field.width, field.height), false) }
    }

    /** A load of [address] (host:port) failed; [description] is WebView's own text,
     *  used only when the reason is none of the common ones. */
    fun show(reason: Reason, address: String, description: String? = null, httpStatus: Int = 0) {
        val (why, advice) = when (reason) {
            Reason.LOOKUP -> res.getString(R.string.conn_error_reason_lookup) to R.string.conn_error_hint_lookup
            Reason.REFUSED -> res.getString(R.string.conn_error_reason_refused) to R.string.conn_error_hint_refused
            Reason.TIMEOUT -> res.getString(R.string.conn_error_reason_timeout) to R.string.conn_error_hint_timeout
            Reason.HTTP -> res.getString(R.string.conn_error_reason_http, httpStatus) to R.string.conn_error_hint_http
            // "net::ERR_INTERNET_DISCONNECTED" -> "internet disconnected"
            Reason.OTHER -> (description ?: "").removePrefix("net::").removePrefix("ERR_")
                .replace('_', ' ').lowercase() to R.string.conn_error_hint_other
        }
        detail.text = if (why.isEmpty()) address else "$address · $why"
        hint.setText(advice)
        if (!field.hasFocus()) field.setText(address)      // never under the user's typing
        connecting = false
        if (!isShown) {
            root.animate().cancel()
            root.alpha = 0f
            root.visibility = View.VISIBLE
            root.requestFocus()                    // not the field: that would pause the retries
            root.animate().alpha(1f).setDuration(200).start()
            pulse.start()
        }
        scheduleRetry()
    }

    /** A load started while the panel is up: wait for its outcome. */
    fun connecting() {
        handler.removeCallbacks(tick)
        connecting = true
        status.setText(R.string.conn_error_connecting)
    }

    /** The page loaded. */
    fun hide() {
        endEditing()
        handler.removeCallbacks(tick)
        attempt = 0
        connecting = false
        if (!isShown) return
        pulse.cancel()
        root.animate().cancel()
        root.animate().alpha(0f).setDuration(200).withEndAction {
            root.visibility = View.GONE; root.alpha = 1f
        }.start()
    }

    /** The activity went to the background: no retries nobody sees. */
    fun pause() {
        paused = true
        handler.removeCallbacks(tick)
    }

    /** Back in the foreground: try again at once rather than finishing the countdown. */
    fun resume() {
        paused = false
        if (isShown && !connecting) retryNow()
    }

    private fun connect() {
        val parsed = parseAddress(field.text.toString(), defaultPort)
        if (parsed == null) { status.setText(R.string.conn_error_bad_address); return }
        endEditing()
        attempt = 0                                  // a new address starts the back-off over
        onConnect(parsed.first, parsed.second)
    }

    private fun endEditing() {
        if (!field.hasFocus()) return
        (root.context.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager)
            .hideSoftInputFromWindow(field.windowToken, 0)
        field.clearFocus()
    }

    private fun retryNow() {
        handler.removeCallbacks(tick)
        onRetry()
    }

    /** [again]: restart the current step's countdown (after editing) rather than the next one. */
    private fun scheduleRetry(again: Boolean = false) {
        handler.removeCallbacks(tick)
        if (field.hasFocus()) { status.setText(R.string.conn_error_editing); return }
        if (again && attempt > 0) attempt--
        secondsLeft = RETRY_SECONDS[minOf(attempt, RETRY_SECONDS.size - 1)]
        attempt++
        if (paused) status.text = "" else tick.run()
    }

    companion object {
        private val RETRY_SECONDS = intArrayOf(5, 10, 20, 30)

        /** "192.168.1.50", "box.lan:9090", "http://10.0.0.7:9090/k/" -> (host, port);
         *  null when there is no host, or the port is not a number in 1..65535. */
        fun parseAddress(text: String, defaultPort: Int): Pair<String, Int>? {
            val s = text.trim().removePrefix("http://").removePrefix("https://").substringBefore('/')
            if (s.isEmpty() || s.any { it.isWhitespace() }) return null
            val colon = s.indexOf(':')
            if (colon < 0) return s to defaultPort
            if (colon != s.lastIndexOf(':')) return null        // one colon only: host:port
            val host = s.substring(0, colon)
            val port = s.substring(colon + 1).toIntOrNull() ?: return null
            return if (host.isEmpty() || port !in 1..65535) null else host to port
        }

        /** Maps a WebView load error to what the panel says about it.  Chromium's
         *  "net::ERR_..." [description] is finer than the code: ERROR_CONNECT covers
         *  both a refused connection (box up, panel down) and an unreachable host. */
        fun reasonFor(errorCode: Int, description: String?): Reason {
            val d = description ?: ""
            return when {
                "CONNECTION_REFUSED" in d -> Reason.REFUSED
                "NAME_NOT_RESOLVED" in d -> Reason.LOOKUP
                "TIMED_OUT" in d || "UNREACHABLE" in d -> Reason.TIMEOUT
                d.isNotEmpty() -> Reason.OTHER
                errorCode == WebViewClient.ERROR_HOST_LOOKUP -> Reason.LOOKUP
                errorCode == WebViewClient.ERROR_TIMEOUT -> Reason.TIMEOUT
                else -> Reason.OTHER
            }
        }
    }
}
