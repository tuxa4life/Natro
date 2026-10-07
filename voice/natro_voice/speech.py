"""Georgian speech recognition with Google Chirp 3 (runs on the VPS; the devices record)."""
import array
import os
import re
import queue
import threading
import time

import av
from google.api_core.client_options import ClientOptions
from google.cloud.speech_v2 import SpeechClient
from google.cloud.speech_v2.types import cloud_speech as cs

SAMPLE_RATE = 16000
CHUNK_SECONDS = 0.1
BYTES_PER_SECOND = SAMPLE_RATE * 2  # 16-bit mono
CHUNK_BYTES = int(BYTES_PER_SECOND * CHUNK_SECONDS)
REGION = "eu"
PRICE_PER_MINUTE = 0.016

# Google ends a stream after about 5 minutes. After ROTATE_SECONDS we start a
# new stream at the next sentence boundary, and at MAX_STREAM_SECONDS regardless.
ROTATE_SECONDS = 240
MAX_STREAM_SECONDS = 290
# One-shot (non-streaming) recognition accepts at most 60 seconds of audio.
MAX_ONE_SHOT_SECONDS = 55


class AudioFile:
    """Plays an audio file (any format) into the same kind of queue, at real-time pace."""

    def __init__(self, path):
        self.chunks = queue.Queue()
        self.pcm = decode_to_pcm(path)
        self._thread = threading.Thread(target=self._feed, daemon=True)

    def _feed(self):
        size = CHUNK_BYTES
        # A second of silence at the end so the last sentence gets finalized.
        audio = self.pcm + b"\0" * BYTES_PER_SECOND
        for i in range(0, len(audio), size):
            self.chunks.put(audio[i:i + size])
            time.sleep(CHUNK_SECONDS)
        self.chunks.put(None)

    def start(self):
        self._thread.start()

    def stop(self):
        pass


def decode_to_pcm(path):
    """Decode any audio file to 16 kHz mono 16-bit PCM bytes."""
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    frames = []
    with av.open(str(path)) as container:
        for packet in container.demux(audio=0):
            try:
                frames += packet.decode()
            except av.error.InvalidDataError:
                pass  # phone recorders sometimes write a broken first packet
    pcm = bytearray()
    for frame in frames + [None]:  # None flushes the resampler
        for out in resampler.resample(frame):
            # Planes can carry padding past the last sample.
            pcm += bytes(out.planes[0])[:out.samples * 2]
    return bytes(pcm)


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


def split_at_pauses(pcm, max_seconds):
    """Split PCM into pieces of at most max_seconds, cutting at the quietest 100 ms near each limit."""
    window = BYTES_PER_SECOND // 10
    limit = max_seconds * BYTES_PER_SECOND
    pieces = []
    while len(pcm) > limit:
        candidates = range(limit - 15 * BYTES_PER_SECOND, limit - window, window)
        cut = min(candidates, key=lambda i: sum(map(abs, array.array("h", pcm[i:i + window]))))
        pieces.append(pcm[:cut])
        pcm = pcm[cut:]
    return pieces + [pcm]


