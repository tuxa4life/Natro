"""Spotify on the owner's Premium account: playback on his Spotify Connect devices, his playlists and Liked Songs.

Signed in once on the PC with `python -m natro_agent.spotify_signin` (it needs a
browser); the token it writes, tokens/spotify.json, goes to the same place on the
VPS, which refreshes it and saves it back. The tools are offered only when that
file exists. Spotify ends a sign-in six months after it was made (refreshing
doesn't extend it), so Natro mentions it in the last two weeks.

Playing, controlling and liking music is not personal (the free Gemini tier may
do it); his playlists and listening history are. Nothing here deletes, so nothing
asks first.

Spotify's rules for "Development Mode" apps (February 2026) shape this: search
returns at most 10 results, saving goes through /me/library with URIs, playlist
contents only for playlists he owns or collaborates on, and Spotify's own
made-for-you playlists (On Repeat, Discover Weekly) can't be looked up, only
played by their link.
"""
import asyncio
import json
import os
import re
import time
from datetime import date, datetime, timedelta

from natro_agent.config import ROOT, timezone
from natro_agent.tools import Tool, ToolError

API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
TOKEN_FILE = ROOT / "tokens" / "spotify.json"
SCOPES = [
    "user-read-playback-state", "user-modify-playback-state", "user-read-currently-playing",
    "user-read-recently-played", "user-library-read", "user-library-modify",
    "playlist-read-private", "playlist-read-collaborative", "playlist-modify-private", "playlist-modify-public",
]
SIGN_IN_AGAIN = "The Spotify sign-in has expired or was revoked: run `python -m natro_agent.spotify_signin` again."
SIGN_IN_DAYS = 182  # Spotify's six months
WARN_DAYS = 14
MAX_PLAYLISTS = 200
VOLUME_STEP = 10
# A Spotify app that just started (say, opened on the PC by Natro) shows up as a device after a few seconds.
START_SECONDS = 10
KINDS = {"track": "track", "album": "album", "artist": "artist", "playlist": "playlist", "podcast": "show"}
# Spotify's made-for-you playlists, which differ per listener and can't be looked up.
MADE_FOR_YOU = re.compile(r"(my )?(on repeat|repeat rewind|discover weekly|release radar|daily mix( \d+)?|daylist|"
                          r"your top songs( \d+)?|time capsule)( playlist)?")
DEVICE_TYPES = {"pc": "computer", "computer": "computer", "laptop": "computer", "phone": "smartphone",
                "mobile": "smartphone", "speaker": "speaker", "tv": "tv"}


class NothingPlaying(ToolError):
    """Spotify has no active device (it answers NO_ACTIVE_DEVICE)."""


class SpotifyAPI:
    """Spotify's Web API with the saved sign-in. Requests run in a thread; errors become ToolErrors."""

    def __init__(self, session, token, path=None, clock=time.time):
        self.session = session  # a requests.Session, or a fake in tests
        self.token = token  # client_id, access_token, refresh_token, expires_at, signed_in
        self.path = path  # where to save a refreshed token
        self.clock = clock
        self._refreshing = asyncio.Lock()

    @classmethod
    def from_token(cls, path=TOKEN_FILE):
        import requests

        return cls(requests.Session(), json.loads(path.read_text(encoding="utf-8")), path)

    def expiry_note(self):
        """A reminder in the last WARN_DAYS before the sign-in ends, else ""."""
        if not self.token.get("signed_in"):
            return ""
        ends = date.fromisoformat(self.token["signed_in"]) + timedelta(days=SIGN_IN_DAYS)
        if ends - datetime.now(timezone()).date() > timedelta(days=WARN_DAYS):
            return ""
        return (f" (Spotify's sign-in ends around {ends:%d %B}: tell your owner to run the Spotify sign-in again "
                "on his PC.)")

    async def _send(self, method, url, **kwargs):
        try:
            return await asyncio.to_thread(self.session.request, method, url, timeout=30, **kwargs)
        except Exception as e:
            raise ToolError(f"Couldn't reach Spotify: {e}")

    async def _access_token(self, renew=False):
        async with self._refreshing:
            def stale():
                return self.token.get("expires_at", 0) < self.clock() + 60

            # The service and the typed chat share the file: take a token the other one refreshed since.
            if (renew or stale()) and self._load_newer():
                renew = False
            if renew or stale():
                response = await self._send("POST", TOKEN_URL, data={
                    "grant_type": "refresh_token", "refresh_token": self.token["refresh_token"],
                    "client_id": self.token["client_id"]})
                if response.status_code >= 400:
                    raise ToolError(SIGN_IN_AGAIN if response.status_code in (400, 401) else
                                    f"Spotify's sign-in service failed (HTTP {response.status_code}).")
                fresh = response.json()
                self.token["access_token"] = fresh["access_token"]
                self.token["expires_at"] = int(self.clock()) + int(fresh.get("expires_in", 3600))
                # Spotify may hand out a new refresh token; the old one may then stop working.
                self.token["refresh_token"] = fresh.get("refresh_token") or self.token["refresh_token"]
                self._save()
            return self.token["access_token"]

    def _load_newer(self):
        """Switch to the saved token if it is newer than ours; whether it was."""
        if not (self.path and self.path.exists()):
            return False
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        if saved.get("expires_at", 0) <= self.token.get("expires_at", 0):
            return False
        self.token = saved
        return True

    def _save(self):
        if self.path:
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.token, indent=1), encoding="utf-8")
            if os.name == "posix":
                os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)

    async def call(self, method, path, params=None, body=None):
        for renew in (False, True):
            headers = {"Authorization": f"Bearer {await self._access_token(renew)}"}
            response = await self._send(method, f"{API}{path}", params=params, json=body, headers=headers)
            if response.status_code != 401:
                break
        if response.status_code >= 400:
            raise spotify_error(response)
        try:
            return response.json() if response.content else {}
        except ValueError:
            return {}


