# Sets up Natro on a Windows PC: the Python environment, the downloaded models and .env.
# Safe to run again: it skips what is already there.
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup-pc.ps1               # everything
#   powershell -ExecutionPolicy Bypass -File scripts\setup-pc.ps1 -SkipModels   # no ~500 MB of models (typed chat only)
param([switch]$SkipModels)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
# Windows' own tar and curl (Git's would come first on some PATHs and can't read C:\ paths).
$Tar = "$env:SystemRoot\System32\tar.exe"
$Curl = "$env:SystemRoot\System32\curl.exe"

# 1. Python 3.13 in .venv with all three programs: the brain (agent, voice) runs on the PC too, for trying it out.
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv is missing. Install it with:  winget install astral-sh.uv  (then open a new terminal)"
    exit 1
}
if (-not (Test-Path .venv\Scripts\python.exe)) {
    uv venv --python 3.13 .venv
    if ($LASTEXITCODE) { exit 1 }
}
uv pip install --python .venv\Scripts\python.exe -e "voice[tools]" -e "agent[dev,signin]" -e "pc[dev]"
if ($LASTEXITCODE) { exit 1 }

# 2. Models from sherpa-onnx's releases: whisper-base hears "OK Natro", Kokoro speaks the replies.
function Get-Model($Name, $Url, $CheckFile, $Members) {
    if (Test-Path "models\$Name\$CheckFile") { Write-Host "models\$Name is there."; return }
    New-Item -ItemType Directory -Force models | Out-Null
    $Archive = "models\$Name.tar.bz2"
    Write-Host "Downloading $Name..."
    & $Curl -L --fail -o $Archive $Url
    if ($LASTEXITCODE) { throw "Download failed: $Url" }
    & $Tar -xjf $Archive -C models @Members
    if ($LASTEXITCODE) { throw "Couldn't extract $Archive" }
    Remove-Item $Archive
}
if (-not $SkipModels) {
    $Releases = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
    # Only the int8 files the wake word uses (75 MB of the 200 MB archive).
    Get-Model "sherpa-onnx-whisper-base" "$Releases/asr-models/sherpa-onnx-whisper-base.tar.bz2" "base-tokens.txt" @(
        "sherpa-onnx-whisper-base/base-encoder.int8.onnx",
        "sherpa-onnx-whisper-base/base-decoder.int8.onnx",
        "sherpa-onnx-whisper-base/base-tokens.txt")
    Get-Model "kokoro-en-v0_19" "$Releases/tts-models/kokoro-en-v0_19.tar.bz2" "model.onnx" @()
}

# 3. Settings.
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Made .env from .env.example: fill it in."
}
if (-not (Get-Command es -ErrorAction SilentlyContinue)) {
    Write-Host "For file search on this PC, install Everything and its command line:"
    Write-Host "  winget install voidtools.Everything voidtools.Everything.Cli"
}
Write-Host "Done. Tests: .venv\Scripts\python.exe -m pytest agent"
