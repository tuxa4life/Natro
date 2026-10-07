"""Tools and devices, as the agent sees them.

Every tool is described the same way, whether it is Natro's own, comes from an
MCP server, or runs on a connected device (PC, phone), so identity, routing and
confirmations treat them all alike.
"""
import json
import re
from dataclasses import dataclass
from functools import partial
from typing import Any, Awaitable, Callable

# Claude accepts tool names of letters, digits, "_" and "-", up to 64 characters.
_NOT_ALLOWED_IN_NAME = re.compile(r"[^a-zA-Z0-9_-]")


def tool_name(*parts):
    return _NOT_ALLOWED_IN_NAME.sub("_", "_".join(parts))[:64]


ASKS_FIRST = ("This asks your owner first: just before calling it, write one sentence saying exactly what you're "
              "about to do (that sentence is his question).")


class ToolError(Exception):
    """A tool failed in a way Claude should hear about: the message goes back to it."""


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    run: Callable[[dict], Awaitable[Any]]  # returns text, or anything JSON can show
    source: str  # "natro", an MCP server's name, or a device's name
    # Its results hold personal data (emails, notes, files...), so only Claude may see them.
    personal: bool = False
    # Ask the owner before running it (deleting, sending, calling, force-closing...).
    confirm: bool = False
    # Its result's first line is a fine spoken reply ("Paused.", "Opened Spotify."): when a simple request
    # needed only this tool, that is the reply, without another model call.
    final: bool = False

    def definition(self):
        """The tool as Claude's API takes it."""
        description = self.description
        if self.confirm:
            # Her sentence before the call is the question the owner hears; without it he only gets the tool's name.
            description += f" {ASKS_FIRST}"
        return {"name": self.name, "description": description, "input_schema": self.input_schema}


def result_text(result):
    """A tool's return value as the text Claude gets."""
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)


class Device:
    """A connected device: the PC app, the phone app, or the typed chat.

    A device offers tools (run on the device), shows replies, and asks the owner
    to confirm risky actions: by voice, a hotkey, or a tap/click.
    """

    name = "device"  # short id used in tool names: "pc", "phone"
    label = "device"  # how Natro calls it: "PC", "phone"

    def tools(self):
        return []

    async def confirm(self, question, details):
        """Ask the owner; True only for a clear yes. details shows exactly what will run."""
        return False


def device_tools(device, specs, call):
    """Tools from a device's own descriptions of them.

    specs: [{"name", "description", "input_schema", "personal", "confirm", "final"}, ...] as
    the device sends them. call(name, args) runs one on the device.
    """
    return [Tool(name=tool_name(device.name, spec["name"]),
                 description=spec["description"],
                 input_schema=spec.get("input_schema") or {"type": "object", "properties": {}},
                 run=partial(call, spec["name"]),
                 source=device.name,
                 personal=spec.get("personal", False),
                 confirm=spec.get("confirm", False),
                 final=spec.get("final", False))
            for spec in specs]
