import anthropic
import httpx2

from conftest import FakeClient, Owner, fallback, message, run, text, thinking, tool_use
from natro_agent import agent as agent_module
from natro_agent.agent import Agent, echo_content
from natro_agent.dryrun import PC_TOOLS, DryRunDevice
from natro_agent.tools import ASKS_FIRST


def make_agent(client, budget=15.0):
    agent = Agent(client=client, budget=budget)
    pc = DryRunDevice("pc", "PC", PC_TOOLS)
    agent.connect(pc)
    return agent, pc


def test_reply_and_cost():
    agent, _ = make_agent(FakeClient(message(thinking(), text("Hi, I'm Natro."))))
    reply = run(agent.respond("who are you?", Owner()))
    assert reply.text == "Hi, I'm Natro."
    assert reply.model == "claude-sonnet-5-5"
    assert abs(reply.cost - (1000 * 2 + 50 * 10) / 1e6) < 1e-12


def test_tool_loop_only_appends_to_the_conversation():
    client = FakeClient(
        message(tool_use("t1", "pc_open_app", app="Chrome"), stop="tool_use"),
        message(text("Chrome is open.")),
        message(text("You asked me to open Chrome.")),
    )
    agent, pc = make_agent(client)
    owner = Owner()
    run(agent.respond("open Chrome and tell me when it's open", owner))
    run(agent.respond("what did I ask?", owner))

    assert pc.calls == [("open_app", {"app": "Chrome"})]
    first, second, third = client.requests
    # Same instructions and tools for the whole conversation...
    assert first["system"] == second["system"] == third["system"]
    assert first["tools"] == second["tools"] == third["tools"]
    # ...and each request repeats the previous one's messages exactly, then adds to them.
    assert second["messages"][:len(first["messages"])] == first["messages"]
    assert third["messages"][:len(second["messages"])] == second["messages"]
    results = second["messages"][-1]["content"]
    assert results == [{"type": "tool_result", "tool_use_id": "t1", "content": "Done (dry run: open_app only pretended to run).",
                        "is_error": False}]


def test_risky_tool_asks_first_with_natros_sentence():
    client = FakeClient(
        message(text("I'll force close Spotify."), tool_use("t1", "pc_force_close_app", app="Spotify"), stop="tool_use"),
        message(text("OK, I left it.")),
    )
    agent, pc = make_agent(client)
    owner = Owner(answer=False)
    run(agent.respond("force close Spotify", owner))
    assert owner.questions == [("I'll force close Spotify.", 'pc_force_close_app {"app": "Spotify"}')]
    assert pc.calls == []
    assert client.requests[1]["messages"][-1]["content"][0]["content"] == "Your owner said no, so this was not done."


def test_risky_tool_runs_after_yes():
    client = FakeClient(
        message(tool_use("t1", "pc_force_close_app", app="Spotify"), stop="tool_use"),
        message(text("Done.")),
    )
    agent, pc = make_agent(client)
    run(agent.respond("force close Spotify", Owner(answer=True)))
    assert pc.calls == [("force_close_app", {"app": "Spotify"})]


def test_device_that_went_offline_mid_conversation():
    client = FakeClient(
        message(text("Hello.")),
        message(tool_use("t1", "pc_open_app", app="Chrome"), stop="tool_use"),
        message(text("Your PC is offline.")),
    )
    agent, pc = make_agent(client)
    run(agent.respond("hi", Owner()))
    agent.disconnect(pc)
    run(agent.respond("open Chrome", Owner()))
    result = client.requests[2]["messages"][-1]["content"][0]
    assert result["is_error"] and "offline" in result["content"]
    assert pc.calls == []
    assert "online: no devices" in client.requests[1]["messages"][-1]["content"]


