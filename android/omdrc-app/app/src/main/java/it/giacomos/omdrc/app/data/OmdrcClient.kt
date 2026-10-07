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

// The box fetches a cover from Qobuz before answering, the first time.
private const val ART_TIMEOUT_MS = 10000

sealed interface NowFetch {
    class Found(val now: NowState) : NowFetch
    object Unsupported : NowFetch
    object Failed : NowFetch
}

sealed interface ArtFetch {
    class Found(val bytes: ByteArray) : ArtFetch
    object None : ArtFetch
    object Failed : ArtFetch
}

object OmdrcClient {

    /** What a widget shows, from one /now request (plus RTI and peak while
     *  DRC runs); a box without /now is asked the older way. */
    suspend fun fetchSnapshot(host: String, port: Int): WidgetSnapshot {
        return when (val fetched = fetchNow(host, port, null, 0)) {
            is NowFetch.Found -> snapshotFromNow(host, port, fetched.now)
            NowFetch.Failed -> WidgetSnapshot(
                drc = null, mpd = null, rti = null, peak = null, renderer = null,
                fetchedAtMillis = System.currentTimeMillis(), reachable = false,
            )
            NowFetch.Unsupported -> fetchLegacySnapshot(host, port)
        }
    }

    private suspend fun snapshotFromNow(host: String, port: Int, now: NowState): WidgetSnapshot =
        withContext(Dispatchers.IO) {
            val music = now.music
            val drc = DrcStatus(
                ok = true,
                running = now.drc.running,
                geometry = now.drc.geometry,
                designId = null,
                description = now.drc.description,
                effectiveAttenuationDb = now.drc.attenuationDb,
                effectiveAttenuationSource = null,
                headroomSafe = now.drc.headroomSafe,
                error = null,
            )
            // A loaded queue is a title or the card's line, even stopped.
            val song = music.title.ifEmpty { music.line1 }.ifEmpty { null }
            val mpd = MpdStatus(
                ok = true,
                state = when (music.state) {
                    "play" -> "playing"
                    "pause" -> "paused"
                    else -> "stopped"
                },
                song = song,
                title = music.title.ifEmpty { null },
                album = music.album.ifEmpty { null },
                sampleRate = music.rate,
                bitDepth = music.bits,
                brutefirRate = null,
            )
            val renderer = RendererStatus(
                qobuzconnect2mpd = music.renderer.contains("qobuzconnect2mpd"),
                upmpdcli = music.renderer.contains("upmpdcli"),
                nowPlaying = music.line1.ifEmpty { null },
            )
            var rti: RtiStatus? = null
            var peak: PeakStatus? = null
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
            }
            WidgetSnapshot(
                drc = drc, mpd = mpd, rti = rti, peak = peak, renderer = renderer,
                fetchedAtMillis = System.currentTimeMillis(), reachable = true,
                player = PlayerStatus(
                    artist = music.artist.ifEmpty { null },
                    edition = music.edition.ifEmpty { null },
                ),
                artPath = music.art,
            )
        }

    private suspend fun fetchLegacySnapshot(host: String, port: Int): WidgetSnapshot = withContext(Dispatchers.IO) {
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
     *  image the kiosk UI's "Now"/cover pages show). Only a 404 means
     *  there's no art; a 502 (the box couldn't fetch the cover itself) or a
     *  timeout - the box may be downloading it from Qobuz first - is a
     *  failed fetch, which mustn't clear the art cached last time. */
    suspend fun fetchArt(host: String, port: Int): ArtFetch = fetchImage(host, port, "/qconnect/art")

    /** An image the box serves at [path] (a cover, a film's poster): None
     *  on 404, Failed on any other answer or no answer. */
    suspend fun fetchImage(host: String, port: Int, path: String): ArtFetch = withContext(Dispatchers.IO) {
        val url = URL("http://$host:$port$path")
        val connection = url.openConnection() as HttpURLConnection
        connection.connectTimeout = TIMEOUT_MS
        connection.readTimeout = ART_TIMEOUT_MS
        connection.requestMethod = "GET"
        try {
            when (connection.responseCode) {
                HttpURLConnection.HTTP_OK -> ArtFetch.Found(connection.inputStream.use { it.readBytes() })
                HttpURLConnection.HTTP_NOT_FOUND -> ArtFetch.None
                else -> ArtFetch.Failed
            }
        } catch (e: Exception) {
            Log.w(TAG, "$path unreachable: ${e.message}")
            ArtFetch.Failed
        } finally {
            connection.disconnect()
        }
    }

    /** GET /now. With [since], the box holds the answer until its state no
     *  longer has that token, or [waitS] seconds pass - so this call can
     *  block that long. Unsupported: a box without /now (older panel). */
    suspend fun fetchNow(host: String, port: Int, since: String?, waitS: Int): NowFetch = withContext(Dispatchers.IO) {
        val query = if (since != null) "?since=$since&wait=$waitS" else ""
        val url = URL("http://$host:$port/now$query")
        val connection = url.openConnection() as HttpURLConnection
        connection.connectTimeout = TIMEOUT_MS
        connection.readTimeout = TIMEOUT_MS + waitS * 1000
        connection.requestMethod = "GET"
        try {
            when (connection.responseCode) {
                HttpURLConnection.HTTP_OK -> {
                    val body = BufferedReader(InputStreamReader(connection.inputStream)).use { it.readText() }
                    NowFetch.Found(NowState.parse(JSONObject(body)))
                }
                HttpURLConnection.HTTP_NOT_FOUND -> NowFetch.Unsupported
                else -> NowFetch.Failed
            }
        } catch (e: Exception) {
            Log.w(TAG, "now unreachable: ${e.message}")
            NowFetch.Failed
        } finally {
            connection.disconnect()
        }
    }

    /** POST /k/api/transport {"action": "play"|"pause"|"next"|"prev"} - the same endpoint
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
