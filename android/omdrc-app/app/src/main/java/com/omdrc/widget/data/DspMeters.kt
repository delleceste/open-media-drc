package com.omdrc.widget.data

import org.json.JSONObject

/** Parsed shape of GET /drc/brutefir-rti - DSP scheduling headroom, only
 *  meaningful (and only fetched) while DRC is actually running. */
data class RtiStatus(
    val available: Boolean,
    val rti: Double?,
) {
    companion object {
        fun parse(json: JSONObject): RtiStatus = RtiStatus(
            available = json.optBoolean("available", false),
            rti = json.optDoubleOrNull("rti"),
        )
    }
}

/** Parsed shape of GET /drc/brutefir-peak - BruteFIR's own peak-hold. */
data class PeakStatus(
    val available: Boolean,
    val peakDb: Double?,
    val clipped: Boolean,
) {
    companion object {
        fun parse(json: JSONObject): PeakStatus = PeakStatus(
            available = json.optBoolean("available", false),
            peakDb = json.optDoubleOrNull("peak_db"),
            clipped = json.optBoolean("clipped", false),
        )
    }
}
