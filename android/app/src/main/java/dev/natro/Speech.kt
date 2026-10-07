package dev.natro

/**
 * When a spoken request is over, from the loudness of each 100 ms block (raw phone levels,
 * before Recorder.louder): after `silence` blocks of quiet once speech was heard, after `wait`
 * blocks with no speech at all, or after `longest` blocks in any case.
 */
class SpeechEnd(
    private val silence: Int = 20,  // 2 s
    private val wait: Int = 60,  // 6 s
    private val longest: Int = 600,  // a minute
) {
    private var floor = Double.MAX_VALUE
    private var blocks = 0
    private var quiet = 0
    var heard = false
        private set
    var done = false
        private set

    fun add(loudness: Double) {
        blocks++
        floor = minOf(floor, maxOf(loudness, 1.0))
        if (loudness > maxOf(MIN_SPEECH, SPEECH_OVER_NOISE * floor)) {
            heard = true
            quiet = 0
        } else if (heard) {
            quiet++
        }
        done = (heard && quiet >= silence) || (!heard && blocks >= wait) || blocks >= longest
    }

    companion object {
        // The phone's microphone: background ~1-5, speech ~30-1,000 (see Recorder.louder).
        const val MIN_SPEECH = 30.0
        const val SPEECH_OVER_NOISE = 4.0

        fun loudness(samples: ShortArray, count: Int = samples.size): Double =
            if (count == 0) 0.0 else (0 until count).sumOf { kotlin.math.abs(samples[it].toInt()) } / count.toDouble()
    }
}

/** Text helpers for contacts and the screen. */
object Text {
    private val GEORGIAN = mapOf(
        'ა' to "a", 'ბ' to "b", 'გ' to "g", 'დ' to "d", 'ე' to "e", 'ვ' to "v", 'ზ' to "z", 'თ' to "t", 'ი' to "i",
        'კ' to "k", 'ლ' to "l", 'მ' to "m", 'ნ' to "n", 'ო' to "o", 'პ' to "p", 'ჟ' to "zh", 'რ' to "r", 'ს' to "s",
        'ტ' to "t", 'უ' to "u", 'ფ' to "p", 'ქ' to "k", 'ღ' to "gh", 'ყ' to "q", 'შ' to "sh", 'ჩ' to "ch",
        'ც' to "ts", 'ძ' to "dz", 'წ' to "ts", 'ჭ' to "ch", 'ხ' to "kh", 'ჯ' to "j", 'ჰ' to "h",
    )

    /** Georgian letters in Latin ones ("ნინო" -> "nino"), so contacts saved either way match a spoken name. */
    fun latin(text: String): String = text.lowercase().map { GEORGIAN[it] ?: it.toString() }.joinToString("")

    /** Buttons that send, buy, pay or delete: tapping them needs the owner's yes (tap_risky). */
    val RISKY = Regex(
        "\\b(send|buy|purchase|pay|payment|order|checkout|check out|delete|remove|erase|clear all|uninstall|" +
            "transfer|submit|post|publish|share|call|subscribe|unsubscribe|book|block|report|sign out|log out)\\b|" +
            "გაგზავნ|წაშალ|წაშლა|ყიდვა|იყიდე|გადახდ|შეკვეთ|გაზიარ|დარეკ",
        RegexOption.IGNORE_CASE,
    )

    fun short(text: String, length: Int = 80): String {
        val flat = text.replace(Regex("\\s+"), " ").trim()
        return if (flat.length <= length) flat else flat.take(length - 1) + "…"
    }
}
