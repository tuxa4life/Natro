package dev.natro

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SpeechTest {
    private fun feed(end: SpeechEnd, vararg levels: Double) = levels.forEach(end::add)

    @Test
    fun endsAfterTwoSecondsOfQuietFollowingSpeech() {
        val end = SpeechEnd()
        feed(end, 3.0, 2.0, 400.0, 600.0, 250.0)
        feed(end, *DoubleArray(19) { 3.0 })
        assertFalse(end.done)  // a thinking pause
        feed(end, 3.0)
        assertTrue(end.heard && end.done)
    }

    @Test
    fun pausesInsideARequestDontEndIt() {
        val end = SpeechEnd()
        feed(end, 500.0, *DoubleArray(15) { 4.0 }, 300.0, *DoubleArray(10) { 4.0 })
        assertFalse(end.done)
    }

    @Test
    fun givesUpWhenNothingIsSaid() {
        val end = SpeechEnd()
        feed(end, *DoubleArray(59) { 5.0 })
        assertFalse(end.done)
        feed(end, 5.0)
        assertTrue(end.done && !end.heard)
    }

    @Test
    fun noisyRoomNeedsSpeechWellAboveTheNoise() {
        val noisy = SpeechEnd()
        feed(noisy, 40.0, 45.0, 120.0)  // a fan at ~40: speech must pass 160
        assertFalse(noisy.heard)
        feed(noisy, 400.0)
        assertTrue(noisy.heard)
    }

    @Test
    fun georgianNamesInLatin() {
        assertEquals("nino beridze", Text.latin("ნინო ბერიძე"))
        assertEquals("giorgi", Text.latin("Giorgi"))
        assertEquals(1.0, Names.score("nino", Text.latin("ნინო")), 0.0)
    }

    @Test
    fun riskyButtons() {
        assertTrue(Text.RISKY.containsMatchIn("Send"))
        assertTrue(Text.RISKY.containsMatchIn("Place order"))
        assertTrue(Text.RISKY.containsMatchIn("გაგზავნა"))
        assertFalse(Text.RISKY.containsMatchIn("Search"))
        assertFalse(Text.RISKY.containsMatchIn("Sender settings"))  // whole words only
        assertEquals("a long…", Text.short("a   long text", 7))
    }
}