def spotify_error(response):
    try:
        error = response.json()["error"]
        message, reason = error.get("message", ""), error.get("reason", "")
    except Exception:
        message, reason = response.text[:300], ""
    status = response.status_code
    if status == 401:
        return ToolError(SIGN_IN_AGAIN)
    if reason == "NO_ACTIVE_DEVICE":
        return NothingPlaying("Spotify isn't playing on any device right now.")
    if reason == "VOLUME_CONTROL_DISALLOW":
        return ToolError("That device doesn't let Spotify change its volume.")
    if reason == "PREMIUM_REQUIRED":
        return ToolError("Spotify says this needs Premium on the signed-in account.")
    if status == 429:
        wait = response.headers.get("Retry-After", "a few")
        return ToolError(f"Spotify is limiting requests right now; try again in {wait} seconds.")
    if "restriction violated" in message.lower():
        return ToolError("Spotify won't do that right now (for example, it's already paused, or there's "
                         "nothing to skip to).")
    return ToolError(f"Spotify said: {message or reason or 'an error'} (HTTP {status})")


# What things are called, out loud.

def artists(item):
    return ", ".join(artist["name"] for artist in item.get("artists", []))


def describe(item):
    kind = item.get("type")
    if kind == "track":
        return f"{item['name']} by {artists(item)}"
    if kind == "album":
        return f"the album {item['name']} by {artists(item)}"
    if kind == "playlist":
        return f"the playlist {item['name']}"
    if kind == "show":
        return f"the podcast {item['name']}"
    if kind == "episode":
        show = item.get("show", {}).get("name")
        return f"{item['name']}" + (f" ({show})" if show else "")
    return item.get("name", "?")


def minutes(ms):
    seconds = int(ms or 0) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def plain(text):
    return re.sub(r"[^\w]+", " ", text.casefold()).strip()


def spotify_uri(value):
    """A spotify: URI from a URI or an open.spotify.com link (as shared from the app)."""
    value = value.strip()
    link = re.match(r"https?://open\.spotify\.com/(?:intl-[\w-]+/)?(\w+)/(\w+)", value)
    if link:
        return f"spotify:{link.group(1)}:{link.group(2)}"
    if re.fullmatch(r"spotify:(?:user:[^:]+:collection|\w+:\w+)", value):
        return value
    raise ToolError(f"{value!r} isn't a Spotify link or URI.")


def uri_kind(uri):
    """The kind of thing a URI names: track, album, playlist, episode... (user for Liked Songs)."""
    return uri.split(":")[1]


