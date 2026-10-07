import base64
import email

import pytest

from conftest import run
from natro_agent.google_tools import CALENDAR, DOCS, DRIVE, GMAIL, SCOPES, SIGN_IN_AGAIN, TASKS, Google, GoogleAPI
from natro_agent.tools import ToolError


class FakeResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
        # bytes are a file's contents (raw); anything else is a JSON reply.
        self.content = data if isinstance(data, bytes) else b"x" if data is not None else b""
        self.text = str(data)

    def json(self):
        return self._data


class FakeGoogle:
    """Answers (method, url) with scripted data; records every request."""

    def __init__(self, answers):
        self.answers = answers
        self.requests = []

    def request(self, method, url, params=None, json=None, timeout=None):
        self.requests.append((method, url, params, json))
        status, data = self.answers.get((method, url), (200, {}))
        return FakeResponse(status, data)


def google(answers):
    fake = FakeGoogle(answers)
    return Google(GoogleAPI(fake)), fake


@pytest.fixture(autouse=True)
def tbilisi_time(monkeypatch):
    monkeypatch.setenv("NATRO_TIMEZONE", "Asia/Tbilisi")


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def test_calendar_events_for_a_day():
    events = {"items": [
        {"id": "e1", "summary": "Dentist", "location": "City clinic",
         "start": {"dateTime": "2026-10-06T06:00:00Z"}, "end": {"dateTime": "2026-10-06T07:00:00Z"}},
        {"id": "e2", "summary": "Nino's birthday", "start": {"date": "2026-10-06"}, "end": {"date": "2026-10-07"}},
    ]}
    g, fake = google({("GET", f"{CALENDAR}/events"): (200, events)})
    text = run(g.events("2026-10-06"))
    assert text == ("Tue 06 Oct, 10:00-11:00: Dentist (at City clinic) [id e1]\n"
                    "Tue 06 Oct, all day: Nino's birthday [id e2]")
    params = fake.requests[0][2]
    assert params["timeMin"] == "2026-10-06T00:00:00+04:00" and params["timeMax"] == "2026-10-07T00:00:00+04:00"
    assert params["singleEvents"] == "true"


def test_create_timed_and_all_day_events():
    created = {"id": "n1", "summary": "Padel", "start": {"dateTime": "2026-10-07T19:00:00+04:00"},
               "end": {"dateTime": "2026-10-07T20:00:00+04:00"}}
    g, fake = google({("POST", f"{CALENDAR}/events"): (200, created)})
    assert run(g.create_event("Padel", "2026-10-07T19:00")) == "Created: Wed 07 Oct, 19:00-20:00: Padel [id n1]"
    body = fake.requests[0][3]
    assert body["start"] == {"dateTime": "2026-10-07T19:00:00+04:00", "timeZone": "Asia/Tbilisi"}
    assert body["end"]["dateTime"] == "2026-10-07T20:00:00+04:00"
    run(g.create_event("Trip", "2026-10-10", "2026-10-12"))
    assert fake.requests[1][3]["start"] == {"date": "2026-10-10"} and fake.requests[1][3]["end"] == {"date": "2026-10-13"}


def test_moving_an_event_keeps_its_length():
    old = {"id": "e1", "start": {"dateTime": "2026-10-06T10:00:00+04:00"}, "end": {"dateTime": "2026-10-06T11:30:00+04:00"}}
    moved = {"id": "e1", "summary": "Dentist", "start": {"dateTime": "2026-10-08T15:00:00+04:00"},
             "end": {"dateTime": "2026-10-08T16:30:00+04:00"}}
    g, fake = google({("GET", f"{CALENDAR}/events/e1"): (200, old), ("PATCH", f"{CALENDAR}/events/e1"): (200, moved)})
    assert run(g.update_event("e1", start="2026-10-08T15:00")) == "Changed: Thu 08 Oct, 15:00-16:30: Dentist [id e1]"
    assert fake.requests[1][3]["end"]["dateTime"] == "2026-10-08T16:30:00+04:00"


