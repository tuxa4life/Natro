# Natro

Natro is a personal voice assistant, like Google Assistant but your own. She
lives on your Windows PC and your Android phone, with an always-on "brain" on
a small server you control. Say "OK Natro" and ask in Georgian (English words
mixed in are fine). She works out what you mean, acts on your PC, your phone
and your online accounts, and answers on screen and out loud, in English.

- **On the PC:** open and close apps; find, read and open files anywhere on
  the PC.
- **On the phone:** be the phone's assistant (long-press the power button) and
  listen for "OK Natro"; open apps, set alarms and timers, control media,
  the flashlight and volume; read notifications and texts, find contacts,
  call and text; use any app through its screen (read, tap, type, scroll).
- **Google:** Calendar, Tasks, Gmail (read, and send after you say yes),
  Drive (read) and Docs.
- **Spotify:** play any song, album, artist, playlist or podcast; pause, skip,
  volume, queue, like; on whichever device has Spotify open.
- **Memory and notes:** she remembers what you tell her and preferences she
  notices herself; longer notes are Markdown files.
- **Web search,** and any other tools you plug in as MCP servers.
- **Asks before anything risky:** deleting, sending, calling, force-closing
  an app, or a tap that sends, buys or deletes waits for your yes (by voice,
  hotkey or tap).
- **A monthly spending cap:** she warns at 80% and asks before spending more.

## How it works

```
        ┌─────────────────────────── Server (always on) ───────────────────────────┐
        │  Voice pipeline: Google Chirp 3 recognition → Claude translates to English │
        │  Agent (the brain): Gemini free tier / Claude · identity · memory · notes  │
        │  routing · confirmations · spending cap                                    │
        │  Cloud tools: Google, Spotify, web search, memory, notes, MCP servers      │
        └───────────▲─────────────────────────────────────────────▲─────────────────┘
          Tailscale │ (private network, no public ports)          │
     ┌──────────────┴──────────────┐                ┌──────────────┴──────────────┐
     │ PC app (Windows)            │                │ Android app                 │
     │ "OK Natro" checked locally  │                │ "OK Natro" / long-press     │
     │ records → sends audio       │                │ records → sends audio       │
     │ PC tools: apps, files       │                │ phone tools: apps, alarms,  │
     │ speaks replies (Kokoro)     │                │ media, texts, screen control│
     └─────────────────────────────┘                │ speaks replies (Android TTS)│
                                                    └─────────────────────────────┘
```

A request, step by step:

1. **Wake word, on the device.** The PC listens with a local Whisper model
   (whisper-base via [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)), the
   phone with sherpa-onnx keyword spotting. Nothing leaves the device before
   "OK Natro", and recordings without speech are never sent.
