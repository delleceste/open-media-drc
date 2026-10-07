package it.giacomos.omdrc.app

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import java.net.Inet4Address

/**
 * Which network the phone is on, as the key AppPrefs remembers a box
 * against: the dashboard and the widgets both go to the box last used on
 * the current network, so one phone follows its owner between boxes on
 * different networks (home, office).
 */
object ServerNetwork {
    /** Wi-Fi names by network fingerprint, learnt by MainActivity (reading
     *  the name is a location access, so it isn't done from here). */
    fun names(context: Context) = context.getSharedPreferences("wifi-names", Context.MODE_PRIVATE)

    /** The network's gateway address + subnet, readable without location. */
    fun fingerprint(cm: ConnectivityManager, net: Network?): String? {
        val lp = cm.getLinkProperties(net) ?: return null
        val gw = lp.routes.firstOrNull { it.isDefaultRoute && it.gateway is Inet4Address }?.gateway?.hostAddress ?: return null
        val addr = lp.linkAddresses.firstOrNull { it.address is Inet4Address } ?: return null
        val prefix = addr.prefixLength
        val ip = addr.address.address
        val mask = if (prefix == 0) 0 else -1 shl (32 - prefix)
        val n = ((ip[0].toInt() and 255) shl 24 or ((ip[1].toInt() and 255) shl 16) or
            ((ip[2].toInt() and 255) shl 8) or (ip[3].toInt() and 255)) and mask
        return "$gw/$prefix/${Integer.toHexString(n)}"
    }

    /** "wifi:<name>" once the Wi-Fi's name is known, else by fingerprint;
     *  null off Wi-Fi and Ethernet. */
    fun currentKey(context: Context): String? {
        val cm = context.getSystemService(ConnectivityManager::class.java)
        val net = cm.activeNetwork ?: return null
        val caps = cm.getNetworkCapabilities(net) ?: return null
        val fingerprint = fingerprint(cm, net) ?: return null
        return when {
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> {
                val name = names(context).getString(fingerprint, null)
                if (name != null) "wifi:$name" else "wifi-network:$fingerprint"
            }
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet:$fingerprint"
            else -> null
        }
    }

    /** The box last used on this network, if any. */
    fun currentServer(context: Context): Pair<String, Int>? =
        currentKey(context)?.let { AppPrefs.networkServer(context, it) }
}
