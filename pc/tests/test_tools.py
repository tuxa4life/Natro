import subprocess
import zipfile
from types import SimpleNamespace

import pytest

from natro_pc import apps, files
from natro_pc.apps import Apps, StartApp, StartMenu, Window, find_open_app, match_score
from natro_pc.files import Files, parse_es, search_terms
from natro_pc.tools import ToolError, all_tools

START_MENU = [StartApp("Spotify", "SpotifyAB.SpotifyMusic!Spotify"), StartApp("Spotify Widget", "w1"),
              StartApp("Visual Studio Code", "Microsoft.VisualStudioCode"), StartApp("Google Chrome", "Chrome"),
              StartApp("Calculator", "Microsoft.WindowsCalculator!App"), StartApp("Steam", "steam.exe")]


def menu():
    launched = []
    return StartMenu(load=lambda: list(START_MENU), launch=launched.append), launched


def test_app_names_as_people_say_them():
    m, launched = menu()
    assert m.open("spotify") == "Opened Spotify."  # not "Spotify Widget"
    assert m.open("VS Code") == "Opened Visual Studio Code."
    assert m.open("chrome") == "Opened Google Chrome."
    assert m.open("calculater") == "Opened Calculator."  # a misheard name
    assert launched == ["SpotifyAB.SpotifyMusic!Spotify", "Microsoft.VisualStudioCode", "Chrome",
                        "Microsoft.WindowsCalculator!App"]
    with pytest.raises(ToolError, match="No app called 'telegram'.*closest"):
        m.open("telegram")
    assert match_score("steam", "Steam") == 1.0


def window(app, title, exe, hwnd=1):
    return Window(hwnd, 100 + hwnd, title, exe, app)


WINDOWS = [window("Google Chrome", "Spotify - Web Player - Google Chrome", r"C:\chrome\chrome.exe", 1),
           window("Spotify", "Yellow - Coldplay", r"C:\Apps\Spotify.exe", 2),
           window("Visual Studio Code", "app.py - Natro 2 - Visual Studio Code", r"C:\VS\Code.exe", 3),
           window("Visual Studio Code", "PLAN.md - Natro 2", r"C:\VS\Code.exe", 4)]


def test_finding_an_open_app():
    # The app itself wins over a browser tab that mentions it.
    assert find_open_app("spotify", WINDOWS)[0] == "Spotify"
    name, windows = find_open_app("vs code", WINDOWS)
    assert name == "Visual Studio Code" and [w.hwnd for w in windows] == [3, 4]
    assert find_open_app("code", WINDOWS)[0] == "Visual Studio Code"  # the program's file name, Code.exe
    with pytest.raises(ToolError, match="Open apps: Google Chrome, Spotify, Visual Studio Code"):
        find_open_app("discord", WINDOWS)


def test_list_open_apps_shows_no_window_titles():
    # Titles can name documents and emails; the list may go to the free tier.
    text = Apps(menu=menu()[0], windows=lambda: WINDOWS).running()
    assert text == "Open apps: Google Chrome, Spotify, Visual Studio Code (2 windows)."
    assert Apps(menu=menu()[0], windows=lambda: []).running() == "No apps are open."


def test_search_terms_leave_out_noise_and_es_options():
    terms = search_terms('invoice "march 2026" -export-csv out.csv ext:pdf')
    assert terms[:4] == ["invoice", '"march 2026"', "out.csv", "ext:pdf"]
    assert "!\\AppData\\" in terms and '!"\\Program Files\\"' in terms
    assert search_terms("cv", everywhere=True) == ["cv"]
    with pytest.raises(ToolError):
        search_terms("-export-csv")


