package it.giacomos.omdrc.app.data

/** What /now says of the playing track beyond MPD's own status: its artist
 *  and edition (label · year). */
data class PlayerStatus(
    val artist: String?,
    val edition: String?,
)
