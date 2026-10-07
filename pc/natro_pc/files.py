"""Files on the PC: find them by name or by what's in them, read them, open them.

- Names: Everything (voidtools; installed with winget, starts at login), which
  indexes every drive and answers instantly. Windows, programs, AppData, the
  Recycle Bin and code-library folders are left out unless asked for.
- Contents: the Windows Search index, which covers his whole user folder
  (Desktop, Documents, Downloads, Pictures, Videos, OneDrive...).
- Reading: text files, Word documents (.docx) and PDFs.
- Opening: with the program Windows uses for that kind of file. Programs and
  scripts are never opened from here (open_app opens apps).

All of it is personal: file names, contents and paths only go to Claude.
"""
import html
import os
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

from natro_pc.tools import PCTool, ToolError

HOME = Path.home()
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
# Left out of name searches unless he asks to search everywhere.
NOISE = ["\\$Recycle.Bin\\", "\\AppData\\", "C:\\Windows\\", "C:\\ProgramData\\", "\\Program Files\\",
         "\\Program Files (x86)\\", "\\node_modules\\", "\\.git\\", "\\.venv\\", "\\site-packages\\",
         "\\__pycache__\\", "\\System Volume Information\\", "\\WindowsApps\\"]
CONTENT_NOISE = ["\\AppData\\", "\\.venv\\", "\\site-packages\\", "\\node_modules\\", "\\.git\\", "\\__pycache__\\"]
TEXT_TYPES = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".log", ".ini", ".cfg", ".yaml",
              ".yml", ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".sql", ".srt", ".tex",
              ".rtf", ".bat", ".ps1", ".sh", ".toml", ".env.example"}
# Opening these would run something.
PROGRAMS = {".exe", ".bat", ".cmd", ".com", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".msi", ".msp",
            ".scr", ".pif", ".reg", ".lnk", ".url", ".hta", ".cpl", ".jar", ".appref-ms", ".psm1", ".sh"}
READ_CHARS = 15000  # per read; a longer text is read on with start
TEXT_BYTES = 5 * 2**20
PDF_PAGES = 40
EVERYTHING_EXE = Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Everything" / "Everything.exe"


def es_path():
    found = shutil.which("es")
    if found:
        return found
    linked = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "es.exe"
    if linked.exists():
        return str(linked)
    raise ToolError("Everything's command-line tool (es) isn't installed: winget install voidtools.Everything.Cli")


def search_terms(query, everywhere=False):
    """The query as Everything search terms. es reads its own command line, so each term goes as written;
    terms that look like es options ("-export-csv") are dropped."""
    terms = [term for term in re.findall(r'!?"[^"]*"|\S+', query) if not term.startswith(("-", "/"))]
    if not terms:
        raise ToolError("Say what to search for.")
    if not everywhere:
        terms += [f'!"{noise}"' if " " in noise else f"!{noise}" for noise in NOISE]
    return terms


def human_size(size):
    if size is None:
        return "folder"
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024


def run_es(terms, count):
    command = (f'"{es_path()}" -n {count} -sort date-modified-descending -size -dm -tsv -date-format 1 '
               f'-size-format 1 {" ".join(terms)}')
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                          creationflags=NO_WINDOW)


def parse_es(output):
    """es's TSV (header, then path, size, date modified) as (path, size or None, date)."""
    rows = []
    for line in output.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0]:
            rows.append((parts[0], int(parts[1]) if parts[1].strip().isdigit() else None, parts[2][:16]))
    return rows


def describe_rows(rows, count, order="newest"):
    lines = [f"{date.replace('T', ' ')}  {human_size(size)}  {path}" for path, size, date in rows]
    head = f"The {order} {count} matches (there may be more):" if len(rows) >= count else f"{len(rows)} matches:"
    return "\n".join([head, *lines])


