"""Short commands: run without a model, or with one model call instead of two."""
import pytest
from google.genai import errors

from conftest import FakeClient, Owner, message, run, text, tool_use
from natro_agent import quick
from natro_agent.agent import Agent
from natro_agent.gemini import REST_SECONDS, Gemini, rest_seconds
from natro_agent.tools import Tool, ToolError
from test_routing import FakeGeminiClient, gemini_calls, gemini_says


def spotify(fails=False):
    """A stand-in for spotify_control that records its calls."""
    calls = []

    async def control(args):
        calls.append(args)
        if fails:
            raise ToolError("Spotify isn't playing on any device right now.")
        return {"pause": "Paused.", "next": "Skipped to the next track."}.get(args["action"], "Volume 60%.")

    tool = Tool(name="spotify_control", description="Control Spotify.", run=control, source="spotify", final=True,
                input_schema={"type": "object", "properties": {"action": {"type": "string"}}})
    return tool, calls


@pytest.mark.parametrize("said, expected", [
    ("Pause the music.", ("spotify_control", {"action": "pause"})),
    ("Okay, Natro. Pause it, please.", ("spotify_control", {"action": "pause"})),
    ("Skip this song.", ("spotify_control", {"action": "next"})),
    ("next song", ("spotify_control", {"action": "next"})),
    ("Go back to the previous song.", ("spotify_control", {"action": "previous"})),
    ("Turn the volume up a bit.", ("spotify_control", {"action": "volume", "value": "up"})),
    ("Quieter", ("spotify_control", {"action": "volume", "value": "down"})),
    ("Resume the music", ("spotify_control", {"action": "resume"})),
    ("next", ("spotify_control", {"action": "next"})),
    ("pause the video on my phone", None),  # not Spotify's to answer
    ("play Yellow by Coldplay", None),
    ("stop", None),  # stop talking? stop the timer? a model decides
])
def test_quick_commands(said, expected):
    assert quick.command(said, in_conversation=False) == expected


def test_bare_words_only_outside_a_conversation():
    # "Next" while she reads his emails means the next email.
    assert quick.command("Next.", in_conversation=True) is None
    assert quick.command("Next song.", in_conversation=True) == ("spotify_control", {"action": "next"})


def test_simple_requests():
    assert quick.simple("Okay, Natro. Play Yellow by Coldplay.")
    assert not quick.simple("Open Spotify and play Kendrick's Fear.")
    assert not quick.simple("Turn the volume up, I can't hear it.")


def test_quick_command_needs_no_model():
    tool, calls = spotify()
    claude = FakeClient()  # no replies: any model call would fail the test
    agent = Agent([tool], client=claude)
    reply = run(agent.respond("Pause the music.", Owner()))
    assert reply.text == "Paused." and reply.model == "none (quick)" and reply.cost == 0
    assert calls == [{"action": "pause"}] and claude.requests == []


def test_quick_command_that_fails_goes_to_the_model():
    tool, calls = spotify(fails=True)
    claude = FakeClient(message(text("Nothing is playing on Spotify; I paused the phone instead.")))
    agent = Agent([tool], client=claude)
    reply = run(agent.respond("Pause the music.", Owner()))
    assert calls == [{"action": "pause"}] and len(claude.requests) == 1
    assert reply.text.startswith("Nothing is playing")


def test_simple_request_ends_with_a_final_tools_result():
    tool, calls = spotify()
    claude = FakeClient(message(tool_use("t1", "spotify_control", action="next"), stop="tool_use"))
    agent = Agent([tool], client=claude)
    reply = run(agent.respond("play the next one", Owner()))  # not a quick phrase, but simple
    assert reply.text == "Skipped to the next track." and len(claude.requests) == 1
    assert agent.session.messages[-1] == {"role": "assistant", "content": "Skipped to the next track."}


def test_several_things_get_the_second_model_call():
    tool, calls = spotify()
    claude = FakeClient(message(tool_use("t1", "spotify_control", action="next"), stop="tool_use"),
                        message(text("Skipped, and it's Fix You now.")))
    agent = Agent([tool], client=claude)
    reply = run(agent.respond("play the one after this and tell me what it is", Owner()))
    assert len(claude.requests) == 2 and reply.text == "Skipped, and it's Fix You now."


def test_gemini_ends_with_a_final_tools_result(tmp_path):
    tool, calls = spotify()
    gemini = FakeGeminiClient(gemini_calls("spotify_control", action="next"))
    agent = Agent([tool], client=FakeClient(), gemini=Gemini(client=gemini))
    reply = run(agent.respond("play the next one", Owner()))
    assert reply.text == "Skipped to the next track." and len(gemini.requests) == 1
    # The conversation goes on normally.
    gemini.responses.append(gemini_says("Sure."))
    assert run(agent.respond("thanks", Owner())).text == "Sure."


def test_exhausted_free_model_rests_until_its_quota_resets():
    daily = errors.APIError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota", "details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "30784s"}]}})
    assert rest_seconds(daily) == 30784
    minute = errors.APIError(429, {"error": {"code": 429, "message": "quota", "details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]}})
    assert rest_seconds(minute) == 30  # at least half a minute
    busy = errors.APIError(503, {"error": {"code": 503, "message": "high demand"}})
    assert rest_seconds(busy) == REST_SECONDS
