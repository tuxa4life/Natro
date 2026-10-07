"""Natro on the PC: say "OK Natro" (or press Enter, or type), and get her reply on screen and out loud.

Recordings go to Natro's brain (NATRO_SERVER: the VPS, over Tailscale), which
recognizes and translates them, decides what to do, and replies here. Nothing
leaves this PC before you press Enter or say "OK Natro": the wake word is
checked on this PC. Recordings without speech are never sent.

When Natro asks before a risky action, say "OK Natro, yes" (or no), or press y
or n (Ctrl+Alt+Y / Ctrl+Alt+N from any window). Each reply ends with how long it
took after you stopped talking and what it cost (voice + agent).

While the app runs, Natro can use this PC's tools (natro_pc/apps.py, files.py):
open and close apps, find, read and open files.

Usage:
    python -m natro_pc.app --wake             # runs in the background; say "OK Natro, ..."
    python -m natro_pc.app                    # press Enter to record
    python -m natro_pc.app --type             # type requests in English (no microphone)
    python -m natro_pc.app --type --quiet     # ...and only show the replies, don't speak them
    python -m natro_pc.app --file clip.wav    # send a recording (16 kHz mono WAV) as a request
    python -m natro_pc.app --save             # also keep each recording as a test clip
    python -m natro_pc.app --silence 4        # stop recording after 4 s of silence instead of 3
"""
import argparse
import asyncio
import itertools
import json
import os
import sys
import threading
import time
import wave
import winsound

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from natro_pc.audio import BYTES_PER_SECOND, SAMPLE_RATE, Microphone, has_speech
from natro_pc.config import ROOT, load_env
from natro_pc.hotkey import wait_for_answer_key
from natro_pc.listen import SILENCE_SECONDS, keys_pressed, record, record_instruction, wait_for_wake_word
from natro_pc.speak import Speaker
from natro_pc.tools import ToolError, all_tools

DEFAULT_SERVER = "ws://127.0.0.1:8700"
MAX_MESSAGE_BYTES = 16 * 2**20
# Recordings shorter than this are ignored (an accidental double Enter).
MIN_SECONDS = 0.5
# Silence that ends a spoken yes or no.
ANSWER_SILENCE_SECONDS = 1.5
RETRY_SECONDS = 5
CLIPS_DIR = ROOT / "testset" / "clips"


