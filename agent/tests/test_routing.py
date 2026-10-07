"""Privacy routing: personal data never reaches the free Gemini tier."""
import asyncio
from types import SimpleNamespace

import pytest
from google.genai import errors, types

from conftest import FakeClient, Owner, message, run, text
from natro_agent.agent import Agent, needs_claude
from natro_agent.dryrun import PC_TOOLS, DryRunDevice
from natro_agent.gemini import Gemini
from natro_agent.memory import Memory
from natro_agent.notes import Notes
from natro_agent.websearch import WebSearch

SECRET = "Tuxa's sister is Nino."


def gemini_says(words):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part.from_text(text=words)]))])


def gemini_calls(name, **args):
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(
        role="model", parts=[types.Part(function_call=types.FunctionCall(id=f"call-{name}", name=name, args=args))]))])


class FakeGeminiClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self.generate_content))

    async def generate_content(self, model, contents, config):
        self.requests.append({"model": model, "contents": list(contents), "config": config})
        response = self.responses.pop(0)
        if response == "hang":
            await asyncio.sleep(3600)
        if isinstance(response, Exception):
            raise response
        return response


def make_agent(tmp_path, claude, gemini, budget=15.0):
    memory = Memory(tmp_path / "memory")
    memory.remember(SECRET, "told")
    search = WebSearch(key="tvly-test", post=lambda url, body, key: {"results": []})
    tools = memory.tools() + Notes(tmp_path / "notes").tools() + search.tools()
    agent = Agent(tools, client=claude, budget=budget, memory=memory, gemini=Gemini(client=gemini))
    pc = DryRunDevice("pc", "PC", PC_TOOLS)
    agent.connect(pc)
    return agent, pc


def declared(request):
    return {d.name for tool in request["config"].tools for d in tool.function_declarations}


def everything_sent(request):
    return request["config"].system_instruction + "".join(
        part.text or "" for content in request["contents"] for part in content.parts)


def test_simple_request_is_answered_by_gemini_without_personal_data(tmp_path):
    claude, gemini = FakeClient(), FakeGeminiClient(gemini_says("Paris."))
    agent, _ = make_agent(tmp_path, claude, gemini)
    reply = run(agent.respond("What's the capital of France?", Owner()))
    assert reply.text == "Paris." and reply.model == "gemini-3.8-flash" and reply.cost == 0
    assert claude.requests == []
    request = gemini.requests[0]
    assert SECRET not in everything_sent(request)
    # Only tools that are neither personal nor need a yes, plus hand_over: no file tools, no force-close.
    assert declared(request) == {"hand_over", "pc_close_app", "pc_list_open_apps", "pc_open_app", "pc_open_link",
                                 "web_search"}


def test_gemini_runs_a_safe_device_tool(tmp_path):
    gemini = FakeGeminiClient(gemini_calls("pc_open_app", app="Chrome"), gemini_says("Chrome is open."))
    agent, pc = make_agent(tmp_path, FakeClient(), gemini)
    reply = run(agent.respond("open Chrome and tell me when it's open", Owner()))
    assert pc.calls == [("open_app", {"app": "Chrome"})]
    assert reply.text == "Chrome is open."
    response_part = gemini.requests[1]["contents"][-1].parts[0].function_response
    assert response_part.name == "pc_open_app" and response_part.id == "call-pc_open_app"
    assert "result" in response_part.response


def test_hand_over_moves_the_conversation_to_claude_for_good(tmp_path):
    claude = FakeClient(message(text("Your sister is Nino.")), message(text("You're welcome.")))
    gemini = FakeGeminiClient(gemini_says("Hi Tuxa!"), gemini_calls("hand_over", reason="asks about his family"))
    agent, _ = make_agent(tmp_path, claude, gemini)
    run(agent.respond("hi", Owner()))
    reply = run(agent.respond("who is my sister?", Owner()))
    assert reply.text == "Your sister is Nino." and reply.model == "claude-sonnet-5-5"
    first = claude.requests[0]
    assert [m["role"] for m in first["messages"]] == ["user", "assistant", "user"]
    assert first["messages"][1]["content"] == "Hi Tuxa!"
    assert SECRET in first["system"][0]["text"]  # Claude gets the memory
    # The follow-up stays on Claude; Gemini isn't asked again.
    run(agent.respond("thanks", Owner()))
    assert len(gemini.requests) == 2 and len(claude.requests) == 2


