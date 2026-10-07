"""Test-set files in testset/clips/.

clipNNN.wav                                  16 kHz mono recording
clipNNN.draft.ka.txt, clipNNN.draft.en.txt   machine drafts, not checked yet
clipNNN.ka.txt, clipNNN.en.txt               checked by the speaker; used for scoring
"""
import wave

from natro_voice.config import ROOT
from natro_voice.speech import SAMPLE_RATE

CLIPS_DIR = ROOT / "testset" / "clips"


def clip_ids():
    return sorted(p.stem for p in CLIPS_DIR.glob("clip*.wav"))


def next_clip_id():
    numbers = [int(c[4:]) for c in clip_ids() if c[4:].isdigit()]
    return f"clip{max(numbers, default=0) + 1:03d}"


def path(clip_id, suffix):
    return CLIPS_DIR / f"{clip_id}{suffix}"


def state(clip_id):
    """"checked", "draft" or "new"."""
    if path(clip_id, ".ka.txt").exists() and path(clip_id, ".en.txt").exists():
        return "checked"
    if path(clip_id, ".draft.ka.txt").exists():
        return "draft"
    return "new"


def read_text(clip_id, suffix):
    return path(clip_id, suffix).read_text(encoding="utf-8").strip()


def write_text(clip_id, suffix, text):
    path(clip_id, suffix).write_text(text.strip() + "\n", encoding="utf-8")


def read_pcm(clip_id):
    with wave.open(str(path(clip_id, ".wav")), "rb") as wf:
        return wf.readframes(wf.getnframes())


def write_wav(clip_id, pcm):
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path(clip_id, ".wav")), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)


def duration(clip_id):
    with wave.open(str(path(clip_id, ".wav")), "rb") as wf:
        return wf.getnframes() / wf.getframerate()
