"""Recording a request on the PC: after Enter, or after "OK Natro".

Recording stops by itself 3 seconds after you stop talking (or press Enter to
stop sooner). Silence at the start and end is trimmed.
"""
import msvcrt
import queue
import sys
import time
from collections import deque

from natro_pc.audio import CHUNK_SECONDS, EndOfSpeech, Microphone, mean_loudness, speech_threshold
from natro_pc.wakeword import CHECK_SECONDS, LEAD_IN_SECONDS, MIN_PAUSE_SECONDS, is_wake_phrase

# Silence that ends a recording. In test memos, thinking pauses inside one
# request lasted up to 3.9 s: a 2 s limit would have cut in 12 times, 3 s 3 times.
SILENCE_SECONDS = 3.0


def keys_pressed():
    """Keys pressed since the last check (Windows console), lowercased."""
    keys = ""
    while msvcrt.kbhit():
        keys += msvcrt.getwch().lower()
    return keys


def record(silence_seconds):
    """Record until silence_seconds of quiet after speech, 8 s without speech, or Enter.

    silence_seconds=None records until Enter, however long the pauses.
    """
    mic = Microphone()
    end = EndOfSpeech(silence_seconds, 8.0) if silence_seconds else EndOfSpeech(float("inf"), float("inf"))
    mic.start()
    started = time.monotonic()
    while not end.done:
        time.sleep(0.05)
        while end.chunks < len(mic.loudness):
            end.add(mic.loudness[end.chunks])
        if "\r" in keys_pressed():
            break
        seconds = int(time.monotonic() - started)
        bar = "▮" * int(mic.level * 30)
        state = "listening" if end.heard_speech else "waiting for speech"
        sys.stdout.write(f"\r● {seconds // 60}:{seconds % 60:02d}  {bar:<30}  {state:<18} (Enter to stop)")
        sys.stdout.flush()
    mic.stop()
    print()
    return end.trim(mic.read_all())


def next_chunk(mic):
    """The next audio chunk; waits in short steps so Ctrl+C still works on Windows."""
    while True:
        try:
            return mic.chunks.get(timeout=0.5)
        except queue.Empty:
            pass


def wait_for_wake_word(mic, wake, verbose=False, muted=lambda: False, stop=lambda: False):
    """Read audio until an utterance starts with "OK Natro"; return that utterance's chunks so far.

    Only the first CHECK_SECONDS of each utterance (speech after a pause) are
    transcribed, locally. The rest of the time nothing runs but a loudness check.
    While muted() (Natro is speaking), audio is skipped, so she doesn't wake
    herself. Returns None as soon as stop() is true.
    """
    recent = deque(maxlen=300)  # the last 30 s of loudness, for the background noise level
    before = deque(maxlen=int(LEAD_IN_SECONDS / CHUNK_SECONDS))  # audio from just before speech starts
    check_chunks = int(CHECK_SECONDS / CHUNK_SECONDS)
    pause_chunks = int(MIN_PAUSE_SECONDS / CHUNK_SECONDS)
    quiet, utterance = pause_chunks, None
    while not stop():
        chunk = next_chunk(mic)
        if muted():
            quiet, utterance = 0, None
            continue
        loudness = mean_loudness(chunk)
        recent.append(loudness)
        speech = loudness > speech_threshold(sorted(recent)[len(recent) // 10])
        if utterance is None and speech and quiet >= pause_chunks:
            utterance = list(before)
        quiet = 0 if speech else quiet + 1
        before.append(chunk)
        if utterance is None:
            continue
        utterance.append(chunk)
        # Check once CHECK_SECONDS are in, or sooner if the speaker already stopped.
        if len(utterance) >= check_chunks or quiet >= 5:
            text = wake.transcribe(b"".join(utterance))
            if verbose:
                print(f"  (heard: {text})")
            if is_wake_phrase(text):
                return utterance
            utterance = None
    return None


def record_instruction(mic, chunks, silence_seconds, console=True, stop=lambda: False):
    """Keep recording after the wake word until silence_seconds of quiet, Enter (console), or stop()."""
    end = EndOfSpeech(silence_seconds or float("inf"), float("inf"))
    for chunk in chunks:
        end.add(mean_loudness(chunk))
    started = time.monotonic()
    while not end.done and not stop() and not (console and "\r" in keys_pressed()):
        chunk = next_chunk(mic)
        chunks.append(chunk)
        end.add(mean_loudness(chunk))
        if console:
            seconds = int(time.monotonic() - started)
            sys.stdout.write(f"\r● {seconds // 60}:{seconds % 60:02d}  {'▮' * int(mic.level * 30):<30}  (Enter to stop)")
            sys.stdout.flush()
    if console:
        print()
    return end.trim(b"".join(chunks))
