"""Google Calendar, Tasks, Gmail, Drive and Docs on the owner's personal account.

Signed in once on the PC with `python -m natro_agent.google_signin` (it needs a
browser); the token it writes, tokens/google.json, goes to the same place on the
VPS. The tools are offered only when that file exists, and only for what the
token allows (Drive and Docs came later: a token from before offers neither).
Everything here is personal, so it never goes to the free Gemini tier. Deleting
an event or a task, sending an email and replacing text in a document ask the
owner first.

Permissions asked for: Calendar, Tasks, reading Gmail, sending Gmail (not
deleting or changing mail), reading Drive (not changing, deleting or sharing
files there), and Google Docs (creating and editing documents).
"""
import asyncio
import base64
import html
import io
import json
import re
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage

from natro_agent.config import ROOT, timezone
from natro_agent.tools import Tool, ToolError

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents",
]
DRIVE_SCOPE, DOCS_SCOPE = SCOPES[-2:]
TOKEN_FILE = ROOT / "tokens" / "google.json"
CALENDAR = "https://www.googleapis.com/calendar/v3/calendars/primary"
TASKS = "https://tasks.googleapis.com/tasks/v1"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
DRIVE = "https://www.googleapis.com/drive/v3/files"
DOCS = "https://docs.googleapis.com/v1/documents"
SIGN_IN_AGAIN = "Google sign-in has expired or was revoked: run `python -m natro_agent.google_signin` again."
MAX_EVENTS = 50
EMAIL_CHARS = 8000
READ_CHARS = 15000  # per read of a Drive file; start reads on
PDF_PAGES = 40
# Drive's kinds of files, as Natro names them.
KINDS = {"doc": "application/vnd.google-apps.document", "sheet": "application/vnd.google-apps.spreadsheet",
         "slides": "application/vnd.google-apps.presentation", "folder": "application/vnd.google-apps.folder",
         "pdf": "application/pdf"}
KIND_NAMES = {"application/vnd.google-apps.document": "Google Doc",
              "application/vnd.google-apps.spreadsheet": "Google Sheet",
              "application/vnd.google-apps.presentation": "Google Slides", "application/vnd.google-apps.folder": "folder",
              "application/pdf": "PDF"}
# Google's own files are read as text exported from them.
EXPORTS = {"application/vnd.google-apps.document": "text/plain",
           "application/vnd.google-apps.spreadsheet": "text/csv",
           "application/vnd.google-apps.presentation": "text/plain"}


class GoogleAPI:
    """Google's REST APIs with the saved sign-in. Requests run in a thread; errors become ToolErrors."""

    def __init__(self, session, scopes=SCOPES):
        self.session = session  # a google.auth AuthorizedSession, or a fake in tests
        self.scopes = set(scopes)  # what the sign-in allows

    @classmethod
    def from_token(cls, path=TOKEN_FILE):
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.credentials import Credentials

        # The token's own permissions: asking for more than it has would make Google refuse to refresh it.
        info = json.loads(path.read_text(encoding="utf-8"))
        scopes = info.get("scopes") or SCOPES
        return cls(AuthorizedSession(Credentials.from_authorized_user_info(info, scopes)), scopes)

    async def call(self, method, url, params=None, body=None, raw=False):
        """Google's JSON reply (raw: the bytes, for file contents)."""
        try:
            response = await asyncio.to_thread(self.session.request, method, url, params=params, json=body, timeout=30)
        except Exception as e:
            if type(e).__name__ == "RefreshError":
                raise ToolError(SIGN_IN_AGAIN)
            raise ToolError(f"Couldn't reach Google: {e}")
        if response.status_code >= 400:
            try:
                message = response.json()["error"]["message"]
            except Exception:
                message = response.text[:300]
            if response.status_code == 401:
                raise ToolError(SIGN_IN_AGAIN)
            raise ToolError(f"Google said: {message} (HTTP {response.status_code})")
        if raw:
            return response.content
        return response.json() if response.content else {}


# Times. Natro passes "2026-10-06" for a day, or "2026-10-06T15:00" for a time
# where the owner is (an explicit offset is respected too).

def parse_when(value):
    """A date, or a datetime in the owner's time zone."""
    value = value.strip()
    try:
        if "T" not in value and len(value) == 10:
            return date.fromisoformat(value)
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise ToolError(f"Can't read the time {value!r}: use 2026-10-06 or 2026-10-06T15:00.")
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone())