2. **To the server.** The device records until you stop talking and sends the
   audio over a WebSocket. Devices reach the server only over
   [Tailscale](https://tailscale.com) and must send a shared token.
3. **Speech to English.** Google's Chirp 3 recognizes the Georgian (and English)
   speech; Claude turns it into accurate English, helped by a list of the names
   and terms you use.
4. **The agent decides.** Requests with no personal data ("open Chrome",
   "play Yellow", "weather tomorrow") start on Gemini's free tier. As soon as a
   request needs anything personal (email, calendar, files, memory,
   notifications) or a risky action, it goes to Claude (Sonnet, with Haiku as
   the fallback), and personal data never reaches the free tier. Tests check
   this.
5. **Tools.** The server has its own tools (memory, notes, web search, Google,
   Spotify, MCP servers) and each connected device offers its own. Natro
   knows which devices are online and says so when one isn't.
6. **The reply** streams back to the device that asked, which shows it and
   speaks it: Kokoro (offline) on the PC, the phone's own text-to-speech on
   Android. Common commands ("pause the music", "next song") skip the model
   entirely and take a fraction of a second.

Cost: a spoken request usually costs about a cent or less (roughly $0.001 to
$0.005 for the voice and $0.001 to $0.03 for the agent). The cap is set in
`.env`.

## What's in this repository

| Folder | What it is | Runs on |
|---|---|---|
| `voice/` | Speech recognition and translation (`natro_voice`) | Server |
| `agent/` | The brain: agent loop, identity, routing, tools, device server (`natro_agent`) | Server |
| `pc/` | The PC app: wake word, recording, PC tools, spoken replies, tray app (`natro_pc`) | Windows PC |
| `android/` | The Android app (Kotlin, Jetpack Compose) | Android 14+ phone (arm64) |
| `deploy/` | Putting the brain on a Linux server: systemd service, backups | Server |
| `scripts/` | Setting up a Windows PC | PC |

Git-ignored and created as you go: `.env`, key files, `tokens/` (sign-ins),
`memory/`, `notes/`, `logs/`, `models/`.

## Setup

Natro is made to grow one piece at a time: start with the typed chat, then add
voice, accounts, a server and the phone.

**You need:** a Windows 10/11 PC, [uv](https://docs.astral.sh/uv/)
(`winget install astral-sh.uv`; it installs Python 3.13 for you), and an
[Anthropic API key](https://console.anthropic.com). Everything else is
optional and listed in the step that needs it.

### 1. Try it typed

```powershell
git clone https://github.com/tuxa4life/Natro.git
cd Natro
powershell -ExecutionPolicy Bypass -File scripts\setup-pc.ps1 -SkipModels
```

The script makes `.venv` with all three Python programs and copies
`.env.example` to `.env`. Put your key in `.env` (`ANTHROPIC_API_KEY=...`), and
tell Natro who you are: in `agent/identity/NATRO.md`, replace "Your Name" and
adjust the rest to taste. Set `NATRO_TIMEZONE` to yours. Then, from the top of
the repository (like every command here):

```powershell
.venv\Scripts\python.exe -m natro_agent.chat
```

Type in English. She has memory and notes already; `--dry-run-pc` adds a
pretend PC whose tools only say what they would do. Each reply ends with the
model, the time and the cost.

**Free extras for `.env`:** `GEMINI_API_KEY`
([Google AI Studio](https://aistudio.google.com/apikey)) sends requests with
no personal data to Gemini's free tier; `TAVILY_API_KEY`
([tavily.com](https://tavily.com), 1,000 free searches a month) turns on web
search.

### 2. Talk to her on the PC

1. Run the setup script again without `-SkipModels`: it downloads the wake
   word and voice models (about 500 MB) into `models/`.
2. In [Google Cloud](https://console.cloud.google.com), make a project, enable
   the **Cloud Speech-to-Text API**, and create a service account with the
   "Cloud Speech Client" role. Save its JSON key as `google-key.json` at the top
   of the repository and set `GOOGLE_CLOUD_PROJECT` in `.env`. Google gives
   60 free minutes a month; after that it's about $1 per hour of speech.
3. Start the brain and the PC app, each in its own terminal:

   ```powershell
   .venv\Scripts\python.exe -m natro_agent.server
   ```

   ```powershell
   .venv\Scripts\python.exe -m natro_pc.app --wake
   ```

   After a short pause, say "OK Natro" and your request. Without `--wake`,
   press Enter to record; `--type` lets you type to the running service.
4. Or use the tray app instead of the console app:
   `.venv\Scripts\pythonw.exe -m natro_pc.tray`. Hold Ctrl+Alt+Space and talk,
   or tap it and talk hands-free; the icon shows whether Natro is online.

**For file search,** install [Everything](https://www.voidtools.com) and its
command line (`winget install voidtools.Everything voidtools.Everything.Cli`).
File contents are searched with the Windows Search index.

**Another language?** The recognizer's languages are in
`voice/natro_voice/speech.py` and the translation instructions in
`voice/natro_voice/translate.py`. Add the names and terms you use to
`voice/wordlist.txt`.

### 3. Google: Calendar, Tasks, Gmail, Drive and Docs

1. In the same Google Cloud project, enable the Google Calendar, Tasks, Gmail,
   Drive and Docs APIs.
2. Set up the OAuth consent screen as **External** and then **publish it ("In
   production")**. In "Testing", Google ends the sign-in after 7 days. Google
   shows an "unverified app" warning once; that's expected for a personal app.
3. Create an OAuth client of type **Desktop app** and save its JSON as
   `google-oauth-client.json` at the top of the repository.
4. Sign in once: `.venv\Scripts\python.exe -m natro_agent.google_signin`. It
   writes `tokens/google.json`; Natro offers the Google tools once it's there.

Permissions are kept narrow: Gmail read and send, Drive read-only.

### 4. Spotify (Premium)

1. At [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard),
   create an app with the Web API and the redirect URI
   `http://127.0.0.1:8899/callback`.
2. Put its Client ID in `.env` as `SPOTIFY_CLIENT_ID`.
3. Sign in once: `.venv\Scripts\python.exe -m natro_agent.spotify_signin`. It
   writes `tokens/spotify.json`. Spotify ends a sign-in after six months; Natro
   reminds you in the last two weeks.

### 5. Put the brain on a server

So that Natro works with the PC off, the brain runs on an always-on Linux
server (2 vCPU and 4 GB are plenty; Natro uses about 100 MB).

1. Install [Tailscale](https://tailscale.com) on the server, the PC and the
   phone, on the same account.
2. Follow [`deploy/README.md`](deploy/README.md): a user, the code, a venv, the
   service. Copy `.env`, `google-key.json` and `tokens/` to the server. Set
   `NATRO_LISTEN` to the server's Tailscale address (`tailscale ip -4`) with
   `:8700`, so it is never reachable from the internet, and make a long random
   `NATRO_DEVICE_TOKEN`.
3. On the PC, `.env` then needs only `NATRO_SERVER=ws://<server's Tailscale
   address>:8700` and the same `NATRO_DEVICE_TOKEN`.

### 6. The Android app

You need a JDK 17 or newer and the Android SDK (Android Studio, or just the
command-line tools with platform 37 and build-tools 37.0.0).

1. Create `android/local.properties`:

   ```properties
   sdk.dir=C\:\\Users\\you\\AppData\\Local\\Android\\Sdk
   natro.server=ws://<server's Tailscale address>:8700
   natro.token=<NATRO_DEVICE_TOKEN>
   ```

2. Put the server's Tailscale address in
   `android/app/src/main/res/xml/network_security_config.xml` too: the app
   allows unencrypted WebSockets only to that address (Tailscale encrypts the
   traffic itself).
3. Build and install over USB (USB debugging on):

   ```powershell
   android\gradlew.bat -p android assembleDebug
   adb install -r android\app\build\outputs\apk\debug\app-debug.apk
   ```

   The first build downloads sherpa-onnx and the wake word model.
4. Open Natro on the phone and work through its "To set up" card: microphone
   and notifications, the assistant role, contacts, calls and texts,
   notification access, and screen control.

## Make her yours

- `agent/identity/NATRO.md`: who she is, who you are, and her house rules.
- `voice/wordlist.txt`: names, brands and terms, spelled the way they should
  come out in English.
- `agent/config/mcp.json`: more tools as MCP servers. Each entry has a
  `command` (or a `url`), and says whether its results are `personal` (Claude
  only) and which tools need your yes (`confirm_tools`). `${NAME}` takes a
  value from `.env`. See `agent/natro_agent/mcp_client.py`.
- `.env`: the monthly budget, time zone, voice and talk key.

## Tests

None of them call a paid API:

```powershell
.venv\Scripts\python.exe -m pytest agent
.venv\Scripts\python.exe -m pytest pc
android\gradlew.bat -p android testDebugUnitTest
```

## Limits

- The PC app is Windows only. The phone app needs Android 14 or newer on an
  arm64 phone, and is installed directly, not from the Play Store.
- She hears Georgian with English mixed in, and replies in English.
- The free Gemini tier allows only a few dozen requests a day; after that,
  Claude answers.
- Spotify control needs Premium. Spotify's rules for personal apps leave out
  recommendations and Spotify's own playlists (On Repeat, Discover Weekly).

## License

[MIT](LICENSE).
