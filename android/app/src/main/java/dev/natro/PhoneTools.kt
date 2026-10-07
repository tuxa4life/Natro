package dev.natro

import android.Manifest
import android.app.AlarmManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.ContactsContract.CommonDataKinds.Phone
import android.provider.Telephony
import android.telephony.SmsManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioManager
import android.provider.AlarmClock
import android.view.KeyEvent
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

/**
 * The phone's tools, as Natro's brain sees them (sent in the hello; see agent/natro_agent/server.py).
 *
 * Apps, alarms, media, flashlight and volume are neither personal nor risky, so the free Gemini
 * tier may use them too. Notifications, contacts, texts and the screen are personal (Claude only);
 * calling, texting and risky taps need the owner's yes. Android lets an app open other apps (and
 * the clock app, calls...) from the background only while it is the phone's assistant or its
 * accessibility service is on; otherwise only while it is on screen.
 */
class PhoneTools(private val context: Context) {
    private class Tool(
        val name: String,
        val description: String,
        val properties: JSONObject,
        val required: List<String>,
        val personal: Boolean = false,
        val confirm: Boolean = false,
        // Its result is a fine spoken reply on its own ("Flashlight on."): the brain may skip wording one.
        val final: Boolean = false,
        val run: (JSONObject) -> String,
    )

    private val audio = context.getSystemService(AudioManager::class.java)
    private val cameras = context.getSystemService(CameraManager::class.java)

    private val tools = listOf(
        Tool("open_app", "Open an app on the phone by name.", props("app" to string("The app's name, as he says it.")),
            listOf("app"), final = true) { openApp(it.getString("app")) },
        Tool("set_alarm", "Set an alarm in the phone's clock app.", props(
            "time" to string("24-hour HH:MM, like 07:30."),
            "label" to string("What it's for (optional)."),
            "days" to JSONObject().put("type", "array").put("items", string("mon, tue, wed, thu, fri, sat or sun"))
                .put("description", "Repeat on these days (optional)."),
        ), listOf("time"), final = true) { setAlarm(it.getString("time"), it.optString("label"), it.optJSONArray("days")) },
        Tool("set_timer", "Start a timer in the phone's clock app.", props(
            "seconds" to JSONObject().put("type", "integer"), "label" to string("What it's for (optional)."),
        ), listOf("seconds"), final = true) { setTimer(it.getInt("seconds"), it.optString("label")) },
        Tool("next_alarm", "When the phone's next alarm goes off.", props(), listOf()) { nextAlarm() },
        Tool("media", "Control whatever is playing on the phone (any app): play, pause, next or previous.",
            props("action" to JSONObject().put("type", "string")
                .put("enum", JSONArray(listOf("play", "pause", "next", "previous")))), listOf("action"), final = true) {
            media(it.getString("action"))
        },
        Tool("flashlight", "Turn the phone's flashlight on or off.", props("on" to JSONObject().put("type", "boolean")),
            listOf("on"), final = true) { flashlight(it.getBoolean("on")) },
        Tool("volume", "Set a phone volume: media (music, videos), ring, alarm or notification.", props(
            "stream" to JSONObject().put("type", "string")
                .put("enum", JSONArray(listOf("media", "ring", "alarm", "notification"))),
            "level" to string("0 to 100, or up, down, max or mute."),
        ), listOf("level"), final = true) {
            volume(it.optString("stream", "media").ifEmpty { "media" }, it.getString("level"))
        },

        Tool("notifications", "The phone's notifications right now, newest first (messages, missed calls, apps).",
            props(), listOf(), personal = true) { notifications() },
        Tool("contacts", "Find people in the phone's contacts by name (saved in Georgian or Latin letters): their " +
            "numbers.", props("name" to string("The name, as he says it.")), listOf("name"), personal = true) {
            contacts(it.getString("name"))
        },
        Tool("call", "Call a phone number (find it with contacts first).", props("number" to string("The number.")),
            listOf("number"), personal = true, confirm = true) { call(it.getString("number")) },
        Tool("send_sms", "Send a text message (SMS) from the phone.", props(
            "number" to string("The number (find it with contacts first)."), "text" to string("The message."),
        ), listOf("number", "text"), personal = true, confirm = true) {
            sendSms(it.getString("number"), it.getString("text"))
        },
        Tool("read_sms", "Recent text messages on the phone, newest first, optionally only with one person.", props(
            "with" to string("A name or number (optional)."),
            "count" to JSONObject().put("type", "integer").put("description", "Default 10."),
        ), listOf(), personal = true) { readSms(it.optString("with"), it.optInt("count", 10)) },

        Tool("screen", "Read what's on the phone's screen now: numbered elements to tap, type into or scroll.",
            props(), listOf(), personal = true) { screen().describe() },
        Tool("tap", "Tap a screen element by its number; returns the new screen. Refuses buttons that send, buy, pay " +
            "or delete: those need tap_risky.", props("element" to integer()), listOf("element"), personal = true) {
            screen().tap(it.getInt("element"), risky = false)
        },
        Tool("tap_risky", "Tap a button that sends, buys, pays or deletes (asks him first).",
            props("element" to integer()), listOf("element"), personal = true, confirm = true) {
            screen().tap(it.getInt("element"), risky = true)
        },
        Tool("type_text", "Type text into a field on screen (replaces what's in it); returns the new screen.",
            props("element" to integer(), "text" to string("The text.")), listOf("element", "text"), personal = true) {
            screen().type(it.getInt("element"), it.getString("text"))
        },
        Tool("scroll", "Scroll the screen (or one list on it) down or up; returns the new screen.", props(
            "direction" to JSONObject().put("type", "string").put("enum", JSONArray(listOf("down", "up"))),
            "element" to integer(),
        ), listOf("direction"), personal = true) {
            screen().scroll(it.getString("direction") == "down", if (it.has("element")) it.getInt("element") else null)
        },
        Tool("press", "Press back or home, or open recents, the notification shade or quick settings.", props(
            "key" to JSONObject().put("type", "string")
                .put("enum", JSONArray(listOf("back", "home", "recents", "notifications", "quick_settings"))),
        ), listOf("key"), personal = true) { screen().press(it.getString("key")) },
    )

