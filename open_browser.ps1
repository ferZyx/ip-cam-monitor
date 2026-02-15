param(
  [string]$Url = "http://localhost:5050/",
  [int]$TimeoutSeconds = 90,
  [switch]$Kiosk = $true,
  [switch]$Debug = $false
)

$ErrorActionPreference = "Stop"

$ScriptRoot = $PSScriptRoot
if (-not $ScriptRoot) {
  $ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}

$LogPath = Join-Path $ScriptRoot "open_browser.log"

function Write-Log([string]$msg) {
  $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
  $line = "[$ts] $msg"
  try {
    Add-Content -Path $LogPath -Value $line -Encoding Ascii
  } catch {
    # If logging fails, do not block browser launch.
  }
  if ($Debug) { Write-Host $line }
}

trap {
  try {
    Write-Log ("Unhandled error: " + $_.Exception.Message)
  } catch {
    # best-effort
  }
  exit 1
}

function Add-FullscreenParam([string]$u) {
  if (-not $u) { return $u }
  if ($u -match "(^|[?&])fullscreen=") { return $u }
  if ($u -match "\?") { return ($u + "&fullscreen=true") }
  if ($u.EndsWith("/")) { return ($u + "?fullscreen=true") }
  return ($u + "/?fullscreen=true")
}

function Test-Endpoint([string]$u) {
  try {
    $uri = [Uri]$u
    $hostName = $uri.DnsSafeHost
    $port = $uri.Port
    if (-not $hostName -or $port -le 0) { return $false }

    $client = New-Object System.Net.Sockets.TcpClient
    $iar = $client.BeginConnect($hostName, $port, $null, $null)
    $ok = $iar.AsyncWaitHandle.WaitOne(700)
    if (-not $ok) {
      $client.Close() | Out-Null
      return $false
    }
    $client.EndConnect($iar)
    $client.Close() | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Find-Exe([string[]]$names, [string[]]$paths) {
  foreach ($n in $names) {
    $cmd = Get-Command $n -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) {
      return $cmd.Source
    }
  }
  foreach ($p in $paths) {
    if ($p -and (Test-Path $p)) {
      return $p
    }
  }
  return $null
}

# Wait until the server is reachable (or timeout).
$deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSeconds))
while ((Get-Date) -lt $deadline) {
  if (Test-Endpoint $Url) { break }
  Start-Sleep -Seconds 1
}

$fallbackUrl = Add-FullscreenParam $Url

$edge = Find-Exe @("msedge.exe") @(
  "$env:ProgramFiles\\Microsoft\\Edge\\Application\\msedge.exe",
  "$env:ProgramFiles (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "$env:LOCALAPPDATA\\Microsoft\\Edge\\Application\\msedge.exe"
)

$chrome = Find-Exe @("chrome.exe") @(
  "$env:ProgramFiles\\Google\\Chrome\\Application\\chrome.exe",
  "$env:ProgramFiles (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "$env:LOCALAPPDATA\\Google\\Chrome\\Application\\chrome.exe"
)

Write-Log "Requested Url: $Url"
Write-Log "Fallback Url:  $fallbackUrl"
Write-Log "Edge:          $edge"
Write-Log "Chrome:        $chrome"

function Try-StartProcess([string]$filePath, [object]$argumentList) {
  try {
    if ($argumentList -ne $null) {
      $p = Start-Process -FilePath $filePath -ArgumentList $argumentList -PassThru
    } else {
      $p = Start-Process -FilePath $filePath -PassThru
    }
    if ($p -and $p.Id) {
      Write-Log ("Started PID: " + $p.Id)
    }
    return $true
  } catch {
    Write-Log ("Start-Process failed: " + $_.Exception.Message)
    return $false
  }
}

if ($Kiosk -and $edge) {
  $args = @(
    "--kiosk",
    $Url,
    "--edge-kiosk-type=fullscreen",
    "--no-first-run",
    "--disable-features=Translate",
    "--new-window"
  )
  Write-Log "Launching Edge (kiosk)."
  if (Try-StartProcess $edge $args) { exit 0 }
}

if ($Kiosk -and $chrome) {
  $args = @(
    "--kiosk",
    "--new-window",
    "--no-first-run",
    $Url
  )
  Write-Log "Launching Chrome (kiosk)."
  if (Try-StartProcess $chrome $args) { exit 0 }
}

# Fallback: default browser (may require a click to enter fullscreen).
Write-Log "Launching default browser (fallback)."
if (Try-StartProcess $fallbackUrl $null) { exit 0 }

# Extra fallback: cmd/explorer shell open.
Write-Log "Launching via cmd /c start (fallback #2)."
if (Try-StartProcess "cmd.exe" @("/c", "start", "", $fallbackUrl)) { exit 0 }

Write-Log "Launching via explorer.exe (fallback #3)."
[void](Try-StartProcess "explorer.exe" @($fallbackUrl))
