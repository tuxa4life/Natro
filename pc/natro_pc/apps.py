"""Apps on the PC: open them from the Start menu, see which are open, close them.

Apps are found the way the Start menu lists them (Get-StartApps: desktop and Store
apps alike) and opened through shell:AppsFolder, as if clicked there. Closing asks
each of the app's windows to close, as clicking X does, so the app itself asks
about unsaved work. Force-closing kills its processes; the brain asks the owner
first.
"""
import ctypes
import difflib
import functools
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from natro_pc.tools import PCTool, ToolError

# Start menu entries Natro never opens: uninstallers, help pages and web links.
NOT_APPS = re.compile(r"\b(uninstall|readme|help|documentation|website|release notes)\b", re.IGNORECASE)
# What people call some apps, by their Start menu names.
ALIASES = {"vs code": "visual studio code", "vscode": "visual studio code", "code": "visual studio code",
           "chrome": "google chrome", "edge": "microsoft edge", "explorer": "file explorer",
           "files": "file explorer", "cmd": "command prompt", "store": "microsoft store",
           "task manager": "task manager", "control panel": "control panel"}
GOOD_MATCH = 0.6
RELOAD_SECONDS = 30
CLOSE_WAIT_SECONDS = 3
# Never force-closed, whatever they're called.
PROTECTED = {"system", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
             "svchost.exe", "dwm.exe", "explorer.exe", "fontdrvhost.exe", "sihost.exe", "tailscaled.exe",
             "tailscale-ipn.exe", "everything.exe", "syncthing.exe"}
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def words(text):
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def match_score(query, name):
    """How well a spoken app name matches a real one, 0 to 1."""
    query, name = words(query), words(name)
    query = ALIASES.get(query, query)
    if not query or not name:
        return 0.0
    if query == name or query.replace(" ", "") == name.replace(" ", ""):
        return 1.0
    name_words = name.split()
    if all(any(word.startswith(part) for word in name_words) for part in query.split()):
        return 0.9 - 0.02 * (len(name_words) - len(query.split()))  # "Spotify" over "Spotify Widget"
    if query in name:
        return 0.75
    return 0.75 * difflib.SequenceMatcher(None, query, name).ratio()


def best(query, candidates, label=lambda c: c):
    """The candidates that match query well enough, best first."""
    scored = sorted(((match_score(query, label(c)), c) for c in candidates), key=lambda pair: -pair[0])
    return [c for score, c in scored if score >= GOOD_MATCH]


def closest_names(query, names, count=5):
    return difflib.get_close_matches(words(query), [words(n) for n in names], n=count, cutoff=0.3)


# Opening

@dataclass
class StartApp:
    name: str
    app_id: str


def start_apps():
    """The Start menu's apps (blocking: runs PowerShell, about a second)."""
    result = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
                            capture_output=True, text=True, encoding="utf-8", timeout=30, creationflags=NO_WINDOW)
    listed = json.loads(result.stdout or "[]")
    listed = [listed] if isinstance(listed, dict) else listed
    return [StartApp(item["Name"], item["AppID"]) for item in listed
            if not NOT_APPS.search(item["Name"]) and not item["AppID"].startswith(("http:", "https:"))]


