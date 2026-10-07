package dev.natro

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/** Keeps Natro connected in the background, with a quiet notification that shows whether she's online. */
class NatroService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    override fun onCreate() {
        super.onCreate()
        val notifications = getSystemService(NotificationManager::class.java)
        notifications.createNotificationChannel(
            NotificationChannel(CHANNEL, "Connection to Natro", NotificationManager.IMPORTANCE_LOW))
        ServiceCompat.startForeground(this, NOTIFICATION, notification("Connecting…"),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        Natro.start(this)
        scope.launch {
            Natro.status.collect { status ->
                val text = when (status) {
                    Natro.Status.Online -> "Connected"
                    Natro.Status.Connecting -> "Connecting…"
                    is Natro.Status.Offline -> "Offline: ${status.reason}"
                }
                notifications.notify(NOTIFICATION, notification(text))
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int) = START_STICKY

    override fun onBind(intent: Intent?) = null

    override fun onDestroy() {
        scope.cancel()
        Natro.stop()
        super.onDestroy()
    }

    private fun notification(text: String) = NotificationCompat.Builder(this, CHANNEL)
        .setSmallIcon(R.drawable.ic_natro)
        .setContentTitle("Natro")
        .setContentText(text)
        .setOngoing(true)
        .setSilent(true)
        .setContentIntent(PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE))
        .build()

    private companion object {
        const val CHANNEL = "connection"
        const val NOTIFICATION = 1
    }
}
