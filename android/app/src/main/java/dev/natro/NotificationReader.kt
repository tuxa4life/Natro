package dev.natro

import android.app.Notification
import android.content.pm.PackageManager
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Reads the phone's notifications for "what did I miss?". The owner turns it on in Settings >
 * Notification access (the Natro app has a button). Everything it reads is personal: only Claude
 * gets it.
 */
class NotificationReader : NotificationListenerService() {
    override fun onListenerConnected() {
        current = this
    }

    override fun onListenerDisconnected() {
        if (current === this) current = null
    }

    /** The notifications on screen now, newest first, as text. */
    fun describe(limit: Int = 25): String {
        val shown = activeNotifications.orEmpty()
            .filter { !it.isOngoing && it.packageName != packageName && text(it).isNotBlank() }
            .sortedByDescending { it.postTime }
        if (shown.isEmpty()) return "No notifications."
        val clock = SimpleDateFormat("EEE HH:mm", Locale.US)
        val lines = shown.take(limit).map { "- ${clock.format(Date(it.postTime))} ${appName(it.packageName)}: ${text(it)}" }
        val more = if (shown.size > limit) "\n(and ${shown.size - limit} more)" else ""
        return lines.joinToString("\n") + more
    }

    private fun text(item: StatusBarNotification): String {
        val extras = item.notification.extras
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val body = (extras.getCharSequence(Notification.EXTRA_BIG_TEXT) ?: extras.getCharSequence(Notification.EXTRA_TEXT))
            ?.toString().orEmpty()
        val lines = extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)?.joinToString(" / ").orEmpty()
        return Text.short(listOf(title, body, lines).filter { it.isNotBlank() }.joinToString(": "), 300)
    }

    private fun appName(pkg: String): String = try {
        packageManager.getApplicationLabel(packageManager.getApplicationInfo(pkg, 0)).toString()
    } catch (e: PackageManager.NameNotFoundException) {
        pkg
    }

    companion object {
        @Volatile var current: NotificationReader? = null
    }
}
