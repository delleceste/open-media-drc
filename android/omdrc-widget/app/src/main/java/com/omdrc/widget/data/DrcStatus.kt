package com.omdrc.widget.data

import org.json.JSONObject

/**
 * Parsed shape of GET /drc/brutefir-config. When DRC is off (or BruteFIR is
 * unreachable) the source JSON only carries {ok:false, running:false,
 * error:...} - none of the geometry/design/attenuation fields exist at all,
 * so every field below is optional and parsing must never throw on that
 * shape.
 */
data class DrcStatus(
    val ok: Boolean,
    val running: Boolean,
    val geometry: String?,
    val designId: String?,
    val description: String?,
    val effectiveAttenuationDb: Double?,
    val effectiveAttenuationSource: String?,
    val headroomSafe: Boolean?,
    val error: String?,
) {
    companion object {
        fun parse(json: JSONObject): DrcStatus = DrcStatus(
            ok = json.optBoolean("ok", false),
            running = json.optBoolean("running", false),
            geometry = json.optStringOrNull("geometry"),
            designId = json.optStringOrNull("design_id"),
            description = json.optStringOrNull("description"),
            effectiveAttenuationDb = json.optDoubleOrNull("effective_attenuation_db"),
            effectiveAttenuationSource = json.optStringOrNull("effective_attenuation_source"),
            headroomSafe = json.optBooleanOrNull("headroom_safe"),
            error = json.optStringOrNull("error"),
        )
    }
}

internal fun JSONObject.optStringOrNull(key: String): String? =
    if (has(key) && !isNull(key)) getString(key) else null

internal fun JSONObject.optDoubleOrNull(key: String): Double? =
    if (has(key) && !isNull(key)) getDouble(key) else null

internal fun JSONObject.optBooleanOrNull(key: String): Boolean? =
    if (has(key) && !isNull(key)) getBoolean(key) else null
