"""Global keys on the PC.

- Ctrl+Alt+Y and Ctrl+Alt+N answer Natro's question from any window. The keys
  are taken from Windows only while she is asking (RegisterHotKey), and given
  back right after, so other programs can use them the rest of the time.
- The talk key (Ctrl+Alt+Space unless NATRO_HOTKEY says otherwise), for the
  tray app: hold it and talk, release to send; or tap it and talk hands-free
  (recording stops when you stop talking); tap again to stop sooner.
"""
import ctypes
import os
import time
from ctypes import wintypes

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001
ANSWER_KEYS = {1: ("y", ord("Y")), 2: ("n", ord("N"))}  # hotkey id -> (answer, virtual key)


def wait_for_answer_key(stop):
    """"y" or "n" once Ctrl+Alt+Y or Ctrl+Alt+N is pressed; None as soon as stop() is true.

    Blocks: run it in its own thread (the keys arrive in this thread's message queue).
    """
    user32 = ctypes.windll.user32
    registered = [id for id, (_, key) in ANSWER_KEYS.items()
                  if user32.RegisterHotKey(None, id, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, key)]
    try:
        message = wintypes.MSG()
        while not stop():
            while user32.PeekMessageW(ctypes.byref(message), None, WM_HOTKEY, WM_HOTKEY, PM_REMOVE):
                if message.wParam in ANSWER_KEYS:
                    return ANSWER_KEYS[message.wParam][0]
            time.sleep(0.05)
        return None
    finally:
        for id in registered:
            user32.UnregisterHotKey(None, id)


TALK_KEY = os.environ.get("NATRO_HOTKEY", "<ctrl>+<alt>+<space>")
# A press shorter than this is a tap (hands-free); longer, the recording ends on release.
TAP_SECONDS = 0.4


class PressAndHold:
    """The talk key's presses and releases as what to do with the recording.

    press: "start" (or "stop", when it's recording hands-free). release: "send"
    after a hold, or "hands_free" after a tap (recording goes on until he stops
    talking). Key repeats and the release after a "stop" do nothing (None).
    """

    def __init__(self, tap_seconds=TAP_SECONDS):
        self.tap_seconds = tap_seconds
        self.down_at = None
        self.hands_free = False
        self._ignore_release = False

    def press(self, now):
        if self.down_at is not None:
            return None
        self.down_at = now
        if self.hands_free:
            self.hands_free, self._ignore_release = False, True
            return "stop"
        return "start"

    def release(self, now):
        if self.down_at is None:
            return None
        held, self.down_at = now - self.down_at, None
        if self._ignore_release:
            self._ignore_release = False
            return None
        if held < self.tap_seconds:
            self.hands_free = True
            return "hands_free"
        return "send"

    def finished(self):
        """The recording ended by itself (he stopped talking while hands-free)."""
        self.hands_free = False


class TalkKey:
    """Watches the talk key from any window and calls on_event with PressAndHold's events.

    Uses a keyboard hook (pynput), since Windows' RegisterHotKey doesn't report releases.
    on_event is called on the hook's thread.
    """

    def __init__(self, on_event, combo=TALK_KEY):
        from pynput import keyboard

        self.on_event = on_event
        self.keys = set(keyboard.HotKey.parse(combo))
        self.down = set()
        self.active = False
        self.state = PressAndHold()
        self.listener = keyboard.Listener(on_press=self._press, on_release=self._release)

    def start(self):
        self.listener.start()

    def stop(self):
        self.listener.stop()

    def _press(self, key):
        self.down.add(self.listener.canonical(key))
        if not self.active and self.keys <= self.down:
            self.active = True
            self._emit(self.state.press(time.monotonic()))

    def _release(self, key):
        self.down.discard(self.listener.canonical(key))
        if self.active and not self.keys <= self.down:
            self.active = False
            self._emit(self.state.release(time.monotonic()))

    def _emit(self, event):
        if event:
            self.on_event(event)
