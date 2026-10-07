"""The agent as a service: devices (PC, phone) connect over a WebSocket and talk to Natro.

Listens on NATRO_LISTEN (host:port). On the VPS that is its Tailscale address,
never a public one; the default 127.0.0.1:8700 is for trying it on one machine.
If NATRO_DEVICE_TOKEN is set in .env, devices must send it in their hello.
GET /health answers "ok", so a device can show "Natro is online".

Usage: python -m natro_agent.server

Protocol: JSON text messages, plus one binary message (the recording, 16 kHz mono
16-bit PCM) right after each "audio" message.

Device to Natro:
  {"type": "hello", "device": "pc", "label": "PC", "token": "...", "tools": [...]}  first; tools as in tools.device_tools
  {"type": "text", "id": "r1", "text": "open Chrome"}               a typed request, in English
  {"type": "audio", "id": "r2"} + recording                         a spoken request, in Georgian
  {"type": "audio", "id": "r3", "answer_to": "q1"} + recording      a spoken yes or no to a question
  {"type": "answer", "id": "q1", "yes": true}                       a yes or no by key or tap
  {"type": "tool_result", "id": "c1", "ok": true, "result": "..."}  the result of a tool_call
Natro to device:
  {"type": "welcome", "spent": 0.42, "budget": 15}                  hello accepted; this month's spending (USD)
  {"type": "english", "id": "r2", "text": "..."}                    the translation, in pieces as it's written
  {"type": "heard", "id": "r2", "georgian": "...", "english": "..."}  what the voice pipeline heard
  {"type": "reply_text", "id": "r1", "text": "..."}                 the reply, in pieces as it's written
  {"type": "reply", "id": "r1", "text": "...", "model": "...", "cost": 0.004, "seconds": 2.1,
   "spent": 0.43, "budget": 15}                                    show and speak it
  {"type": "confirm", "id": "q1", "question": "...", "details": "..."}  ask the owner yes or no
  {"type": "tool_call", "id": "c1", "name": "open_app", "args": {...}}  run one of the device's tools
  {"type": "error", "id": "r2", "message": "..."}
"""
import asyncio
import hmac
import itertools
import json
import os
import re
import signal
import sys
import threading

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from natro_agent import log
from natro_agent.config import load_env
from natro_agent.mcp_client import MCPServers
from natro_agent.setup import build_agent
from natro_agent.tools import Device, ToolError, device_tools

DEFAULT_LISTEN = "127.0.0.1:8700"
# Recordings are sent whole: 16 MB is over 8 minutes of audio.
MAX_MESSAGE_BYTES = 16 * 2**20
HELLO_SECONDS = 10

YES_WORDS = {"yes", "yeah", "yep", "sure", "ok", "okay", "confirm", "correct", "right", "go", "do"}
NO_WORDS = {"no", "nope", "not", "don't", "dont", "cancel", "stop", "wait", "never"}


def is_yes(english):
    """Whether a spoken answer (translated) means yes. Anything unclear counts as no."""
    words = re.sub(r"[^\w\s']", " ", english.casefold()).split()
    # "OK Natro, no" must not count as yes because of the "OK".
    if words[:2] in (["ok", "natro"], ["okay", "natro"]):
        words = words[2:]
    return not NO_WORDS.intersection(words) and bool(YES_WORDS.intersection(words))


class Voice:
    """Georgian recordings to English, with the voice pipeline from voice/ (Google Chirp 3 + Claude)."""

    def __init__(self):
        from natro_voice.config import load_env as load_voice_env, load_wordlist
        from natro_voice.speech import Recognizer, has_speech
        from natro_voice.translate import Translator

        load_voice_env()  # resolves the Google key file's path
        self.has_speech = has_speech
        self.recognizer = Recognizer()
        self.translator = Translator(terms=load_wordlist())
        self._lock = threading.Lock()  # one recording at a time; the translator keeps context

    def process(self, pcm, on_english=None):
        """Returns (georgian, english, cost). Blocks: run it in a thread."""
        with self._lock:
            before = self.recognizer.cost + self.translator.cost
            if not self.has_speech(pcm):
                return "", "", 0.0
            heard, as_georgian = self.recognizer.transcribe(pcm)
            english = self.translator.translate(heard, as_georgian=as_georgian, on_text=on_english) if heard else ""
            return as_georgian or heard, english, self.recognizer.cost + self.translator.cost - before


class RemoteDevice(Device):
    """A device connected over the WebSocket. Messages to it go out in order through an outbox."""

    def __init__(self, connection, hello):
        self.connection = connection
        self.name = hello["device"]
        self.label = hello.get("label") or self.name
        self.specs = hello.get("tools", [])
        self.outbox = asyncio.Queue()
        self._waiting = {}  # message id -> future for the device's answer
        self._ids = itertools.count(1)

    def tools(self):
        return device_tools(self, self.specs, self.call_tool)

    def post(self, **message):
        """Queue a message for the device (safe to call from synchronous code)."""
        self.outbox.put_nowait(json.dumps(message, ensure_ascii=False))

    async def send_all(self):
        while True:
            await self.connection.send(await self.outbox.get())

    async def _ask(self, kind, **message):
        """Send a message that needs an answer, and wait for it."""
        id = f"{kind[0]}{next(self._ids)}"
        future = asyncio.get_running_loop().create_future()
        self._waiting[id] = future
        try:
            self.post(type=kind, id=id, **message)
            return await future
        finally:
            self._waiting.pop(id, None)

    async def call_tool(self, name, args):
        result = await self._ask("tool_call", name=name, args=args)
        if not result.get("ok"):
            raise ToolError(result.get("result") or f"{name} failed on the {self.label}.")
        return result.get("result", "")

    async def confirm(self, question, details):
        try:
            return bool((await self._ask("confirm", question=question, details=details)).get("yes"))
        except ToolError:  # disconnected before answering
            return False

    def answered(self, id, answer):
        future = self._waiting.get(id)
        if future and not future.done():
            future.set_result(answer)

    def disconnected(self):
        for future in self._waiting.values():
            if not future.done():
                future.set_exception(ToolError(f"The {self.label} disconnected."))


