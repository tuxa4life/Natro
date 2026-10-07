"""Natro's brain: the conversation loop with Claude (and the free Gemini tier), tools, confirmations and costs.

There is one conversation at a time for all devices: a follow-up within
SESSION_MINUTES continues it, from any device; after that a new one starts.

Privacy routing: a new conversation starts on the free Gemini tier (if
configured), which only gets tools that are neither personal nor risky. When a
request needs more, Gemini hands it over, and Claude answers it with the
conversation so far; from then on the conversation stays on Claude. Requests
that plainly involve personal data (see needs_claude) go straight to Claude, so
they never reach the free tier. Memory only ever goes to Claude.

Claude's prompt cache and its thinking blocks are tied to the exact
conversation, so within a Claude conversation the instructions and the tool list
never change and messages are only ever appended. A device that disconnects
mid-conversation keeps its tools in the list; calling them returns "offline".
"""
import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime

import anthropic

from natro_agent import costs, identity, log, quick
from natro_agent.config import monthly_budget, timezone
from natro_agent.gemini import HAND_OVER_RULES
from natro_agent.models import DEFAULT_MODEL, FALLBACK_MODEL, cost, options
from natro_agent.tools import ToolError, result_text

# A follow-up within this time continues the conversation (it matches how long
# Claude keeps its prompt cache).
SESSION_MINUTES = 5
# Model calls per request, so a confused tool loop can't run up costs. Using an app on the phone by its
# screen takes a step per tap.
MAX_STEPS = 20
MAX_TOKENS = 16000
TOOL_SECONDS = 60
CONFIRM_SECONDS = 60
# Longer tool results are cut, to keep requests small.
RESULT_CHARS = 20000
# NATRO_CHECK_HISTORY=1 makes Claude reject any edit to a past conversation (see models.options).
CHECK_HISTORY = os.environ.get("NATRO_CHECK_HISTORY") == "1"
TOKEN_KINDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")

# Words that mark a request as personal or risky: it goes straight to Claude.
# Broad on purpose: a wrong guess here only costs a little money.
PERSONAL_HINTS = re.compile(
    r"\b(remember|forget|remind|reminder|memory|note|notes|to-?do|tasks?|e-?mails?|gmail|inbox|mail|calendar|"
    r"schedule|meeting|appointment|events?|files?|documents?|docs?|drive|sheets?|spreadsheets?|folders?|photos?|"
    r"messages?|sms|texts?|whatsapp|"
    r"notifications?|contacts?|call|delete|remove|send|share|pay|password|bank|address|about me|my name)\b",
    re.IGNORECASE)


def needs_claude(text):
    """Whether a request plainly involves personal data or a risky action."""
    return bool(PERSONAL_HINTS.search(text))


@dataclass
class Reply:
    text: str
    model: str
    cost: float = 0.0
    seconds: float = 0.0
    tool_calls: list = field(default_factory=list)
    # Tokens over all Claude calls, to see what caching saves.
    tokens: dict = field(default_factory=lambda: dict.fromkeys(TOKEN_KINDS, 0))


class Conversation:
    def __init__(self):
        self.last_used = time.monotonic()

    @property
    def expired(self):
        return time.monotonic() - self.last_used > SESSION_MINUTES * 60


class Session(Conversation):
    """A conversation with Claude: fixed instructions and tools, and messages that only grow.

    earlier: (request, reply, tools used) turns handed over from Gemini, as plain
    text. The tools used are noted with each reply, so Claude doesn't take an
    answer that was looked up for a guess and look it up again.
    """

    def __init__(self, model, tools, devices, memory=None, earlier=()):
        super().__init__()
        self.model = model
        self.tools = {tool.name: tool for tool in tools}
        self.tool_definitions = [tool.definition() for tool in sorted(tools, key=lambda tool: tool.name)]
        # The cache marker lets the next conversation reuse the cached instructions and tools.
        self.system = [{"type": "text", "text": identity.system_prompt(tools, devices, memory=memory),
                        "cache_control": {"type": "ephemeral"}}]
        self.messages = []
        for request, reply, tools_used in earlier:
            if tools_used:
                reply = f"{reply}\n[Tools used for this answer: {'; '.join(tools_used)}]"
            self.messages += [{"role": "user", "content": request}, {"role": "assistant", "content": reply}]


