import asyncio
import json
import urllib.request

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from conftest import FakeClient, message, run, text, tool_use
from natro_agent.agent import Agent
from natro_agent.server import Server, health, is_yes

PC_TOOLS = [{"name": "force_close_app", "description": "Kill an app.", "confirm": True,
             "input_schema": {"type": "object", "properties": {"app": {"type": "string"}}, "required": ["app"]}}]
HELLO = {"type": "hello", "device": "pc", "label": "PC", "token": "secret", "tools": PC_TOOLS}


class FakeVoice:
    """Returns the scripted (georgian, english, cost) for each recording."""

    def __init__(self, *results):
        self.results = list(results)
        self.recordings = []

    def process(self, pcm, on_english=None):
        self.recordings.append(pcm)
        georgian, english, cost = self.results.pop(0)
        if on_english and english:
            on_english(english)
        return georgian, english, cost


async def session(server, device_side):
    """Run the server on a free port and play device_side(ws, port) against it."""
    async with serve(server.handle, "127.0.0.1", 0, process_request=health) as listener:
        port = listener.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            return await device_side(ws, port)


async def until_reply(ws, on_confirm=None, on_tool_call=None):
    """Read messages until a reply; returns all of them. Answers confirms and tool calls as told."""
    seen = []
    while True:
        data = json.loads(await asyncio.wait_for(ws.recv(), 5))
        seen.append(data)
        if data["type"] == "confirm" and on_confirm:
            await on_confirm(data)
        elif data["type"] == "tool_call" and on_tool_call:
            await ws.send(json.dumps({"type": "tool_result", "id": data["id"], **on_tool_call(data)}))
        elif data["type"] in ("reply", "error"):
            return seen


def risky_turn():
    return FakeClient(
        message(text("I'll force close Spotify."), tool_use("t1", "pc_force_close_app", app="Spotify"), stop="tool_use"),
        message(text("Done.")),
    )


def test_typed_request_with_a_device_tool_and_a_yes_by_key():
    server = Server(Agent(client=risky_turn()), token="secret")
    calls = []

    async def device(ws, port):
        await ws.send(json.dumps(HELLO))
        welcome = json.loads(await ws.recv())
        assert welcome["type"] == "welcome" and welcome["budget"] == server.agent.ledger.budget
        await ws.send(json.dumps({"type": "text", "id": "r1", "text": "force close Spotify"}))

        async def yes(question):
            await ws.send(json.dumps({"type": "answer", "id": question["id"], "yes": True}))

        def tool(call):
            calls.append((call["name"], call["args"]))
            return {"ok": True, "result": "closed"}

        return await until_reply(ws, on_confirm=yes, on_tool_call=tool)

    seen = run(session(server, device))
    confirm = next(m for m in seen if m["type"] == "confirm")
    assert confirm["question"] == "I'll force close Spotify."
    assert calls == [("force_close_app", {"app": "Spotify"})]
    assert seen[-1]["type"] == "reply" and seen[-1]["text"] == "I'll force close Spotify. Done."
    assert seen[-1]["spent"] >= 0 and seen[-1]["budget"] == 15
    assert any(m["type"] == "reply_text" for m in seen)


def test_spoken_request_counts_voice_cost():
    voice = FakeVoice(("ქრომი გახსენი", "Open Chrome.", 0.002))
    server = Server(Agent(client=FakeClient(message(text("I can't yet.")))), voice=voice, token="secret")

    async def device(ws, port):
        await ws.send(json.dumps(HELLO))
        await ws.recv()
        await ws.send(json.dumps({"type": "audio", "id": "r1"}))
        await ws.send(b"\x01\x00" * 1600)
        return await until_reply(ws)

    seen = run(session(server, device))
    assert [m["type"] for m in seen[:2]] == ["english", "heard"]
    assert seen[1]["georgian"] == "ქრომი გახსენი"
    assert voice.recordings == [b"\x01\x00" * 1600]
    assert seen[-1]["cost"] == round(0.002 + 0.0025, 5)


def test_spoken_no_to_a_question():
    voice = FakeVoice(("ზედმეტად ...", "Force close Spotify.", 0.001), ("ოკ ნატრო, არა", "OK Natro, no.", 0.001))
    server = Server(Agent(client=risky_turn()), voice=voice, token="secret")
    calls = []

    async def device(ws, port):
        await ws.send(json.dumps(HELLO))
        await ws.recv()
        await ws.send(json.dumps({"type": "audio", "id": "r1"}))
        await ws.send(b"request")

        async def say_no(question):
            await ws.send(json.dumps({"type": "audio", "id": "r2", "answer_to": question["id"]}))
            await ws.send(b"answer")

        return await until_reply(ws, on_confirm=say_no, on_tool_call=lambda call: calls.append(call) or {"ok": True})

    run(session(server, device))
    assert calls == []


def test_wrong_token_is_refused():
    server = Server(Agent(client=FakeClient()), token="secret")

    async def device(ws, port):
        await ws.send(json.dumps({**HELLO, "token": "guess"}))
        with pytest.raises(ConnectionClosed) as closed:
            await ws.recv()
        return closed.value.rcvd.code

    assert run(session(server, device)) == 1008


def test_health_and_device_list():
    agent = Agent(client=FakeClient())
    server = Server(agent, token="secret")

    async def device(ws, port):
        await ws.send(json.dumps(HELLO))
        await ws.recv()
        body = await asyncio.to_thread(lambda: urllib.request.urlopen(f"http://127.0.0.1:{port}/health").read())
        return body, set(agent.devices), [tool.name for tool in agent.offered_tools()]

    body, devices, tools = run(session(server, device))
    assert body == b"ok\n" and devices == {"pc"} and tools == ["pc_force_close_app"]


@pytest.mark.parametrize("english, yes", [
    ("Yes.", True), ("OK Natro, yes.", True), ("Yeah, go ahead.", True), ("Sure, do it.", True),
    ("OK Natro, no.", False), ("No, don't.", False), ("Wait.", False), ("Hmm.", False), ("", False),
])
def test_spoken_answers(english, yes):
    assert is_yes(english) is yes
