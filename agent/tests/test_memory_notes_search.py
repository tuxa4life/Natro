import pytest

from conftest import run
from natro_agent.memory import Memory
from natro_agent.notes import Notes
from natro_agent.tools import ToolError
from natro_agent.websearch import WebSearch, format_results


def test_memory_remembers_and_forgets(tmp_path):
    memory = Memory(tmp_path / "memory")
    assert memory.prompt_section() == "Nothing yet."
    assert memory.remember("Tuxa's sister is Nino.", "told") == "Remembered."
    assert memory.remember("tuxa's sister is nino.", "told") == "Already remembered."
    memory.remember("Tuxa likes his coffee black.", "learned")
    section = memory.prompt_section()
    assert "Things your owner asked you to remember:\n- Tuxa's sister is Nino. (" in section
    assert "Preferences you noticed yourself:\n- Tuxa likes his coffee black. (" in section
    assert (tmp_path / "memory" / "told.md").read_text(encoding="utf-8").startswith("# Things your owner")

    memory.remember("Tuxa's brother is Giorgi.", "told")
    with pytest.raises(ToolError, match="More than one"):
        memory.forget("Tuxa's")
    assert memory.forget("sister").startswith("Forgot: Tuxa's sister is Nino.")
    assert [fact.split(" (")[0] for fact in memory.facts("told")] == ["Tuxa's brother is Giorgi."]
    with pytest.raises(ToolError):
        memory.forget("dog")


def test_memory_tools_are_personal_and_forget_asks():
    remember, forget = Memory(None).tools()
    assert remember.personal and forget.personal
    assert not remember.confirm and forget.confirm


def test_notes(tmp_path):
    notes = Notes(tmp_path / "notes")
    assert notes.list() == "There are no notes yet."
    assert notes.write("Shopping list", "- milk\n- bread", "create") == "Created the note 'Shopping list'."
    with pytest.raises(ToolError, match="already exists"):
        notes.write("shopping LIST", "x", "create")
    notes.write("shopping list", "\n- eggs\n\n", "append")
    assert notes.read("SHOPPING LIST") == "- milk\n- bread\n- eggs\n"
    notes.edit("Shopping list", "- bread\n", "")
    assert notes.read("Shopping list") == "- milk\n- eggs\n"
    with pytest.raises(ToolError, match="isn't in"):
        notes.edit("Shopping list", "butter", "")
    notes.write("Ideas", "Learn to play padel", "append")  # append creates a missing note
    assert notes.search("padel") == "Ideas: Learn to play padel"
    assert set(notes.titles()) == {"Shopping list", "Ideas"}
    assert notes.delete("ideas") == "Deleted the note 'Ideas'."
    with pytest.raises(ToolError, match="no note called"):
        notes.read("Ideas")


def test_note_titles_stay_inside_the_folder(tmp_path):
    notes = Notes(tmp_path / "notes")
    notes.write("../../evil: plan?", "x", "create")
    assert [path.name for path in (tmp_path / "notes").iterdir()] == ["evil plan.md"]
    with pytest.raises(ToolError, match="needs a title"):
        notes.write("///", "x", "create")


def test_notes_tools():
    tools = {tool.name: tool for tool in Notes(None).tools()}
    assert set(tools) == {"notes_list", "notes_read", "notes_search", "notes_write", "notes_edit", "notes_delete"}
    assert all(tool.personal for tool in tools.values())
    assert [name for name, tool in tools.items() if tool.confirm] == ["notes_delete"]


def test_web_search_sends_a_basic_search_and_formats_results():
    sent = []

    def fake_post(url, body, key):
        sent.append((url, body, key))
        return {"answer": "Sunny, 24°C.", "results": [
            {"title": "Tbilisi weather", "url": "https://example.com/w", "content": "Sunny  all\nday.",
             "published_date": "2026-10-05"}]}

    search = WebSearch(key="tvly-test", post=fake_post)
    (tool,) = search.tools()
    assert tool.name == "web_search" and not tool.personal and not tool.confirm
    text = run(tool.run({"query": "weather in Tbilisi", "topic": "news", "time_range": "day"}))
    assert text == ("Summary: Sunny, 24°C.\n1. Tbilisi weather (https://example.com/w, 2026-10-05)\n"
                    "   Sunny all day.")
    url, body, key = sent[0]
    assert url == "https://api.tavily.com/search" and key == "tvly-test"
    assert body == {"query": "weather in Tbilisi", "search_depth": "basic", "max_results": 5,
                    "include_answer": True, "topic": "news", "time_range": "day"}


def test_web_search_failures_and_no_key(monkeypatch):
    def failing_post(url, body, key):
        raise OSError("HTTP Error 432: plan limit")

    with pytest.raises(ToolError, match="plan limit"):
        run(WebSearch(key="k", post=failing_post).search("x"))
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert WebSearch().tools() == []
    assert format_results({}) == "No results."
