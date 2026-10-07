package dev.natro

/** Plain functions for the phone's tools: matching spoken names, reading times and levels. */
object Names {
    const val GOOD_MATCH = 0.6

    fun words(text: String): String =
        Regex("[\\p{L}\\p{N}]+").findAll(text.lowercase()).joinToString(" ") { it.value }

    /** How well a spoken app name matches a real one, 0 to 1 (as in the PC app's apps.py). */
    fun score(query: String, name: String): Double {
        val q = words(query)
        val n = words(name)
        if (q.isEmpty() || n.isEmpty()) return 0.0
        if (q == n || q.replace(" ", "") == n.replace(" ", "")) return 1.0
        val nameWords = n.split(" ")
        val queryWords = q.split(" ")
        if (queryWords.all { part -> nameWords.any { it.startsWith(part) } }) {
            return 0.9 - 0.02 * (nameWords.size - queryWords.size)  // "Spotify" over "Spotify Lite"
        }
        if (q in n) return 0.75
        return 0.75 * similarity(q, n)
    }

    fun best(query: String, names: List<String>): String? =
        names.map { it to score(query, it) }.filter { it.second >= GOOD_MATCH }.maxByOrNull { it.second }?.first

    fun closest(query: String, names: List<String>, count: Int = 5): List<String> =
        names.sortedByDescending { similarity(words(query), words(it)) }.take(count)

    /** 1 minus the edit distance over the longer length. */
    fun similarity(a: String, b: String): Double {
        if (a.isEmpty() && b.isEmpty()) return 1.0
        var previous = IntArray(b.length + 1) { it }
        for (i in 1..a.length) {
            val current = IntArray(b.length + 1)
            current[0] = i
            for (j in 1..b.length) {
                val cost = if (a[i - 1] == b[j - 1]) 0 else 1
                current[j] = minOf(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            }
            previous = current
        }
        return 1.0 - previous[b.length].toDouble() / maxOf(a.length, b.length)
    }

    /** "07:30", "7:30" or "19:05" as (hour, minute). */
    fun time(text: String): Pair<Int, Int> {
        val match = Regex("^\\s*(\\d{1,2})[:.](\\d{2})\\s*$").find(text)
            ?: throw ToolFailure("Give the time as HH:MM in 24 hours, like 07:30 or 19:05.")
        val hour = match.groupValues[1].toInt()
        val minute = match.groupValues[2].toInt()
        if (hour > 23 || minute > 59) throw ToolFailure("$text isn't a time of day.")
        return hour to minute
    }

    /** A timer's length as said aloud: "5 minutes", "1 hour 30 minutes", "45 seconds". */
    fun duration(seconds: Int): String {
        fun part(count: Int, unit: String) = if (count == 0) null else "$count $unit${if (count == 1) "" else "s"}"
        return listOfNotNull(part(seconds / 3600, "hour"), part(seconds % 3600 / 60, "minute"), part(seconds % 60, "second"))
            .joinToString(" ").ifEmpty { "0 seconds" }
    }

    /** A volume request ("60", "up", "down", "max", "mute") as a new percentage, given the current one. */
    fun level(request: String, current: Int, step: Int = 10): Int = when (val value = request.trim().lowercase()) {
        "up" -> current + step
        "down" -> current - step
        "max" -> 100
        "mute", "off" -> 0
        else -> value.removeSuffix("%").toIntOrNull()
            ?: throw ToolFailure("The level is 0 to 100, or up, down, max or mute.")
    }.coerceIn(0, 100)
}

class ToolFailure(message: String) : Exception(message)