class GeminiSession(Conversation):
    """A conversation on the free Gemini tier: no personal or risky tools, no memory."""

    def __init__(self, tools, devices):
        super().__init__()
        self.tool_list = sorted((tool for tool in tools if not tool.personal and not tool.confirm),
                                key=lambda tool: tool.name)
        self.tools = {tool.name: tool for tool in self.tool_list}
        self.system = identity.system_prompt(self.tool_list, devices, hand_over_rules=HAND_OVER_RULES)
        self.model = None  # the free model answering this conversation, once one has
        self.contents = []  # Gemini's own history, with its thought signatures
        self.turns = []  # (request, reply, tools used), for Claude if it takes over


def echo_content(content):
    """The assistant turn to send back with the next request.

    Unchanged, thinking blocks included (Claude needs them exactly as they were),
    except when the API switched to a fallback model partway through the reply:
    then the declined model's thinking and tool calls before the switch are left
    out, as the API requires. The switch marker itself is dropped too.
    """
    switches = [i for i, block in enumerate(content) if block.type == "fallback"]
    if not switches:
        return list(content)
    boundary = switches[-1]
    return [block for block in content[:boundary] if block.type == "text"] + list(content[boundary + 1:])


def first_line(text):
    return text.strip().splitlines()[0] if text.strip() else text


def text_of(content):
    return "".join(block.text for block in content if block.type == "text").strip()


