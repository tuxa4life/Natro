package dev.natro

import android.app.role.RoleManager
import android.content.Context
import android.media.AudioManager
import android.media.ToneGenerator
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

/**
 * Natro on the phone: the connection to her brain on the VPS (over Tailscale), what the screen shows,
 * and the phone's tools.
 *
 * One for the whole app, kept alive by NatroService, so the connection stays open with the screen
 * off and the brain can use the phone's tools even for a request made on the PC. The messages are
 * the ones in agent/natro_agent/server.py.
 */
object Natro {
    enum class Who { YOU, NATRO, TOOL, NOTE }

    /** A line on screen. ref ties it to a request or message id, so streamed pieces land in it. */
    data class Line(val key: Long, val ref: String, val who: Who, val text: String)

    data class Question(val id: String, val text: String, val details: String)

    sealed interface Status {
        data object Connecting : Status
        data object Online : Status
        data class Offline(val reason: String) : Status
    }

    val status = MutableStateFlow<Status>(Status.Offline("not started"))
    val lines = MutableStateFlow<List<Line>>(emptyList())
    val question = MutableStateFlow<Question?>(null)
    val recording = MutableStateFlow(false)
    val speakReplies = MutableStateFlow(true)
    /** Whether the wake word is on (ListenService). */
    val listening = MutableStateFlow(false)

    /** Whether the app is on screen. */
    @Volatile var onScreen = false

    /** Whether Natro is speaking a reply. */
    val speaking get() = speaker?.speaking == true

    /** Whether Android lets Natro open apps and other screens now: on screen, as the assistant, or with screen control on. */
    fun canOpenScreens(context: Context) = onScreen || ScreenControl.current != null ||
        context.getSystemService(RoleManager::class.java).isRoleHeld(RoleManager.ROLE_ASSISTANT)

    private val client = OkHttpClient.Builder().pingInterval(30, TimeUnit.SECONDS).build()
    private val keys = AtomicLong()
    private val requestIds = AtomicLong()
    private var scope: CoroutineScope? = null
    private var socket: WebSocket? = null
    private var speaker: Speaker? = null
    private var tools: PhoneTools? = null
    private var cacheDir: File? = null
    private val recorder = Recorder()
    private var retryMillis = RETRY_FIRST_MILLIS
    @Volatile private var refused = false

    fun start(context: Context) {
        if (scope != null) return
        val app = context.applicationContext
        cacheDir = app.cacheDir
        scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        speaker = Speaker(app)
        tools = PhoneTools(app)
        refused = false
        connect()
    }

    fun stop() {
        scope?.cancel()
        scope = null
        socket?.close(1000, "stopped")
        socket = null
        speaker?.shutdown()
        speaker = null
        status.value = Status.Offline("stopped")
    }

    // The connection

    private fun connect() {
        status.value = Status.Connecting
        socket = client.newWebSocket(Request.Builder().url(BuildConfig.NATRO_SERVER).build(), Listener())
    }

