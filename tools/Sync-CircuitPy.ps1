<#
.SYNOPSIS
    Keeps the git repo and the CIRCUITPY drive in sync without putting .git on the device.

.DESCRIPTION
    The device file set is every file git knows about (tracked, plus untracked files that aren't ignored)
    minus repo-only files (README, .vscode, tools, requirements.txt, ...). lib/ is managed by circup and
    secrets.py / settings.toml are device-local, so none of those are ever touched.

    A small state file (.circuitpy-sync.json, gitignored) records the content hash of each file at the last
    sync. That's what lets Sync tell which side changed:

        repo changed, device didn't  -> copy to device
        device changed, repo didn't  -> copy to repo
        both changed (or no state)   -> conflict, nothing copied; resolve with Push or Pull

    Line endings are ignored when comparing, so a CRLF copy on the device and an LF copy in the repo are equal.

.PARAMETER Mode
    Sync    Two-way sync using the state file (default).
    Status  Show what Sync would do, without copying anything.
    Push    Repo wins: copy every differing file to the device.
    Pull    Device wins: copy every differing file to the repo, including new files created on the device.
    Watch   Sync, then keep watching both sides and sync on every change. Ctrl+C to stop.

.PARAMETER Path
    Only consider files matching these wildcard patterns (repo-relative, forward slashes), e.g. 'main.py' or 'static/*'.

.PARAMETER Drive
    Device root. Defaults to $env:CIRCUITPY_DRIVE, else the first drive labelled CIRCUITPY.

.EXAMPLE
    ./tools/Sync-CircuitPy.ps1 Watch
    Edit in the repo or on E:\ and the other side follows. The device reloads as soon as a file lands.

.EXAMPLE
    ./tools/Sync-CircuitPy.ps1 Pull -Path network.py
    Take the device's copy of network.py after a conflict.
#>
#Requires -Version 7

[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Position = 0)]
    [ValidateSet('Sync', 'Status', 'Push', 'Pull', 'Watch')]
    [string]$Mode = 'Sync',

    [string[]]$Path = @('*'),

    [string]$Drive = $env:CIRCUITPY_DRIVE
)

$ErrorActionPreference = 'Stop'

# Files that live in the repo but never go to the device.
$RepoOnly = @('.gitignore', '.gitattributes', '*.md', 'requirements.txt', '.vscode/*', 'tools/*', '.github/*')

# Files/folders on the device that this script never reads or writes.
$DeviceOnly = @('lib/*', 'secrets.py', 'settings.toml', 'boot_out.txt', 'sd/*',
    'System Volume Information/*', '.fseventsd/*', '.Trashes/*', '.Trash-*', '.metadata_never_index')

# Written last so a multi-file push doesn't reload into a half-updated set.
$LoadLast = @('code.py', 'main.py')

$TextExtensions = @('.py', '.txt', '.html', '.htm', '.css', '.js', '.json', '.toml', '.csv', '.md')

$RepoRoot = Split-Path $PSScriptRoot
$StatePath = Join-Path $RepoRoot '.circuitpy-sync.json'

function Find-DeviceRoot {
    if ($Drive) {
        if (-not (Test-Path -LiteralPath $Drive)) { throw "Device path '$Drive' doesn't exist." }
        return (Resolve-Path -LiteralPath $Drive).Path
    }
    $found = [IO.DriveInfo]::GetDrives() | Where-Object { $_.IsReady -and $_.VolumeLabel -eq 'CIRCUITPY' } | Select-Object -First 1
    if (-not $found) { throw 'No CIRCUITPY drive found. Plug in the board or pass -Drive.' }
    return $found.RootDirectory.FullName
}

function Test-Match([string]$rel, [string[]]$patterns) {
    foreach ($p in $patterns) { if ($rel -like $p) { return $true } }
    return $false
}

function Get-RepoFiles {
    $files = git -C $RepoRoot ls-files --cached --others --exclude-standard
    if ($LASTEXITCODE) { throw 'git ls-files failed.' }
    $files | Where-Object { -not (Test-Match $_ $RepoOnly) -and -not (Test-Match $_ $DeviceOnly) -and (Test-Path -LiteralPath (Join-Path $RepoRoot $_)) }
}

function Get-DeviceFiles([string]$root) {
    Get-ChildItem -LiteralPath $root -Recurse -File -Force -ErrorAction SilentlyContinue |
        ForEach-Object { [IO.Path]::GetRelativePath($root, $_.FullName).Replace('\', '/') } |
        Where-Object { -not (Test-Match $_ $DeviceOnly) -and -not (Test-Match $_ $RepoOnly) -and $_ -notlike '*__pycache__*' -and $_ -notlike '.*' }
}

function Get-ContentHash([string]$file) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return $null }
    $bytes = [IO.File]::ReadAllBytes($file)
    if ($TextExtensions -contains [IO.Path]::GetExtension($file).ToLowerInvariant()) {
        $bytes = [Text.Encoding]::UTF8.GetBytes([Text.Encoding]::UTF8.GetString($bytes).Replace("`r`n", "`n"))
    }
    [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes))
}

function Read-State {
    $state = @{}
    if (Test-Path -LiteralPath $StatePath) {
        (Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $state[$_.Name] = $_.Value }
    }
    $state
}

function Save-State($state) {
    $ordered = [ordered]@{}
    $state.Keys | Sort-Object | ForEach-Object { $ordered[$_] = $state[$_] }
    $ordered | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding utf8
}

