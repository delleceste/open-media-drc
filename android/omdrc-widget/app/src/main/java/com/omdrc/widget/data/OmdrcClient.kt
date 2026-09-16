package com.omdrc.widget.data

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
                drc = null, mpd = null, rti = null, peak = null,
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

        WidgetSnapshot(drc = drc, mpd = mpd, rti = rti, peak = peak, fetchedAtMillis = now, reachable = true)
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
