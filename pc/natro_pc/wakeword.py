"""Listening for "OK Natro" on this PC: free, offline, nothing is sent anywhere.

Each time someone starts speaking after a pause, a local Whisper model
transcribes the first few seconds; if they start with "OK Natro", that speech
is an instruction. Only then does audio go to Google and Claude.

Why Whisper: an English keyword spotter (sherpa-onnx KWS) couldn't hear
"Natro" in any spelling (1 hit in 7 test clips). whisper-base with the
language set to Georgian writes it as "ok natro", but also "ok natural",
"ok, neutral" or "ok, no tro" depending on exactly where the audio starts,
so the match accepts any n-vowel-t-r word after "OK"; none of ~40 other
speech starts in the test clips matched. whisper-tiny and the English setting
did much worse. A check takes about 0.8 s on the CPU.

Model (about 75 MB used): https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-whisper-base.tar.bz2
extracted into models/.
"""
import array
import re

import sherpa_onnx

from natro_pc.config import ROOT
from natro_pc.audio import SAMPLE_RATE

MODEL_DIR = ROOT / "models" / "sherpa-onnx-whisper-base"
# How much of the start of each utterance is checked for the wake phrase.
CHECK_SECONDS = 2.5
# Audio kept from just before speech starts; with less, "OK" gets clipped ("Kri natro").
LEAD_IN_SECONDS = 0.5
# Speech only counts as a new utterance after at least this much quiet.
MIN_PAUSE_SECONDS = 0.8

OK_WORDS = {"ok", "okay", "okey", "oke", "ოკ", "ოკეი", "ოქეი"}
# natro, natrou, natural, neutral, notro ("no tro"), ნატრო
NATRO_LIKE = re.compile(r"^(n[aeiouy]{1,2}t[aeiou]?r|ნ[ა-ჰ]{0,2}ტრ)")


def is_wake_phrase(text):
    """Whether the text has "OK" followed by something like "Natro"."""
    words = re.sub(r"[^\w\s]", " ", text.casefold()).split()
    for i, word in enumerate(words):
        # Join the next two words too, since Whisper sometimes splits it ("ok, no tro").
        if word in OK_WORDS and NATRO_LIKE.match("".join(words[i + 1:i + 3])):
            return True
    return False


class WakeWord:
    def __init__(self):
        if not MODEL_DIR.exists():
            raise FileNotFoundError(f"Wake-word model missing: {MODEL_DIR} (see pc/natro_pc/wakeword.py for the download)")
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=str(MODEL_DIR / "base-encoder.int8.onnx"),
            decoder=str(MODEL_DIR / "base-decoder.int8.onnx"),
            tokens=str(MODEL_DIR / "base-tokens.txt"),
            language="ka",
            num_threads=2,
        )

    def transcribe(self, pcm):
        stream = self._recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, [sample / 32768 for sample in array.array("h", pcm)])
        self._recognizer.decode_stream(stream)
        return stream.result.text.strip()
