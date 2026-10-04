package dev.personalnotify

import android.content.Context
import androidx.work.*
import com.google.firebase.messaging.FirebaseMessaging
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.URI
import java.net.URL
import java.util.concurrent.TimeUnit
import javax.net.ssl.HttpsURLConnection

class AppSettings(context: Context) {
    private val prefs = context.getSharedPreferences("settings", Context.MODE_PRIVATE)
    val server: String get() = prefs.getString("server", "")!!
    val device: String get() = prefs.getString("device", "my-phone")!!
    val key: String get() = prefs.getString("key", "")!!
    val status: String get() = prefs.getString("status", "尚未绑定")!!
    val ready: Boolean get() = server.isNotBlank() && key.isNotBlank()

    fun save(server: String, device: String, key: String) {
        val uri = URI(server)
        require(uri.scheme == "https" && !uri.host.isNullOrBlank() &&
            uri.userInfo == null && uri.query == null && uri.fragment == null) {
            "服务端地址必须是 HTTPS URL，且不能包含账号、查询参数或片段"
        }
        require(device.matches(Regex("[A-Za-z0-9_-]{1,64}"))) { "设备名只能包含字母、数字、下划线或短横线" }
        require(key.length >= 32) { "绑定密钥至少需要 32 个字符" }
        prefs.edit().putString("server", server.trimEnd('/')).putString("device", device)
            .putString("key", key).putString("status", "等待注册").apply()
    }

    fun status(value: String) { prefs.edit().putString("status", value).apply() }
}

class RegistrationWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val settings = AppSettings(applicationContext)
        if (!settings.ready) return Result.success()
        val server = settings.server
        val device = settings.device
        val key = settings.key
        return try {
            val token = FirebaseMessaging.getInstance().token.await()
            val code = withContext(Dispatchers.IO) {
                val connection = URL("$server/devices/$device").openConnection() as HttpsURLConnection
                try {
                    connection.requestMethod = "PUT"
                    connection.instanceFollowRedirects = false
                    connection.connectTimeout = 15000
                    connection.readTimeout = 15000
                    connection.doOutput = true
                    connection.setRequestProperty("Authorization", "Bearer $key")
                    connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                    connection.outputStream.use { it.write(JSONObject().put("token", token).toString().toByteArray(Charsets.UTF_8)) }
                    connection.responseCode
                } finally { connection.disconnect() }
            }
            when {
                code in 200..299 -> {
                    settings.status("已绑定 · ${java.text.DateFormat.getDateTimeInstance().format(java.util.Date())}")
                    Result.success()
                }
                code == 429 || code >= 500 -> {
                    settings.status("服务端暂不可用，将自动重试（HTTP $code）")
                    Result.retry()
                }
                else -> {
                    settings.status("注册失败（HTTP $code），请检查地址、设备名和绑定密钥")
                    Result.failure()
                }
            }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: Exception) {
            settings.status("网络或 Firebase 暂不可用，将自动重试")
            Result.retry()
        }
    }
}

object Registration {
    private fun constraints() = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
    fun enqueue(context: Context) {
        if (!AppSettings(context).ready) return
        WorkManager.getInstance(context).enqueueUniqueWork(
            "register-device", ExistingWorkPolicy.APPEND_OR_REPLACE,
            OneTimeWorkRequestBuilder<RegistrationWorker>().setConstraints(constraints())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS).build()
        )
    }
    fun periodic(context: Context) {
        if (!AppSettings(context).ready) return
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            "refresh-device", ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<RegistrationWorker>(1, TimeUnit.DAYS)
                .setConstraints(constraints()).build()
        )
    }
}