def start_of(day):
    return datetime.combine(day, time(), tzinfo=timezone())


def as_moment(when):
    return when if isinstance(when, datetime) else start_of(when)


def event_time(field):
    """A Google event's start or end, as the owner's local date or datetime."""
    if "dateTime" in field:
        return datetime.fromisoformat(field["dateTime"]).astimezone(timezone())
    return date.fromisoformat(field["date"])


def describe_event(event):
    start, end = event_time(event["start"]), event_time(event["end"])
    if isinstance(start, datetime):
        when = f"{start:%a %d %b}, {start:%H:%M}-{end:%H:%M}"
    else:
        last = end - timedelta(days=1)
        when = f"{start:%a %d %b}, all day" + (f" until {last:%a %d %b}" if last > start else "")
    where = f" (at {event['location']})" if event.get("location") else ""
    return f"{when}: {event.get('summary', '(no title)')}{where} [id {event['id']}]"


def event_times(start, end):
    """Google's start and end fields: all-day for dates (end exclusive), else timed (an hour by default)."""
    if isinstance(start, date) and not isinstance(start, datetime):
        last = end if isinstance(end, date) and not isinstance(end, datetime) else start
        return {"date": start.isoformat()}, {"date": (last + timedelta(days=1)).isoformat()}
    end = as_moment(end) if end else start + timedelta(hours=1)
    zone = str(timezone())
    return ({"dateTime": start.isoformat(), "timeZone": zone}, {"dateTime": end.isoformat(), "timeZone": zone})


