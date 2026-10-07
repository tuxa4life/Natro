"""The PC's tools, as Natro's brain sees them: each runs here and returns text for her.

A tool is described to the brain in the device's hello (see agent/natro_agent/server.py):
name, description, input schema, whether its results are personal (so only Claude
sees them) and whether the owner must say yes first.
"""
from dataclasses import dataclass, field
from typing import Callable


class ToolError(Exception):
    """A tool failed in a way Natro should hear about: the message goes back to her."""


@dataclass
class PCTool:
    name: str
    description: str
    run: Callable[..., str]  # blocking; called with the arguments as keywords
    properties: dict = field(default_factory=dict)
    required: list = field(default_factory=list)
    personal: bool = False
    confirm: bool = False
    final: bool = False  # its result is a fine spoken reply on its own ("Opened Spotify.")

    def spec(self):
        return {"name": self.name, "description": self.description, "personal": self.personal,
                "confirm": self.confirm, "final": self.final,
                "input_schema": {"type": "object", "properties": self.properties, "required": self.required}}


def all_tools():
    import threading

    from natro_pc import apps, files

    pc_apps = apps.Apps()
    threading.Thread(target=pc_apps.menu.warm_up, daemon=True).start()
    return apps.tools(pc_apps) + files.tools()
