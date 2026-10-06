package it.giacomos.omdrc.app.data

import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL

private const val TAG = "OmdrcClient"
private const val TIMEOUT_MS = 4000

object OmdrcClient {

    suspend fun fetchSnapshot(host: String, port: Int): WidgetSnapshot = withContext(Dispatchers.IO) {
        val now = System.currentTimeMillis()
        val drcJson = try {
            get(host, port, "/drc/brutefir-config")
        } catch (e: Exception) {
            Log.w(TAG, "drc/brutefir-config unreachable: ${e.message}")
            return@withContext WidgetSnapshot(
                drc = null, mpd = null, rti = null, peak = null, renderer = null,
                fetchedAtMillis = now, reachable = false,
            )
        }
        val drc = DrcStatus.parse(drcJson)

        val mpd = try {
            MpdStatus.parse(get(host, port, "/mpd/info"))
        } catch (e: Exception) {
            // Best-effort only: a failed secondary fetch must not blank out
            // a DRC status fetch that did succeed.
            Log.w(TAG, "mpd/info unreachable: ${e.message}")
            null
        }

        val services = try {
            RendererStatus.parse(get(host, port, "/qconnect/services"))
        } catch (e: Exception) {
            Log.w(TAG, "qconnect/services unreachable: ${e.message}")
            null
        }
        // qobuzconnect2mpd's own self-reported track title - what the web
        // dashboard's Renderer card shows - only fetched (and only
        // meaningful) while it's the active renderer.
        val renderer = if (services?.qobuzconnect2mpd == true) {
            try {
                val line1 = get(host, port, "/qconnect/status").optStringOrNull("line1")
                services.copy(nowPlaying = line1?.takeIf { it.isNotBlank() })
            } catch (e: Exception) {
                Log.w(TAG, "qconnect/status unreachable: ${e.message}")
                services
            }
        } else {
            services
        }

        // RTI/peak only mean anything while BruteFIR is actually running -
        // same condition the web dashboard uses to decide whether to poll
        // them at all (see loadDspGauge() in index.html).
        val rti: RtiStatus?
        val peak: PeakStatus?
        if (drc.running) {
            rti = try {
                RtiStatus.parse(get(host, port, "/drc/brutefir-rti"))
            } catch (e: Exception) {
                Log.w(TAG, "brutefir-rti unreachable: ${e.message}")
                null
            }
            peak = try {
                PeakStatus.parse(get(host, port, "/drc/brutefir-peak"))
            } catch (e: Exception) {
                Log.w(TAG, "brutefir-peak unreachable: ${e.message}")
                null
            }
        } else {
            rti = null
            peak = null
        }

        WidgetSnapshot(
            drc = drc, mpd = mpd, rti = rti, peak = peak, renderer = renderer,
            fetchedAtMillis = now, reachable = true,
        )
    }

    /** Current track's cover art, straight from GET /qconnect/art (same
     *  image the kiosk UI's "Now"/cover pages show) - null on 404 (nothing
     *  playing / no art) or any transport failure, both best-effort like
     *  the other secondary fetches above. */
    suspend fun fetchArt(host: String, port: Int): ByteArray? = withContext(Dispatchers.IO) {
        val url = URL("http://$host:$port/qconnect/art")
        val connection = url.openConnection() as HttpURLConnection
        connection.connectTimeout = TIMEOUT_MS
        connection.readTimeout = TIMEOUT_MS
        connection.requestMethod = "GET"
        try {
            if (connection.responseCode != HttpURLConnection.HTTP_OK) return@withContext null
            connection.inputStream.use { it.readBytes() }
        } catch (e: Exception) {
            Log.w(TAG, "qconnect/art unreachable: ${e.message}")
            null
        } finally {
            connection.disconnect()
        }
    }

    /** POST /k/api/transport {"action": "play"|"pause"} - the same endpoint
     *  the kiosk touchscreen UI's transport buttons use. No "toggle" action
     *  exists server-side; the caller decides play vs pause from the last
     *  known MPD state. Returns whether the box accepted it; the caller's
     *  own refresh right afterward is what reconciles the widget with
     *  whatever actually happened. */
    suspend fun sendTransport(host: String, port: Int, action: String): Boolean = withContext(Dispatchers.IO) {
        val url = URL("http://$host:$port/k/api/transport")
        val connection = url.openConnection() as HttpURLConnection
        connection.connectTimeout = TIMEOUT_MS
        connection.readTimeout = TIMEOUT_MS
        connection.requestMethod = "POST"
        connection.doOutput = true
        connection.setRequestProperty("Content-Type", "application/json")
        try {
            connection.outputStream.use {
                it.write(JSONObject(mapOf("action" to action)).toString().toByteArray(Charsets.UTF_8))
            }
            val ok = connection.responseCode in 200..299
            if (!ok) Log.w(TAG, "transport($action) failed: HTTP ${connection.responseCode}")
            ok
        } catch (e: Exception) {
            Log.w(TAG, "transport($action) unreachable: ${e.message}")
            false
        } finally {
            connection.disconnect()
        }
    }

    private fun get(host: String, port: Int, path: String): JSONObject {
        val url = URL("http://$host:$port$path")
        val connection = url.openConnection() as HttpURLConnection
        connection.connectTimeout = TIMEOUT_MS
        connection.readTimeout = TIMEOUT_MS
        connection.requestMethod = "GET"
        try {
            val body = BufferedReader(InputStreamReader(connection.inputStream)).use { it.readText() }
            return JSONObject(body)
        } catch (e: SocketTimeoutException) {
            throw e
        } finally {
            connection.disconnect()
        }
    }
}
