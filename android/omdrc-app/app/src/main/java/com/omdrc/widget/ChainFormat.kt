package com.omdrc.widget

import com.omdrc.widget.data.MpdStatus

/**
 * "24/96000 → 96000 Hz" style source->output bitrate chain - only shown
 * while DRC is actually running (BruteFIR is the thing doing the rate
 * conversion/passthrough) and only when MPD reports a source rate/depth.
 * Shared between the widget and the status notification so the two can't
 * drift apart.
 */
object ChainFormat {
    fun bitrateChain(mpd: MpdStatus?, drcRunning: Boolean): String? {
        if (!drcRunning || mpd == null) return null
        val bits = mpd.bitDepth ?: return null
        val srcRate = mpd.sampleRate ?: return null
        val outRate = mpd.brutefirRate ?: return null
        return "$bits/$srcRate → $outRate Hz"
    }
}
