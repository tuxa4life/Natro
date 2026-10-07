package dev.natro

import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.content.res.AssetManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.KeywordSpotter
import com.k2fsa.sherpa.onnx.KeywordSpotterConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig
import java.io.ByteArrayOutputStream

/**
 * "OK Natro" on the phone, checked on the phone: sherpa-onnx keyword spotting with a small
 * English/Chinese phoneme model (zipformer, 3M parameters), and "Natro" written out as phonemes
 * (assets/wake/keywords.txt). On the owner's 8 test recordings it caught 5 with no false wake-ups,
 * using ~5% of one PC core. Nothing leaves the phone before the wake word.
 */
class WakeWord(assets: AssetManager) {
    private val spotter = KeywordSpotter(assets, KeywordSpotterConfig(
        featConfig = FeatureConfig(sampleRate = Recorder.RATE, featureDim = 80),
        modelConfig = OnlineModelConfig(
            transducer = OnlineTransducerModelConfig(
                encoder = "$MODEL/encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx",
                decoder = "$MODEL/decoder-epoch-13-avg-2-chunk-8-left-64.onnx",
                joiner = "$MODEL/joiner-epoch-13-avg-2-chunk-8-left-64.int8.onnx",
            ),
            tokens = "$MODEL/tokens.txt",
            numThreads = 1,
        ),
        maxActivePaths = 4,
        keywordsFile = "wake/keywords.txt",
        keywordsScore = 2.5f,
        keywordsThreshold = 0.05f,
    ))
    private val stream = spotter.createStream("")

    /** Whether the wake word ended in these samples (raw phone levels). */
    fun heard(samples: ShortArray, count: Int): Boolean {
        // The phone's microphone is ~10 times quieter than the PC's the model was tested with.
        stream.acceptWaveform(FloatArray(count) { (samples[it] * GAIN / 32768f).coerceIn(-1f, 1f) }, Recorder.RATE)
        var found = false
        while (spotter.isReady(stream)) {
            spotter.decode(stream)
            if (spotter.getResult(stream).keyword.isNotBlank()) {
                spotter.reset(stream)
                found = true
            }
        }
        return found
    }

    fun release() {
        stream.release()
        spotter.release()
    }

    private companion object {
        const val MODEL = "kws"
        const val GAIN = 8f
    }
}

/**
 * Listens for "OK Natro" while the owner has it on (the switch in the app), with a notification
 * saying so; Android also shows its microphone dot. After the wake word it beeps, records the
 * request until he stops talking (the 1.5 s before the beep included, as on the PC), and sends it.
 * It doesn't listen while Natro speaks or while the mic button is held.
 */
class ListenService : Service() {
    @Volatile private var running = false
    private var worker: Thread? = null

    override fun onCreate() {
        super.onCreate()
        val notifications = getSystemService(NotificationManager::class.java)
        notifications.createNotificationChannel(NotificationChannel(CHANNEL, "Listening for OK Natro",
            NotificationManager.IMPORTANCE_LOW))
        val stop = PendingIntent.getService(this, 0, Intent(this, ListenService::class.java).setAction(STOP),
            PendingIntent.FLAG_IMMUTABLE)
        val notification = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_natro)
            .setContentTitle("Listening for \"OK Natro\"")
            .setContentText("Checked on the phone; nothing is sent before the wake word.")
            .setOngoing(true)
            .setSilent(true)
            .addAction(0, "Stop", stop)
            .setContentIntent(PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
                PendingIntent.FLAG_IMMUTABLE))
            .build()
        ServiceCompat.startForeground(this, NOTIFICATION, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        running = true
        worker = Thread(::listen, "wake-word").also { it.start() }
        Natro.listening.value = true
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == STOP) {
            setEnabled(this, false)
            stopSelf()
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?) = null

    override fun onDestroy() {
        running = false
        worker?.join(1000)
        Natro.listening.value = false
        super.onDestroy()
    }

    @SuppressLint("MissingPermission")  // only started with the microphone allowed
    private fun listen() {
        val wake = try {
            WakeWord(assets)
        } catch (e: Exception) {
            Natro.note("The wake word couldn't start: ${e.message}")
            stopSelf()
            return
        }
        val block = ShortArray(Recorder.RATE / 10)  // 100 ms
        val record = AudioRecord(MediaRecorder.AudioSource.MIC, Recorder.RATE, AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT, block.size * 2 * 4)
        val before = ArrayDeque<ShortArray>()  // the last 1.5 s
        var request: ByteArrayOutputStream? = null
        var end: SpeechEnd? = null
        record.startRecording()
        try {
            while (running) {
                val count = record.read(block, 0, block.size)
                if (count <= 0) continue
                val chunk = block.copyOf(count)
                if (request != null) {
                    request.write(bytes(chunk))
                    end!!.add(SpeechEnd.loudness(chunk))
                    if (end.done) {
                        if (end.heard) Natro.sendRecording(request.toByteArray()) else Natro.note("Didn't hear a request.")
                        request = null
                        before.clear()
                    }
                    continue
                }
                if (Natro.recording.value || Natro.speaking) {
                    before.clear()
                    continue
                }
                before.addLast(chunk)
                if (before.size > 15) before.removeFirst()
                if (wake.heard(chunk, count)) {
                    Natro.beep()
                    request = ByteArrayOutputStream().also { out -> before.forEach { out.write(bytes(it)) } }
                    end = SpeechEnd()
                }
            }
        } finally {
            record.stop()
            record.release()
            wake.release()
        }
    }

    companion object {
        private const val CHANNEL = "wake-word"
        private const val NOTIFICATION = 2
        private const val STOP = "dev.natro.STOP_LISTENING"
        private const val PREFERENCE = "listen-for-wake-word"

        fun enabled(context: Context) =
            context.getSharedPreferences("natro", Context.MODE_PRIVATE).getBoolean(PREFERENCE, false)

        /** Turn listening on or off, and remember it. Turning it on must happen while the app is on screen. */
        fun setEnabled(context: Context, on: Boolean) {
            context.getSharedPreferences("natro", Context.MODE_PRIVATE).edit().putBoolean(PREFERENCE, on).apply()
            val intent = Intent(context, ListenService::class.java)
            if (on) context.startForegroundService(intent) else context.stopService(intent)
        }

        fun bytes(samples: ShortArray): ByteArray = ByteArray(samples.size * 2).also { out ->
            samples.forEachIndexed { i, sample ->
                out[2 * i] = sample.toInt().toByte()
                out[2 * i + 1] = (sample.toInt() shr 8).toByte()
            }
        }
    }
}