class Recognizer:
    """Google Chirp 3 streaming recognition.

    Expecting Georgian and English together keeps English words in Latin letters.
    The word list is not sent as phrase hints: with hints, Chirp 3 returned empty
    results for about half the Georgian sentences in testing. Claude gets it instead.
    Chirp 3 sends no interim results for Georgian, only a final one per phrase.
    """

    def __init__(self, languages=("ka-GE", "en-US")):
        self.client = SpeechClient(client_options=ClientOptions(api_endpoint=f"{REGION}-speech.googleapis.com"))
        self.recognizer = f"projects/{os.environ['GOOGLE_CLOUD_PROJECT']}/locations/{REGION}/recognizers/_"
        self.audio_seconds = 0.0

        self.config = cs.StreamingRecognitionConfig(
            config=cs.RecognitionConfig(
                explicit_decoding_config=cs.ExplicitDecodingConfig(
                    encoding=cs.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                    sample_rate_hertz=SAMPLE_RATE, audio_channel_count=1),
                model="chirp_3",
                language_codes=list(languages),
                features=cs.RecognitionFeatures(enable_automatic_punctuation=True),
            ),
            streaming_features=cs.StreamingRecognitionFeatures(
                interim_results=True,
                enable_voice_activity_events=True,
                endpointing_sensitivity=cs.StreamingRecognitionFeatures.EndpointingSensitivity.ENDPOINTING_SENSITIVITY_SHORT,
            ),
        )

    @property
    def cost(self):
        return self.audio_seconds / 60 * PRICE_PER_MINUTE

    def recognize(self, pcm, languages=None):
        """Transcribe a whole recording at once rather than live.

        Returns (text, the language codes Google picked, lowercase). languages
        overrides the ones the Recognizer was made with.
        """
        config = self.config.config
        if languages:
            config = cs.RecognitionConfig(config)
            config.language_codes = list(languages)
        texts, picked = [], set()
        for piece in split_at_pauses(pcm, MAX_ONE_SHOT_SECONDS):
            response = self.client.recognize(request=cs.RecognizeRequest(
                recognizer=self.recognizer, config=config, content=piece))
            self.audio_seconds += len(piece) / BYTES_PER_SECOND
            for result in response.results:
                if result.alternatives:
                    texts.append(result.alternatives[0].transcript.strip())
                    picked.add(result.language_code.lower())
        # Chirp sometimes puts "{}" inside a Georgian word ("ჩ {}ანიშნე", "ხელსაწ {}ყო").
        text = re.sub(r"\s*\{\}\s*", "", " ".join(t for t in texts if t))
        return text, picked

    def transcribe(self, pcm):
        """Transcribe a whole recording at once. Returns (text, as_georgian).

        Google picks one language for a whole recording. A Georgian request that
        starts with English- or Russian-sounding words ("OK Natro, karoche...")
        can be taken for English and come back as Georgian written in Latin
        letters. So when Google picks English, the recording is transcribed
        again as Georgian only, and as_georgian is that text (otherwise None).
        Claude then decides which one is right: the speaker may really have
        spoken English.
        """
        text, picked = self.recognize(pcm)
        if any(code.startswith("en") for code in picked):
            return text, self.recognize(pcm, languages=["ka-GE"])[0]
        return text, None

    def listen(self, source):
        """Yield (kind, text) until the audio source ends.

        kind is "final" or "interim" (with text), or "speech_started" /
        "speech_ended" (text is empty) when the speaker starts or stops talking.
        """
        self._source_done = False
        while not self._source_done:
            yield from self._stream(source)

    def _stream(self, source):
        state = {"seconds": 0.0, "between_sentences": True}

        def requests():
            yield cs.StreamingRecognizeRequest(recognizer=self.recognizer, streaming_config=self.config)
            while True:
                chunk = source.chunks.get()
                if chunk is None:
                    self._source_done = True
                    return
                yield cs.StreamingRecognizeRequest(audio=chunk)
                seconds = len(chunk) / BYTES_PER_SECOND
                state["seconds"] += seconds
                self.audio_seconds += seconds
                if (state["seconds"] > ROTATE_SECONDS and state["between_sentences"]) or state["seconds"] > MAX_STREAM_SECONDS:
                    return

        events = cs.StreamingRecognizeResponse.SpeechEventType
        for response in self.client.streaming_recognize(requests=requests()):
            if response.speech_event_type == events.SPEECH_ACTIVITY_BEGIN:
                yield "speech_started", ""
            elif response.speech_event_type == events.SPEECH_ACTIVITY_END:
                yield "speech_ended", ""

            finals = "".join(r.alternatives[0].transcript for r in response.results if r.is_final and r.alternatives).strip()
            interim = "".join(r.alternatives[0].transcript for r in response.results if not r.is_final and r.alternatives).strip()
            if finals:
                state["between_sentences"] = True
                yield "final", finals
            if interim:
                state["between_sentences"] = False
                yield "interim", interim