class Google:
    def __init__(self, api):
        self.api = api

    # Calendar

    async def events(self, start=None, end=None, query=None):
        first = parse_when(start) if start else datetime.now(timezone()).date()
        if end:
            last = parse_when(end)
            until = last + timedelta(days=1) if not isinstance(last, datetime) else last
        else:
            until = first + timedelta(days=1 if start else 7)
        params = {"timeMin": as_moment(first).isoformat(), "timeMax": as_moment(until).isoformat(),
                  "singleEvents": "true", "orderBy": "startTime", "maxResults": MAX_EVENTS}
        if query:
            params["q"] = query
        items = (await self.api.call("GET", f"{CALENDAR}/events", params)).get("items", [])
        return "\n".join(describe_event(event) for event in items) or "No events then."

    async def create_event(self, title, start, end=None, location=None, description=None):
        start_field, end_field = event_times(parse_when(start), parse_when(end) if end else None)
        body = {"summary": title, "start": start_field, "end": end_field}
        if location:
            body["location"] = location
        if description:
            body["description"] = description
        event = await self.api.call("POST", f"{CALENDAR}/events", body=body)
        return f"Created: {describe_event(event)}"

    async def update_event(self, event_id, title=None, start=None, end=None, location=None, description=None):
        body = {key: value for key, value in (("summary", title), ("location", location),
                                              ("description", description)) if value is not None}
        if start or end:
            old = await self.api.call("GET", f"{CALENDAR}/events/{event_id}")
            old_start, old_end = event_time(old["start"]), event_time(old["end"])
            new_start = parse_when(start) if start else old_start
            if end:
                new_end = parse_when(end)
            elif isinstance(new_start, datetime) and isinstance(old_start, datetime):
                new_end = new_start + (old_end - old_start)  # keep the length
            else:
                new_end = None
            body["start"], body["end"] = event_times(new_start, new_end)
        if not body:
            raise ToolError("Nothing to change.")
        event = await self.api.call("PATCH", f"{CALENDAR}/events/{event_id}", body=body)
        return f"Changed: {describe_event(event)}"

    async def delete_event(self, event_id):
        await self.api.call("DELETE", f"{CALENDAR}/events/{event_id}")
        return "Deleted the event."

    # Tasks (the default list, "My Tasks")

    async def tasks(self, show_completed=False):
        params = {"showCompleted": "true" if show_completed else "false", "showHidden": "false", "maxResults": 100}
        items = (await self.api.call("GET", f"{TASKS}/lists/@default/tasks", params)).get("items", [])
        lines = []
        for task in items:
            due = f" (due {date.fromisoformat(task['due'][:10]):%a %d %b})" if task.get("due") else ""
            done = " (done)" if task.get("status") == "completed" else ""
            lines.append(f"- {task.get('title', '(no title)')}{due}{done} [id {task['id']}]")
        return "\n".join(lines) or "No tasks."

    async def add_task(self, title, due=None, notes=None):
        body = {"title": title}
        if due:
            day = parse_when(due)
            day = day.date() if isinstance(day, datetime) else day
            body["due"] = f"{day.isoformat()}T00:00:00.000Z"  # Tasks keeps only the date
        if notes:
            body["notes"] = notes
        task = await self.api.call("POST", f"{TASKS}/lists/@default/tasks", body=body)
        return f"Added the task {task.get('title', title)!r} [id {task['id']}]."

    async def complete_task(self, task_id):
        task = await self.api.call("PATCH", f"{TASKS}/lists/@default/tasks/{task_id}", body={"status": "completed"})
        return f"Marked {task.get('title', 'the task')!r} as done."

    async def delete_task(self, task_id):
        await self.api.call("DELETE", f"{TASKS}/lists/@default/tasks/{task_id}")
        return "Deleted the task."

    # Gmail

    async def search_mail(self, query="in:inbox", max_results=10):
        params = {"q": query, "maxResults": max(1, min(int(max_results), 20))}
        listing = await self.api.call("GET", f"{GMAIL}/messages", params)
        found = listing.get("messages", [])
        messages = await asyncio.gather(*(self.api.call(
            "GET", f"{GMAIL}/messages/{item['id']}",
            {"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]}) for item in found))
        lines = []
        for message in messages:
            headers = header_map(message)
            unread = " (unread)" if "UNREAD" in message.get("labelIds", []) else ""
            lines.append(f"- From {headers.get('from', '?')}: {headers.get('subject', '(no subject)')}{unread}, "
                         f"{headers.get('date', '')} [id {message['id']}]\n  {html.unescape(message.get('snippet', ''))}")
        if not lines:
            return "No emails match."
        if listing.get("nextPageToken"):
            # Only the first ones were fetched; Gmail's count of the rest is an estimate.
            total = max(listing.get("resultSizeEstimate", 0), len(lines) + 1)
            lines.insert(0, f"The newest {len(lines)} of about {total} matching emails:")
        return "\n".join(lines)

    async def read_mail(self, message_id):
        message = await self.api.call("GET", f"{GMAIL}/messages/{message_id}", {"format": "full"})
        headers = header_map(message)
        text = body_text(message.get("payload", {})) or html.unescape(message.get("snippet", ""))
        if len(text) > EMAIL_CHARS:
            text = f"{text[:EMAIL_CHARS]}\n[cut here: the email is {len(text)} characters long]"
        return (f"From: {headers.get('from', '?')}\nTo: {headers.get('to', '?')}\nDate: {headers.get('date', '')}\n"
                f"Subject: {headers.get('subject', '')}\n\n{text}")

    async def send_mail(self, to, subject, body, reply_to=None):
        email = EmailMessage()
        email["To"] = to
        email["Subject"] = subject
        email.set_content(body)
        request = {}
        if reply_to:
            original = await self.api.call("GET", f"{GMAIL}/messages/{reply_to}",
                                           {"format": "metadata", "metadataHeaders": ["Message-ID", "References"]})
            headers = header_map(original)
            if headers.get("message-id"):
                email["In-Reply-To"] = headers["message-id"]
                email["References"] = f"{headers.get('references', '')} {headers['message-id']}".strip()
            request["threadId"] = original["threadId"]
        request["raw"] = base64.urlsafe_b64encode(email.as_bytes()).decode()
        await self.api.call("POST", f"{GMAIL}/messages/send", body=request)
        return f"Sent the email to {to}."

    # Drive (reading) and Docs

    async def drive_search(self, query=None, kind=None, max_results=10):
        terms = ["trashed = false"]
        if query:
            quoted = query.replace("\\", "\\\\").replace("'", "\\'")
            terms.append(f"(name contains '{quoted}' or fullText contains '{quoted}')")
        if kind:
            if kind not in KINDS:
                raise ToolError(f"kind is one of: {', '.join(KINDS)}.")
            terms.append(f"mimeType = '{KINDS[kind]}'")
        params = {"q": " and ".join(terms), "pageSize": max(1, min(int(max_results), 30)),
                  "fields": "files(id,name,mimeType,modifiedTime,owners(displayName,me))"}
        if not query:
            params["orderBy"] = "modifiedTime desc"  # Drive orders word searches by relevance itself
        files = (await self.api.call("GET", DRIVE, params)).get("files", [])
        lines = []
        for item in files:
            changed = datetime.fromisoformat(item["modifiedTime"].replace("Z", "+00:00")).astimezone(timezone())
            owner = next((o for o in item.get("owners", [])), {})
            by = "" if owner.get("me", True) else f", owned by {owner.get('displayName', 'someone else')}"
            kind_name = KIND_NAMES.get(item["mimeType"], item["mimeType"].split("/")[-1])
            lines.append(f"- {item['name']} ({kind_name}, changed {changed:%a %d %b %Y}{by}) [id {item['id']}]")
        return "\n".join(lines) or "No files match."

    async def drive_read(self, file_id, start=0):
        meta = await self.api.call("GET", f"{DRIVE}/{file_id}", {"fields": "name,mimeType"})
        kind = meta["mimeType"]
        if kind in EXPORTS:
            data = await self.api.call("GET", f"{DRIVE}/{file_id}/export", {"mimeType": EXPORTS[kind]}, raw=True)
            text = data.decode("utf-8", "replace")
        elif kind.startswith("text/") or kind in ("application/json", "application/xml"):
            text = (await self.api.call("GET", f"{DRIVE}/{file_id}", {"alt": "media"}, raw=True)).decode("utf-8", "replace")
        elif kind == "application/pdf":
            text = pdf_text(await self.api.call("GET", f"{DRIVE}/{file_id}", {"alt": "media"}, raw=True))
        else:
            raise ToolError(f"Natro can't read {KIND_NAMES.get(kind, kind)} files from Drive.")
        text = text.replace("\r\n", "\n").strip()
        start = max(0, int(start))
        end = start + READ_CHARS
        if end < len(text):
            return f"{meta['name']}:\n{text[start:end]}\n[cut at character {end} of {len(text)}: read again with start={end} for more]"
        return f"{meta['name']}:\n{text[start:]}" if text[start:] else f"{meta['name']} has no more text."

    async def docs_create(self, title, text=None):
        doc = await self.api.call("POST", DOCS, body={"title": title})
        if text:
            await self.api.call("POST", f"{DOCS}/{doc['documentId']}:batchUpdate",
                                body={"requests": [{"insertText": {"location": {"index": 1}, "text": text}}]})
        return f"Created the Google Doc {title!r} [id {doc['documentId']}]."

    async def docs_append(self, document_id, text):
        await self.api.call("POST", f"{DOCS}/{document_id}:batchUpdate", body={"requests": [
            {"insertText": {"endOfSegmentLocation": {}, "text": "\n" + text}}]})
        return "Added it at the end of the document."

    async def docs_replace(self, document_id, find, replace_with):
        result = await self.api.call("POST", f"{DOCS}/{document_id}:batchUpdate", body={"requests": [
            {"replaceAllText": {"containsText": {"text": find, "matchCase": True}, "replaceText": replace_with}}]})
        changed = result.get("replies", [{}])[0].get("replaceAllText", {}).get("occurrencesChanged", 0)
        return f"Replaced {changed} place{'s' if changed != 1 else ''}." if changed else "That text isn't in the document."

    def tools(self):
        def tool(name, description, properties, run, required=(), confirm=False):
            async def call(args):
                return await run(**args)
            return Tool(name=name, description=description, run=call, source="google", personal=True,
                        confirm=confirm, input_schema={"type": "object", "properties": properties,
                                                       "required": list(required)})

        when = {"type": "string", "description": "2026-10-06 for a day, or 2026-10-06T15:00 for a time (his local time)."}
        text = {"type": "string"}
        tools = [
            tool("calendar_events", "List events in your owner's Google Calendar, from start to end (default: today "
                 "and the next 6 days; just start: that day). query searches titles and details.",
                 {"start": when, "end": when, "query": text}, self.events),
            tool("calendar_create_event", "Add an event. A day without a time makes an all-day event; a timed "
                 "event lasts an hour unless end is given.",
                 {"title": text, "start": when, "end": when, "location": text, "description": text},
                 self.create_event, ["title", "start"]),
            tool("calendar_update_event", "Change an event (by id from calendar_events): only the fields given. "
                 "Moving the start keeps its length unless end is given.",
                 {"event_id": text, "title": text, "start": when, "end": when, "location": text,
                  "description": text}, self.update_event, ["event_id"]),
            tool("calendar_delete_event", "Delete an event.", {"event_id": text}, self.delete_event, ["event_id"],
                 confirm=True),
            tool("tasks_list", "List your owner's Google Tasks (his to-dos).",
                 {"show_completed": {"type": "boolean"}}, self.tasks),
            tool("tasks_add", "Add a to-do to Google Tasks, optionally due on a day.",
                 {"title": text, "due": when, "notes": text}, self.add_task, ["title"]),
            tool("tasks_complete", "Mark a task as done.", {"task_id": text}, self.complete_task, ["task_id"]),
            tool("tasks_delete", "Delete a task.", {"task_id": text}, self.delete_task, ["task_id"], confirm=True),
            tool("gmail_search", "Find emails with Gmail search syntax (default \"in:inbox\"; e.g. \"is:unread\", "
                 "\"from:nino newer_than:7d\"). Shows sender, subject, date and a snippet.",
                 {"query": text, "max_results": {"type": "integer", "description": "Up to 20; default 10."}},
                 self.search_mail),
            tool("gmail_read", "Read one email (by id from gmail_search).", {"message_id": text}, self.read_mail,
                 ["message_id"]),
            tool("gmail_send", "Send an email from your owner's Gmail. To reply, give reply_to (the id of the email "
                 "you're answering) so it stays in the same thread.",
                 {"to": text, "subject": text, "body": text, "reply_to": text},
                 self.send_mail, ["to", "subject", "body"], confirm=True),
        ]
        if DRIVE_SCOPE in self.api.scopes:
            tools += [
                tool("drive_search", "Find files in his Google Drive by words in their name or text (best match "
                     "first), or without query the most recently changed. kind narrows it.",
                     {"query": text, "kind": {"type": "string", "enum": list(KINDS)},
                      "max_results": {"type": "integer", "description": "Default 10."}}, self.drive_search),
                tool("drive_read", f"Read a Drive file (by id from drive_search): Google Docs, Sheets (as CSV) and "
                     f"Slides, PDFs and text files, {READ_CHARS} characters at a time; a longer text says where it "
                     "was cut, and start reads on from there.",
                     {"file_id": text, "start": {"type": "integer"}}, self.drive_read, ["file_id"]),
            ]
        if DOCS_SCOPE in self.api.scopes:
            tools += [
                tool("docs_create", "Create a Google Doc in his Drive, optionally with text in it.",
                     {"title": text, "text": text}, self.docs_create, ["title"]),
                tool("docs_append", "Add text at the end of a Google Doc (by id from drive_search).",
                     {"document_id": text, "text": text}, self.docs_append, ["document_id", "text"]),
                tool("docs_replace", "Replace every occurrence of some text in a Google Doc (exact, case "
                     "sensitive); an empty replacement deletes it.",
                     {"document_id": text, "find": text, "replace_with": text}, self.docs_replace,
                     ["document_id", "find", "replace_with"], confirm=True),
            ]
        return tools


def pdf_text(data):
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages[:PDF_PAGES]]
    except Exception as e:
        raise ToolError(f"Couldn't read that PDF: {e}")
    more = f"\n[only the first {PDF_PAGES} of {len(reader.pages)} pages]" if len(reader.pages) > PDF_PAGES else ""
    return "\n".join(pages) + more


def header_map(message):
    return {header["name"].lower(): header["value"] for header in message.get("payload", {}).get("headers", [])}


def body_text(part):
    """The email's text: its text/plain part, or its HTML part without the tags."""
    plain = find_part(part, "text/plain")
    if plain:
        return plain.strip()
    markup = find_part(part, "text/html")
    if markup:
        markup = re.sub(r"(?is)<(script|style).*?</\1>", "", markup)
        markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", markup)
        return re.sub(r"\n\s*\n+", "\n\n", html.unescape(re.sub(r"<[^>]+>", "", markup))).strip()
    return ""


def find_part(part, mime_type):
    if part.get("mimeType") == mime_type and part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"] + "==").decode("utf-8", "replace")
    for child in part.get("parts", []):
        if found := find_part(child, mime_type):
            return found
    return ""
