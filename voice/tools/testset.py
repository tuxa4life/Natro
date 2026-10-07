"""Manage the test set: add recordings, draft their text, and mark them checked.

Each clip needs the Georgian you actually said (clipNNN.ka.txt) and a correct
English translation (clipNNN.en.txt). Drafts are made by Google Chirp 3
(transcribing the whole clip at once) and Claude Opus; correct them by hand,
then approve.

Usage:
    python tools/testset.py add voice1.m4a voice2.aac   # copy recordings in and draft them
    python tools/testset.py draft                       # draft clips recorded with record_tests.py
    python tools/testset.py approve clip003 clip004     # drafts checked: use them for scoring
    python tools/testset.py status
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from natro_voice import testset
from natro_voice.config import load_env, load_wordlist
from natro_voice.speech import Recognizer, decode_to_pcm
from natro_voice.translate import Translator

DRAFT_MODEL = "claude-opus-5-5"


def draft(clip_id):
    recognizer = Recognizer()
    heard, as_georgian = recognizer.transcribe(testset.read_pcm(clip_id))
    georgian = as_georgian or heard
    translator = Translator(model=DRAFT_MODEL, terms=load_wordlist())
    english = translator.translate(georgian) if georgian else ""
    testset.write_text(clip_id, ".draft.ka.txt", georgian)
    testset.write_text(clip_id, ".draft.en.txt", english)
    print(f"\n{clip_id} ({testset.duration(clip_id):.0f} s) draft, cost ${recognizer.cost + translator.cost:.3f}")
    print(f"  ka: {georgian}")
    print(f"  en: {english}")


def add(files):
    for file in files:
        clip_id = testset.next_clip_id()
        testset.write_wav(clip_id, decode_to_pcm(file))
        print(f"Added {file} as {clip_id}")
        draft(clip_id)


def draft_new(clip_ids):
    todo = clip_ids or [c for c in testset.clip_ids() if testset.state(c) == "new"]
    if not todo:
        print("Nothing to draft.")
    for clip_id in todo:
        draft(clip_id)


def approve(clip_ids):
    for clip_id in clip_ids:
        for kind in ("ka", "en"):
            testset.path(clip_id, f".draft.{kind}.txt").replace(testset.path(clip_id, f".{kind}.txt"))
        print(f"{clip_id} checked")


def status():
    totals = {"new": 0.0, "draft": 0.0, "checked": 0.0}
    for clip_id in testset.clip_ids():
        seconds, state = testset.duration(clip_id), testset.state(clip_id)
        totals[state] += seconds
        print(f"{clip_id}  {seconds:4.0f} s  {state}")
    print(f"\nChecked: {totals['checked'] / 60:.1f} min · drafts to check: {totals['draft'] / 60:.1f} min · "
          f"not drafted: {totals['new'] / 60:.1f} min")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("add").add_argument("files", nargs="+")
    commands.add_parser("draft").add_argument("clips", nargs="*")
    commands.add_parser("approve").add_argument("clips", nargs="+")
    commands.add_parser("status")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    load_env()
    if args.command == "add":
        add(args.files)
    elif args.command == "draft":
        draft_new(args.clips)
    elif args.command == "approve":
        approve(args.clips)
    else:
        status()


if __name__ == "__main__":
    main()
