"""What Natro remembers about her owner: plain Markdown files in memory/, editable by hand.

    memory/told.md      things the owner asked her to remember
    memory/learned.md   preferences she noticed herself (she says so when she saves one)

One fact per line: "- Tuxa's sister is Nino. (2026-10-05)". Lines that don't
start with "- " are ignored, so headings and notes can be added by hand.
Everything remembered is in Claude's instructions for each new conversation.
Memory is personal: it never goes to the free Gemini tier.
"""
import os
from datetime import date

from natro_agent.tools import Tool, ToolError

KINDS = {
    "told": "Things your owner asked you to remember",
    "learned": "Preferences you noticed yourself",
}


class Memory:
    def __init__(self, folder):
        self.folder = folder

    def _path(self, kind):
        return self.folder / f"{kind}.md"

    def facts(self, kind):
        path = self._path(kind)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        return [line[2:].strip() for line in lines if line.startswith("- ") and line[2:].strip()]

    def prompt_section(self):
        """What Natro remembers, for her instructions."""
        parts = []
        for kind, title in KINDS.items():
            if facts := self.facts(kind):
                parts.append(f"{title}:\n" + "\n".join(f"- {fact}" for fact in facts))
        return "\n\n".join(parts) or "Nothing yet."

    def remember(self, fact, kind="told"):
        fact = " ".join(fact.split())
        if kind not in KINDS:
            raise ToolError(f"kind must be one of: {', '.join(KINDS)}")
        if not fact:
            raise ToolError("Nothing to remember: the fact is empty.")
        if any(fact.casefold() == saved.rsplit(" (", 1)[0].casefold() for saved in self.facts(kind)):
            return "Already remembered."
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(kind)
        text = path.read_text(encoding="utf-8") if path.exists() else f"# {KINDS[kind]}\n\n"
        if not text.endswith("\n"):
            text += "\n"
        self._write(path, f"{text}- {fact} ({date.today():%Y-%m-%d})\n")
        return "Remembered."

    def forget(self, text):
        """Remove the one fact containing text (in either file)."""
        wanted = text.casefold().strip()
        if not wanted:
            raise ToolError("Say which fact to forget.")
        matches = [(kind, fact) for kind in KINDS for fact in self.facts(kind) if wanted in fact.casefold()]
        if not matches:
            raise ToolError(f"Nothing remembered contains {text!r}.")
        if len(matches) > 1:
            listed = "; ".join(fact for _, fact in matches)
            raise ToolError(f"More than one fact matches, be more specific: {listed}")
        kind, fact = matches[0]
        path = self._path(kind)
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [line for line in lines if not (line.startswith("- ") and line[2:].strip() == fact)]
        self._write(path, "".join(kept))
        return f"Forgot: {fact}"

    @staticmethod
    def _write(path, text):
        # Write a new file and swap it in, so Syncthing or an editor never sees half a file.
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)

    def tools(self):
        async def remember(args):
            return self.remember(args.get("fact", ""), args.get("kind", "told"))

        async def forget(args):
            return self.forget(args.get("text", ""))

        return [
            Tool(name="remember",
                 description=("Save a lasting fact about your owner or his preferences. kind \"told\" when he asks "
                              "you to remember something; \"learned\" when you noticed a preference yourself "
                              "(then tell him briefly that you'll remember it)."),
                 input_schema={"type": "object", "properties": {
                     "fact": {"type": "string", "description": "One short sentence, about him by name."},
                     "kind": {"type": "string", "enum": list(KINDS)}},
                     "required": ["fact", "kind"]},
                 run=remember, source="natro", personal=True),
            Tool(name="forget",
                 description="Forget one remembered fact, when your owner asks you to.",
                 input_schema={"type": "object", "properties": {
                     "text": {"type": "string", "description": "Part of the fact, enough to match only it."}},
                     "required": ["text"]},
                 run=forget, source="natro", personal=True, confirm=True),
        ]