def test_name_search_output():
    output = ("Filename\tSize\tDate Modified\n"
              "C:\\Users\\Tuxa\\Downloads\\cv.pdf\t36201\t2026-10-01T02:26:21\n"
              "C:\\Users\\Tuxa\\Documents\\CV\t\t2026-09-01T10:00:00\n")
    assert parse_es(output) == [("C:\\Users\\Tuxa\\Downloads\\cv.pdf", 36201, "2026-10-01T02:26"),
                                ("C:\\Users\\Tuxa\\Documents\\CV", None, "2026-09-01T10:00")]
    calls = []

    def run(terms, count):
        calls.append((terms, count))
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    text = Files(run=run).search("cv", max_results=2)
    assert text == ("The newest 2 matches (there may be more):\n"
                    "2026-10-01 02:26  35.4 KB  C:\\Users\\Tuxa\\Downloads\\cv.pdf\n"
                    "2026-09-01 10:00  folder  C:\\Users\\Tuxa\\Documents\\CV")
    assert calls[0][1] == 2
    empty = Files(run=lambda terms, count: SimpleNamespace(returncode=0, stdout="Filename\tSize\tDate Modified\n",
                                                            stderr=""))
    assert empty.search("zzz") == "No files match."


def test_content_search_uses_the_index(tmp_path):
    asked = []

    def index(text, scope, count):
        asked.append((text, scope, count))
        return [("C:\\Users\\Tuxa\\Documents\\lease.docx", 20480, "2026-05-02T09:30")]

    text = Files(index=index).search_contents("rent deposit", folder=str(tmp_path))
    assert text == "1 matches:\n2026-05-02 09:30  20.0 KB  C:\\Users\\Tuxa\\Documents\\lease.docx"
    assert asked == [("rent deposit", tmp_path, 10)]


def test_reading_files(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("# Ideas\nA trip to Kazbegi.", encoding="utf-8")
    assert Files().read(str(note)) == "# Ideas\nA trip to Kazbegi."
    document = tmp_path / "letter.docx"
    with zipfile.ZipFile(document, "w") as archive:
        archive.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>Dear Nino,</w:t></w:r></w:p>"
                                              "<w:p><w:r><w:t>See you &amp; Gio at 7.</w:t></w:r></w:p></w:body>"
                                              "</w:document>")
    assert Files().read(str(document)) == "Dear Nino,\nSee you & Gio at 7."
    long = tmp_path / "plan.md"
    long.write_text("a" * 15000 + "phase 5: Android" + "b" * 10, encoding="utf-8")
    first = Files().read(str(long))
    assert first.endswith("[cut at character 15000 of 15026: read again with start=15000 for more]")
    assert Files().read(str(long), start=15000) == "phase 5: Android" + "b" * 10
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8")
    with pytest.raises(ToolError, match="can't read .jpg"):
        Files().read(str(tmp_path / "photo.jpg"))
    with pytest.raises(ToolError, match="There's no file"):
        Files().read(str(tmp_path / "missing.txt"))


def test_programs_and_odd_links_are_never_opened(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(files.os, "startfile", opened.append)
    (tmp_path / "setup.exe").write_bytes(b"MZ")
    (tmp_path / "cv.pdf").write_bytes(b"%PDF")
    with pytest.raises(ToolError, match="program or script"):
        Files().open(str(tmp_path / "setup.exe"))
    assert Files().open(str(tmp_path / "cv.pdf")) == "Opened cv.pdf."
    assert Files().open(str(tmp_path)) == f"Opened {tmp_path.name}."
    with pytest.raises(ToolError, match="Only web links"):
        Files.open_link("file:///C:/Windows/System32/cmd.exe")
    assert Files.open_link("https://youtube.com") == "Opened it in the browser."
    assert opened == [str(tmp_path / "cv.pdf"), str(tmp_path), "https://youtube.com"]


def test_tool_descriptions_for_the_brain(monkeypatch):
    monkeypatch.setattr(apps.StartMenu, "warm_up", lambda self: None)
    specs = {tool.name: tool.spec() for tool in all_tools()}
    assert set(specs) == {"open_app", "close_app", "force_close_app", "list_open_apps", "search_files",
                          "search_file_contents", "read_file", "open_file", "open_link"}
    # File names, contents and paths only go to Claude; force-closing asks first.
    assert {name for name, spec in specs.items() if spec["personal"]} == {
        "search_files", "search_file_contents", "read_file", "open_file"}
    assert {name for name, spec in specs.items() if spec["confirm"]} == {"force_close_app"}
    assert specs["open_app"]["input_schema"] == {"type": "object", "properties": specs["open_app"]["input_schema"][
        "properties"], "required": ["app"]}