class Files:
    def __init__(self, run=run_es, index=None):
        self._run = run
        self._index = index or windows_search

    def search(self, query, everywhere=False, max_results=15):
        count = max(1, min(int(max_results), 50))
        terms = search_terms(query, everywhere)
        result = self._run(terms, count)
        if result.returncode == 8 and EVERYTHING_EXE.exists():  # Everything isn't running: start it, once
            subprocess.Popen([str(EVERYTHING_EXE), "-startup"])
            time.sleep(3)
            result = self._run(terms, count)
        if result.returncode != 0:
            raise ToolError(f"The file search failed: {(result.stderr or result.stdout).strip() or result.returncode}")
        rows = parse_es(result.stdout)
        return describe_rows(rows, count) if rows else "No files match."

    def search_contents(self, text, folder=None, max_results=10):
        count = max(1, min(int(max_results), 30))
        scope = Path(folder).expanduser() if folder else HOME
        rows = self._index(text, scope, count)
        return describe_rows(rows, count, "best") if rows else f"No indexed files in {scope} contain that."

    def read(self, path, start=0):
        file = Path(path).expanduser()
        if not file.is_file():
            raise ToolError(f"There's no file {path!r}.")
        kind = file.suffix.lower()
        if kind == ".docx":
            text = docx_text(file)
        elif kind == ".pdf":
            text = pdf_text(file)
        elif kind in TEXT_TYPES or file.name.lower().endswith(tuple(TEXT_TYPES)):
            data = file.read_bytes()[:TEXT_BYTES]
            text = data.decode("utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else data.decode("utf-8", "replace")
        else:
            raise ToolError(f"Natro can't read {kind or 'those'} files; she can open it for him instead.")
        text = text.replace("\r\n", "\n").strip()
        if not text:
            return "The file has no text (a scanned PDF, maybe)."
        start = max(0, int(start))
        end = start + READ_CHARS
        if end < len(text):
            return f"{text[start:end]}\n[cut at character {end} of {len(text)}: read again with start={end} for more]"
        return text[start:] or f"The text ends at character {len(text)}."

    def open(self, path):
        target = Path(path).expanduser()
        if not target.exists():
            raise ToolError(f"There's no file or folder {path!r}.")
        if target.is_file() and target.suffix.lower() in PROGRAMS:
            raise ToolError("That's a program or script; Natro doesn't run those from here. open_app opens apps.")
        os.startfile(str(target))
        return f"Opened {target.name or target}."

    @staticmethod
    def open_link(url):
        if not re.match(r"https?://", url.strip()):
            raise ToolError("Only web links (http or https) can be opened this way.")
        os.startfile(url.strip())
        return "Opened it in the browser."


def windows_search(text, scope, count):
    """Files under scope whose contents (or names) contain every word of text, from the Windows Search index."""
    import pythoncom
    import win32com.client

    terms = re.findall(r"[\w'-]+", text)
    if not terms:
        raise ToolError("Say what text to look for.")
    condition = " AND ".join(f'"{term.replace(chr(39), chr(39) * 2)}*"' for term in terms)
    where = str(scope).replace("\\", "/").replace("'", "''")
    # Code libraries and app data under his folder are indexed too, and only crowd out his own files.
    skip = "".join(f" AND NOT System.ItemPathDisplay LIKE '%{part}%'" for part in CONTENT_NOISE)
    sql = (f"SELECT TOP {count} System.ItemPathDisplay, System.Size, System.DateModified FROM SystemIndex "
           f"WHERE SCOPE='file:{where}' AND CONTAINS(*, '{condition}'){skip} ORDER BY System.Search.Rank DESC")
    pythoncom.CoInitialize()  # tools run in their own threads
    try:
        connection = win32com.client.Dispatch("ADODB.Connection")
        connection.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
        try:
            records, _ = connection.Execute(sql)
            rows = []
            while not records.EOF:
                path, size, modified = (records.Fields.Item(i).Value for i in range(3))
                rows.append((path, size, f"{modified:%Y-%m-%dT%H:%M}" if modified else ""))
                records.MoveNext()
            return rows
        finally:
            connection.Close()
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"Windows Search failed: {e}")
    finally:
        pythoncom.CoUninitialize()


def docx_text(file):
    with zipfile.ZipFile(file) as archive:
        markup = archive.read("word/document.xml").decode("utf-8", "replace")
    markup = re.sub(r"</w:p>|<w:br/>|<w:tab/>", lambda m: "\t" if "tab" in m.group() else "\n", markup)
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


def pdf_text(file):
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(file))
        pages = [page.extract_text() or "" for page in reader.pages[:PDF_PAGES]]
    except Exception as e:
        raise ToolError(f"Couldn't read that PDF: {e}")
    more = f"\n[only the first {PDF_PAGES} of {len(reader.pages)} pages]" if len(reader.pages) > PDF_PAGES else ""
    return "\n".join(pages) + more


def tools(files=None):
    files = files or Files()
    path = {"type": "string", "description": f"A full path. His home folder is {HOME}."}
    return [
        PCTool("search_files", "Find files and folders on the PC by name, newest first, with Everything's search "
               "syntax: words must all appear in the name or path (\"cv\", \"invoice 2026\"); ext:pdf;docx for "
               "kinds; dm:today, dm:thisweek or dm:last7days for when changed; size:>100mb; folder: for folders "
               "only; a term with a backslash matches the path (\"\\Downloads\\ ext:mp4\"). Windows, programs, "
               "AppData and the Recycle Bin are left out unless everywhere is true.", files.search,
               {"query": {"type": "string"}, "everywhere": {"type": "boolean"},
                "max_results": {"type": "integer", "description": "Default 15, up to 50."}}, ["query"],
               personal=True),
        PCTool("search_file_contents", f"Find files whose text contains all these words (documents, PDFs, notes; "
               f"from the Windows Search index of his user folder, {HOME}). Best match first.",
               files.search_contents, {"text": {"type": "string"}, "folder": path,
                                       "max_results": {"type": "integer", "description": "Default 10."}},
               ["text"], personal=True),
        PCTool("read_file", f"Read a text file, Word document (.docx) or PDF on the PC, {READ_CHARS} characters at "
               "a time: a longer text says where it was cut, and start reads on from there.", files.read,
               {"path": path, "start": {"type": "integer", "description": "Character to start from (default 0)."}},
               ["path"], personal=True),
        PCTool("open_file", "Open a file or folder on the PC with its usual program (a PDF in the PDF viewer, a "
               "folder in File Explorer). Not for programs: open_app opens apps.", files.open, {"path": path},
               ["path"], personal=True),
        PCTool("open_link", "Open a web page in the PC's browser.", files.open_link,
               {"url": {"type": "string", "description": "An http or https link."}}, ["url"], final=True),
    ]