    /** The tools as the hello describes them. */
    fun specs(): JSONArray = JSONArray(tools.map { tool ->
        JSONObject().put("name", tool.name).put("description", tool.description).put("personal", tool.personal)
            .put("confirm", tool.confirm).put("final", tool.final).put("input_schema", JSONObject().put("type", "object")
                .put("properties", tool.properties).put("required", JSONArray(tool.required)))
    })

    fun run(name: String, args: JSONObject): String {
        val tool = tools.firstOrNull { it.name == name } ?: throw ToolFailure("The phone has no tool called $name.")
        return tool.run(args)
    }

    private fun start(intent: Intent, what: String) {
        try {
            context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: ActivityNotFoundException) {
            throw ToolFailure("The phone has no app that can $what.")
        }
    }

    /** Said after starting something on screen, which Android may block while Natro is in the background. */
    private fun ifAllowed(done: String) =
        if (Natro.canOpenScreens(context)) done else "$done (Natro isn't on screen, so Android may have blocked it.)"

    private fun need(permission: String, what: String) {
        if (context.checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) {
            throw ToolFailure("Natro isn't allowed to $what on the phone yet: he can allow it in the Natro app.")
        }
    }

    private fun screen() = ScreenControl.current
        ?: throw ToolFailure("Screen control is off: he can turn it on in Settings > Accessibility > Natro.")

    private fun notifications() = (NotificationReader.current
        ?: throw ToolFailure("Natro can't read notifications yet: he can allow notification access in the Natro app."))
        .describe()

    /** (name, number, kind) for every phone number in the contacts. */
    private fun allNumbers(): List<Triple<String, String, String>> {
        need(Manifest.permission.READ_CONTACTS, "read contacts")
        val found = mutableListOf<Triple<String, String, String>>()
        context.contentResolver.query(Phone.CONTENT_URI, arrayOf(Phone.DISPLAY_NAME, Phone.NUMBER, Phone.TYPE, Phone.LABEL),
            null, null, null)?.use { rows ->
            while (rows.moveToNext()) {
                val kind = Phone.getTypeLabel(context.resources, rows.getInt(2), rows.getString(3)).toString().lowercase()
                found += Triple(rows.getString(0).orEmpty(), rows.getString(1).orEmpty(), kind)
            }
        }
        return found
    }

