"""The most common short commands, run without a model: ~0.3 s instead of 2.5-7 s.

Only music on Spotify, and only exact, unambiguous phrasings ("pause the music",
"next song", "volume up"), after dropping "OK Natro", "please" and the like. If
Spotify can't do it (nothing is playing, say), the request goes to a model as
usual, which may know better (the phone's media, for one). Bare words like
"next" or "back" count only outside a conversation, where they can't mean "the
next email".
"""
import re

MUSIC = r"(the |my )?(music|song|track|spotify|playback)"
SONG = r"(song|track)"

# (pattern, tool, arguments, bare): bare patterns count only outside a conversation.
COMMANDS = [
    (rf"pause( it| this| that| {MUSIC})?|stop {MUSIC}", "spotify_control", {"action": "pause"}, False),
    (rf"(resume|unpause|continue)( {MUSIC})?|(resume|continue) playing", "spotify_control", {"action": "resume"},
     False),
    (rf"(next|skip( to the next)?) {SONG}|skip( this| the)? {SONG}|play the next {SONG}|skip it",
     "spotify_control", {"action": "next"}, False),
    (rf"(previous|last) {SONG}|play the previous {SONG}|go back( to the previous {SONG}| a {SONG})",
     "spotify_control", {"action": "previous"}, False),
    (r"(turn )?(the )?volume up|turn (it|the music|the volume) up( a bit)?|(a bit )?louder|raise the volume",
     "spotify_control", {"action": "volume", "value": "up"}, False),
    (r"(turn )?(the )?volume down|turn (it|the music|the volume) down( a bit)?|(a bit )?(quieter|softer)|"
     r"lower the volume", "spotify_control", {"action": "volume", "value": "down"}, False),
    (r"next|skip", "spotify_control", {"action": "next"}, True),
    (r"previous|back|go back", "spotify_control", {"action": "previous"}, True),
]
LEADING = re.compile(r"^((ok|okay|hey) )?natro |^(ok|okay) |^(please|can you|could you|would you) ")
TRAILING = re.compile(r" (please|now|natro|for me)$")


def words(text):
    """Lowercase words without punctuation or polite padding: "Okay, Natro. Pause it, please." -> "pause it"."""
    text = " ".join(re.findall(r"[a-z0-9']+", text.lower()))
    for _ in range(3):
        text = TRAILING.sub("", LEADING.sub("", text))
    return text


def command(text, in_conversation):
    """(tool name, arguments) for a quick command, or None."""
    said = words(text)
    for pattern, tool, args, bare in COMMANDS:
        if re.fullmatch(pattern, said) and not (bare and in_conversation):
            return tool, dict(args)
    return None


def simple(text):
    """A request with one thing to do: short, with no "and", "then" or commas joining several."""
    said = words(text)
    return len(said.split()) <= 12 and not re.search(r"\b(and|then|also|after|before|plus|but)\b", said) \
        and not re.search(r"[,;]", re.sub(r"^\W*(ok|okay|hey)?\W*natro\W*", "", text.lower()))
