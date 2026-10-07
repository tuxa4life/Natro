package dev.natro

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Test

class NamesTest {
    private val apps = listOf("Spotify", "Spotify Lite", "YouTube", "YouTube Music", "Google Maps", "Clock", "Camera",
        "Calculator", "Nothing X")

    @Test
    fun appNamesAsPeopleSayThem() {
        assertEquals("Spotify", Names.best("spotify", apps))
        assertEquals("YouTube", Names.best("youtube", apps))
        assertEquals("YouTube Music", Names.best("youtube music", apps))
        assertEquals("Google Maps", Names.best("maps", apps))
        assertEquals("Calculator", Names.best("calculater", apps))  // misheard
        assertNull(Names.best("telegram", apps))
        assertEquals("Camera", Names.closest("camra", apps, 1).single())
    }

    @Test
    fun times() {
        assertEquals(7 to 30, Names.time("07:30"))
        assertEquals(7 to 5, Names.time("7.05"))
        assertEquals(19 to 0, Names.time(" 19:00 "))
        assertThrows(ToolFailure::class.java) { Names.time("7am") }
        assertThrows(ToolFailure::class.java) { Names.time("25:00") }
    }

    @Test
    fun durations() {
        assertEquals("5 minutes", Names.duration(300))
        assertEquals("1 hour 30 minutes", Names.duration(5400))
        assertEquals("1 minute 5 seconds", Names.duration(65))
    }

    @Test
    fun volumeLevels() {
        assertEquals(60, Names.level("up", 50))
        assertEquals(0, Names.level("down", 5))
        assertEquals(35, Names.level("35%", 80))
        assertEquals(100, Names.level("max", 10))
        assertEquals(0, Names.level("mute", 70))
        assertEquals(100, Names.level("150", 10))
        assertThrows(ToolFailure::class.java) { Names.level("loud", 50) }
    }
}

class LouderTest {
    private fun pcm(vararg samples: Int) = ByteArray(samples.size * 2).also { out ->
        samples.forEachIndexed { i, s -> out[2 * i] = s.toByte(); out[2 * i + 1] = (s shr 8).toByte() }
    }

    private fun samples(pcm: ByteArray) = List(pcm.size / 2) { ((pcm[2 * it + 1].toInt() shl 8) or (pcm[2 * it].toInt() and 0xFF)).toShort().toInt() }

    @Test
    fun quietSpeechIsMadeLouderWithinLimits() {
        // Loudest frame 200 -> gain 20 (4,000 / 200); negatives survive the round trip.
        val quiet = pcm(*IntArray(Recorder.RATE / 50) { if (it % 2 == 0) 200 else -200 })
        assertEquals(listOf(4000, -4000), samples(Recorder.louder(quiet)).take(2))
        // Near-silence is capped at 30 times, so it stays quiet.
        val silence = pcm(*IntArray(Recorder.RATE / 50) { 3 })
        assertEquals(90, samples(Recorder.louder(silence)).first())
        // Loud enough already: unchanged.
        val loud = pcm(*IntArray(Recorder.RATE / 50) { 5000 })
        assertEquals(5000, samples(Recorder.louder(loud)).first())
    }
}
