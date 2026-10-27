# Lead List Builder, one-line install for Windows. Paste this into PowerShell:
#
#   irm https://raw.githubusercontent.com/topkdev/lead-builder/main/install.ps1 | iex
#
# It puts everything in a "Lead Builder" folder in your user folder: the app,
# its own copy of Python (so you don't need to install one), and a "Lead
# Builder" shortcut on your Desktop. Running the same line again updates the
# app; your key and your lists are kept. To remove it, delete the folder and
# the shortcut.
$ErrorActionPreference = "Stop"
# Windows PowerShell's download progress bar slows downloads to a crawl.
$ProgressPreference = "SilentlyContinue"

# Where the app is downloaded from. Override for testing.
$ZipUrl = if ($env:LEAD_BUILDER_ZIP) { $env:LEAD_BUILDER_ZIP } else { "https://github.com/topkdev/lead-builder/archive/refs/heads/main.zip" }
$Dest = if ($env:LEAD_BUILDER_DIR) { $env:LEAD_BUILDER_DIR } else { Join-Path $HOME "Lead Builder" }

function Say($text) { Write-Host "`n==> $text" }
function Fail($text) {
    Write-Host "`nSomething went wrong: $text`nCheck your internet connection and paste the line again."
    throw $text
}

Say "Getting the setup helper"
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    # uv (astral.sh) installs Python for the app without touching the rest of
    # the computer. It's downloaded as a plain program, not through its own
    # install script: that script stops (and closes PowerShell) under
    # Windows' default execution policy, which blocks downloaded scripts.
    $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    $target = @{ "AMD64" = "x86_64"; "ARM64" = "aarch64"; "x86" = "i686" }[$arch]
    if (-not $target) { $target = "x86_64" }
    $bin = Join-Path $HOME ".local\bin"
    $uvZip = Join-Path ([IO.Path]::GetTempPath()) ("uv-" + [guid]::NewGuid() + ".zip")
    try {
        New-Item -ItemType Directory -Force $bin | Out-Null
        Invoke-WebRequest "https://github.com/astral-sh/uv/releases/latest/download/uv-$($target)-pc-windows-msvc.zip" `
            -OutFile $uvZip -UseBasicParsing
        Expand-Archive $uvZip $bin -Force
        Remove-Item $uvZip -Force
    } catch { Fail "couldn't download the setup helper" }
    $uv = Join-Path $bin "uv.exe"
}
if (-not (Test-Path $uv)) { Fail "the setup helper didn't install" }

Say "Downloading the Lead List Builder"
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("lead-builder-" + [guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
try {
    Invoke-WebRequest $ZipUrl -OutFile (Join-Path $tmp "app.zip") -UseBasicParsing
    Expand-Archive (Join-Path $tmp "app.zip") (Join-Path $tmp "x")
} catch { Fail "couldn't download the app" }
$src = (Get-ChildItem (Join-Path $tmp "x") -Recurse -Filter leads.py | Select-Object -First 1).DirectoryName
if (-not $src) { Fail "the download didn't contain the app" }
New-Item -ItemType Directory -Force $Dest | Out-Null
# The code is replaced; the person's key (.env), lists (jobs) and
# spreadsheets (exports) are not touched.
robocopy $src $Dest /MIR /XD jobs exports .venv /XF .env /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Fail "couldn't copy the app into place" }
Remove-Item -Recurse -Force $tmp

Say "Setting up (about a minute the first time)"
Set-Location $Dest
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $uv venv --quiet --seed --python 3.12 .venv
    if ($LASTEXITCODE -ne 0) { Fail "couldn't set up Python" }
}
& $uv pip install --quiet --python .venv\Scripts\python.exe -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "couldn't install the app's parts" }
Copy-Item requirements.txt .venv\requirements.txt -Force

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop "Lead Builder.lnk"))
$shortcut.TargetPath = Join-Path $Dest "Start Lead Builder.bat"
$shortcut.WorkingDirectory = $Dest
$shortcut.Save()

Say "Done. Double-click ""Lead Builder"" on your Desktop to open it any time."
if (-not $env:LEAD_BUILDER_NO_START) { Start-Process (Join-Path $Dest "Start Lead Builder.bat") -WorkingDirectory $Dest }