class Agent:
    """memory: a Memory (its contents go into Claude's instructions). gemini: a Gemini, or None for Claude only."""

    def __init__(self, tools=(), model=DEFAULT_MODEL, client=None, budget=None, memory=None, gemini=None):
        self.client = client or anthropic.AsyncAnthropic()
        self.model = model
        self.own_tools = list(tools)  # Natro's own and the MCP servers'
        self.memory = memory
        self.gemini = gemini
        self.devices = {}  # name -> Device, while connected
        self.session = None
        self.ledger = costs.Ledger(monthly_budget() if budget is None else budget)
        self._busy = asyncio.Lock()  # one request at a time
        self._asking = asyncio.Lock()  # one confirmation question at a time

    def connect(self, device):
        self.devices[device.name] = device

    def disconnect(self, device):
        if self.devices.get(device.name) is device:
            del self.devices[device.name]

    def offered_tools(self):
        """Every tool on offer right now: Natro's own and the connected devices'."""
        tools = list(self.own_tools)
        for device in self.devices.values():
            tools += [replace(tool, run=self._while_connected(device, tool)) for tool in device.tools()]
        return tools

    def _while_connected(self, device, tool):
        async def run_on_device(args):
            current = self.devices.get(device.name)
            if current is None:
                raise ToolError(f"Your owner's {device.label} is offline right now.")
            if current is device:
                return await tool.run(args)
            # The device reconnected (its app restarted, or the network blinked): its tool of the same name.
            same = next((t for t in current.tools() if t.name == tool.name), None)
            if same is None:
                raise ToolError(f"Your owner's {device.label} no longer offers {tool.name}.")
            return await same.run(args)
        return run_on_device

    async def respond(self, text, device, on_text=None, voice_cost=0.0):
        """Handle one request (English text) from a device and return Natro's reply.

        on_text receives the reply in pieces as it is written. voice_cost is what
        recognizing and translating the request cost, for the monthly total.
        """
        on_text = on_text or (lambda piece: None)
        async with self._busy:
            started = time.monotonic()
            try:
                reply = await self._respond(text, device, on_text)
            except anthropic.APIError as e:
                log.write("error", device=device.name, text=text, error=repr(e))
                reply = Reply("Sorry, I can't reach Claude right now, so I can't do that. Please try again in a bit.",
                              model=self.model)
                on_text(reply.text)
            reply.seconds = time.monotonic() - started
            if self.ledger.add(reply.cost + voice_cost):
                note = (f"By the way, this month's spending has passed {costs.WARN_AT:.0%} "
                        f"of the {self.ledger.budget:g} dollar budget.")
                reply.text = f"{reply.text} {note}".strip()
                on_text(f" {note}")
            log.write("request", device=device.name, text=text, reply=reply.text, model=reply.model,
                      cost=round(reply.cost, 6), voice_cost=round(voice_cost, 6), tokens=reply.tokens,
                      seconds=round(reply.seconds, 2), tools=[call["tool"] for call in reply.tool_calls])
            return reply

    async def _respond(self, text, device, on_text):
        request = f"{self._header(device)}\n{text}"
        if (reply := await self._quick(text, request, device, on_text)) is not None:
            return reply
        if self.session is None or self.session.expired:
            if self.gemini and not needs_claude(text):
                self.session = GeminiSession(self.offered_tools(), list(self.devices.values()))
            else:
                self.session = self._claude_session()
        simple = quick.simple(text)
        if isinstance(self.session, GeminiSession):
            if not needs_claude(text):
                reply = await self._gemini_turn(self.session, request, device, on_text, simple)
                if reply is not None:
                    return reply
            # Claude takes over, with the conversation so far, and keeps it.
            self.session = self._claude_session(earlier=self.session.turns)
        return await self._claude_turn(self.session, request, device, on_text, simple)

    async def _quick(self, text, request, device, on_text):
        """A common short command run without a model (see quick.py), or None if it isn't one or didn't work."""
        in_conversation = self.session is not None and not self.session.expired
        found = quick.command(text, in_conversation)
        tools = {tool.name: tool for tool in self.offered_tools()}
        if found is None or found[0] not in tools:
            return None
        name, args = found
        reply = Reply("", "none (quick)")
        content, failed = await self._call_tool(tools, name, args, device, "", reply)
        if failed:
            return None  # a model may know better (say, the phone's media rather than Spotify)
        reply.text = first_line(content)
        on_text(reply.text)
        # Keep the conversation whole, for follow-ups.
        if isinstance(self.session, GeminiSession) and in_conversation:
            self.session.turns.append((request, reply.text, [f"{name} {json.dumps(args)}"]))
            self.session.last_used = time.monotonic()
        elif isinstance(self.session, Session) and in_conversation:
            self.session.messages += [{"role": "user", "content": request},
                                      {"role": "assistant", "content": f"{reply.text} [ran {name} {json.dumps(args)}]"}]
            self.session.last_used = time.monotonic()
        return reply

    def _claude_session(self, earlier=()):
        memory = self.memory.prompt_section() if self.memory else None
        return Session(self.model, self.offered_tools(), list(self.devices.values()), memory, earlier)

    async def _gemini_turn(self, session, request, device, on_text, simple=False):
        """Gemini's reply, or None when Claude should answer instead."""
        reply = Reply("", self.gemini.model)

        async def run_tool(name, args):
            return await self._call_tool(session.tools, name, args, device, "", reply)

        try:
            outcome = await self.gemini.respond(session, request, run_tool, shortcut=simple)
        except Exception as e:  # rate limit, outage, odd response: Claude answers instead
            log.write("model_error", model=self.gemini.model, error=repr(e), switched_to=self.model)
            return None
        if outcome.handed_over:
            log.write("hand_over", reason=outcome.reason)
            return None
        used = [f"{call['tool']} {json.dumps(call['args'], ensure_ascii=False)}" for call in reply.tool_calls]
        session.turns.append((request, outcome.text, used))
        session.last_used = time.monotonic()
        reply.text, reply.model = outcome.text, outcome.model
        on_text(outcome.text)
        return reply

    async def _claude_turn(self, session, request, device, on_text, simple=False):
        if self.ledger.over_budget and not await self._ask(
                device, f"This month's {self.ledger.budget:g} dollar budget is used up. Use Claude anyway?",
                f"spent this month: ${self.ledger.spent:.2f}"):
            return Reply("OK, I won't use Claude for that.", model="none")

        session.messages.append({"role": "user", "content": request})
        reply = Reply("", session.model)
        said = []
        for step in range(MAX_STEPS):
            response, model = await self._call(session, self._spaced(on_text, bool(said)))
            reply.cost += cost(model, response.usage)
            for kind in TOKEN_KINDS:
                reply.tokens[kind] += getattr(response.usage, kind) or 0
            content = echo_content(response.content)
            if response.stop_reason == "refusal":
                said = ["Sorry, I can't help with that one."]
                self.session = None  # don't carry a declined request into follow-ups
                break
            if text_of(content):
                said.append(text_of(content))
            if response.stop_reason == "max_tokens":
                self.session = None  # the reply was cut off, maybe mid tool call
                break
            if content:
                session.messages.append({"role": "assistant", "content": content})
            if response.stop_reason == "pause_turn":
                continue
            tool_uses = [block for block in content if block.type == "tool_use"]
            if not tool_uses:
                break
            # Natro's sentence before a risky tool is the confirmation question.
            results = await asyncio.gather(*(self._use_tool(session, block, device, text_of(content), reply)
                                             for block in tool_uses))
            session.messages.append({"role": "user", "content": list(results)})
            if simple and step == 0 and not said and len(results) == 1 and not results[0]["is_error"] \
                    and getattr(session.tools.get(tool_uses[0].name), "final", False):
                # The tool's result says it all ("Paused."): no second model call to word it.
                said = [first_line(results[0]["content"])]
                on_text(said[0])
                session.messages.append({"role": "assistant", "content": said[0]})
                break
        else:
            said.append("I stopped there: that was taking too many steps.")
        session.last_used = time.monotonic()
        reply.text = " ".join(said)
        reply.model = session.model
        return reply

    def _header(self, device):
        """The first line of each request: when, from where, and which devices are online."""
        now = datetime.now(timezone())
        online = [d.label for name, d in self.devices.items() if name in identity.KNOWN_DEVICES]
        return f"[{now:%A %d %B %Y, %H:%M} · from the {device.label} · online: {', '.join(online) or 'no devices'}]"

    @staticmethod
    def _spaced(on_text, after_earlier_text):
        """on_text, with a space before the first piece if an earlier step already wrote something."""
        pending = [after_earlier_text]

        def show(piece):
            if pending[0]:
                on_text(" ")
                pending[0] = False
            on_text(piece)
        return show

    async def _call(self, session, on_text):
        """One model call. If the model's API fails, the rest of the session moves to its fallback model."""
        try:
            return await self._stream(session, session.model, on_text), session.model
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
            fallback = FALLBACK_MODEL.get(session.model)
            retryable = isinstance(e, anthropic.APIConnectionError) or e.status_code == 429 or e.status_code >= 500
            if not (fallback and retryable):
                raise
            log.write("model_error", model=session.model, error=repr(e), switched_to=fallback)
            session.model = fallback
            return await self._stream(session, fallback, on_text), fallback

    async def _stream(self, session, model, on_text):
        request = dict(model=model, max_tokens=MAX_TOKENS, system=session.system, messages=session.messages,
                       # Also cache the conversation so far, for the next step and follow-ups.
                       cache_control={"type": "ephemeral"}, **options(model, CHECK_HISTORY))
        if session.tool_definitions:
            request["tools"] = session.tool_definitions
        async with self.client.beta.messages.stream(**request) as stream:
            async for piece in stream.text_stream:
                on_text(piece)
            return await stream.get_final_message()

    async def _use_tool(self, session, block, device, question, reply):
        """Run one of Claude's tool calls and return its tool_result."""
        args = block.input if isinstance(block.input, dict) else {}
        content, is_error = await self._call_tool(session.tools, block.name, args, device, question, reply)
        return {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}

    async def _call_tool(self, tools, name, args, device, question, reply):
        """Run one tool call, asking first if it needs a yes. Returns (text for the model, whether it failed)."""
        tool = tools.get(name)
        if tool is None:
            # Models sometimes get the letter case of a name wrong.
            tool = next((t for known, t in tools.items() if known.lower() == name.lower()), None)
        call = {"tool": name, "args": args}
        if tool is None:
            content, is_error = f"There is no tool named {name!r}.", True
        elif tool.confirm and not await self._ask(device, question or f"Should I run {tool.name}?",
                                                  f"{tool.name} {json.dumps(args, ensure_ascii=False)}"):
            content, is_error = "Your owner said no, so this was not done.", False
            call["declined"] = True
        else:
            content, is_error = await self._run(tool, args)
        call.update(ok=not is_error, result=content[:500])
        log.write("tool", device=device.name, **call)
        reply.tool_calls.append(call)
        if len(content) > RESULT_CHARS:
            content = f"{content[:RESULT_CHARS]}\n[cut here: the whole result was {len(content)} characters]"
        return content, is_error

    async def _run(self, tool, args):
        """Returns (text for the model, whether it failed)."""
        try:
            return result_text(await asyncio.wait_for(tool.run(args), TOOL_SECONDS)), False
        except ToolError as e:
            return str(e), True
        except asyncio.TimeoutError:
            return f"{tool.name} took longer than {TOOL_SECONDS} seconds and was stopped.", True
        except Exception as e:
            return f"{tool.name} failed: {type(e).__name__}: {e}", True

    async def _ask(self, device, question, details):
        """Ask the owner on the device; no answer within CONFIRM_SECONDS counts as no."""
        async with self._asking:
            try:
                return await asyncio.wait_for(device.confirm(question, details), CONFIRM_SECONDS)
            except asyncio.TimeoutError:
                return False
