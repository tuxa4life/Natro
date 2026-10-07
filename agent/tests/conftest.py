"""A scripted stand-in for Claude, and helpers to build its replies."""
import asyncio
import copy
from types import SimpleNamespace

import pytest
from anthropic.types.beta import BetaMessage

from natro_agent import log
from natro_agent.tools import Device


def text(value):
    return {"type": "text", "text": value}


def tool_use(id, name, **args):
    return {"type": "tool_use", "id": id, "name": name, "input": args}


def thinking():
    return {"type": "thinking", "thinking": "", "signature": "sig"}


def fallback():
    return {"type": "fallback", "from": {"model": "claude-sonnet-5-5"}, "to": {"model": "claude-sonnet-5"},
            "trigger": {"type": "refusal", "category": "cyber"}}


def message(*blocks, stop="end_turn", input_tokens=1000, output_tokens=50, cache_read=0):
    return BetaMessage.model_validate({
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": list(blocks), "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": cache_read},
    })


class FakeStream:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def pieces():
            for block in self.response.content:
                if block.type == "text":
                    yield block.text
        return pieces()

    async def get_final_message(self):
        return self.response


class FakeClient:
    """Returns the scripted replies in order (an exception is raised instead); records each request."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

    def stream(self, **request):
        self.requests.append(copy.deepcopy(request))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return FakeStream(reply)


class Owner(Device):
    """The device a request comes from; answers confirmations with `answer` and records the questions."""

    name, label = "chat", "typed chat"

    def __init__(self, answer=True):
        self.answer = answer
        self.questions = []

    async def confirm(self, question, details):
        self.questions.append((question, details))
        return self.answer


@pytest.fixture(autouse=True)
def log_dir(tmp_path, monkeypatch):
    """Keep test logs (and the costs summed from them) out of the real logs/."""
    monkeypatch.setattr(log, "LOG_DIR", tmp_path)
    return tmp_path


def run(coroutine):
    return asyncio.run(coroutine)
