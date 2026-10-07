"""Speaking Natro's replies on the PC with Kokoro (sherpa-onnx): a female English voice, free and offline.

Model (not in git): download kokoro-en-v0_19.tar.bz2 from
https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models and extract it
into models/. Without it, replies are only shown.
NATRO_VOICE picks the speaker: 0 is "af", an American female voice.

On the PC it was measured slower than real time (a 3 s sentence took 5 s or
more, though with the CPU busy with another program), so each sentence plays
as soon as it's ready, while the next one is generated. The int8 version of
the model was slower still.
"""
import os
import queue
import threading

import numpy as np
import sherpa_onnx
import sounddevice as sd

from natro_pc.config import ROOT

MODEL_DIR = ROOT / "models" / "kokoro-en-v0_19"


class Speaker:
    """Speaks texts one after another in a background thread."""

    def __init__(self, quiet=False):
        """quiet: show replies without speaking them (and don't load the voice)."""
        self.voice = int(os.environ.get("NATRO_VOICE", "0"))
        self._queue = queue.Queue()
        self._busy = threading.Event()  # set while something is queued or playing
        self.tts = None
        self.muted = False  # the tray app's "Speak replies" switch
        if quiet:
            pass
        elif (MODEL_DIR / "model.onnx").exists():
            self.tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                        model=str(MODEL_DIR / "model.onnx"),
                        voices=str(MODEL_DIR / "voices.bin"),
                        tokens=str(MODEL_DIR / "tokens.txt"),
                        data_dir=str(MODEL_DIR / "espeak-ng-data")),
                    num_threads=2),  # more threads were no faster
                max_num_sentences=1))  # hand over audio one sentence at a time
            threading.Thread(target=self._run, daemon=True).start()
        else:
            print(f"(No voice: the Kokoro model isn't in {MODEL_DIR}; see pc/natro_pc/speak.py. Replies are shown only.)")

    @property
    def speaking(self):
        return self._busy.is_set()

    def say(self, text):
        if self.tts and not self.muted and text.strip():
            self._busy.set()
            self._queue.put(text)

    def wait(self):
        """Until everything queued has been spoken."""
        self._queue.join()

    def _run(self):
        while True:
            text = self._queue.get()
            try:
                self._speak(text)
            except Exception as e:
                print(f"(Couldn't speak: {e})")
            finally:
                self._queue.task_done()
                if self._queue.unfinished_tasks == 0:
                    self._busy.clear()

    def _speak(self, text):
        sentences = queue.Queue()

        def generated(samples, progress):
            sentences.put(np.array(samples, dtype=np.float32))
            return 1  # keep going; 0 stops (despite what the docstring says)

        player = threading.Thread(target=self._play, args=(sentences,))
        player.start()
        try:
            self.tts.generate(text, sid=self.voice, speed=1.0, callback=generated)
        finally:
            sentences.put(None)
            player.join()

    def _play(self, sentences):
        with sd.OutputStream(samplerate=self.tts.sample_rate, channels=1, dtype="float32") as stream:
            while (samples := sentences.get()) is not None:
                stream.write(samples.reshape(-1, 1))
