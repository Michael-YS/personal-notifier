package dev.personalnotify

import android.Manifest
import android.app.*
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking

class NotifyApp : Application() {
    override fun onCreate() {
        super.onCreate()
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel("alerts", "通知", NotificationManager.IMPORTANCE_HIGH)
        )
        Registration.periodic(this)
    }
}

class PushService : FirebaseMessagingService() {
    override fun onNewToken(token: String) { Registration.enqueue(this) }

    override fun onMessageReceived(message: RemoteMessage) {
        val data = message.data
        val messageId = data["id"] ?: message.messageId ?: return
        val title = data["title"] ?: return
        val body = data["body"] ?: return
        // Complete the small local write inside FCM's callback lifetime; no network work here.
        val id = runBlocking(Dispatchers.IO) {
            HistoryDatabase.get(this@PushService).history().insert(
                HistoryItem(messageId = messageId, title = title, body = body,
                    source = data["source"] ?: "", sentAt = data["sent_at"] ?: "")
            )
        }
        if (id == -1L) return // Duplicate message: keep one history row and one notification.
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
        val intent = Intent(this, MainActivity::class.java).putExtra("history_id", id)
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        val tap = PendingIntent.getActivity(this, id.toInt(), intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val notification = NotificationCompat.Builder(this, "alerts")
            .setSmallIcon(R.drawable.ic_notification).setContentTitle(title).setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body)).setContentIntent(tap)
            .setAutoCancel(true).build()
        getSystemService(NotificationManager::class.java).notify(id.toString(), 0, notification)
    }
}
