"""The speech-to-English pipeline shared by live.py and the test tools."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

SENTENCE_END = (".", "?", "!", "…")
# How long to wait for more speech after a piece that ends mid-sentence.
WAIT_SECONDS = 1.5
# At most this many pieces are joined into one sentence.
MAX_PARTS = 2


class SentenceJoiner:
    """Joins recognized pieces that end mid-sentence, so Claude gets whole sentences.

    A piece ending in . ? or ! is passed on right away. A piece without one is
    joined with the next piece if the speaker keeps talking, or passed on after
    WAIT_SECONDS of silence.
    """

    def __init__(self, on_sentence):
        self.on_sentence = on_sentence
        self.parts = []
        self._timer = None
        self._lock = threading.Lock()

    def pending_text(self):
        with self._lock:
            return " ".join(self.parts)

    def add(self, text):
        with self._lock:
            self._cancel_timer()
            self.parts.append(text)
            if text.endswith(SENTENCE_END) or len(self.parts) >= MAX_PARTS:
                self._send()
            else:
                self._start_timer()

    def speech_started(self):
        with self._lock:
            self._cancel_timer()

    def speech_ended(self):
        # Covers speech that produces no text (noise), so pieces aren't held forever.
        with self._lock:
            if self.parts and not self._timer:
                self._start_timer()

    def flush(self):
        with self._lock:
            self._cancel_timer()
            self._send()

    def _send(self):
        if self.parts:
            self.on_sentence(" ".join(self.parts))
            self.parts = []

    def _start_timer(self):
        self._timer = threading.Timer(WAIT_SECONDS, self.flush)
        self._timer.daemon = True
        self._timer.start()

    def _cancel_timer(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None


class Session:
    """Runs one audio source through recognition, sentence joining and translation.

    on_result(georgian, english) is called once per sentence, in order.
    on_interim(text) gets the sentence in progress (Chirp 3 sends none for Georgian).
    on_error(exception) is called if recognition stops with an error.
    latencies holds, per sentence, the seconds from the end of speech until the
    English was ready.
    """

    def __init__(self, source, recognizer, translator, on_result, on_interim=None, on_error=None):
        self.source = source
        self.recognizer = recognizer
        self.translator = translator
        self.on_result = on_result
        self.on_interim = on_interim or (lambda text: None)
        self.on_error = on_error or (lambda error: None)
        self.latencies = []
        self._last_speech_end = None
        self._pending = []
        self._executor = ThreadPoolExecutor(max_workers=1)  # one at a time keeps sentences in order
        self._joiner = SentenceJoiner(self._submit)
        self._worker = threading.Thread(target=self._recognize, daemon=True)

    def start(self):
        self.source.start()
        self._worker.start()

    def finish(self):
        """Wait for the source to end and every sentence to be translated."""
        self._worker.join()
        self._joiner.flush()
        wait(self._pending)
        self._executor.shutdown()

    def _recognize(self):
        try:
            for kind, text in self.recognizer.listen(self.source):
                if kind == "final":
                    self._joiner.add(text)
                elif kind == "interim":
                    self._joiner.speech_started()
                    self.on_interim(f"{self._joiner.pending_text()} {text}".strip())
                elif kind == "speech_started":
                    self._joiner.speech_started()
                else:
                    self._last_speech_end = time.monotonic()
                    self._joiner.speech_ended()
        except Exception as e:
            self.on_error(e)

    def _submit(self, georgian):
        spoken_at = self._last_speech_end or time.monotonic()
        self._pending.append(self._executor.submit(self._translate, georgian, spoken_at))

    def _translate(self, georgian, spoken_at):
        try:
            english = self.translator.translate(georgian)
        except Exception as e:
            english = f"[translation failed: {e}]"
        self.latencies.append(time.monotonic() - spoken_at)
        self.on_result(georgian, english)
