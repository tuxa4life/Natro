"""Natro on the PC as a tray app: an icon by the clock, a talk key, and a small window.

    pythonw -m natro_pc.tray      # no console (python -m natro_pc.tray shows one, for debugging)

- The icon's color says how Natro is: grey offline, green online, blue recording,
  amber thinking. Hovering shows that and this month's spending; right-click for
  the menu, click for the window.
- Talk key (Ctrl+Alt+Space, or NATRO_HOTKEY in .env): hold it and talk, release
  to send; or tap it and talk hands-free (recording stops when you stop
  talking), tap again to stop sooner.
- "Listen for OK Natro" in the menu turns on the wake word, checked on this PC
  as with `natro_pc.app --wake`.
- Replies show in the window, which pops up by the tray and hides itself after a
  while, and are spoken. You can type there too. When Natro asks before
  something risky: Yes or No in the window, Ctrl+Alt+Y / Ctrl+Alt+N from any
  window, or the talk key and say yes or no.
- Like the console app, it runs this PC's tools for Natro.
"""
import argparse
import asyncio
import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
import winsound
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from natro_pc.app import App, in_thread, run
from natro_pc.audio import EndOfSpeech, Microphone, mean_loudness
from natro_pc.config import ROOT, load_env
from natro_pc.hotkey import TALK_KEY, TalkKey, wait_for_answer_key
from natro_pc.listen import SILENCE_SECONDS, record_instruction, wait_for_wake_word

COLORS = {"offline": "#9e9e9e", "online": "#2e7d32", "recording": "#1565c0", "thinking": "#f9a825"}
HIDE_AFTER_SECONDS = 25
MAX_RECORDING_SECONDS = 120
TALK_KEY_NAME = TALK_KEY.replace("<", "").replace(">", "").title()


def icon_image(color):
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2, 2, 62, 62), fill=color)
    draw.line([(21, 45), (21, 19), (43, 45), (43, 19)], fill="white", width=6, joint="curve")
    return image