def in_thread(func, *args):
    """Run blocking func in a daemon thread and await its result; Ctrl+C can always quit."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def settle(method, value):
        if not future.done():
            method(value)

    def work():
        try:
            result = func(*args)
        except BaseException as e:
            loop.call_soon_threadsafe(settle, future.set_exception, e)
        else:
            loop.call_soon_threadsafe(settle, future.set_result, result)

    threading.Thread(target=work, daemon=True).start()
    return future


def read_wav(path):
    with wave.open(str(path), "rb") as f:
        if (f.getframerate(), f.getnchannels(), f.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise ValueError(f"{path} is not a 16 kHz mono 16-bit WAV file")
        return f.readframes(f.getnframes())


def save_clip(pcm, georgian, english):
    """Keep a recording as a test clip, with what Natro heard as the draft to check (see voice/tools/testset.py)."""
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    numbers = [int(p.stem[4:]) for p in CLIPS_DIR.glob("clip*.wav") if p.stem[4:].isdigit()]
    clip_id = f"clip{max(numbers, default=0) + 1:03d}"
    with wave.open(str(CLIPS_DIR / f"{clip_id}.wav"), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(pcm)
    (CLIPS_DIR / f"{clip_id}.draft.ka.txt").write_text(georgian + "\n", encoding="utf-8")
    (CLIPS_DIR / f"{clip_id}.draft.en.txt").write_text(english + "\n", encoding="utf-8")
    print(f"  saved as test clip {clip_id}: correct its draft text, then run voice/tools/testset.py approve {clip_id}")


class App:
    def __init__(self, args):
        self.args = args
        self.silence = None if args.manual else args.silence
        self.speaker = Speaker(quiet=args.quiet)
        self.ids = itertools.count(1)
        self.ws = None
        self.replies = {}  # request id -> its reply message, once it arrives
        self.question = None  # Natro's confirm message, waiting for the owner's answer
        self.changed = asyncio.Event()
        self.closed = False
        self.sent_at = {}  # request id -> when it was sent
        self.recordings = {}  # request id -> recording, kept for --save until "heard" arrives
        self.streaming = None  # what is being printed piece by piece: ("english" | "reply", id)
        self.mic = self.wake = None
        self.stopping = False
        self.tools = {tool.name: tool for tool in all_tools()}

    async def run(self, ws, url):
        self.ws, self.closed = ws, False
        await ws.send(json.dumps({"type": "hello", "device": "pc", "label": "PC",
                                  "token": os.environ.get("NATRO_DEVICE_TOKEN", ""),
                                  "tools": [tool.spec() for tool in self.tools.values()]}))
        welcome = json.loads(await ws.recv())
        if welcome.get("type") != "welcome":
            raise ConnectionError("Natro didn't welcome this device")
        self.on_connected(url, welcome)
        receiver = asyncio.create_task(self.receive())
        try:
            await self.talk()
        finally:
            receiver.cancel()

    async def talk(self):
        """Take the owner's requests until he quits (the tray app has its own ways)."""
        if self.args.file:
            await self.send_files()
        elif self.args.type:
            await self.typed()
        elif self.args.wake:
            await self.listen_for_wake_word()
        else:
            await self.press_enter()

    # Messages from Natro

    async def receive(self):
        try:
            async for message in self.ws:
                self.handle(json.loads(message))
        except ConnectionClosed:
            pass
        finally:
            self.closed = True
            self.changed.set()

    def handle(self, data):
        kind, id = data.get("type"), data.get("id")
        if kind == "english":
            self.on_english(id, data["text"])
        elif kind == "heard":
            self.on_heard(id, data)
            if self.args.save and id in self.recordings:
                save_clip(self.recordings.pop(id), data["georgian"], data["english"])
        elif kind == "reply_text":
            self.on_reply_text(id, data["text"])
        elif kind == "reply":
            self.on_reply(id, data)
            self.speaker.say(data["text"])
            self.replies[id] = data
        elif kind == "confirm":
            self.on_question(data)
            winsound.Beep(800, 150)
            self.speaker.say(data["question"])
            self.question = data
        elif kind == "tool_call":
            asyncio.create_task(self.run_tool(id, data.get("name"), data.get("args") or {}))
        elif kind == "error":
            self.on_error(id, data["message"])
            self.replies[id] = data
        self.changed.set()

    # What the console shows (the tray app shows it in its window instead)

    def on_connected(self, url, welcome):
        print(f"Connected to Natro at {url}.\n")

    def on_offline(self, message):
        print(message)

    def on_english(self, id, piece):
        self.stream(("english", id), "  en: ", piece)

    def on_heard(self, id, data):
        self.end_stream()
        print(f"  ka: {data['georgian']}")
        if id in self.sent_at:
            print(f"  (heard after {time.monotonic() - self.sent_at[id]:.1f} s)")

    def on_reply_text(self, id, piece):
        self.stream(("reply", id), "  Natro: ", piece)

    def on_reply(self, id, data):
        streamed = self.streaming == ("reply", id)
        self.end_stream()
        if not streamed:
            print(f"  Natro: {data['text']}")
        timing = f"reply after {time.monotonic() - self.sent_at.pop(id):.1f} s, " if id in self.sent_at else ""
        print(f"  ({timing}{data.get('model')}, ${data.get('cost', 0):.4f})\n")

    def on_question(self, data):
        self.end_stream()
        print(f"\n  ? {data['question']}\n    ({data['details']})")

    def on_tool(self, name, result, ok):
        self.end_stream()
        print(f"  [PC {name}] {result.splitlines()[0] if result else ''}")

    def on_error(self, id, message):
        self.end_stream()
        print(f"  ! {message}\n")

    def on_note(self, text):
        print(text)

    def stream(self, key, prefix, piece):
        if self.streaming != key:
            self.end_stream()
            print(prefix, end="")
            self.streaming = key
        print(piece, end="", flush=True)

    def end_stream(self):
        if self.streaming:
            print()
            self.streaming = None

    async def run_tool(self, id, name, args):
        """Run one of this PC's tools for Natro and send back what happened."""
        tool = self.tools.get(name)
        try:
            if tool is None:
                raise ToolError(f"The PC has no tool called {name!r}.")
            result, ok = await in_thread(lambda: tool.run(**args)), True
        except ToolError as e:
            result, ok = str(e), False
        except Exception as e:
            result, ok = f"{name} failed on the PC: {type(e).__name__}: {e}", False
        self.on_tool(name, result, ok)
        await self.ws.send(json.dumps({"type": "tool_result", "id": id, "ok": ok, "result": result}))

    # Requests

    async def send_text(self, text):
        id = f"r{next(self.ids)}"
        self.sent_at[id] = time.monotonic()
        await self.ws.send(json.dumps({"type": "text", "id": id, "text": text}))
        return id

    async def send_audio(self, pcm, answer_to=None):
        id = f"r{next(self.ids)}"
        message = {"type": "audio", "id": id}
        if answer_to:
            message["answer_to"] = answer_to
        else:
            self.sent_at[id] = time.monotonic()
        await self.ws.send(json.dumps(message))
        await self.ws.send(pcm)
        return id

    async def request_audio(self, pcm):
        if len(pcm) / BYTES_PER_SECOND < MIN_SECONDS:
            self.on_note("Too short, ignored.\n")
        elif not has_speech(pcm):
            self.on_note("No speech detected, nothing sent.\n")
        else:
            id = await self.send_audio(pcm)
            if self.args.save:
                self.recordings[id] = pcm
            await self.wait_for_reply(id)

    async def wait_for_reply(self, id):
        """Until the reply to request id arrives, answering Natro's questions on the way."""
        while id not in self.replies:
            if self.closed:
                raise ConnectionError("lost the connection to Natro")
            if self.question:
                question, self.question = self.question, None
                await self.answer(question)
                continue
            self.changed.clear()
            await self.changed.wait()
        return self.replies.pop(id)

    async def answer(self, question):
        """The owner's yes or no to a question: typed or a key, or spoken."""
        if self.args.type or self.args.file:
            # Typing doesn't have to wait for the spoken question to end.
            typed = await in_thread(input, "    yes or no? [y/n] ")
            await self.send_answer(question, typed.strip().lower() in ("y", "yes"))
        elif self.args.wake:
            await in_thread(self.speaker.wait)  # let Natro finish asking, or the microphone hears her
            print('    Say "OK Natro, yes" or "OK Natro, no", or press y or n (Ctrl+Alt+Y / Ctrl+Alt+N anywhere).')
            keys = []  # pressed in this window, or "y"/"n" from the hotkeys
            asked = threading.Event()

            def hotkeys():
                if answer := wait_for_answer_key(asked.is_set):
                    keys.append(answer)

            threading.Thread(target=hotkeys, daemon=True).start()

            def key_answer():
                keys.append(keys_pressed())
                return self.stopping or any(key in "yn" for key in "".join(keys))

            self.mic.discard_waiting()
            try:
                chunks = await in_thread(wait_for_wake_word, self.mic, self.wake, self.args.verbose,
                                         lambda: self.speaker.speaking, key_answer)
            finally:
                asked.set()  # give the hotkeys back
            if chunks is None:
                pressed = next((key for key in "".join(keys) if key in "yn"), "n")
                await self.send_answer(question, pressed == "y")
            else:
                winsound.Beep(1000, 120)
                pcm = await in_thread(record_instruction, self.mic, chunks, ANSWER_SILENCE_SECONDS)
                await self.send_audio(pcm, answer_to=question["id"])
        else:
            await in_thread(self.speaker.wait)
            typed = await in_thread(input, "    Type y or n, or press Enter and say yes or no: ")
            if typed.strip():
                await self.send_answer(question, typed.strip().lower() in ("y", "yes"))
            else:
                await self.send_audio(await in_thread(record, ANSWER_SILENCE_SECONDS), answer_to=question["id"])

    async def send_answer(self, question, yes):
        self.on_note("    yes" if yes else "    no")
        await self.ws.send(json.dumps({"type": "answer", "id": question["id"], "yes": yes}))

    # Ways to talk to Natro

    async def typed(self):
        while True:
            text = (await in_thread(input, "> ")).strip()
            if text.lower() in ("q", "quit", "exit"):
                return
            if text:
                await self.wait_for_reply(await self.send_text(text))

    async def press_enter(self):
        while (await in_thread(input, "Press Enter to speak, or type q to quit: ")).strip().lower() != "q":
            await self.request_audio(await in_thread(record, self.silence))

    async def send_files(self):
        for path in self.args.file:
            print(f"{path}:")
            await self.request_audio(read_wav(path))
        await in_thread(self.speaker.wait)

    async def listen_for_wake_word(self):
        from natro_pc.wakeword import WakeWord  # loads the model, so only in this mode

        self.wake = WakeWord()
        self.mic = Microphone(keep_loudness=False)
        self.mic.start()
        print('Listening for "OK Natro" (checked on this PC; nothing is sent until you say it). Ctrl+C to quit.\n')
        try:
            while True:
                chunks = await in_thread(wait_for_wake_word, self.mic, self.wake, self.args.verbose,
                                         lambda: self.speaker.speaking, lambda: self.stopping)
                winsound.Beep(1000, 120)
                print('● "OK Natro" heard, go ahead...')
                await self.request_audio(await in_thread(record_instruction, self.mic, chunks, self.silence))
                await in_thread(self.speaker.wait)
                self.mic.discard_waiting()  # don't scan what was said while Natro was busy
        finally:
            self.stopping = True
            self.mic.stop()


