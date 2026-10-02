package it.giacomos.omdrc.app

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTimestamp
import android.media.MediaRecorder
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.os.Build
import kotlin.math.abs
import kotlin.math.log10
import kotlin.math.max

/**
 * Records the microphone for a few seconds and reduces it to a loudness envelope:
 * one peak level (dBFS) every [stepMs], with the wall-clock time (ms, the same clock
 * as JavaScript's Date.now()) of the first step.  The audio itself is never kept or
 * sent anywhere; only this envelope goes to the page, which lines it up with the
 * level meters to measure how far they run ahead of the sound.
 *
 * Timing uses AudioRecord.getTimestamp, i.e. when the samples were captured, not
 * when this code got to read them.
 */
class MicEnvelope(private val durationMs: Int, private val stepMs: Int) {

    @Volatile private var rec: AudioRecord? = null
    @Volatile private var cancelled = false

    /** Stop listening now, from any thread; record() then ends with an error.  Stopping
     *  the AudioRecord also unblocks a read() that is waiting for samples. */
    fun cancel() {
        cancelled = true
        try { rec?.stop() } catch (_: Exception) {}
    }

    data class Result(val t0WallMs: Double, val stepMs: Int, val db: FloatArray, val source: String)

    @SuppressLint("MissingPermission")    // checked by the caller
    fun record(): Result {
        val rate = 48000
        val step = rate * stepMs / 1000
        val minBuf = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        // UNPROCESSED where available: no gain control or noise suppression bending the envelope
        var source = MediaRecorder.AudioSource.MIC
        var sourceName = "mic"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            source = MediaRecorder.AudioSource.UNPROCESSED; sourceName = "unprocessed"
        }
        var rec0 = AudioRecord(source, rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, max(minBuf, step * 8))
        if (rec0.state != AudioRecord.STATE_INITIALIZED) {
            rec0.release()
            source = MediaRecorder.AudioSource.MIC; sourceName = "mic"
            rec0 = AudioRecord(source, rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, max(minBuf, step * 8))
        }
        if (rec0.state != AudioRecord.STATE_INITIALIZED) { rec0.release(); error("the microphone could not be opened") }
        val rec = rec0
        this.rec = rec
        var agc: AutomaticGainControl? = null
        var ns: NoiseSuppressor? = null
        try { if (AutomaticGainControl.isAvailable()) agc = AutomaticGainControl.create(rec.audioSessionId)?.also { it.enabled = false } } catch (_: Exception) {}
        try { if (NoiseSuppressor.isAvailable()) ns = NoiseSuppressor.create(rec.audioSessionId)?.also { it.enabled = false } } catch (_: Exception) {}

        val steps = durationMs / stepMs
        val db = FloatArray(steps)
        val buf = ShortArray(step)
        val anchors = mutableListOf<Double>()
        var fallbackWall = Double.NaN
        // Keep one wall/monotonic mapping for the entire recording.
        val wallMinusMonoMs = System.currentTimeMillis().toDouble() - System.nanoTime() / 1e6
        var lastTimestampFrame = -1L
        val ts = AudioTimestamp()
        val deadline = System.nanoTime() + (durationMs + 3000L) * 1_000_000L
        try {
            rec.startRecording()
            check(rec.recordingState == AudioRecord.RECORDSTATE_RECORDING) { "the microphone is in use by another app" }
            for (i in 0 until steps) {
                var got = 0
                while (got < step) {
                    check(!cancelled) { "microphone recording cancelled" }
                    check(System.nanoTime() < deadline) { "microphone recording ran too long" }
                    val n = rec.read(buf, got, step - got)
                    check(!cancelled) { "microphone recording cancelled" }
                    if (n <= 0) error("microphone read failed ($n)")
                    got += n
                }
                var peak = 0
                for (s in buf) peak = max(peak, abs(s.toInt()))
                db[i] = if (peak == 0) -120f else (20.0 * log10(peak / 32768.0)).toFloat()
                // Hardware timestamps often appear only after recording warms up.
                // Continue asking after the first read, and use a median of independent
                // anchors so a single startup timestamp cannot bias every calibration.
                if (i % 10 == 0 && rec.getTimestamp(ts, AudioTimestamp.TIMEBASE_MONOTONIC) == AudioRecord.SUCCESS &&
                    ts.framePosition > lastTimestampFrame && ts.nanoTime > 0) {
                    anchors.add(wallMinusMonoMs + ts.nanoTime / 1e6 - ts.framePosition * 1000.0 / rate)
                    lastTimestampFrame = ts.framePosition
                }
                if (i == 0) fallbackWall = System.currentTimeMillis() - stepMs.toDouble()

            }
        } finally {
            this.rec = null
            try { rec.stop() } catch (_: Exception) {}
            try { agc?.release() } catch (_: Exception) {}
            try { ns?.release() } catch (_: Exception) {}
            try { rec.release() } catch (_: Exception) {}
        }
        val sorted = anchors.sorted()
        val t0Wall = if (sorted.isNotEmpty()) {
            val p10 = sorted[sorted.size / 10]
            val p90 = sorted[(sorted.size * 9 / 10).coerceAtMost(sorted.lastIndex)]
            check(p90 - p10 <= 20.0) { "microphone capture timing drifted during recording; try again" }
            sourceName += "+capturetime"
            sorted[sorted.size / 2]
        } else {
            sourceName += "+readtime"
            fallbackWall
        }
        return Result(t0Wall, stepMs, db, sourceName)
    }
}