class Server:
    def __init__(self, agent, voice=None, token=None):
        self.agent = agent
        self.voice = voice
        self.token = token
        self._tasks = set()

    def _start(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def handle(self, connection):
        try:
            hello = json.loads(await asyncio.wait_for(connection.recv(), HELLO_SECONDS))
        except (asyncio.TimeoutError, ConnectionClosed, ValueError):
            return
        token_ok = not self.token or hmac.compare_digest(str(hello.get("token", "")), self.token)
        if hello.get("type") != "hello" or not hello.get("device") or not token_ok:
            await connection.close(1008, "bad hello")
            return

        device = RemoteDevice(connection, hello)
        sender = asyncio.create_task(device.send_all())
        self.agent.connect(device)
        log.write("device", device=device.name, event="connected", tools=[spec["name"] for spec in device.specs])
        device.post(type="welcome", **self._month())
        try:
            async for message in connection:
                if isinstance(message, bytes):
                    continue  # a recording without its "audio" message
                data = json.loads(message)
                kind = data.get("type")
                if kind in ("answer", "tool_result"):
                    device.answered(data.get("id"), data)
                elif kind == "text":
                    self._start(self.text_request(device, data))
                elif kind == "audio":
                    pcm = await connection.recv()
                    self._start(self.audio_request(device, data, pcm))
        except ConnectionClosed:
            pass
        finally:
            self.agent.disconnect(device)
            device.disconnected()
            sender.cancel()
            log.write("device", device=device.name, event="disconnected")

    async def text_request(self, device, data):
        await self._respond(device, data.get("id"), data.get("text", ""), voice_cost=0.0)

    async def audio_request(self, device, data, pcm):
        id = data.get("id")
        if self.voice is None:
            device.post(type="error", id=id, message="Voice isn't set up on the server (see voice/).")
            return
        loop = asyncio.get_running_loop()

        def on_english(piece):
            loop.call_soon_threadsafe(lambda: device.post(type="english", id=id, text=piece))

        try:
            georgian, english, voice_cost = await asyncio.to_thread(self.voice.process, pcm, on_english)
        except Exception as e:
            log.write("error", device=device.name, error=repr(e), step="voice")
            device.post(type="error", id=id, message=f"The voice pipeline failed: {e}")
            return
        device.post(type="heard", id=id, georgian=georgian, english=english)
        if answer_to := data.get("answer_to"):
            self._count_voice(device, voice_cost, answer=english)
            device.answered(answer_to, {"yes": is_yes(english)})
        elif not english:
            self._count_voice(device, voice_cost)
            device.post(type="reply", id=id, text="I didn't catch that.", model="none", cost=round(voice_cost, 5),
                        **self._month())
        else:
            await self._respond(device, id, english, voice_cost)

    def _count_voice(self, device, voice_cost, **fields):
        """Voice spending that didn't become a request still counts toward the month."""
        self.agent.ledger.add(voice_cost)
        log.write("voice", device=device.name, voice_cost=round(voice_cost, 6), **fields)

    async def _respond(self, device, id, english, voice_cost):
        reply = await self.agent.respond(english, device, voice_cost=voice_cost,
                                         on_text=lambda piece: device.post(type="reply_text", id=id, text=piece))
        device.post(type="reply", id=id, text=reply.text, model=reply.model, cost=round(reply.cost + voice_cost, 5),
                    seconds=round(reply.seconds, 2), **self._month())

    def _month(self):
        """This month's spending, for devices to show."""
        return {"spent": round(self.agent.ledger.spent, 4), "budget": self.agent.ledger.budget}


def health(connection, request):
    if request.path == "/health":
        return connection.respond(200, "ok\n")
    return None


def make_voice():
    try:
        return Voice()
    except Exception as e:  # not installed, or no Google credentials
        print(f"Voice requests are off: {e}")
        return None


async def run():
    load_env()
    servers = MCPServers()
    agent = await build_agent(servers)
    token = os.environ.get("NATRO_DEVICE_TOKEN")
    if not token:
        print("NATRO_DEVICE_TOKEN is not set: any device that can reach this address may connect.")
    server = Server(agent, make_voice(), token)
    host, port = os.environ.get("NATRO_LISTEN", DEFAULT_LISTEN).rsplit(":", 1)
    try:
        async with serve(server.handle, host, int(port), process_request=health,
                         max_size=MAX_MESSAGE_BYTES) as listener:
            if sys.platform != "win32":  # systemd stops the service with SIGTERM
                asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, listener.close)
            print(f"Natro is listening on {host}:{port}")
            try:
                await listener.serve_forever()
            except asyncio.CancelledError:
                print("Natro stopped.")  # systemctl stop/restart: a normal exit, not a failure
    finally:
        await servers.close()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
