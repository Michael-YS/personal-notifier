package dev.personalnotify

import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.URI
import java.net.URL
import javax.net.ssl.HttpsURLConnection

object Pairing {
    data class Ticket(val server: String, val code: String)
    data class Binding(val server: String, val device: String, val key: String)

    fun parse(text: String): Ticket {
        require(text.length <= 2048)
        val uri = Uri.parse(text)
        require(uri.scheme == "personalnotify" && uri.host == "pair" && uri.fragment == null)
        require(uri.getQueryParameters("server").size == 1 && uri.getQueryParameters("code").size == 1)
        val server = uri.getQueryParameter("server")!!.trimEnd('/')
        val target = URI(server)
        require(target.scheme == "https" && !target.host.isNullOrBlank() && target.userInfo == null &&
            target.query == null && target.fragment == null)
        val code = uri.getQueryParameter("code")!!
        require(code.matches(Regex("[A-Za-z0-9_-]{32,128}")))
        return Ticket(server, code)
    }

    suspend fun redeem(ticket: Ticket): Binding = withContext(Dispatchers.IO) {
        val connection = URL("${ticket.server}/pairings/redeem").openConnection() as HttpsURLConnection
        try {
            connection.requestMethod = "POST"
            connection.instanceFollowRedirects = false
            connection.connectTimeout = 15000
            connection.readTimeout = 15000
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            connection.outputStream.use {
                it.write(JSONObject().put("code", ticket.code).toString().toByteArray(Charsets.UTF_8))
            }
            check(connection.responseCode == 200)
            val buffer = ByteArray(8193)
            var count = 0
            connection.inputStream.use { input ->
                while (count < buffer.size) {
                    val read = input.read(buffer, count, buffer.size - count)
                    if (read == -1) break
                    count += read
                }
            }
            check(count <= 8192)
            val result = JSONObject(String(buffer, 0, count, Charsets.UTF_8))
            check(result.getString("server") == ticket.server)
            val device = result.getString("device")
            val key = result.getString("key")
            check(device.matches(Regex("[A-Za-z0-9_-]{1,64}")) && key.length in 32..128)
            Binding(ticket.server, device, key)
        } finally { connection.disconnect() }
    }
}