def test_tasks():
    items = {"items": [{"id": "t1", "title": "Buy milk", "due": "2026-10-06T00:00:00.000Z", "status": "needsAction"}]}
    g, fake = google({("GET", f"{TASKS}/lists/@default/tasks"): (200, items),
                      ("POST", f"{TASKS}/lists/@default/tasks"): (200, {"id": "t2", "title": "Call the bank"}),
                      ("PATCH", f"{TASKS}/lists/@default/tasks/t1"): (200, {"id": "t1", "title": "Buy milk"})})
    assert run(g.tasks()) == "- Buy milk (due Tue 06 Oct) [id t1]"
    assert run(g.add_task("Call the bank", due="2026-10-09")) == "Added the task 'Call the bank' [id t2]."
    assert fake.requests[1][3] == {"title": "Call the bank", "due": "2026-10-09T00:00:00.000Z"}
    assert run(g.complete_task("t1")) == "Marked 'Buy milk' as done."
    assert fake.requests[2][3] == {"status": "completed"}


def test_gmail_search_and_read():
    listed = {"messages": [{"id": "m1"}]}
    metadata = {"id": "m1", "labelIds": ["UNREAD", "INBOX"], "snippet": "See you at 7 &amp; bring the ball",
                "payload": {"headers": [{"name": "From", "value": "Nino <nino@example.com>"},
                                        {"name": "Subject", "value": "Padel"},
                                        {"name": "Date", "value": "Mon, 5 Oct 2026 18:00:00 +0400"}]}}
    full = {"id": "m1", "payload": {"headers": metadata["payload"]["headers"], "mimeType": "multipart/alternative",
                                    "parts": [{"mimeType": "text/html", "body": {"data": b64("<p>Hi<br>Tuxa</p>")}}]}}
    g, fake = google({("GET", f"{GMAIL}/messages"): (200, listed), ("GET", f"{GMAIL}/messages/m1"): (200, metadata)})
    assert run(g.search_mail("is:unread")) == ("- From Nino <nino@example.com>: Padel (unread), Mon, 5 Oct 2026 "
                                               "18:00:00 +0400 [id m1]\n  See you at 7 & bring the ball")
    assert fake.requests[0][2] == {"q": "is:unread", "maxResults": 10}
    fake.answers[("GET", f"{GMAIL}/messages/m1")] = (200, full)
    assert run(g.read_mail("m1")).endswith("Subject: Padel\n\nHi\nTuxa")
    # When there are more than were fetched, say so (with Gmail's estimate).
    fake.answers[("GET", f"{GMAIL}/messages")] = (200, {**listed, "nextPageToken": "p2", "resultSizeEstimate": 57})
    fake.answers[("GET", f"{GMAIL}/messages/m1")] = (200, metadata)
    assert run(g.search_mail("is:unread", 1)).startswith("The newest 1 of about 57 matching emails:\n- From Nino")


def test_gmail_reply_stays_in_the_thread():
    original = {"id": "m1", "threadId": "th1", "payload": {"headers": [{"name": "Message-ID", "value": "<abc@mail>"}]}}
    g, fake = google({("GET", f"{GMAIL}/messages/m1"): (200, original),
                      ("POST", f"{GMAIL}/messages/send"): (200, {"id": "sent"})})
    assert run(g.send_mail("nino@example.com", "Re: Padel", "I'll be there.", reply_to="m1")) == \
        "Sent the email to nino@example.com."
    sent = fake.requests[1][3]
    assert sent["threadId"] == "th1"
    message = email.message_from_bytes(base64.urlsafe_b64decode(sent["raw"]))
    assert message["To"] == "nino@example.com" and message["In-Reply-To"] == "<abc@mail>"
    assert message.get_payload().strip() == "I'll be there."