    private fun contacts(name: String): String {
        val everyone = allNumbers()
        val matches = everyone.map { it to Names.score(name, Text.latin(it.first)) }
            .filter { it.second >= Names.GOOD_MATCH }.sortedByDescending { it.second }.map { it.first }
        if (matches.isEmpty()) {
            val close = Names.closest(Text.latin(name), everyone.map { Text.latin(it.first) }.distinct(), 5)
            throw ToolFailure("No contact called '$name'. The closest names: ${close.joinToString(", ")}.")
        }
        return matches.distinct().take(15).joinToString("\n") { (who, number, kind) -> "- $who: $number ($kind)" }
    }

    private fun digits(number: String): String {
        val dialable = number.filter { it.isDigit() || it == '+' }
        if (dialable.length < 3) throw ToolFailure("'$number' isn't a phone number: find it with contacts.")
        return dialable
    }

    private fun call(number: String): String {
        need(Manifest.permission.CALL_PHONE, "make calls")
        start(Intent(Intent.ACTION_CALL, Uri.parse("tel:${digits(number)}")), "make calls")
        return ifAllowed("Calling $number.")
    }

    private fun sendSms(number: String, text: String): String {
        need(Manifest.permission.SEND_SMS, "send texts")
        if (text.isBlank()) throw ToolFailure("The message is empty.")
        val sms = context.getSystemService(SmsManager::class.java)
        sms.sendMultipartTextMessage(digits(number), null, sms.divideMessage(text), null, null)
        return "Sent the text to $number."
    }

    private fun readSms(with: String, count: Int): String {
        need(Manifest.permission.READ_SMS, "read texts")
        val names = try {
            allNumbers().associate { (who, number, _) -> number.filter(Char::isDigit).takeLast(9) to who }
        } catch (e: ToolFailure) {
            emptyMap()
        }
        val wantedDigits = with.filter(Char::isDigit)
        val clock = SimpleDateFormat("EEE d MMM HH:mm", Locale.US)
        val lines = mutableListOf<String>()
        context.contentResolver.query(Telephony.Sms.CONTENT_URI, arrayOf(Telephony.Sms.ADDRESS, Telephony.Sms.BODY,
            Telephony.Sms.DATE, Telephony.Sms.TYPE), null, null, "${Telephony.Sms.DATE} DESC")?.use { rows ->
            while (rows.moveToNext() && lines.size < count.coerceIn(1, 30)) {
                val address = rows.getString(0).orEmpty()
                val who = names[address.filter(Char::isDigit).takeLast(9)] ?: address
                val wanted = with.isBlank() || Names.score(with, Text.latin(who)) >= Names.GOOD_MATCH ||
                    (wantedDigits.length >= 6 && address.filter(Char::isDigit).endsWith(wantedDigits.takeLast(9)))
                if (!wanted) continue
                val direction = if (rows.getInt(3) == Telephony.Sms.MESSAGE_TYPE_SENT) "to" else "from"
                lines += "- ${clock.format(Date(rows.getLong(2)))} $direction $who: " +
                    Text.short(rows.getString(1).orEmpty(), 300)
            }
        }
        return lines.joinToString("\n").ifEmpty { "No texts found." }
    }