class Window:
    """The conversation, a box to type in, and Natro's questions.

    Tk lives on the main thread; other threads hand it work with call().
    """

    def __init__(self, on_text, on_answer):
        self.root = tk.Tk()
        self.root.title("Natro")
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.root.geometry("420x520")
        self._work = queue.Queue()
        self._hide_at = None
        self._closed = False
        self.lines = []  # [ref, who, text]; who: "you", "natro" or "note"
        font = ("Segoe UI", 10)

        self.status = tk.StringVar(value="Connecting…")
        tk.Label(self.root, textvariable=self.status, anchor="w", font=("Segoe UI", 9), fg="#555").pack(
            fill="x", padx=10, pady=(8, 0))
        self.text = tk.Text(self.root, wrap="word", state="disabled", relief="flat", padx=8, pady=8, font=font,
                            background="#fafafa")
        self.text.tag_configure("you", foreground="#1565c0", lmargin1=60, lmargin2=60, spacing3=6)
        self.text.tag_configure("natro", foreground="#212121", spacing3=6)
        self.text.tag_configure("note", foreground="#888888", font=("Segoe UI", 9, "italic"), spacing3=4)
        self.text.pack(fill="both", expand=True, padx=10, pady=8)

        self.asking = tk.Frame(self.root)
        self.question = tk.Label(self.asking, wraplength=390, justify="left", font=("Segoe UI", 10, "bold"))
        self.question.pack(fill="x")
        buttons = tk.Frame(self.asking)
        buttons.pack(anchor="e", pady=4)
        tk.Button(buttons, text="No", width=8, command=lambda: on_answer(False)).pack(side="left", padx=4)
        tk.Button(buttons, text="Yes", width=8, command=lambda: on_answer(True)).pack(side="left")
        tk.Label(self.asking, text="Or Ctrl+Alt+Y / Ctrl+Alt+N, or the talk key and say it.", fg="#777",
                 font=("Segoe UI", 8)).pack(anchor="w")

        self.typing = tk.Frame(self.root)
        self.typing.pack(fill="x", padx=10)
        self.entry = tk.Entry(self.typing, font=font)
        self.entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.entry.bind("<Return>", lambda event: self._send(on_text))
        tk.Button(self.typing, text="Send", command=lambda: self._send(on_text)).pack(side="left", padx=(6, 0))
        tk.Label(self.root, text=f"Hold {TALK_KEY_NAME} to talk; tap it for hands-free.", fg="#777",
                 font=("Segoe UI", 8)).pack(anchor="w", padx=10, pady=(2, 8))

        self.root.withdraw()
        self.root.after(50, self._poll)

    def call(self, work):
        """Run work on Tk's thread (safe from any thread)."""
        self._work.put(work)

    def _poll(self):
        while not self._work.empty():
            self._work.get()()
        if self._closed:
            return
        busy = self.root.focus_displayof() is not None or self.asking.winfo_ismapped() or self._pointer_inside()
        if self._hide_at and time.monotonic() > self._hide_at and not busy:
            self.hide()
        self.root.after(50, self._poll)

    def _pointer_inside(self):
        x, y = self.root.winfo_pointerxy()
        return (self.root.winfo_rootx() <= x < self.root.winfo_rootx() + self.root.winfo_width()
                and self.root.winfo_rooty() <= y < self.root.winfo_rooty() + self.root.winfo_height())

    def _send(self, on_text):
        text = self.entry.get().strip()
        if text:
            self.entry.delete(0, "end")
            on_text(text)

    def show(self, focus=False):
        """Pop up by the tray (without taking the keyboard unless focus), and hide again after a while."""
        if not self.root.winfo_viewable():
            width, height = 420, 520
            x = self.root.winfo_screenwidth() - width - 16
            y = self.root.winfo_screenheight() - height - 64  # above the taskbar
            self.root.geometry(f"{width}x{height}+{x}+{y}")
            self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        if focus:
            self.root.focus_force()
            self.entry.focus_set()
        self._hide_at = time.monotonic() + HIDE_AFTER_SECONDS

    def hide(self):
        self._hide_at = None
        self.root.withdraw()

    def close(self):
        self._closed = True
        self.root.destroy()

    def ask(self, question):
        self.question.configure(text=question)
        self.asking.pack(fill="x", padx=10, pady=(0, 6), before=self.typing)
        self.show()

    def done_asking(self):
        self.asking.pack_forget()

    # The conversation: lines keyed by ref, so streamed pieces land in their line.

    def add(self, ref, who, text):
        self.lines = (self.lines + [[ref, who, text]])[-100:]
        self._render()

    def put(self, ref, who, text):
        """Replace the text of the latest line for ref (or add one)."""
        for line in reversed(self.lines):
            if line[0] == ref and line[1] == who:
                line[2] = text
                return self._render()
        self.add(ref, who, text)

    def append(self, ref, who, piece):
        for line in reversed(self.lines):
            if line[0] == ref and line[1] == who:
                line[2] += piece
                return self._render()
        self.add(ref, who, piece)

    def _render(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for _, who, text in self.lines:
            self.text.insert("end", text.strip() + "\n", who)
        self.text.configure(state="disabled")
        self.text.see("end")


class TrayApp(App):
    """The console app's connection, requests and PC tools, shown in the tray and the window instead."""

    def __init__(self):
        super().__init__(argparse.Namespace(file=None, type=False, wake=False, quiet=False, save=False,
                                            manual=False, silence=SILENCE_SECONDS, verbose=False))
        self.window = self.icon = self.talk_key = None
        self.loop = None
        self.online = False
        self.offline_reason = "connecting"
        self.spent = self.budget = None
        self.recording = False
        self.pending = 0  # requests waiting for their reply
        self.answering = None  # a future for the owner's answer to Natro's question
        self._stop_recording = threading.Event()
        self._hands_free = False
        self.wake_on = False
        self._wake_stop = threading.Event()

    # Status: the icon, its tooltip and menu, the window's top line

    def state(self):
        if not self.online:
            return "offline"
        return "recording" if self.recording else "thinking" if self.pending else "online"

    def status_text(self):
        if not self.online:
            return f"Natro is offline ({self.offline_reason})"
        return {"recording": "Natro is listening…", "thinking": "Natro is thinking…"}.get(self.state(),
                                                                                         "Natro is online")

    def spending_text(self):
        if self.spent is None:
            return "This month: not known yet"
        return f"This month: ${self.spent:.2f} of ${self.budget:g}"

    def show_status(self):
        if self.icon:
            self.icon.icon = icon_image(COLORS[self.state()])
            self.icon.title = f"{self.status_text()} · {self.spending_text()}"
            self.icon.update_menu()
        if self.window:
            text = f"{self.status_text()} · {self.spending_text()}"
            self.window.call(lambda: self.window.status.set(text))

    def _spending(self, data):
        if data.get("budget") is not None:
            self.spent, self.budget = data.get("spent", 0.0), data["budget"]

    # What arrives from Natro (instead of the console's prints)

    async def talk(self):
        """Requests come from the talk key, the wake word and the window, not from here: wait for the connection to end."""
        while not self.closed:
            self.changed.clear()
            await self.changed.wait()
        self.online = False
        self.show_status()
        raise ConnectionError("lost the connection")

    def on_connected(self, url, welcome):
        self.online = True
        self._spending(welcome)
        self.show_status()

    def on_offline(self, message):
        self.online = False
        self.offline_reason = "the device token was refused" if "refused" in message else "reconnecting"
        self.show_status()

    def on_english(self, id, piece):
        self.window.call(lambda: self.window.append(id, "you", piece))

    def on_heard(self, id, data):
        text = data["english"] or "(nothing heard)"
        self.window.call(lambda: self.window.put(id, "you", text))

    def on_reply_text(self, id, piece):
        self.window.call(lambda: (self.window.append(f"{id}.reply", "natro", piece), self.window.show()))

    def on_reply(self, id, data):
        self._spending(data)
        self.window.call(lambda: (self.window.put(f"{id}.reply", "natro", data["text"]), self.window.show()))
        self.show_status()

    def on_question(self, data):
        self.window.call(lambda: self.window.ask(data["question"]))

    def on_tool(self, name, result, ok):
        line = f"[PC {name}] {result.splitlines()[0] if result else ''}"
        self.window.call(lambda: self.window.add("tool", "note", line))

    def on_error(self, id, message):
        self.window.call(lambda: (self.window.add(f"{id}.error", "note", message), self.window.show()))

    def on_note(self, text):
        self.window.call(lambda: self.window.add("note", "note", text.strip()))

    # Asking the owner

    async def answer(self, question):
        """His yes or no: the window's buttons, Ctrl+Alt+Y/N, or the talk key and spoken."""
        self.answering = self.loop.create_future()
        asked = threading.Event()

        def hotkeys():
            key = wait_for_answer_key(asked.is_set)
            if key:
                self.loop.call_soon_threadsafe(self._answered, ("key", key == "y"))

        threading.Thread(target=hotkeys, daemon=True).start()
        try:
            how, value = await self.answering
        finally:
            asked.set()
            self.answering = None
            self.window.call(self.window.done_asking)
        if how == "audio":
            await self.send_audio(value, answer_to=question["id"])
        else:
            await self.send_answer(question, value)

    def _answered(self, answer):
        if self.answering and not self.answering.done():
            self.answering.set_result(answer)

    def answer_from_window(self, yes):
        self.loop.call_soon_threadsafe(self._answered, ("key", yes))

    # Requests: typed, the talk key, the wake word

    def typed_from_window(self, text):
        asyncio.run_coroutine_threadsafe(self._typed(text), self.loop)

    async def _typed(self, text):
        if not self.online:
            self.on_note("Natro is offline.")
            return
        id = await self.send_text(text)
        self.window.call(lambda: self.window.add(id, "you", text))
        await self._waiting(self.wait_for_reply(id))

    async def _waiting(self, request):
        self.pending += 1
        self.show_status()
        try:
            await request
        except ConnectionError:
            self.on_note("Lost the connection to Natro.")
        finally:
            self.pending -= 1
            self.show_status()

    def talk_event(self, event):
        """From the talk key (on the loop's thread)."""
        if event == "start" and not self.recording:
            self.recording, self._hands_free = True, False
            self._stop_recording.clear()
            asyncio.create_task(self._record())
        elif event == "hands_free":
            self._hands_free = True
        elif event in ("send", "stop"):
            self._stop_recording.set()

    async def _record(self):
        winsound.Beep(1000, 80)
        self.show_status()
        try:
            pcm = await in_thread(self._record_until_done)
        finally:
            self.recording = False
            self.talk_key.state.finished()
            self.show_status()
        await self._spoken(pcm)

    def _record_until_done(self):
        """Until the key is released (held), he stops talking (hands-free), or the key is tapped again."""
        mic = Microphone()
        end = EndOfSpeech(SILENCE_SECONDS, 8.0)
        chunks = []
        started = time.monotonic()
        mic.start()
        try:
            while not self._stop_recording.is_set() and time.monotonic() - started < MAX_RECORDING_SECONDS:
                try:
                    chunk = mic.chunks.get(timeout=0.2)
                except queue.Empty:
                    continue
                chunks.append(chunk)
                end.add(mean_loudness(chunk))
                if self._hands_free and end.done:
                    break
        finally:
            mic.stop()
        pcm = b"".join(chunks)
        return end.trim(pcm) if self._hands_free else pcm

    async def _spoken(self, pcm):
        """A recording: his answer if Natro is asking, else a request."""
        if self.answering and not self.answering.done():
            self.answering.set_result(("audio", pcm))
        elif not self.online:
            self.on_note("Natro is offline.")
        else:
            await self._waiting(self.request_audio(pcm))

    def set_wake_word(self, on):
        if on == self.wake_on:
            return
        self.wake_on = on
        if on:
            self._wake_stop.clear()
            threading.Thread(target=self._listen_for_wake_word, daemon=True).start()
        else:
            self._wake_stop.set()
        self.show_status()

    def _listen_for_wake_word(self):
        from natro_pc.wakeword import WakeWord  # loads the model, so only when turned on

        try:
            wake = WakeWord()
        except Exception as e:
            self.on_note(f"The wake word couldn't start: {e}")
            self.wake_on = False
            self.show_status()
            return
        mic = Microphone(keep_loudness=False)
        mic.start()
        try:
            while not self._wake_stop.is_set():
                chunks = wait_for_wake_word(mic, wake, muted=lambda: self.speaker.speaking or self.recording,
                                            stop=self._wake_stop.is_set)
                if chunks is None:
                    break
                winsound.Beep(1000, 120)
                self.recording = True
                self.show_status()
                pcm = record_instruction(mic, chunks, SILENCE_SECONDS, console=False, stop=self._wake_stop.is_set)
                self.recording = False
                self.show_status()
                asyncio.run_coroutine_threadsafe(self._spoken(pcm), self.loop)
                mic.discard_waiting()
        finally:
            mic.stop()


def tray_icon(app, window, quit):
    def show(icon, item):
        window.call(lambda: window.show(focus=True))

    def toggle_wake(icon, item):
        app.set_wake_word(not app.wake_on)

    def toggle_speaking(icon, item):
        app.speaker.muted = not app.speaker.muted

    menu = pystray.Menu(
        pystray.MenuItem("Show Natro", show, default=True),
        pystray.MenuItem(lambda item: app.status_text(), lambda icon, item: None, enabled=False),
        pystray.MenuItem(lambda item: app.spending_text(), lambda icon, item: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Listen for "OK Natro"', toggle_wake, checked=lambda item: app.wake_on),
        pystray.MenuItem("Speak replies", toggle_speaking, checked=lambda item: not app.speaker.muted),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda icon, item: quit()),
    )
    return pystray.Icon("Natro", icon_image(COLORS["offline"]), "Natro: connecting…", menu)


def already_running():
    """One tray app at a time (a second one would take over the PC's connection to Natro)."""
    ctypes.windll.kernel32.CreateMutexW(None, False, "Natro.PC.Tray")
    return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def find_tcl():
    """In a virtual environment made by uv, Tk looks for its Tcl library in the wrong place: point it at Python's."""
    tcl = Path(sys.base_prefix) / "tcl"
    for variable, folder in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
        if (tcl / folder).is_dir():
            os.environ.setdefault(variable, str(tcl / folder))


def main():
    if sys.stdout is None:  # pythonw: no console, so anything printed goes to a log
        (ROOT / "logs").mkdir(exist_ok=True)
        sys.stdout = sys.stderr = open(ROOT / "logs" / "pc-tray.log", "a", encoding="utf-8", buffering=1)
    find_tcl()
    if already_running():
        ctypes.windll.user32.MessageBoxW(None, "Natro is already running (look for her icon by the clock).",
                                         "Natro", 0x40)
        return
    load_env()
    app = TrayApp()
    window = Window(on_text=app.typed_from_window, on_answer=app.answer_from_window)
    app.window = window
    app.loop = asyncio.new_event_loop()
    threading.Thread(target=lambda: app.loop.run_until_complete(run(app)), daemon=True).start()

    def quit():
        app.set_wake_word(False)
        app.talk_key.stop()
        app.icon.stop()
        window.call(window.close)

    app.icon = tray_icon(app, window, quit)
    threading.Thread(target=app.icon.run, daemon=True).start()
    app.talk_key = TalkKey(lambda event: app.loop.call_soon_threadsafe(app.talk_event, event))
    app.talk_key.start()
    window.root.mainloop()


if __name__ == "__main__":
    main()
