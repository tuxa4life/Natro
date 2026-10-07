"""MCP servers from config/mcp.json: started with the agent, their tools offered to Claude.

Each server entry:
    "enabled": false turns it off without deleting it.
    "command" + "args" (+ "env"): a local server, started as a process.
    "url": a remote server (Streamable HTTP).
    "personal": its results hold personal data, so they only go to Claude.
    "confirm_tools": tool names that need the owner's yes first ("*" for all).
${NAME} in args, env or url is replaced by that environment variable (from
.env), so secrets stay out of the config; ${ROOT} is the repo folder.
Tool names get the server's name in front ("notes_read_file") so two servers
can't clash.
"""
import json
import os
import re
from contextlib import AsyncExitStack
from functools import partial

from mcp import Client, StdioServerParameters

from natro_agent import log
from natro_agent.config import ROOT
from natro_agent.tools import Tool, ToolError, tool_name


def expand(value):
    """Replace ${NAME} with the environment variable NAME (${ROOT}: the repo folder)."""
    def lookup(match):
        name = match.group(1)
        if name == "ROOT":
            return str(ROOT)
        if name not in os.environ:
            raise KeyError(f"${{{name}}} is used in config/mcp.json but not set in .env")
        return os.environ[name]
    return re.sub(r"\$\{(\w+)\}", lookup, value)


def target(settings):
    """What mcp.Client connects to: a URL, or a command to start."""
    if "url" in settings:
        return expand(settings["url"])
    env = {key: expand(value) for key, value in settings.get("env", {}).items()}
    return StdioServerParameters(command=settings["command"], args=[expand(arg) for arg in settings.get("args", [])],
                                 env=env or None)


def result_text(result):
    """An MCP tool result as text for Claude; an error result raises ToolError."""
    parts = []
    for item in result.content:
        if item.type == "text":
            parts.append(item.text)
        else:
            parts.append(f"[{item.type} content left out]")
    if not parts and result.structured_content is not None:
        parts.append(json.dumps(result.structured_content, ensure_ascii=False))
    text = "\n".join(parts)
    if result.is_error:
        raise ToolError(text or "The tool reported an error.")
    return text


class MCPServers:
    """The connected MCP servers. Open and close them from the same task."""

    def __init__(self):
        self._stack = AsyncExitStack()
        self.tools = []

    async def start(self, config_path):
        """Connect every enabled server in the config. A server that fails is skipped (and logged)."""
        servers = json.loads(config_path.read_text(encoding="utf-8")).get("servers", {})
        for name, settings in servers.items():
            if not settings.get("enabled", True):
                continue
            try:
                await self.connect(name, target(settings), settings)
            except Exception as e:
                log.write("mcp_error", server=name, error=repr(e))
                print(f"MCP server {name!r} not started: {e}")
        return self.tools

    async def connect(self, name, server, settings):
        """Connect one server (a URL, StdioServerParameters, or an in-process server) and add its tools."""
        client = await self._stack.enter_async_context(Client(server))
        listed, cursor = [], None
        while True:
            page = await client.list_tools(cursor=cursor)
            listed += page.tools
            if not (cursor := page.next_cursor):
                break
        confirm = set(settings.get("confirm_tools", []))
        for tool in listed:
            self.tools.append(Tool(
                name=tool_name(name, tool.name),
                description=tool.description or tool.name,
                input_schema=tool.input_schema,
                run=partial(self._call, client, tool.name),
                source=name,
                personal=settings.get("personal", True),
                confirm="*" in confirm or tool.name in confirm,
            ))

    @staticmethod
    async def _call(client, name, args):
        return result_text(await client.call_tool(name, args))

    async def close(self):
        await self._stack.aclose()
