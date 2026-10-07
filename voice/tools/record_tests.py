"""Record test clips for the Natro Voice test set with the PC microphone.

Each clip is saved as testset/clips/clipNNN.wav (16 kHz mono, 16-bit).
Afterwards, run `python tools/testset.py draft` to draft their text.

Usage: python tools/record_tests.py
"""
import queue
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sounddevice as sd

from natro_voice import testset
from natro_voice.speech import SAMPLE_RATE

# Talk about these in Georgian, the way you normally would, English words included.
TOPICS = [
    "What you did today, at work or study",
    "A show, film or video you watched recently",
    "Apps and sites you use on your phone and why",
    "Plans for the weekend",
    "Something you saw on Instagram, TikTok or YouTube",
    "A problem you had with a computer, phone or app",
    "A person you know and what they do",
    "Music you've been listening to (songs, artists)",
    "A task you'd give an assistant: open, find, send or schedule something",
    "A task you'd give an assistant: write or summarize something",
    "Explain your job or project to someone new",
    "Food, a restaurant, or a place you went",
]


def total_minutes():
    return sum(testset.duration(c) for c in testset.clip_ids()) / 60


def record():
    chunks = queue.Queue()

    def callback(indata, frames, time, status):
        if status:
            print(status, file=sys.stderr)
        chunks.put(bytes(indata))

    with sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback):
        input("  Recording... press Enter to stop.")

    pcm = bytearray()
    while not chunks.empty():
        pcm += chunks.get()
    return bytes(pcm)


def main():
    print(f"Microphone: {sd.query_devices(kind='input')['name']}")
    print(f"Saving to:  {testset.CLIPS_DIR}")
    print("Aim for 20-60 seconds per clip and about 30 minutes in total.\n")

    while True:
        print(f"Recorded so far: {total_minutes():.1f} min")
        print(f"Topic idea: {random.choice(TOPICS)}")
        if input("Press Enter to record, or type q to quit: ").strip().lower() == "q":
            break

        pcm = record()
        seconds = len(pcm) / (SAMPLE_RATE * 2)
        if input(f"  {seconds:.0f} s recorded. Keep it? [Y/n] ").strip().lower() == "n":
            print("  Discarded.\n")
            continue

        clip_id = testset.next_clip_id()
        testset.write_wav(clip_id, pcm)
        print(f"  Saved {clip_id}.\n")

    print("Next: python tools/testset.py draft")


if __name__ == "__main__":
    main()
