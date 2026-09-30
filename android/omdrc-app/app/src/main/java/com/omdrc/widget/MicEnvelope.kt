package com.omdrc.widget

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
        var t0Wall = Double.NaN
        val ts = AudioTimestamp()
        val deadline = System.nanoTime() + (durationMs + 3000L) * 1_000_000L
        try {
            rec.startRecording()
            check(rec.recordingState == AudioRecord.RECORDSTATE_RECORDING) { "the microphone is in use by another app" }
            var framesRead = 0L
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
                // Anchor the capture time of sample 0 once a hardware timestamp exists.
                if (t0Wall.isNaN() && rec.getTimestamp(ts, AudioTimestamp.TIMEBASE_MONOTONIC) == AudioRecord.SUCCESS) {
                    val nowWall = System.currentTimeMillis().toDouble()
                    val nowMono = System.nanoTime()
                    val tsWall = nowWall - (nowMono - ts.nanoTime) / 1e6
                    t0Wall = tsWall - ts.framePosition * 1000.0 / rate
                }
                framesRead += step
                if (i == 0 && t0Wall.isNaN()) {
                    // no timestamp yet: assume the first step was just captured
                    t0Wall = System.currentTimeMillis() - stepMs.toDouble()
                    sourceName += "+readtime"
                }
            }
        } finally {
            this.rec = null
            try { rec.stop() } catch (_: Exception) {}
            try { agc?.release() } catch (_: Exception) {}
            try { ns?.release() } catch (_: Exception) {}
            try { rec.release() } catch (_: Exception) {}
        }
        return Result(t0Wall, stepMs, db, sourceName)
    }
}
