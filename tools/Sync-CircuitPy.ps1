<#
.SYNOPSIS
    Keeps the git repo and a CircuitPython device in sync without putting .git on the device.

.DESCRIPTION
    The device is either the CIRCUITPY USB drive or, over WiFi, CircuitPython's web workflow file API
    (http://<device>/fs/). Both behave the same way.

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

.PARAMETER Diff
    Also print a git diff for each file that differs, always from the repo copy (---) to the device copy (+++),
    ignoring line endings. Most useful with Status or after a conflict warning.

.PARAMETER Drive
    Sync with a mounted drive. Defaults to $env:CIRCUITPY_DRIVE, else the first drive labelled CIRCUITPY.

.PARAMETER Address
    Sync over WiFi using the web workflow, e.g. 'cpy-abc123.local' or '192.168.1.50'. Used automatically from
    $env:CIRCUITPY_WEB_ADDRESS when no CIRCUITPY drive is mounted.

.PARAMETER Password
    Web workflow password (CIRCUITPY_WEB_API_PASSWORD in the device's settings.toml). Defaults to
    $env:CIRCUITPY_WEB_API_PASSWORD, else the value in a settings.toml at the repo root.

.PARAMETER PollSeconds
    How often Watch checks the device for changes when syncing over the web.

.EXAMPLE
    ./tools/Sync-CircuitPy.ps1 Watch
    Edit in the repo or on E:\ and the other side follows. The device reloads as soon as a file lands.

.EXAMPLE
    ./tools/Sync-CircuitPy.ps1 Status -Address cpy-abc123.local
    Compare against the device over WiFi.

.EXAMPLE
    ./tools/Sync-CircuitPy.ps1 Pull -Path network.py
    Take the device's copy of network.py after a conflict.
#>
#Requires -Version 7

[CmdletBinding(SupportsShouldProcess, DefaultParameterSetName = 'Drive')]
param(
    [Parameter(Position = 0)]
    [ValidateSet('Sync', 'Status', 'Push', 'Pull', 'Watch')]
    [string]$Mode = 'Sync',

    [string[]]$Path = @('*'),

    [switch]$Diff,

    [Parameter(ParameterSetName = 'Drive')]
    [string]$Drive = $env:CIRCUITPY_DRIVE,

    [Parameter(ParameterSetName = 'Web', Mandatory)]
    [string]$Address,

    [string]$Password = $env:CIRCUITPY_WEB_API_PASSWORD,

    [double]$PollSeconds = 2
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
$DeviceRoot = $null
$DeviceUrl = $null
$Http = $null

function Test-Match([string]$rel, [string[]]$patterns) {
    foreach ($p in $patterns) { if ($rel -like $p) { return $true } }
    return $false
}

function Test-Syncable([string]$rel) {
    -not (Test-Match $rel $DeviceOnly) -and -not (Test-Match $rel $RepoOnly) -and $rel -notlike '*__pycache__*' -and $rel -notlike '.*'
}

#region Device access
# Everything below goes through these functions, so the rest of the script doesn't care whether the device is a
# drive ($DeviceRoot) or the web workflow ($DeviceUrl).

function Connect-Device {
    if ($PSCmdlet.ParameterSetName -eq 'Drive') {
        if ($Drive) {
            if (-not (Test-Path -LiteralPath $Drive)) { throw "Device path '$Drive' doesn't exist." }
            $script:DeviceRoot = (Resolve-Path -LiteralPath $Drive).Path
            return
        }
        $found = [IO.DriveInfo]::GetDrives() | Where-Object { $_.IsReady -and $_.VolumeLabel -eq 'CIRCUITPY' } | Select-Object -First 1
        if ($found) { $script:DeviceRoot = $found.RootDirectory.FullName; return }
        if (-not $env:CIRCUITPY_WEB_ADDRESS) { throw 'No CIRCUITPY drive found. Plug in the board, or pass -Drive or -Address.' }
        $script:Address = $env:CIRCUITPY_WEB_ADDRESS
    }

    $url = $Address.TrimEnd('/')
    if ($url -notmatch '^https?://') { $url = "http://$url" }
    $script:DeviceUrl = $url

    if (-not $Password) {
        $toml = Join-Path $RepoRoot 'settings.toml'
        if ((Test-Path -LiteralPath $toml) -and ((Get-Content -LiteralPath $toml -Raw) -match '(?m)^\s*CIRCUITPY_WEB_API_PASSWORD\s*=\s*"([^"]*)"')) {
            $script:Password = $Matches[1]
        }
    }
    if (-not $Password) { throw 'No web workflow password. Pass -Password or set $env:CIRCUITPY_WEB_API_PASSWORD.' }

    # The web workflow closes the socket after every request without saying so, which makes a pooled connection
    # look reusable until the next request fails on it ("An error occurred while sending the request"). A zero
    # pooled lifetime gives every request its own connection. Auth is basic with an empty user name.
    $handler = [Net.Http.SocketsHttpHandler]::new()
    $handler.PooledConnectionLifetime = [TimeSpan]::Zero
    $script:Http = [Net.Http.HttpClient]::new($handler)
    $script:Http.Timeout = [TimeSpan]::FromSeconds(15)
    $script:Http.DefaultRequestHeaders.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new(
        'Basic', [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(":$Password")))
}

function Get-DeviceLabel { if ($DeviceUrl) { $DeviceUrl } else { $DeviceRoot } }

function Get-WebUrl([string]$rel) {
    "$DeviceUrl/fs/" + (($rel -split '/' | ForEach-Object { [uri]::EscapeDataString($_) }) -join '/')
}

# Returns the response body as byte[] (wrapped with the unary comma so PowerShell doesn't unroll it).
function Invoke-Web([string]$method, [string]$rel, [byte[]]$body, [switch]$Json, [switch]$AllowMissing) {
    $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::new($method.ToUpperInvariant()), (Get-WebUrl $rel))
    if ($Json) { $request.Headers.Accept.ParseAdd('application/json') }
    if ($null -ne $body) {
        $request.Content = [Net.Http.ByteArrayContent]::new($body)
        $request.Headers.Add('X-Timestamp', [string][DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
    }
    try { $response = $Http.SendAsync($request).GetAwaiter().GetResult() }
    catch {
        $inner = $_.Exception.GetBaseException().Message
        throw "$method $(Get-WebUrl $rel) failed: $inner (is the device reachable at ${DeviceUrl}?)"
    }
    $status = [int]$response.StatusCode
    if ($AllowMissing -and $status -eq 404) { return $null }
    if ($response.IsSuccessStatusCode) { return , $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult() }
    $hint = switch ($status) {
        401 { ' Wrong password?' }
        409 { ' The filesystem is read-only over the web while it is mounted over USB; eject the drive or use -Drive.' }
        default { '' }
    }
    throw "$method $(Get-WebUrl $rel) failed: $status $($response.ReasonPhrase).$hint"
}

# Lists the device's directories recursively, skipping ones the script never touches (lib/ and friends), so a
# listing is a handful of small requests. Returns @{ 'path/file.py' = 'size|modified' }.
function Get-WebListing {
    $files = @{}
    $dirs = [Collections.Generic.Queue[string]]::new()
    $dirs.Enqueue('')
    $script:WebDirs = [Collections.Generic.HashSet[string]]::new()
    while ($dirs.Count) {
        $dir = $dirs.Dequeue()
        [void]$WebDirs.Add($dir)
        $json = [Text.Encoding]::UTF8.GetString((Invoke-Web Get ($dir ? "$dir/" : '') -Json)) | ConvertFrom-Json -NoEnumerate
        # CircuitPython 8 returns a bare array; 9+ wraps it in an object with disk info.
        $entries = if ($json -is [array]) { $json } else { $json.files }
        foreach ($entry in $entries) {
            $rel = $dir ? "$dir/$($entry.name)" : $entry.name
            if ($entry.directory) {
                if (-not (Test-Match "$rel/" $DeviceOnly) -and $entry.name -notlike '.*' -and $entry.name -ne '__pycache__') { $dirs.Enqueue($rel) }
            }
            else { $files[$rel] = "$($entry.file_size)|$($entry.modified_ns)" }
        }
    }
    $files
}

function Get-DeviceFiles {
    if ($DeviceUrl) {
        $script:WebListing = Get-WebListing
        return $WebListing.Keys | Where-Object { Test-Syncable $_ }
    }
    Get-ChildItem -LiteralPath $DeviceRoot -Recurse -File -Force -ErrorAction SilentlyContinue |
        ForEach-Object { [IO.Path]::GetRelativePath($DeviceRoot, $_.FullName).Replace('\', '/') } |
        Where-Object { Test-Syncable $_ }
}

function Read-DeviceFile([string]$rel) {
    if ($DeviceUrl) {
        if (-not $WebListing.ContainsKey($rel)) { return $null }
        $bytes = Invoke-Web Get $rel -AllowMissing
        if ($null -ne $bytes) { return , $bytes }
        return $null
    }
    $file = Join-Path $DeviceRoot $rel
    if (Test-Path -LiteralPath $file -PathType Leaf) { , [IO.File]::ReadAllBytes($file) }
}

function Write-DeviceFile([string]$rel, [byte[]]$bytes) {
    if ($DeviceUrl) {
        # PUT on a path ending in / creates a directory; parents have to exist first.
        $parts = $rel -split '/'
        for ($i = 1; $i -lt $parts.Count; $i++) {
            $dir = $parts[0..($i - 1)] -join '/'
            if ($WebDirs.Add($dir)) { Invoke-Web Put "$dir/" ([byte[]]@()) | Out-Null }
        }
        Invoke-Web Put $rel $bytes | Out-Null
        return
    }
    Write-LocalFile (Join-Path $DeviceRoot $rel) $bytes
}

# Cheap fingerprint of the device's files, so Watch can poll the web workflow without downloading anything.
function Get-DeviceSignature {
    $listing = Get-WebListing
    ($listing.Keys | Where-Object { Test-Syncable $_ } | Sort-Object | ForEach-Object { "$_=$($listing[$_])" }) -join ';'
}
#endregion

function Read-RepoFile([string]$rel) {
    $file = Join-Path $RepoRoot $rel
    if (Test-Path -LiteralPath $file -PathType Leaf) { , [IO.File]::ReadAllBytes($file) }
}

function Write-LocalFile([string]$file, [byte[]]$bytes) {
    $dir = Split-Path -Parent $file
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    [IO.File]::WriteAllBytes($file, $bytes)
}

function Get-RepoFiles {
    $files = git -C $RepoRoot ls-files --cached --others --exclude-standard
    if ($LASTEXITCODE) { throw 'git ls-files failed.' }
    $files | Where-Object { (Test-Syncable $_) -and (Test-Path -LiteralPath (Join-Path $RepoRoot $_)) }
}

function Get-ContentHash([string]$rel, [byte[]]$bytes) {
    if ($null -eq $bytes) { return $null }
    if ($TextExtensions -contains [IO.Path]::GetExtension($rel).ToLowerInvariant()) {
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

# git diff --no-index prints whatever paths it's given, so stage both copies under repo/ and device/ in a temp
# folder to get readable headers like '--- repo/main.py' / '+++ device/main.py'.
function Show-Diff($item) {
    $WhatIfPreference = $false  # scratch copies only; -WhatIf shouldn't suppress the diff
    $tmp = Join-Path ([IO.Path]::GetTempPath()) "circuitpy-diff-$PID"
    try {
        $sides = foreach ($side in @{ Name = 'repo'; Bytes = $item.RepoBytes }, @{ Name = 'device'; Bytes = $item.DeviceBytes }) {
            if ($null -ne $side.Bytes) {
                Write-LocalFile (Join-Path $tmp "$($side.Name)/$($item.Path)") $side.Bytes
                "$($side.Name)/$($item.Path)"
            }
            else { '/dev/null' }
        }
        Push-Location $tmp
        # Out-Host keeps the diff out of Invoke-Sync's return value; that redirect also stops git auto-coloring.
        $color = if ([Console]::IsOutputRedirected) { '--color=never' } else { '--color=always' }
        try { git diff --no-index --no-prefix --ignore-cr-at-eol $color -- $sides[0] $sides[1] | Out-Host }
        finally { Pop-Location }
    }
    finally { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}

# Works out what each file needs and (unless Status) does it. Returns $true if anything was copied.
function Invoke-Sync([string]$mode) {
    $state = Read-State
    $repoFiles = @(Get-RepoFiles)
    $deviceFiles = @(Get-DeviceFiles)
    $label = Get-DeviceLabel

    $all = @($repoFiles) + @($deviceFiles | Where-Object { $repoFiles -notcontains $_ }) |
        Where-Object { Test-Match $_ $Path } |
        Sort-Object { [int]($LoadLast -contains $_) }, { $_ }

    $plan = foreach ($rel in $all) {
        $repoBytes = Read-RepoFile $rel
        $deviceBytes = Read-DeviceFile $rel
        $r = Get-ContentHash $rel $repoBytes
        $d = Get-ContentHash $rel $deviceBytes
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

        [pscustomobject]@{
            Path = $rel; Action = $action; RepoBytes = $repoBytes; DeviceBytes = $deviceBytes
            Hash = if ($action -eq 'pull') { $d } else { $r }
        }
    }

    $changed = $false
    foreach ($item in $plan) {
        if ($Diff -and $item.Action -ne 'same') { Show-Diff $item }
        switch ($item.Action) {
            'same' { $state[$item.Path] = $item.Hash }
            'push' {
                if ($mode -eq 'Status') { Write-Host "  -> device  $($item.Path)" -ForegroundColor Cyan }
                elseif ($PSCmdlet.ShouldProcess("$label/$($item.Path)", 'Copy from repo')) {
                    Write-DeviceFile $item.Path $item.RepoBytes
                    $state[$item.Path] = $item.Hash
                    $changed = $true
                    Write-Host "$(Get-Date -Format HH:mm:ss) -> device  $($item.Path)" -ForegroundColor Cyan
                }
            }
            'pull' {
                if ($mode -eq 'Status') { Write-Host "  <- repo    $($item.Path)" -ForegroundColor Green }
                elseif ($PSCmdlet.ShouldProcess((Join-Path $RepoRoot $item.Path), 'Copy from device')) {
                    Write-LocalFile (Join-Path $RepoRoot $item.Path) $item.DeviceBytes
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

Connect-Device
Write-Verbose "Repo: $RepoRoot  Device: $(Get-DeviceLabel)"

if ($Mode -ne 'Watch') {
    Invoke-Sync $Mode | Out-Null
    return
}

Invoke-Sync 'Sync' | Out-Null
Write-Host "Watching $RepoRoot and $(Get-DeviceLabel). Ctrl+C to stop." -ForegroundColor DarkGray

# The repo (and a mounted drive) get a FileSystemWatcher; the web workflow has no change notifications, so it's
# polled by listing, which only costs a request per directory.
$watchRoots = @($RepoRoot) + @($DeviceRoot | Where-Object { $_ })
$watchers = foreach ($root in $watchRoots) {
    $w = [IO.FileSystemWatcher]::new($root)
    $w.IncludeSubdirectories = $true
    $w.NotifyFilter = [IO.NotifyFilters]'FileName, DirectoryName, LastWrite, Size'
    foreach ($evt in 'Changed', 'Created', 'Renamed') {
        Register-ObjectEvent -InputObject $w -EventName $evt -SourceIdentifier "CircuitPySync.$root.$evt" | Out-Null
    }
    $w.EnableRaisingEvents = $true
    $w
}

$signature = if ($DeviceUrl) { Get-DeviceSignature }
$nextPoll = [DateTime]::UtcNow.AddSeconds($PollSeconds)

try {
    while ($true) {
        $e = Wait-Event -Timeout 1
        $sync = $false

        if ($e) {
            # Let a burst of writes (editor save, git checkout) settle, then handle them in one pass.
            Start-Sleep -Milliseconds 300
            $events = @(Get-Event)
            $events | Remove-Event
            $sync = [bool]($events | Where-Object { $_.SourceEventArgs.FullPath -notmatch '[\\/](\.git|__pycache__)([\\/]|$)' -and $_.SourceEventArgs.FullPath -ne $StatePath })
        }

        try {
            if ($DeviceUrl -and [DateTime]::UtcNow -ge $nextPoll) {
                if ((Get-DeviceSignature) -ne $signature) { $sync = $true }
                $nextPoll = [DateTime]::UtcNow.AddSeconds($PollSeconds)
            }
            if ($sync) {
                Invoke-Sync 'Sync' | Out-Null
                if ($DeviceUrl) { $signature = Get-DeviceSignature }
            }
        }
        catch { Write-Warning "Sync failed (device unplugged or offline?): $_" }
    }
}
finally {
    Get-EventSubscriber | Where-Object SourceIdentifier -like 'CircuitPySync.*' | Unregister-Event
    $watchers | ForEach-Object { $_.Dispose() }
    if ($Http) { $Http.Dispose() }
}
