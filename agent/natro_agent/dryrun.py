"""Devices that only record what they would do: for tests, and for trying the agent without the device apps."""
from natro_agent.tools import Device, device_tools


def _spec(name, description, properties, personal=False, confirm=False, final=False):
    return {"name": name, "description": description, "personal": personal, "confirm": confirm, "final": final,
            "input_schema": {"type": "object", "properties": {key: {"type": kind} for key, kind in properties.items()},
                             "required": list(properties)[:1]}}


# The PC app's tools (pc/natro_pc/apps.py and files.py), with the same names and flags.
PC_TOOLS = [
    _spec("open_app", "Open an app on the PC by name, as from the Start menu.", {"app": "string"}, final=True),
    _spec("close_app", "Close an app normally, as if clicking X on its windows.", {"app": "string"}, final=True),
    _spec("force_close_app", "Kill an app that won't close. Unsaved work in it is lost.", {"app": "string"},
          confirm=True),
    _spec("list_open_apps", "Which apps have windows open on the PC.", {}),
    _spec("search_files", "Find files and folders on the PC by name, newest first.",
          {"query": "string", "everywhere": "boolean"}, personal=True),
    _spec("search_file_contents", "Find files whose text contains all these words.", {"text": "string"},
          personal=True),
    _spec("read_file", "Read a text file, Word document or PDF on the PC.", {"path": "string"}, personal=True),
    _spec("open_file", "Open a file or folder on the PC with its usual program.", {"path": "string"}, personal=True),
    _spec("open_link", "Open a web page in the PC's browser.", {"url": "string"}, final=True),
]


class DryRunDevice(Device):
    """calls lists every (tool, args) it was asked to run."""

    def __init__(self, name, label, specs):
        self.name, self.label, self.specs = name, label, specs
        self.calls = []

    def tools(self):
        return device_tools(self, self.specs, self._run)

    async def _run(self, tool, args):
        self.calls.append((tool, args))
        return f"Done (dry run: {tool} only pretended to run)."