class Spotify:
    def __init__(self, api, start_seconds=START_SECONDS):
        self.api = api
        self.start_seconds = start_seconds
        self._me = None

    async def _user_id(self):
        if self._me is None:
            self._me = (await self.api.call("GET", "/me"))["id"]
        return self._me

    # Devices

    async def devices(self):
        return (await self.api.call("GET", "/me/player/devices")).get("devices", [])

    async def _devices_soon(self, enough):
        """The devices, waiting up to start_seconds for enough(devices) while Spotify may be starting."""
        deadline = time.monotonic() + self.start_seconds
        while True:
            devices = await self.devices()
            if enough(devices) or time.monotonic() >= deadline:
                return devices
            await asyncio.sleep(1)

    async def _find_device(self, name):
        wanted = DEVICE_TYPES.get(plain(name))

        def match(devices):
            return next((d for d in devices if (wanted and d["type"].lower() == wanted)
                         or plain(name) in plain(d["name"])), None)

        devices = await self._devices_soon(match)
        if found := match(devices):
            return found
        open_on = ", ".join(f"{d['name']} ({d['type'].lower()})" for d in devices) or "no device"
        raise ToolError(f"Spotify isn't open on a device called {name!r}. Open Spotify devices: {open_on}.")

    async def _fallback_device(self):
        """Where to play when nothing is playing: his computer, else any open Spotify."""
        devices = await self._devices_soon(bool)
        if not devices:
            raise ToolError("Spotify isn't open on any of his devices, so there's nothing to play on. If his PC is "
                            "online, open Spotify there first, then play.")
        return next((d for d in devices if d["type"].lower() == "computer"), devices[0])

    async def _player(self, method, path, params=None, body=None, device=None, start=False):
        """A player command, on `device` if named, else where Spotify is playing.

        start: if nothing is playing, send it to the fallback device instead (for
        playing; pausing or skipping then has nothing to act on). Returns the device
        it was sent to by name (None: wherever Spotify was already playing).
        """
        target = await self._find_device(device) if device else None

        def on(target):
            return dict(params or {}, **({"device_id": target["id"]} if target else {}))

        try:
            await self.api.call(method, path, on(target), body)
        except NothingPlaying:
            if target or not start:
                raise
            target = await self._fallback_device()
            await self.api.call(method, path, on(target), body)
        return target

    # Finding things

    async def search(self, query, kind):
        found = await self.api.call("GET", "/search", {"q": query, "type": kind, "limit": 5})
        return [item for item in found.get(f"{kind}s", {}).get("items", []) if item]

    async def playlists(self):
        """His playlists (owned, followed, collaborative), as Spotify lists them."""
        items, offset = [], 0
        while offset < MAX_PLAYLISTS:
            page = await self.api.call("GET", "/me/playlists", {"limit": 50, "offset": offset})
            items += [item for item in page.get("items", []) if item]
            if not page.get("next"):
                break
            offset += 50
        return items

    async def _his_playlist(self, name, editable=False):
        mine = await self.playlists()
        if editable:
            me = await self._user_id()
            mine = [p for p in mine if p.get("owner", {}).get("id") == me or p.get("collaborative")]
        wanted = re.sub(r"^(my|the) | playlist$", "", plain(name))  # "my gym playlist": gym
        exact = [p for p in mine if plain(p["name"]) == wanted]
        close = sorted((p for p in mine if wanted in plain(p["name"])), key=lambda p: len(p["name"]))
        return (exact or close or [None])[0]

    async def _current_item(self):
        state = await self.api.call("GET", "/me/player/currently-playing", {"additional_types": "track,episode"})
        if not state.get("item"):
            raise ToolError("Nothing is playing on Spotify right now.")
        return state["item"]

    # Tools

    async def play(self, query=None, kind="track", uri=None, device=None, queue=False):
        item, others = None, []
        if uri:
            uri = spotify_uri(uri)
            what, item_kind = "that", uri_kind(uri)
        elif kind == "liked_songs":
            uri, what, item_kind = f"spotify:user:{await self._user_id()}:collection", "his Liked Songs", "collection"
        elif query:
            if kind not in KINDS:
                raise ToolError(f"kind must be one of: {', '.join([*KINDS, 'liked_songs'])}.")
            mix = MADE_FOR_YOU.fullmatch(plain(query))
            item = await self._his_playlist(query) if kind == "playlist" or mix else None
            if item is None and mix:
                # A search would find strangers' playlists with that name.
                raise ToolError(f"Spotify doesn't let apps find his own {query!r} mix. Once he saves it to his "
                                "library in the Spotify app, or gives you its link, you can play it.")
            if item is None:
                found = await self.search(query, KINDS[kind])
                if not found:
                    raise ToolError(f"Spotify found no {kind} for {query!r}.")
                item, others = found[0], found[1:4]
            if item.get("type") == "show":  # a podcast: its newest episode
                episodes = await self.api.call("GET", f"/shows/{item['id']}/episodes", {"limit": 1})
                newest = next((e for e in episodes.get("items", []) if e), None)
                if newest is None:
                    raise ToolError(f"{describe(item)} has no episodes.")
                item = dict(newest, show={"name": item["name"]})
            uri, what, item_kind = item["uri"], describe(item), uri_kind(item["uri"])
        else:
            if queue:
                raise ToolError("Say what to add to the queue.")
            if device:  # move what's playing (or what last played) to that device
                target = await self._find_device(device)
                await self.api.call("PUT", "/me/player", body={"device_ids": [target["id"]], "play": True})
                return f"Playing on {target['name']}."
            target = await self._player("PUT", "/me/player/play", start=True)
            return "Resumed" + (f" on {target['name']}." if target else ".")

        if queue:
            if item_kind not in ("track", "episode"):
                raise ToolError("Only songs and episodes can be added to the queue.")
            target = await self._player("POST", "/me/player/queue", {"uri": uri}, device=device)
            text = f"Added {what} to the queue"
        else:
            album = (item or {}).get("album", {}).get("uri")
            if item_kind not in ("track", "episode"):
                body = {"context_uri": uri}
            elif album:  # in its album, so the music goes on after it (a lone song just stops)
                body = {"context_uri": album, "offset": {"uri": uri}}
            else:
                body = {"uris": [uri]}
            target = await self._player("PUT", "/me/player/play", body=body, device=device, start=True)
            text = f"Playing {what}"
        text += f" on {target['name']}." if target else "."
        if others:
            text += "\nOther matches: " + "; ".join(f"{describe(item)} [{item['uri']}]" for item in others)
        return text

    async def control(self, action, value=None):
        value = "" if value is None else str(value).strip().lower()
        if action == "pause":
            await self._player("PUT", "/me/player/pause")
            return "Paused."
        if action == "resume":
            target = await self._player("PUT", "/me/player/play", start=True)
            return "Resumed" + (f" on {target['name']}." if target else ".")
        if action in ("next", "previous"):
            await self._player("POST", f"/me/player/{action}")
            return "Skipped to the next track." if action == "next" else "Went back."
        if action == "volume":
            if value in ("up", "down"):
                state = await self.api.call("GET", "/me/player")
                current = (state.get("device") or {}).get("volume_percent")
                if current is None:
                    raise ToolError("Spotify isn't playing anywhere, so there's no volume to change.")
                level = current + (VOLUME_STEP if value == "up" else -VOLUME_STEP)
            else:
                level = number(value, "volume")
            level = max(0, min(100, round(level)))
            await self._player("PUT", "/me/player/volume", {"volume_percent": level})
            return f"Volume {level}%."
        if action == "shuffle":
            on = value in ("on", "true", "yes", "1")
            await self._player("PUT", "/me/player/shuffle", {"state": "true" if on else "false"})
            return f"Shuffle {'on' if on else 'off'}."
        if action == "repeat":
            state = {"track": "track", "song": "track", "off": "off"}.get(value, "context")
            await self._player("PUT", "/me/player/repeat", {"state": state})
            return {"track": "Repeating this track.", "off": "Repeat off."}.get(state, "Repeating the playlist or album.")
        if action == "seek":
            seconds = number(value, "seek")  # "+30"/"-15" are relative
            if value[:1] in ("+", "-"):
                state = await self.api.call("GET", "/me/player")
                seconds += (state.get("progress_ms") or 0) / 1000
            position = max(0, int(seconds * 1000))
            await self._player("PUT", "/me/player/seek", {"position_ms": position})
            return f"Jumped to {minutes(position)}."
        raise ToolError(f"Unknown action {action!r}.")

    async def now_playing(self):
        state, devices = await asyncio.gather(
            self.api.call("GET", "/me/player", {"additional_types": "track,episode"}), self.devices())
        device_list = ", ".join(f"{d['name']} ({d['type'].lower()}{', playing' if d.get('is_active') else ''})"
                                for d in devices) or "none"
        item = state.get("item")
        if not item:
            return f"Nothing is playing. Spotify is open on: {device_list}."
        playing = "Playing" if state.get("is_playing") else "Paused on"
        device = state.get("device") or {}
        volume = f", volume {device['volume_percent']}%" if device.get("volume_percent") is not None else ""
        repeat = {"track": "this track", "context": "on"}.get(state.get("repeat_state"), "off")
        modes = f"Shuffle {'on' if state.get('shuffle_state') else 'off'}, repeat {repeat}."
        return (f"{playing} {describe(item)} [{item['uri']}], {minutes(state.get('progress_ms'))} of "
                f"{minutes(item.get('duration_ms'))}, on {device.get('name', '?')}{volume}. {modes}\n"
                f"Spotify is open on: {device_list}.")

    async def like(self, uri=None):
        item = {"uri": spotify_uri(uri)} if uri else await self._current_item()
        name = describe(item) if "name" in item else "it"
        where = "his Liked Songs" if item["uri"].startswith("spotify:track:") else "his library"
        # Saving it again would move it to the top of his Liked Songs.
        if (await self.api.call("GET", "/me/library/contains", {"uris": item["uri"]})) == [True]:
            return f"{name[0].upper()}{name[1:]} is already in {where}."
        await self.api.call("PUT", "/me/library", {"uris": item["uri"]})
        return f"Saved {name} to {where}."

    async def add_to_playlist(self, playlist, uri=None):
        found = await self._his_playlist(playlist, editable=True)
        if found is None:
            raise ToolError(f"He has no playlist called {playlist!r} that he can add to.")
        item = {"uri": spotify_uri(uri)} if uri else await self._current_item()
        await self.api.call("POST", f"/playlists/{found['id']}/items", body={"uris": [item["uri"]]})
        return f"Added {describe(item) if 'name' in item else 'it'} to {describe(found)}."

    async def library(self, what="playlists"):
        if what == "recently_played":
            played = await self.api.call("GET", "/me/player/recently-played", {"limit": 15})
            lines = []
            for entry in played.get("items", []):
                when = datetime.fromisoformat(entry["played_at"].replace("Z", "+00:00")).astimezone(timezone())
                lines.append(f"- {when:%a %H:%M}: {describe(entry['track'])} [{entry['track']['uri']}]")
            return "\n".join(lines) or "Nothing played recently."
        me = await self._user_id()
        lines = []
        for playlist in await self.playlists():
            owner = "" if playlist.get("owner", {}).get("id") == me else \
                f", by {playlist.get('owner', {}).get('display_name') or 'someone else'}"
            count = (playlist.get("items") or playlist.get("tracks") or {}).get("total")
            size = f", {count} items" if count is not None else ""
            lines.append(f"- {playlist['name']}{size}{owner} [{playlist['uri']}]")
        return "\n".join(lines) or "He has no playlists."

    def tools(self):
        def tool(name, description, properties, run, required=(), personal=False, final=False):
            async def call(args):
                first, _, rest = (await run(**args)).partition("\n")
                # On the first line, which may be the whole spoken reply (final tools).
                return first + self.api.expiry_note() + (f"\n{rest}" if rest else "")
            return Tool(name=name, description=description, run=call, source="spotify", personal=personal,
                        final=final,
                        input_schema={"type": "object", "properties": properties, "required": list(required)})

        uri = {"type": "string", "description": "A spotify: URI or an open.spotify.com link."}
        device = {"type": "string", "description": "\"PC\", \"phone\", or a Spotify device's name."}
        return [
            tool("spotify_play", "Play music or a podcast on your owner's Spotify. With query: searches Spotify (for "
                 "kind playlist, his own playlists first) and plays the best match; the result names it and other "
                 "matches. With uri: plays exactly that. kind liked_songs plays his Liked Songs. With nothing to "
                 "play: resumes, or moves playback to device. Plays where Spotify is playing now; if it's playing "
                 "nowhere, on his PC if Spotify is open there, else any open Spotify. queue: add a song to the "
                 "queue instead.",
                 {"query": {"type": "string", "description": "What to search for, e.g. \"Yellow Coldplay\"."},
                  "kind": {"type": "string", "enum": [*KINDS, "liked_songs"], "description": "Default track."},
                  "uri": uri, "device": device, "queue": {"type": "boolean"}}, self.play, final=True),
            tool("spotify_control", "Control Spotify playback: pause, resume, next, previous; volume (value 0-100, "
                 "\"up\" or \"down\"); shuffle (\"on\"/\"off\"); repeat (\"track\", \"all\" or \"off\"); seek "
                 "(seconds from the start, or \"+30\"/\"-15\" to jump).",
                 {"action": {"type": "string", "enum": ["pause", "resume", "next", "previous", "volume", "shuffle",
                                                        "repeat", "seek"]},
                  "value": {"type": "string"}}, self.control, ["action"], final=True),
            tool("spotify_now_playing", "What's playing on Spotify, where, at what volume, and which devices have "
                 "Spotify open.", {}, self.now_playing),
            tool("spotify_like", "Save the song that's playing (or uri) to his Liked Songs; albums, playlists and "
                 "podcasts to his library.", {"uri": uri}, self.like),
            tool("spotify_add_to_playlist", "Add the song that's playing (or uri) to one of his own playlists, by "
                 "name.", {"playlist": {"type": "string"}, "uri": uri}, self.add_to_playlist, ["playlist"]),
            tool("spotify_library", "List his Spotify playlists, or what he played recently.",
                 {"what": {"type": "string", "enum": ["playlists", "recently_played"]}}, self.library,
                 personal=True),
        ]


def number(value, action):
    try:
        return float(value)
    except ValueError:
        raise ToolError(f"{action} needs a number, not {value!r}.")
