"""Recording on this PC: the microphone, and deciding when the speaker has finished.

Only the recording happens here; recognition and translation run on the VPS.
mean_loudness, speech_threshold and has_speech are the same as in
voice/natro_voice/speech.py, so a recording judged to have speech here is judged
the same way there.
"""
import array
import queue

import sounddevice as sd

SAMPLE_RATE = 16000
CHUNK_SECONDS = 0.1
BYTES_PER_SECOND = SAMPLE_RATE * 2  # 16-bit mono
CHUNK_BYTES = int(BYTES_PER_SECOND * CHUNK_SECONDS)


def mean_loudness(pcm):
    samples = array.array("h", pcm)
    return sum(map(abs, samples)) / max(len(samples), 1)


def speech_threshold(noise_floor):
    """Loudness above which audio counts as speech.

    Measured (mean loudness of short frames): quiet room up to ~180, speech
    ~1,000-16,000; in a noisy recording the background was ~800.
    """
    return max(300, 6 * noise_floor)


def has_speech(pcm):
    """Whether the recording has at least 0.3 s clearly louder than its background noise.

    Generative recognizers like Chirp 3 can produce text from noise, and silence
    costs money, so recordings without speech are not sent at all.
    """
    frame = BYTES_PER_SECOND // 50
    loudness = sorted(mean_loudness(pcm[i:i + frame]) for i in range(0, len(pcm) - frame + 1, frame))
    if not loudness:
        return False
    threshold = speech_threshold(loudness[len(loudness) // 10])
    return sum(level > threshold for level in loudness) >= 15


class Microphone:
    """Puts 16 kHz mono 16-bit audio chunks (CHUNK_SECONDS each) on a queue; None marks the end.

    level is the peak of the latest chunk, from 0 to 1, for a level meter.
    loudness has the mean loudness of every chunk so far, for speech detection,
    unless keep_loudness is False (for listening for hours).
    """

    def __init__(self, keep_loudness=True):
        self.chunks = queue.Queue()
        self.level = 0.0
        self.loudness = []
        self._keep_loudness = keep_loudness
        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=CHUNK_BYTES // 2, callback=self._callback)

    def _callback(self, indata, frames, time_info, status):
        chunk = bytes(indata)
        samples = array.array("h", chunk)
        self.level = max(map(abs, samples), default=0) / 32768
        if self._keep_loudness:
            self.loudness.append(mean_loudness(chunk))
        self.chunks.put(chunk)

    def discard_waiting(self):
        """Drop audio that arrived but hasn't been read (for example while busy processing)."""
        while not self.chunks.empty():
            self.chunks.get_nowait()

    def read_all(self):
        """All audio recorded so far, up to the end marker. Call after stop()."""
        pcm = bytearray()
        while (chunk := self.chunks.get()) is not None:
            pcm += chunk
        return bytes(pcm)

    def start(self):
        self._stream.start()

    def stop(self):
        self._stream.stop()
        self._stream.close()
        self.chunks.put(None)


class EndOfSpeech:
    """Decides from each chunk's loudness when the speaker has finished.

    done: after silence_seconds of quiet following speech, or no_speech_seconds
    without any speech at all.
    """

    def __init__(self, silence_seconds=3.0, no_speech_seconds=8.0):
        self.silence_seconds = silence_seconds
        self.no_speech_seconds = no_speech_seconds
        self.chunks = 0
        self.first_speech = None  # chunk numbers
        self.last_speech = None
        self._quietest = None

    def add(self, loudness):
        # The quietest chunk so far stands in for the background noise level.
        self._quietest = loudness if self._quietest is None else min(self._quietest, loudness)
        if loudness > speech_threshold(self._quietest):
            if self.first_speech is None:
                self.first_speech = self.chunks
            self.last_speech = self.chunks
        self.chunks += 1

    @property
    def heard_speech(self):
        return self.first_speech is not None

    @property
    def done(self):
        if not self.heard_speech:
            return self.chunks * CHUNK_SECONDS >= self.no_speech_seconds
        return (self.chunks - 1 - self.last_speech) * CHUNK_SECONDS >= self.silence_seconds

    def trim(self, pcm):
        """Cut the silence before the first and after the last speech, keeping a little margin."""
        if not self.heard_speech:
            return pcm
        start = max(0, self.first_speech - 3) * CHUNK_BYTES
        end = (self.last_speech + 1 + 5) * CHUNK_BYTES
        return pcm[start:end]