async def run(app):
    """Connect app to Natro, and reconnect whenever the connection is lost."""
    url = os.environ.get("NATRO_SERVER", DEFAULT_SERVER)
    while True:
        try:
            async with connect(url, max_size=MAX_MESSAGE_BYTES) as ws:
                await app.run(ws, url)
                return
        except ConnectionClosed as e:
            if e.rcvd and e.rcvd.code == 1008:
                app.on_offline("Natro refused this device: check NATRO_DEVICE_TOKEN in .env.")
                return
            app.on_offline(f"Lost the connection to Natro. Trying again in {RETRY_SECONDS} s...")
        except OSError as e:  # includes ConnectionError
            app.on_offline(f"Can't reach Natro at {url} ({e}). Trying again in {RETRY_SECONDS} s...")
        await asyncio.sleep(RETRY_SECONDS)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wake", action="store_true", help='run in the background and start on "OK Natro"')
    parser.add_argument("--type", action="store_true", help="type requests in English instead of speaking")
    parser.add_argument("--file", nargs="+", help="send these recordings (16 kHz mono WAV) as requests")
    parser.add_argument("--silence", type=float, default=SILENCE_SECONDS, help="seconds of silence that end a recording")
    parser.add_argument("--manual", action="store_true", help="don't stop on silence; only Enter stops")
    parser.add_argument("--quiet", action="store_true", help="show replies without speaking them")
    parser.add_argument("--save", action="store_true", help="keep each recording as a test clip (testset/clips)")
    parser.add_argument("--verbose", action="store_true", help="with --wake, show what each wake-word check heard")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    load_env()
    try:
        asyncio.run(run(App(args)))
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