def test_claude_sees_which_tools_gemini_used(tmp_path):
    claude = FakeClient(message(text("I don't know yet.")))
    gemini = FakeGeminiClient(gemini_calls("web_search", query="Tbilisi weather"), gemini_says("Sunny, 24 degrees."),
                              gemini_calls("hand_over", reason="family"))
    agent, _ = make_agent(tmp_path, claude, gemini)
    run(agent.respond("weather in Tbilisi?", Owner()))
    run(agent.respond("who is my sister?", Owner()))
    earlier_reply = claude.requests[0]["messages"][1]["content"]
    assert earlier_reply == 'Sunny, 24 degrees.\n[Tools used for this answer: web_search {"query": "Tbilisi weather"}]'


def test_plainly_personal_request_never_reaches_gemini(tmp_path):
    claude, gemini = FakeClient(message(text("I'll remember that."))), FakeGeminiClient()
    agent, _ = make_agent(tmp_path, claude, gemini)
    run(agent.respond("Remember that my dentist is on Friday", Owner()))
    assert gemini.requests == [] and len(claude.requests) == 1


def test_personal_follow_up_in_a_gemini_conversation_skips_gemini(tmp_path):
    claude = FakeClient(message(text("Noted.")))
    gemini = FakeGeminiClient(gemini_says("Hello!"))
    agent, _ = make_agent(tmp_path, claude, gemini)
    run(agent.respond("hello", Owner()))
    run(agent.respond("add milk to my shopping note", Owner()))
    assert len(gemini.requests) == 1 and len(claude.requests) == 1


def busy():
    return errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})


def test_busy_free_model_falls_back_to_the_next_and_rests(tmp_path):
    gemini = FakeGeminiClient(busy(), gemini_says("Canberra."), gemini_says("Hi!"))
    agent, _ = make_agent(tmp_path, FakeClient(), gemini)
    reply = run(agent.respond("capital of Australia?", Owner()))
    assert reply.text == "Canberra." and reply.model == "gemini-3.6-flash"
    assert [r["model"] for r in gemini.requests] == ["gemini-3.8-flash", "gemini-3.6-flash"]
    agent.session = None  # a new conversation doesn't try the resting model again
    run(agent.respond("hello", Owner()))
    assert gemini.requests[2]["model"] == "gemini-3.6-flash"


def test_free_model_that_hangs_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr("natro_agent.gemini.ANSWER_SECONDS", 0.05)
    gemini = FakeGeminiClient("hang", gemini_says("Canberra."))
    agent, _ = make_agent(tmp_path, FakeClient(), gemini)
    reply = run(agent.respond("capital of Australia?", Owner()))
    assert reply.text == "Canberra." and reply.model == "gemini-3.6-flash"


def test_gemini_failure_falls_back_to_claude(tmp_path):
    claude = FakeClient(message(text("Paris.")))
    agent, _ = make_agent(tmp_path, claude, FakeGeminiClient(RuntimeError("429 quota")))
    reply = run(agent.respond("capital of France?", Owner()))
    assert reply.text == "Paris." and reply.model == "claude-sonnet-5-5"


def test_over_budget_gemini_stays_free_and_claude_asks(tmp_path):
    claude = FakeClient()
    gemini = FakeGeminiClient(gemini_says("Paris."))
    agent, _ = make_agent(tmp_path, claude, gemini, budget=0.0)
    owner = Owner(answer=False)
    assert run(agent.respond("capital of France?", owner)).text == "Paris."
    assert owner.questions == []
    agent.session = None
    assert run(agent.respond("remember I like jazz", owner)).text == "OK, I won't use Claude for that."
    assert "budget is used up" in owner.questions[0][0] and claude.requests == []


@pytest.mark.parametrize("request_text, personal", [
    ("open Chrome", False), ("play On Repeat on Spotify", False), ("weather in Tbilisi tomorrow", False),
    ("what's 15% of 80?", False), ("remember that I like jazz", True), ("what's in my calendar today?", True),
    ("read my latest email", True), ("add eggs to the shopping note", True), ("delete that file", True),
    ("call Nino", True), ("what do you know about me?", True),
])
def test_needs_claude(request_text, personal):
    assert needs_claude(request_text) is personal