    private fun openApp(app: String): String {
        val pm = context.packageManager
        val launchers = pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0)
        val apps = launchers.associate { it.loadLabel(pm).toString() to it.activityInfo.packageName }
        val name = Names.best(app, apps.keys.toList())
            ?: throw ToolFailure("No app called '$app' on the phone. The closest: " +
                Names.closest(app, apps.keys.toList()).joinToString(", ") + ".")
        val intent = pm.getLaunchIntentForPackage(apps.getValue(name)) ?: throw ToolFailure("$name can't be opened.")
        start(intent, "open $name")
        return ifAllowed("Opened $name.")
    }

    private fun setAlarm(time: String, label: String, days: JSONArray?): String {
        val (hour, minute) = Names.time(time)
        val intent = Intent(AlarmClock.ACTION_SET_ALARM)
            .putExtra(AlarmClock.EXTRA_HOUR, hour)
            .putExtra(AlarmClock.EXTRA_MINUTES, minute)
            .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
        if (label.isNotBlank()) intent.putExtra(AlarmClock.EXTRA_MESSAGE, label)
        val repeat = (0 until (days?.length() ?: 0)).mapNotNull { DAYS[days!!.getString(it).take(3).lowercase()] }
        if (repeat.isNotEmpty()) intent.putExtra(AlarmClock.EXTRA_DAYS, ArrayList(repeat))
        start(intent, "set alarms")
        return ifAllowed("Set an alarm for ${"%02d:%02d".format(hour, minute)}.")
    }

    private fun setTimer(seconds: Int, label: String): String {
        if (seconds !in 1..86_400) throw ToolFailure("A timer is 1 second to 24 hours.")
        val intent = Intent(AlarmClock.ACTION_SET_TIMER)
            .putExtra(AlarmClock.EXTRA_LENGTH, seconds)
            .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
        if (label.isNotBlank()) intent.putExtra(AlarmClock.EXTRA_MESSAGE, label)
        start(intent, "set timers")
        return ifAllowed("Timer set for ${Names.duration(seconds)}.")
    }

    private fun nextAlarm(): String {
        val next = context.getSystemService(AlarmManager::class.java).nextAlarmClock ?: return "No alarm is set."
        return "The next alarm is ${SimpleDateFormat("EEE d MMM, HH:mm", Locale.US).format(Date(next.triggerTime))}."
    }

    private fun media(action: String): String {
        val key = mapOf(
            "play" to KeyEvent.KEYCODE_MEDIA_PLAY, "pause" to KeyEvent.KEYCODE_MEDIA_PAUSE,
            "next" to KeyEvent.KEYCODE_MEDIA_NEXT, "previous" to KeyEvent.KEYCODE_MEDIA_PREVIOUS,
        )[action] ?: throw ToolFailure("The action is play, pause, next or previous.")
        audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, key))
        audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, key))
        return mapOf("play" to "Playing.", "pause" to "Paused.", "next" to "Next track.",
            "previous" to "Previous track.").getValue(action)
    }

    private fun flashlight(on: Boolean): String {
        val camera = cameras.cameraIdList.firstOrNull {
            cameras.getCameraCharacteristics(it).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true
        } ?: throw ToolFailure("The phone has no flashlight.")
        try {
            cameras.setTorchMode(camera, on)
        } catch (e: Exception) {
            throw ToolFailure("The flashlight is busy (maybe the camera is in use).")
        }
        return "Flashlight ${if (on) "on" else "off"}."
    }

    private fun volume(stream: String, level: String): String {
        val type = STREAMS[stream] ?: throw ToolFailure("The stream is media, ring, alarm or notification.")
        val max = audio.getStreamMaxVolume(type)
        val current = audio.getStreamVolume(type) * 100 / max
        val percent = Names.level(level, current)
        try {
            audio.setStreamVolume(type, Math.round(percent * max / 100f), AudioManager.FLAG_SHOW_UI)
        } catch (e: SecurityException) {
            throw ToolFailure("Android won't change the $stream volume while Do Not Disturb is on.")
        }
        return "${stream.replaceFirstChar { it.uppercase() }} volume $percent%."
    }

    private companion object {
        val DAYS = mapOf(
            "mon" to Calendar.MONDAY, "tue" to Calendar.TUESDAY, "wed" to Calendar.WEDNESDAY,
            "thu" to Calendar.THURSDAY, "fri" to Calendar.FRIDAY, "sat" to Calendar.SATURDAY, "sun" to Calendar.SUNDAY,
        )
        val STREAMS = mapOf(
            "media" to AudioManager.STREAM_MUSIC, "ring" to AudioManager.STREAM_RING,
            "alarm" to AudioManager.STREAM_ALARM, "notification" to AudioManager.STREAM_NOTIFICATION,
        )

        fun string(description: String): JSONObject = JSONObject().put("type", "string").put("description", description)

        fun integer(): JSONObject = JSONObject().put("type", "integer")

        fun props(vararg entries: Pair<String, JSONObject>): JSONObject =
            JSONObject().apply { entries.forEach { (key, value) -> put(key, value) } }
    }
}
