"""Natro's instructions: who she is (identity/NATRO.md), what she can do right now, and what she remembers.

The capabilities part is generated from the connected devices and the tools on
offer, so "what can you do?" and "I can't reach your PC right now" stay true. It
must come out the same for the same devices and tools: any change to the
instructions means Claude can't reuse its cached copy. What Natro remembers comes
last, since it changes most often.

Claude gets the memory; the free Gemini tier never does (it gets the hand-over
rules instead).
"""
from collections import defaultdict

from natro_agent.config import IDENTITY_FILE

# The devices Natro knows about, so she can tell when one is offline.
KNOWN_DEVICES = {"pc": "PC", "phone": "phone"}


def system_prompt(tools, devices, memory=None, hand_over_rules=None):
    """tools: every tool on offer, the devices' included. devices: the connected ones.

    memory: what Natro remembers (for Claude). hand_over_rules: for Gemini instead.
    """
    identity = IDENTITY_FILE.read_text(encoding="utf-8").strip()
    parts = [identity, f"## Right now\n\n{capabilities(tools, devices)}"]
    if hand_over_rules:
        parts.append(hand_over_rules)
    if memory is not None:
        parts.append(f"## What you remember about your owner\n\n{memory}")
    return "\n\n".join(parts)


def capabilities(tools, devices):
    connected = {device.name: device for device in devices}
    lines = []
    for name, label in KNOWN_DEVICES.items():
        if name in connected:
            lines.append(f"- Your owner's {label} is connected.")
        else:
            lines.append(f"- Your owner's {label} is offline, so you can't use it or its tools right now.")

    by_source = defaultdict(list)
    for tool in tools:
        by_source[tool.source].append(tool.name)
    for source, names in sorted(by_source.items()):
        if source in connected:
            where = f"the {connected[source].label}"
        elif source == "natro":
            where = "you (memory, notes, web)"
        elif source == "google":
            where = "his Google account (Calendar, Tasks, Gmail, Drive, Docs)"
        elif source == "spotify":
            where = "his Spotify (Premium; plays on any device where Spotify is open)"
        else:
            where = source
        lines.append(f"- Tools from {where}: {', '.join(sorted(names))}.")
    if not tools:
        lines.append("- You have no tools yet. You can talk and answer from your own knowledge, "
                     "and you say so when a request needs a tool you don't have.")
    return "\n".join(lines)