function Copy-SyncFile([string]$from, [string]$to) {
    $dir = Split-Path -Parent $to
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Copy-Item -LiteralPath $from -Destination $to -Force
}

# Works out what each file needs and (unless Status) does it. Returns $true if anything was copied.
function Invoke-Sync([string]$deviceRoot, [string]$mode) {
    $state = Read-State
    $repoFiles = @(Get-RepoFiles)
    $deviceFiles = @(Get-DeviceFiles $deviceRoot)

    $all = @($repoFiles) + @($deviceFiles | Where-Object { $repoFiles -notcontains $_ }) |
        Where-Object { Test-Match $_ $Path } |
        Sort-Object { [int]($LoadLast -contains $_) }, { $_ }

    $plan = foreach ($rel in $all) {
        $repoFile = Join-Path $RepoRoot $rel
        $deviceFile = Join-Path $deviceRoot $rel
        $r = Get-ContentHash $repoFile
        $d = Get-ContentHash $deviceFile
        $b = $state[$rel]

        $action =
            if ($r -eq $d) { 'same' }
            elseif (-not $r) { if ($mode -eq 'Pull') { 'pull' } else { 'device-only' } }
            elseif (-not $d) { if ($mode -eq 'Pull') { 'repo-only' } else { 'push' } }
            elseif ($mode -eq 'Push') { 'push' }
            elseif ($mode -eq 'Pull') { 'pull' }
            elseif ($b -eq $r) { 'pull' }
            elseif ($b -eq $d) { 'push' }
            else { 'conflict' }

        [pscustomobject]@{ Path = $rel; Action = $action; Repo = $repoFile; Device = $deviceFile; Hash = if ($action -eq 'pull') { $d } else { $r } }
    }

    $changed = $false
    foreach ($item in $plan) {
        switch ($item.Action) {
            'same' { $state[$item.Path] = $item.Hash }
            'push' {
                if ($mode -eq 'Status') { Write-Host "  -> device  $($item.Path)" -ForegroundColor Cyan }
                elseif ($PSCmdlet.ShouldProcess($item.Device, 'Copy from repo')) {
                    Copy-SyncFile $item.Repo $item.Device
                    $state[$item.Path] = $item.Hash
                    $changed = $true
                    Write-Host "$(Get-Date -Format HH:mm:ss) -> device  $($item.Path)" -ForegroundColor Cyan
                }
            }
            'pull' {
                if ($mode -eq 'Status') { Write-Host "  <- repo    $($item.Path)" -ForegroundColor Green }
                elseif ($PSCmdlet.ShouldProcess($item.Repo, 'Copy from device')) {
                    Copy-SyncFile $item.Device $item.Repo
                    $state[$item.Path] = $item.Hash
                    $changed = $true
                    Write-Host "$(Get-Date -Format HH:mm:ss) <- repo    $($item.Path)" -ForegroundColor Green
                }
            }
            'conflict' { Write-Warning "Both sides changed: $($item.Path). Resolve with 'Push -Path $($item.Path)' or 'Pull -Path $($item.Path)'." }
            'device-only' { Write-Host "  ?  device only: $($item.Path) (Pull to bring it into the repo)" -ForegroundColor DarkYellow }
            'repo-only' { Write-Host "  ?  repo only: $($item.Path) (Push to copy it to the device)" -ForegroundColor DarkYellow }
        }
    }

    if ($mode -ne 'Status' -and -not $WhatIfPreference) { Save-State $state }
    elseif ($mode -eq 'Status' -and -not ($plan | Where-Object Action -ne 'same')) { Write-Host 'Everything in sync.' }
    $changed
}

$deviceRoot = Find-DeviceRoot
Write-Verbose "Repo: $RepoRoot  Device: $deviceRoot"

if ($Mode -ne 'Watch') {
    Invoke-Sync $deviceRoot $Mode | Out-Null
    return
}

Invoke-Sync $deviceRoot 'Sync' | Out-Null
Write-Host "Watching $RepoRoot and $deviceRoot. Ctrl+C to stop." -ForegroundColor DarkGray

$watchers = foreach ($root in $RepoRoot, $deviceRoot) {
    $w = [IO.FileSystemWatcher]::new($root)
    $w.IncludeSubdirectories = $true
    $w.NotifyFilter = [IO.NotifyFilters]'FileName, DirectoryName, LastWrite, Size'
    foreach ($evt in 'Changed', 'Created', 'Renamed') {
        Register-ObjectEvent -InputObject $w -EventName $evt -SourceIdentifier "CircuitPySync.$root.$evt" | Out-Null
    }
    $w.EnableRaisingEvents = $true
    $w
}

try {
    while ($true) {
        $e = Wait-Event -Timeout 1
        if (-not $e) { continue }

        # Let a burst of writes (editor save, git checkout) settle, then handle them in one pass.
        Start-Sleep -Milliseconds 300
        $events = @(Get-Event)
        $events | Remove-Event
        $relevant = $events | Where-Object { $_.SourceEventArgs.FullPath -notmatch '[\\/](\.git|__pycache__)([\\/]|$)' -and $_.SourceEventArgs.FullPath -ne $StatePath }
        if (-not $relevant) { continue }

        try { Invoke-Sync $deviceRoot 'Sync' | Out-Null }
        catch { Write-Warning "Sync failed (device unplugged?): $_" }
    }
}
finally {
    Get-EventSubscriber | Where-Object SourceIdentifier -like 'CircuitPySync.*' | Unregister-Event
    $watchers | ForEach-Object { $_.Dispose() }
}