def test_device_that_reconnected_mid_conversation_is_used():
    client = FakeClient(
        message(text("Hello.")),
        message(tool_use("t1", "pc_open_app", app="Chrome"), stop="tool_use"),
        message(text("Done.")),
    )
    agent, old_pc = make_agent(client)
    run(agent.respond("hi", Owner()))
    agent.disconnect(old_pc)  # the PC app restarted
    new_pc = DryRunDevice("pc", "PC", PC_TOOLS)
    agent.connect(new_pc)
    run(agent.respond("open Chrome and tell me when it's open", Owner()))
    assert new_pc.calls == [("open_app", {"app": "Chrome"})] and old_pc.calls == []
    assert not client.requests[2]["messages"][-1]["content"][0]["is_error"]


def test_follow_up_continues_then_conversation_expires():
    client = FakeClient(message(text("One.")), message(text("Two.")), message(text("Three.")))
    agent, _ = make_agent(client)
    run(agent.respond("first", Owner()))
    run(agent.respond("second", Owner()))
    assert len(client.requests[1]["messages"]) == 3
    agent.session.last_used -= agent_module.SESSION_MINUTES * 60 + 1
    run(agent.respond("third", Owner()))
    assert len(client.requests[2]["messages"]) == 1


def test_echo_after_a_fallback_drops_the_declined_models_internals():
    reply = message(thinking(), text("Partial "), tool_use("t0", "pc_open_app", app="x"), fallback(),
                    thinking(), text("answer."))
    kept = echo_content(reply.content)
    assert [block.type for block in kept] == ["text", "thinking", "text"]
    assert echo_content(message(thinking(), text("Hi")).content) == list(message(thinking(), text("Hi")).content)


def test_api_failure_moves_the_conversation_to_haiku():
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    client = FakeClient(error, message(text("Hi.")))
    agent, _ = make_agent(client)
    reply = run(agent.respond("hi", Owner()))
    assert reply.text == "Hi." and reply.model == "claude-haiku-4-5"
    assert client.requests[1]["model"] == "claude-haiku-4-5"
    assert "betas" not in client.requests[1]


def test_refusal_starts_a_fresh_conversation():
    client = FakeClient(message(stop="refusal"), message(text("Hi.")))
    agent, _ = make_agent(client)
    reply = run(agent.respond("something declined", Owner()))
    assert reply.text == "Sorry, I can't help with that one."
    run(agent.respond("hi", Owner()))
    assert len(client.requests[1]["messages"]) == 1


def test_too_many_steps_stops_the_loop():
    steps = [message(tool_use(f"t{i}", "pc_open_app", app="Chrome"), stop="tool_use")
             for i in range(agent_module.MAX_STEPS)]
    agent, pc = make_agent(FakeClient(*steps))
    reply = run(agent.respond("open Chrome, then open it again forever", Owner()))
    assert len(pc.calls) == agent_module.MAX_STEPS
    assert "too many steps" in reply.text


def test_budget_warning_then_cap():
    # Each reply costs $0.0025; the budget is $0.005.
    client = FakeClient(message(text("One.")), message(text("Two.")))
    agent, _ = make_agent(client, budget=0.005)
    assert "One." == run(agent.respond("first", Owner())).text
    assert "passed 80%" in run(agent.respond("second", Owner())).text
    owner = Owner(answer=False)
    reply = run(agent.respond("third", owner))
    assert reply.text == "OK, I won't use Claude for that." and len(client.requests) == 2
    assert "budget is used up" in owner.questions[0][0]


def test_spending_is_summed_from_this_months_log():
    run(make_agent(FakeClient(message(text("One."))))[0].respond("first", Owner()))
    assert abs(Agent(client=FakeClient()).ledger.spent - 0.0025) < 1e-9


def test_tools_that_ask_first_say_so_to_claude():
    tools = {tool.name: tool for tool in make_agent(FakeClient())[0].offered_tools()}
    assert tools["pc_force_close_app"].definition()["description"].endswith(ASKS_FIRST)
    assert ASKS_FIRST not in tools["pc_open_app"].definition()["description"]
