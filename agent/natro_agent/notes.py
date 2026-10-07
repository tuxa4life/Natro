"""Longer notes: Markdown files in notes/, synced to the PC (Documents\\Natro Notes) by Syncthing.

One note per file, named after its title ("Shopping list.md"). Natro can list,
read, search, write (new note or add to one) and edit notes right away; deleting
a note needs the owner's yes. The PC keeps old versions of changed and deleted
notes (Syncthing's file versioning), so an edit can be undone there.
Notes are personal: they never go to the free Gemini tier.
"""
import os
import re
from datetime import datetime

from natro_agent.tools import Tool, ToolError

# Characters Windows doesn't allow in file names (the notes are synced there too).
_NOT_IN_FILE_NAMES = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_TITLE = 100
LIST_LIMIT = 50
SEARCH_LIMIT = 10


class Notes:
    def __init__(self, folder):
        self.folder = folder

    def _all(self):
        if not self.folder.exists():
            return []
        return sorted(self.folder.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)

    def _find(self, title):
        """The note with this title (ignoring case), or None."""
        name = self._file_name(title).casefold()
        return next((path for path in self._all() if path.name.casefold() == name), None)

    def _existing(self, title):
        path = self._find(title)
        if path is None:
            raise ToolError(f"There is no note called {title!r}. Notes: {', '.join(self.titles()) or 'none yet'}.")
        return path

    @staticmethod
    def _file_name(title):
        title = _NOT_IN_FILE_NAMES.sub("", title).strip().strip(".")[:MAX_TITLE].strip()
        if not title:
            raise ToolError("A note needs a title (letters or digits).")
        return title.removesuffix(".md") + ".md"

    def titles(self):
        return [path.stem for path in self._all()]

    def list(self):
        notes = self._all()[:LIST_LIMIT]
        if not notes:
            return "There are no notes yet."
        return "\n".join(f"{path.stem} (changed {datetime.fromtimestamp(path.stat().st_mtime):%Y-%m-%d %H:%M})"
                         for path in notes)

    def read(self, title):
        return self._existing(title).read_text(encoding="utf-8")

    def search(self, query):
        wanted = query.casefold().strip()
        if not wanted:
            raise ToolError("Say what to search for.")
        found = []
        for path in self._all():
            lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if wanted in line.casefold()]
            if lines or wanted in path.stem.casefold():
                found.append(f"{path.stem}: " + (" | ".join(lines[:3]) if lines else "(title matches)"))
            if len(found) == SEARCH_LIMIT:
                break
        return "\n".join(found) or f"No note mentions {query!r}."

    def write(self, title, text, mode="create"):
        path = self._find(title)
        if mode == "create":
            if path is not None:
                raise ToolError(f"A note called {path.stem!r} already exists: add to it (mode \"append\") or edit it.")
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self.folder / self._file_name(title)
            self._save(path, text.rstrip() + "\n")
            return f"Created the note {path.stem!r}."
        if mode == "append":
            if path is None:
                return self.write(title, text, "create")
            old = path.read_text(encoding="utf-8")
            # Blank lines at the ends of the added text would break a list in two.
            text = text.strip("\r\n")
            self._save(path,f"{old.rstrip()}\n{text.rstrip()}\n" if old.strip() else text.rstrip() + "\n")
            return f"Added to the note {path.stem!r}."
        raise ToolError('mode must be "create" or "append"')

    def edit(self, title, old_text, new_text):
        path = self._existing(title)
        text = path.read_text(encoding="utf-8")
        count = text.count(old_text) if old_text else 0
        if count != 1:
            problem = "isn't in" if count == 0 else f"appears {count} times in"
            raise ToolError(f"That text {problem} the note {path.stem!r}; quote exactly one passage.")
        changed = text.replace(old_text, new_text)
        # Removing a whole line shouldn't leave an empty one behind.
        changed = re.sub(r"\n{3,}", "\n\n", changed)
        self._save(path, changed)
        return f"Changed the note {path.stem!r}."

    def delete(self, title):
        path = self._existing(title)
        path.unlink()
        return f"Deleted the note {path.stem!r}."

    @staticmethod
    def _save(path, text):
        # Write a new file and swap it in, so Syncthing never syncs half a note.
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)

    def tools(self):
        def tool(name, description, properties, run, required=(), confirm=False):
            async def call(args):
                return run(**args)
            return Tool(name=name, description=description, run=call, source="natro", personal=True, confirm=confirm,
                        input_schema={"type": "object", "properties": properties, "required": list(required)})

        title = {"type": "string", "description": "The note's title."}
        return [
            tool("notes_list", "List your owner's notes, newest first.", {}, lambda: self.list()),
            tool("notes_read", "Read a note.", {"title": title}, self.read, ["title"]),
            tool("notes_search", "Find notes that mention something.",
                 {"query": {"type": "string"}}, self.search, ["query"]),
            tool("notes_write",
                 "Create a note (mode \"create\"), or add text at the end of one (mode \"append\"; creates it if "
                 "missing). Markdown is fine.",
                 {"title": title, "text": {"type": "string"},
                  "mode": {"type": "string", "enum": ["create", "append"]}},
                 self.write, ["title", "text", "mode"]),
            tool("notes_edit",
                 "Change part of a note: old_text (quoted exactly, appearing once) becomes new_text. An empty "
                 "new_text removes it.",
                 {"title": title, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                 self.edit, ["title", "old_text", "new_text"]),
            tool("notes_delete", "Delete a whole note.", {"title": title}, self.delete, ["title"], confirm=True),
        ]
