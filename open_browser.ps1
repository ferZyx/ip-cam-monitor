$ErrorActionPreference = "SilentlyContinue"

param(
  [string]$Url = "http://localhost:5050/",
  [int]$TimeoutSeconds = 90,
  [switch]$Kiosk = $true
)

function Add-FullscreenParam([string]$u) {
  if (-not $u) { return $u }
  if ($u -match "(^|[?&])fullscreen=") { return $u }
  if ($u -match "\?") { return ($u + "&fullscreen=true") }
  if ($u.EndsWith("/")) { return ($u + "?fullscreen=true") }
  return ($u + "/?fullscreen=true")
}

function Test-Url([string]$u) {
  try {
    Invoke-WebRequest -UseBasicParsing -Uri $u -TimeoutSec 2 | Out-Null
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
  if (Test-Url $Url) { break }
  Start-Sleep -Seconds 1
}

$fallbackUrl = Add-FullscreenParam $Url

$edge = Find-Exe @("msedge.exe") @(
  "$env:ProgramFiles\\Microsoft\\Edge\\Application\\msedge.exe",
  "$env:ProgramFiles (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
)

$chrome = Find-Exe @("chrome.exe") @(
  "$env:ProgramFiles\\Google\\Chrome\\Application\\chrome.exe",
  "$env:ProgramFiles (x86)\\Google\\Chrome\\Application\\chrome.exe"
)

if ($Kiosk -and $edge) {
  $args = @(
    "--kiosk",
    $Url,
    "--edge-kiosk-type=fullscreen",
    "--no-first-run",
    "--disable-features=Translate",
    "--new-window"
  )
  Start-Process -FilePath $edge -ArgumentList $args | Out-Null
  exit 0
}

if ($Kiosk -and $chrome) {
  $args = @(
    "--kiosk",
    "--new-window",
    "--no-first-run",
    $Url
  )
  Start-Process -FilePath $chrome -ArgumentList $args | Out-Null
  exit 0
}

# Fallback: default browser (may require a click to enter fullscreen).
Start-Process $fallbackUrl | Out-Null