    private class Listener : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            webSocket.send(JSONObject().put("type", "hello").put("device", "phone").put("label", "phone")
                .put("token", BuildConfig.NATRO_TOKEN).put("tools", tools?.specs()).toString())
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (webSocket === socket) handle(JSONObject(text))
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            if (code == 1008) refused = true  // a wrong device token
            webSocket.close(1000, null)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = lost(webSocket, "closed")

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) =
            lost(webSocket, t.message ?: t.javaClass.simpleName)
    }

    private fun lost(webSocket: WebSocket, reason: String) {
        if (webSocket !== socket) return
        socket = null
        if (refused) {
            status.value = Status.Offline("Natro refused this phone: check natro.token in local.properties")
            return
        }
        status.value = Status.Offline(reason)
        question.value = null
        val wait = retryMillis
        retryMillis = minOf(retryMillis * 2, RETRY_MAX_MILLIS)
        scope?.launch {
            delay(wait)
            if (socket == null && scope != null) connect()
        }
    }

    /** Try again now (the screen's status line, when tapped). */
    fun reconnect() {
        if (scope == null || status.value == Status.Online) return
        refused = false
        retryMillis = RETRY_FIRST_MILLIS
        socket?.cancel()
        socket = null
        connect()
    }

    private fun send(message: JSONObject): Boolean {
        val sent = socket?.send(message.toString()) ?: false
        if (!sent) note("Not connected to Natro.")
        return sent
    }

    // Messages from Natro

    private fun handle(data: JSONObject) {
        val id = data.optString("id")
        when (data.optString("type")) {
            "welcome" -> {
                status.value = Status.Online
                retryMillis = RETRY_FIRST_MILLIS
            }
            "english" -> append(id, Who.YOU, data.optString("text"))
            "heard" -> put(id, Who.YOU, data.optString("english").ifBlank { "(nothing heard)" })
            "reply_text" -> append("$id.reply", Who.NATRO, data.optString("text"))
            "reply" -> {
                val text = data.optString("text")
                put("$id.reply", Who.NATRO, text)
                if (speakReplies.value) speaker?.say(text)
            }
            "confirm" -> {
                val text = data.optString("question")
                question.value = Question(id, text, data.optString("details"))
                if (speakReplies.value) speaker?.say(text)
            }
            "tool_call" -> scope?.launch { runTool(id, data.optString("name"), data.optJSONObject("args") ?: JSONObject()) }
            "error" -> put("$id.error", Who.NOTE, data.optString("message"))
        }
    }

    private suspend fun runTool(id: String, name: String, args: JSONObject) {
        val phone = tools ?: return
        val (ok, result) = try {
            true to withContext(Dispatchers.IO) { phone.run(name, args) }
        } catch (e: ToolFailure) {
            false to (e.message ?: "$name failed.")
        } catch (e: Exception) {
            false to "$name failed on the phone: ${e.javaClass.simpleName}: ${e.message}"
        }
        add("tool.$id", Who.TOOL, "$name: $result")
        send(JSONObject().put("type", "tool_result").put("id", id).put("ok", ok).put("result", result))
    }

    // What the owner does

    fun sendText(text: String) {
        val id = "r${requestIds.incrementAndGet()}"
        add(id, Who.YOU, text)
        send(JSONObject().put("type", "text").put("id", id).put("text", text))
    }

    fun startRecording() {
        speaker?.stop()  // don't record Natro herself
        recorder.start()
        recording.value = true
    }

    /** Record until he stops talking (opened as the assistant): no button to hold. */
    fun listen() {
        if (recording.value) return
        speaker?.stop()
        beep()
        val end = SpeechEnd()
        recorder.start { loudness ->
            end.add(loudness)
            if (end.done) scope?.launch { if (end.heard) stopRecording() else cancelRecording() }
        }
        recording.value = true
    }

    private fun cancelRecording() {
        recorder.stop()
        recording.value = false
        note("Didn't hear anything.")
    }

    fun beep() {
        ToneGenerator(AudioManager.STREAM_MUSIC, 70).startTone(ToneGenerator.TONE_PROP_BEEP, 150)
    }

    /** Sends the recording: as the answer to Natro's question if she's asking, else as a request. */
    fun stopRecording() {
        val raw = recorder.stop()
        if (!recording.value) return
        recording.value = false
        if (raw.size < Recorder.BYTES_PER_SECOND / 2) {
            note("Hold the button while you talk.")
            return
        }
        sendRecording(raw)
    }

    /** Send a spoken request (raw phone levels; made louder here), or the answer to her question. */
    fun sendRecording(raw: ByteArray) {
        val pcm = Recorder.louder(raw)
        // Debug builds keep the last recording, to check the microphone's levels: adb exec-out run-as dev.natro cat cache/last.pcm
        if (BuildConfig.DEBUG) cacheDir?.let { File(it, "last.pcm").writeBytes(raw) }
        val id = "r${requestIds.incrementAndGet()}"
        val message = JSONObject().put("type", "audio").put("id", id)
        question.value?.let {
            message.put("answer_to", it.id)
            question.value = null
        }
        if (send(message)) socket?.send(pcm.toByteString())
    }

    fun answer(yes: Boolean) {
        val asked = question.value ?: return
        question.value = null
        add("answer.${asked.id}", Who.YOU, if (yes) "Yes." else "No.")
        send(JSONObject().put("type", "answer").put("id", asked.id).put("yes", yes))
    }

    fun note(text: String) = add("note", Who.NOTE, text)

    // The lines on screen

    private fun add(ref: String, who: Who, text: String) =
        lines.update { (it + Line(keys.incrementAndGet(), ref, who, text)).takeLast(MAX_LINES) }

    /** Replace the text of the latest line for ref (or add one). */
    private fun put(ref: String, who: Who, text: String) = lines.update { current ->
        val at = current.indexOfLast { it.ref == ref && it.who == who }
        if (at < 0) (current + Line(keys.incrementAndGet(), ref, who, text)).takeLast(MAX_LINES)
        else current.toMutableList().also { it[at] = it[at].copy(text = text) }
    }

    /** Add a streamed piece to the latest line for ref (or start one). */
    private fun append(ref: String, who: Who, piece: String) = lines.update { current ->
        val at = current.indexOfLast { it.ref == ref && it.who == who }
        if (at < 0) (current + Line(keys.incrementAndGet(), ref, who, piece)).takeLast(MAX_LINES)
        else current.toMutableList().also { it[at] = it[at].copy(text = it[at].text + piece) }
    }

    private const val RETRY_FIRST_MILLIS = 2_000L
    private const val RETRY_MAX_MILLIS = 60_000L
    private const val MAX_LINES = 200
}
