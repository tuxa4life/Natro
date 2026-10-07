import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as ServerToolError

from conftest import run
from natro_agent.dryrun import PC_TOOLS, DryRunDevice
from natro_agent.identity import capabilities, system_prompt
from natro_agent.mcp_client import MCPServers, expand
from natro_agent.tools import ToolError


def test_capabilities_say_which_devices_are_offline():
    pc = DryRunDevice("pc", "PC", PC_TOOLS)
    text = capabilities(pc.tools(), [pc])
    assert "Your owner's PC is connected." in text
    assert "phone is offline" in text
    assert ("Tools from the PC: pc_close_app, pc_force_close_app, pc_list_open_apps, pc_open_app, pc_open_file, "
            "pc_open_link, pc_read_file, pc_search_file_contents, pc_search_files.") in text
    assert "no tools yet" in capabilities([], [])


def test_system_prompt_is_the_same_for_the_same_devices():
    pc = DryRunDevice("pc", "PC", PC_TOOLS)
    assert system_prompt(pc.tools(), [pc]) == system_prompt(list(reversed(pc.tools())), [pc])
    assert system_prompt([], []).startswith("You are Natro")


def test_expand_reads_the_environment(monkeypatch):
    monkeypatch.setenv("SPOTIFY_ID", "abc")
    assert expand("id=${SPOTIFY_ID}") == "id=abc"
    with pytest.raises(KeyError):
        expand("${NOT_SET_ANYWHERE}")


def test_mcp_server_tools_are_offered_and_called():
    server = MCPServer("test")

    @server.tool()
    def add(a: int, b: int) -> str:
        """Add two numbers."""
        return str(a + b)

    @server.tool()
    def delete_everything() -> str:
        """Fails on purpose."""
        raise ServerToolError("not allowed")

    async def scenario():
        servers = MCPServers()
        await servers.connect("calc", server, {"personal": False, "confirm_tools": ["delete_everything"]})
        tools = {tool.name: tool for tool in servers.tools}
        try:
            assert set(tools) == {"calc_add", "calc_delete_everything"}
            assert tools["calc_add"].description == "Add two numbers."
            assert not tools["calc_add"].confirm and tools["calc_delete_everything"].confirm
            assert await tools["calc_add"].run({"a": 2, "b": 3}) == "5"
            with pytest.raises(ToolError, match="not allowed"):
                await tools["calc_delete_everything"].run({})
        finally:
            await servers.close()

    run(scenario())