def test_google_errors():
    g, _ = google({("GET", f"{CALENDAR}/events"): (401, {"error": {"message": "Invalid Credentials"}}),
                   ("DELETE", f"{TASKS}/lists/@default/tasks/x"): (404, {"error": {"message": "Task not found."}})})
    with pytest.raises(ToolError, match="sign-in has expired"):
        run(g.events())
    with pytest.raises(ToolError, match=r"Task not found. \(HTTP 404\)"):
        run(g.delete_task("x"))
    with pytest.raises(ToolError, match="Can't read the time"):
        run(g.events("next tuesday"))
    assert "google_signin" in SIGN_IN_AGAIN


def test_google_tools_are_personal_and_risky_ones_ask():
    tools = {tool.name: tool for tool in Google(GoogleAPI(None)).tools()}
    assert len(tools) == 16 and all(tool.personal for tool in tools.values())
    assert sorted(name for name, tool in tools.items() if tool.confirm) == \
        ["calendar_delete_event", "docs_replace", "gmail_send", "tasks_delete"]



def test_drive_search_and_read():
    files = {"files": [
        {"id": "d1", "name": "Trip to Kazbegi", "mimeType": "application/vnd.google-apps.document",
         "modifiedTime": "2026-10-05T18:30:00Z", "owners": [{"displayName": "Tuxa", "me": True}]},
        {"id": "s1", "name": "Budget", "mimeType": "application/vnd.google-apps.spreadsheet",
         "modifiedTime": "2026-09-01T08:00:00Z", "owners": [{"displayName": "Nino", "me": False}]},
    ]}
    g, fake = google({("GET", DRIVE): (200, files),
                      ("GET", f"{DRIVE}/d1"): (200, {"name": "Trip to Kazbegi",
                                                     "mimeType": "application/vnd.google-apps.document"}),
                      ("GET", f"{DRIVE}/d1/export"): (200, "Day 1: Stepantsminda\r\nDay 2: Gergeti".encode())})
    assert run(g.drive_search("kazbegi's")) == (
        "- Trip to Kazbegi (Google Doc, changed Mon 05 Oct 2026) [id d1]\n"
        "- Budget (Google Sheet, changed Tue 01 Sep 2026, owned by Nino) [id s1]")
    params = fake.requests[0][2]
    assert params["q"] == "trashed = false and (name contains 'kazbegi\\'s' or fullText contains 'kazbegi\\'s')"
    assert "orderBy" not in params  # Drive can't sort word searches
    run(g.drive_search(kind="sheet"))
    assert fake.requests[1][2]["orderBy"] == "modifiedTime desc"
    assert "mimeType = 'application/vnd.google-apps.spreadsheet'" in fake.requests[1][2]["q"]
    assert run(g.drive_read("d1")) == "Trip to Kazbegi:\nDay 1: Stepantsminda\nDay 2: Gergeti"
    assert fake.requests[-1][2] == {"mimeType": "text/plain"}


def test_docs_create_append_and_replace():
    g, fake = google({("POST", DOCS): (200, {"documentId": "doc9"}),
                      ("POST", f"{DOCS}/doc9:batchUpdate"): (200, {"replies": [{"replaceAllText": {
                          "occurrencesChanged": 2}}]})})
    assert run(g.docs_create("Packing list", "Boots")) == "Created the Google Doc 'Packing list' [id doc9]."
    assert fake.requests[1][3] == {"requests": [{"insertText": {"location": {"index": 1}, "text": "Boots"}}]}
    run(g.docs_append("doc9", "Jacket"))
    assert fake.requests[2][3]["requests"][0]["insertText"] == {"endOfSegmentLocation": {}, "text": "\nJacket"}
    assert run(g.docs_replace("doc9", "Boots", "Hiking boots")) == "Replaced 2 places."


def test_drive_and_docs_only_with_a_sign_in_that_allows_them():
    old_token = Google(GoogleAPI(None, scopes=SCOPES[:4])).tools()
    assert not {"drive_search", "docs_create"} & {tool.name for tool in old_token}
    tools = {tool.name: tool for tool in Google(GoogleAPI(None)).tools()}
    assert {"drive_search", "drive_read", "docs_create", "docs_append", "docs_replace"} <= set(tools)
    assert tools["docs_replace"].confirm and not tools["docs_append"].confirm
