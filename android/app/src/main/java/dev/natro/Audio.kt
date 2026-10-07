package dev.natro

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.speech.tts.TextToSpeech
import java.io.ByteArrayOutputStream
import java.util.Locale
import java.util.UUID

/**
 * Records 16 kHz mono 16-bit PCM, the format Natro's voice pipeline takes: while the mic button is
 * held, or until onBlock (given each 100 ms's loudness) decides the request is over.
 */
class Recorder {
    private var record: AudioRecord? = null
    private var reader: Thread? = null
    private val pcm = ByteArrayOutputStream()

    @SuppressLint("MissingPermission")  // the screen asks for the microphone before recording
    fun start(onBlock: ((Double) -> Unit)? = null) {
        val block = ShortArray(RATE / 10)
        val size = maxOf(AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT),
            block.size * 2)
        val recording = AudioRecord(MediaRecorder.AudioSource.MIC, RATE, AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT, size * 2)
        synchronized(pcm) { pcm.reset() }
        recording.startRecording()
        record = recording
        reader = Thread {
            while (record === recording && pcm.size() < MAX_BYTES) {
                val read = recording.read(block, 0, block.size)
                if (read < 0) break
                synchronized(pcm) { pcm.write(ListenService.bytes(block.copyOf(read))) }
                onBlock?.invoke(SpeechEnd.loudness(block, read))
            }
        }.also { it.start() }
    }

    /** Stops and returns what was recorded (nothing if it wasn't recording). */
    @Synchronized
    fun stop(): ByteArray {
        val recording = record ?: return ByteArray(0)
        record = null
        if (Thread.currentThread() !== reader) reader?.join(500)
        recording.stop()
        recording.release()
        return synchronized(pcm) { pcm.toByteArray() }
    }

    companion object {
        const val RATE = 16_000
        const val BYTES_PER_SECOND = RATE * 2
        const val MAX_BYTES = BYTES_PER_SECOND * 120  // two minutes
        // The phone's microphone records far quieter than the PC's (speech at a mean loudness of
        // ~100-300 against ~1,000-16,000), and Natro's voice pipeline ignores audio below 300.
        const val TARGET_LOUDNESS = 4_000
        const val MAX_GAIN = 30.0
        private const val FRAME = RATE / 50  // 20 ms

        /**
         * A quiet recording made louder, so its loudest 20 ms reach TARGET_LOUDNESS (at most MAX_GAIN
         * times). Noise grows as much as speech, so the pipeline's speech check (louder than the
         * background) still turns silence away.
         */
        fun louder(pcm: ByteArray): ByteArray {
            val samples = ShortArray(pcm.size / 2) { ((pcm[2 * it + 1].toInt() shl 8) or (pcm[2 * it].toInt() and 0xFF)).toShort() }
            val loudest = (samples.indices step FRAME).maxOfOrNull { start ->
                val end = minOf(start + FRAME, samples.size)
                (start until end).sumOf { kotlin.math.abs(samples[it].toInt()) } / (end - start).toDouble()
            } ?: return pcm
            if (loudest <= 0.0) return pcm
            val gain = minOf(MAX_GAIN, TARGET_LOUDNESS / loudest)
            if (gain <= 1.0) return pcm
            val out = ByteArray(samples.size * 2)
            samples.forEachIndexed { i, sample ->
                val scaled = (sample * gain).toInt().coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt())
                out[2 * i] = scaled.toByte()
                out[2 * i + 1] = (scaled shr 8).toByte()
            }
            return out
        }
    }
}

/** Speaks Natro's replies with the phone's own text-to-speech (free, offline). */
class Speaker(context: Context) : TextToSpeech.OnInitListener {
    private val tts = TextToSpeech(context, this)
    private val waiting = mutableListOf<String>()
    @Volatile private var ready = false

    /** Whether it's speaking (the wake word doesn't listen meanwhile, or Natro could wake herself). */
    val speaking get() = ready && tts.isSpeaking

    override fun onInit(status: Int) {
        if (status != TextToSpeech.SUCCESS) return
        tts.language = Locale.US
        ready = true
        synchronized(waiting) { waiting.forEach(::say); waiting.clear() }
    }

    fun say(text: String) {
        if (text.isBlank()) return
        if (!ready) synchronized(waiting) { waiting += text } else
            tts.speak(text, TextToSpeech.QUEUE_ADD, null, UUID.randomUUID().toString())
    }

    fun stop() {
        tts.stop()
    }

    fun shutdown() {
        tts.shutdown()
    }
}