class StartMenu:
    def __init__(self, load=start_apps, launch=None):
        self._load = load
        self._launch = launch or (lambda app_id: subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"]))
        self.apps, self.loaded_at = [], 0.0
        self._loading = threading.Lock()

    def _reload(self):
        with self._loading:
            self.apps, self.loaded_at = self._load(), time.monotonic()

    def warm_up(self):
        """Load the list ahead of the first request (it takes about a second)."""
        if not self.apps:
            self._reload()

    def find(self, name):
        if not self.apps:
            self._reload()
        found = best(name, self.apps, lambda app: app.name)
        if not found and time.monotonic() - self.loaded_at > RELOAD_SECONDS:  # maybe just installed
            self._reload()
            found = best(name, self.apps, lambda app: app.name)
        if not found:
            close = closest_names(name, [app.name for app in self.apps])
            hint = f" The closest names: {', '.join(close)}." if close else ""
            raise ToolError(f"No app called {name!r} is installed on the PC.{hint}")
        return found[0]

    def open(self, app):
        found = self.find(app)
        self._launch(found.app_id)
        return f"Opened {found.name}."


# Open windows

@dataclass
class Window:
    hwnd: int
    pid: int
    title: str
    exe: str  # full path of the program behind the window
    app: str  # what the program calls itself ("Google Chrome"), else its file name


def is_cloaked(hwnd):
    """Store apps keep hidden ("cloaked") windows that look visible."""
    cloaked = ctypes.c_int(0)
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return bool(cloaked.value)


@functools.lru_cache(maxsize=256)
def program_name(exe):
    """A program's own name for itself (its FileDescription), else its file name."""
    import win32api

    try:
        language, codepage = win32api.GetFileVersionInfo(exe, "\\VarFileInfo\\Translation")[0]
        described = win32api.GetFileVersionInfo(exe, f"\\StringFileInfo\\{language:04x}{codepage:04x}\\FileDescription")
        if described and described.strip():
            return described.strip()
    except Exception:
        pass
    return Path(exe).stem


def process_path(pid):
    import psutil

    try:
        return psutil.Process(pid).exe()
    except Exception:
        return ""


def open_windows():
    """The app windows on screen (or minimized), as the taskbar shows them."""
    import win32con
    import win32gui
    import win32process

    found = []

    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or win32gui.GetWindow(hwnd, win32con.GW_OWNER):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title or win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW:
            return True
        if is_cloaked(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        exe = process_path(pid)
        if Path(exe).name.lower() == "applicationframehost.exe":
            # A Store app's frame: the app itself is the process of a child window.
            children = []
            win32gui.EnumChildWindows(hwnd, lambda child, _: children.append(child) or True, None)
            pids = {win32process.GetWindowThreadProcessId(child)[1] for child in children} - {pid}
            if pids:
                pid = pids.pop()
                exe = process_path(pid)
            found.append(Window(hwnd, pid, title, exe, title))
        else:
            found.append(Window(hwnd, pid, title, exe, program_name(exe) if exe else title))
        return True

    win32gui.EnumWindows(visit, None)
    return found


def group_by_app(windows):
    apps = {}
    for window in windows:
        apps.setdefault(window.app, []).append(window)
    return apps


def find_open_app(name, windows):
    """(app name, its windows) for the open app that best matches name."""
    apps = group_by_app(windows)

    def score(app):
        exes = {Path(w.exe).stem for w in apps[app] if w.exe}
        return max([match_score(name, app)] + [match_score(name, exe) for exe in exes])

    ranked = sorted(apps, key=score, reverse=True)
    if ranked and score(ranked[0]) >= GOOD_MATCH:
        return ranked[0], apps[ranked[0]]
    # Else a window whose title names it ("Spotify" in a browser tab's title doesn't win over the app itself).
    titled = [w for w in windows if words(name) and words(name) in words(w.title)]
    if titled:
        return titled[0].app, [w for w in windows if w.app == titled[0].app]
    open_now = ", ".join(sorted(apps)) or "none"
    raise ToolError(f"No open app called {name!r}. Open apps: {open_now}.")


class Apps:
    def __init__(self, menu=None, windows=open_windows):
        self.menu = menu or StartMenu()
        self._windows = windows

    def open(self, app):
        return self.menu.open(app)

    def running(self):
        apps = group_by_app(self._windows())
        if not apps:
            return "No apps are open."
        return "Open apps: " + ", ".join(f"{app} ({len(ws)} windows)" if len(ws) > 1 else app
                                         for app, ws in sorted(apps.items())) + "."

    def close(self, app):
        import win32con
        import win32gui

        name, windows = find_open_app(app, self._windows())
        for window in windows:
            win32gui.PostMessage(window.hwnd, win32con.WM_CLOSE, 0, 0)
        deadline = time.monotonic() + CLOSE_WAIT_SECONDS
        while time.monotonic() < deadline:
            still = [w for w in windows if win32gui.IsWindow(w.hwnd) and win32gui.IsWindowVisible(w.hwnd)]
            if not still:
                return f"Closed {name}."
            time.sleep(0.2)
        return (f"Asked {name} to close, but {len(still)} of its windows are still open: it may be asking about "
                "unsaved work.")

    def force_close(self, app):
        import psutil

        windows = self._windows()
        try:
            name, matched = find_open_app(app, windows)
            exes = {w.exe.lower() for w in matched if w.exe}
        except ToolError:
            name, exes = app, set()
        protected_pids = {os.getpid(), *(p.pid for p in psutil.Process().parents())}
        victims = []
        for process in psutil.process_iter(["pid", "name", "exe"]):
            exe = (process.info["exe"] or "").lower()
            process_name = (process.info["name"] or "").lower()
            if process.info["pid"] in protected_pids or process_name in PROTECTED:
                continue
            # Its windows' programs; or, with no window (say, in the tray), a program of that name.
            if (exe and exe in exes) or (not exes and exe and match_score(app, program_name(process.info["exe"]))
                                         >= GOOD_MATCH):
                victims.append(process)
        if not victims:
            raise ToolError(f"No running app called {app!r}.")
        killed = 0
        for process in victims:
            try:
                process.kill()
                killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if not killed:
            raise ToolError(f"Windows didn't let Natro force-close {name}.")
        return f"Force-closed {name} ({killed} process{'es' if killed > 1 else ''})."


def tools(apps=None):
    apps = apps or Apps()
    app = {"type": "string", "description": "The app's name, as he says it (\"Spotify\", \"VS Code\")."}
    return [
        PCTool("open_app", "Open an app on the PC by name, as from the Start menu.", apps.open, {"app": app},
               ["app"], final=True),
        PCTool("close_app", "Close an app normally, as if clicking X on its windows: the app itself asks about "
               "unsaved work. Some apps (like Spotify) only hide in the tray.", apps.close, {"app": app}, ["app"],
               final=True),
        PCTool("force_close_app", "Kill an app that won't close or is stuck, with all its processes. Unsaved work "
               "in it is lost.", apps.force_close, {"app": app}, ["app"], confirm=True),
        PCTool("list_open_apps", "Which apps have windows open on the PC.", apps.running),
    ]
