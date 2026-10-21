# SPDX-License-Identifier: Apache-2.0
#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Position=0)]
    [ValidateSet('install','start','stop','status','logs','update','updates','backup','restore','remove','help')]
    [string]$Command = 'help',
    [Parameter(Position=1)][ValidateSet('on','off')][string]$Choice,
    [string]$Dir,
    [ValidateRange(1024,65535)][int]$Port = 8765,
    [switch]$AutoUpdate,
    [switch]$NoAutoUpdate,
    [string]$File,
    [switch]$Purge
)
$ErrorActionPreference = 'Stop'
function Invoke-Docker {
    $DockerArguments = @()
    if ($DockerContext) { $DockerArguments += @('--context', $DockerContext) }
    & docker @DockerArguments @args
    if ($LASTEXITCODE -ne 0) { throw "Command exited with code $LASTEXITCODE. See the output above; use logs for service startup errors." }
}
function Assert-NotLink([string]$Path) {
    if ((Test-Path -LiteralPath $Path) -and ((Get-Item -Force -LiteralPath $Path).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "The managed path must not be a link: $Path"
    }
}
if ($Command -eq 'help') {
    Write-Output @'
Usage: .\ternela.ps1 install|start|stop|status|logs|update|updates|backup|restore|remove [options]
  -Dir PATH         One managed folder (default: $HOME\ternela)
  -Port NUMBER      Install on this loopback port (default: 8765)
  -AutoUpdate       Explicitly opt in during install
  -NoAutoUpdate     Explicitly opt out during install
  updates on|off    Change consent on an existing running install
  backup            Save a compressed snapshot in this folder/backups
  restore -File PATH Restore a snapshot; first back up current data
  remove -Purge     Delete live volume, folder, backups and local image
Docker Desktop with Linux containers and Compose is required. No host Python.
'@
    return
}
try {
    if (-not $Dir) {
        if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.ternela-install')) { $Dir = $PSScriptRoot }
        else { $Dir = Join-Path $HOME 'ternela' }
    }
    $Dir = [IO.Path]::GetFullPath($Dir).TrimEnd([IO.Path]::DirectorySeparatorChar)
    if (-not $Dir -or $Dir -eq [IO.Path]::GetPathRoot($Dir).TrimEnd('\') -or $Dir -eq $HOME) { throw 'Choose a dedicated install folder' }
    Assert-NotLink $Dir
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Install Docker Desktop, then retry' }
    $DockerContext = $null
    $ContextMarker = Join-Path $Dir '.ternela-context'
    if (Test-Path -LiteralPath $ContextMarker) {
        Assert-NotLink $ContextMarker
        $DockerContext = [IO.File]::ReadAllText($ContextMarker).Trim()
        if (-not $DockerContext) { throw 'Empty Docker context marker' }
    } elseif ($Command -eq 'install') {
        $DockerContext = Invoke-Docker context show
        if (-not $DockerContext) { throw 'Select a named Docker context before installing' }
    }
    Invoke-Docker info --format '{{.OSType}}' | ForEach-Object { if ($_ -ne 'linux') { throw 'Switch Docker Desktop to Linux containers' } }
    Invoke-Docker compose version
    if ($Command -eq 'install') {
        if (Test-Path -LiteralPath $Dir) { throw 'Folder already exists; use start, or choose another -Dir' }
        if ($AutoUpdate -and $NoAutoUpdate) { throw 'Choose one update preference' }
        $SourceDir = Split-Path -Parent $PSScriptRoot
        if (-not (Test-Path -LiteralPath (Join-Path $SourceDir 'Dockerfile'))) { throw 'Run the installer from an extracted release or repository checkout' }
        $Consent = 'false'
        if ($AutoUpdate) { $Consent = 'true' }
        elseif (-not $NoAutoUpdate) {
            if ([Console]::IsInputRedirected -or -not [Environment]::UserInteractive) { throw 'Noninteractive install needs -AutoUpdate or -NoAutoUpdate' }
            $Answer = Read-Host 'Automatically install newer stable Ternela releases while running? [y/N]'
            if ($Answer -match '^(y|yes)$') { $Consent = 'true' }
        }
        $Identity = [Guid]::NewGuid().ToString('N').Substring(0,16)
        $EngineId = Invoke-Docker info --format '{{.ID}}'
        if (-not $EngineId) { throw 'Docker engine identity is unavailable' }
        Write-Output 'Building Ternela; the first build downloads its Python image and public API docs.'
        Invoke-Docker build --tag "ternela-local:$Identity" $SourceDir
        New-Item -ItemType Directory -Path $Dir | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $Dir 'backups') | Out-Null
        $Encoding = New-Object Text.UTF8Encoding($false)
        [IO.File]::WriteAllText((Join-Path $Dir '.ternela-storage'), "native-v1`n", $Encoding)
        [IO.File]::WriteAllText((Join-Path $Dir '.ternela-install'), "$Identity`n", $Encoding)
        [IO.File]::WriteAllText((Join-Path $Dir '.ternela-engine'), "$EngineId`n", $Encoding)
        [IO.File]::WriteAllText((Join-Path $Dir '.ternela-context'), "$DockerContext`n", $Encoding)
        Copy-Item -LiteralPath (Join-Path $SourceDir 'compose.yaml') -Destination $Dir
        foreach ($Name in @('ternela.sh','ternela.ps1')) {
            Copy-Item -LiteralPath (Join-Path $SourceDir "scripts/$Name") -Destination $Dir
        }
        $EnvText = "TERNELA_INSTALL_ID=$Identity`nTERNELA_PORT=$Port`nTERNELA_AUTO_UPDATE=$Consent`n"
        [IO.File]::WriteAllText((Join-Path $Dir 'docker.env'), $EnvText, $Encoding)
    }
    $Marker = Join-Path $Dir '.ternela-install'
    Assert-NotLink $Marker
    Assert-NotLink (Join-Path $Dir 'state')
    if (-not (Test-Path -LiteralPath $Marker)) { throw 'This is not a marked Ternela install folder' }
    $Identity = [IO.File]::ReadAllText($Marker).Trim()
    if ($Identity -notmatch '^[0-9a-f]{16}$') { throw 'Invalid install marker' }
    $EngineMarker = Join-Path $Dir '.ternela-engine'
    Assert-NotLink $EngineMarker
    if (-not (Test-Path -LiteralPath $EngineMarker)) { throw 'Missing Docker engine marker; use the original script or reinstall into a new folder after backing up data' }
    $CurrentEngine = Invoke-Docker info --format '{{.ID}}'
    if (-not $CurrentEngine -or $CurrentEngine -ne [IO.File]::ReadAllText($EngineMarker).Trim()) { throw 'Docker engine differs from this installation; switch back to its original Docker context' }
    $StorageMarker = Join-Path $Dir '.ternela-storage'
    Assert-NotLink $StorageMarker
    if (-not (Test-Path -LiteralPath $StorageMarker) -or [IO.File]::ReadAllText($StorageMarker).Trim() -ne 'native-v1') { throw 'Legacy folder storage needs a verified migration before using this script' }
    $Volume = "ternela-$Identity-state"
    $VolumeExists = @(Invoke-Docker volume ls --quiet --filter "name=^$Volume`$")
    if ($VolumeExists.Count) {
        $VolumeOwner = Invoke-Docker volume inspect --format '{{index .Labels "io.ternela.install"}}' $Volume
        if ($VolumeOwner -ne $Identity) { throw 'Live volume belongs to another installation; nothing changed' }
    } elseif ($Command -notin @('install','start','remove')) { throw 'Live volume is missing; restore a backup before continuing' }
    $ComposeArgs = @('compose','--project-directory',$Dir,'--env-file',(Join-Path $Dir 'docker.env'),'-f',(Join-Path $Dir 'compose.yaml'),'-p',"ternela-$Identity")
    $ContainerIds = @(Invoke-Docker ps -aq --filter "label=com.docker.compose.project=ternela-$Identity")
    foreach ($ContainerId in $ContainerIds) {
        $Labels = (Invoke-Docker inspect --format '{{json .Config.Labels}}' $ContainerId | ConvertFrom-Json)
        $Owner = $Labels.'com.docker.compose.project.working_dir'
        # Docker Desktop may normalize Windows separators in Compose labels.
        if ($Owner.Replace('/','\').TrimEnd('\') -ine $Dir.Replace('/','\').TrimEnd('\')) { throw 'Another folder owns this container project' }
    }
    function Invoke-Compose {
        $Keys = @('TERNELA_INSTALL_ID','TERNELA_PORT','TERNELA_UID','TERNELA_GID','TERNELA_AUTO_UPDATE','TERNELA_STATE','TERNELA_CONTAINER','TERNELA_PUBLIC_PORT')
        $Previous = @{}
        try {
            foreach ($Key in $Keys) {
                $Previous[$Key] = [Environment]::GetEnvironmentVariable($Key, 'Process')
                [Environment]::SetEnvironmentVariable($Key, $null, 'Process')
            }
            Invoke-Docker @ComposeArgs @args
        } finally {
            foreach ($Key in $Keys) { [Environment]::SetEnvironmentVariable($Key, $Previous[$Key], 'Process') }
        }
    }
    function Invoke-Control { Invoke-Compose exec -T ternela python /opt/runtime/local_control.py @args }
    function Invoke-Transfer([string]$Action, [string]$Path) {
        # PowerShell 5 text pipelines corrupt binary gzip streams. Copy bytes directly.
        $Info = New-Object Diagnostics.ProcessStartInfo
        $Info.FileName = (Get-Command docker).Source
        $ContextArguments = if ($DockerContext) { '--context "' + $DockerContext.Replace('"', '\"') + '" ' } else { '' }
        $Info.Arguments = $ContextArguments + "run --rm -i --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true --user 1000:1000 --mount type=volume,source=$Volume,target=/state --entrypoint python ternela-local:$Identity /opt/runtime/storage_control.py $Action"
        $Info.UseShellExecute = $false
        $Info.RedirectStandardInput = $true
        $Info.RedirectStandardOutput = $true
        $Process = New-Object Diagnostics.Process
        $Process.StartInfo = $Info
        if (-not $Process.Start()) { throw 'Could not start storage transfer' }
        try {
            if ($Action -eq 'backup') {
                $Process.StandardInput.Close()
                $Stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew)
                try { $Process.StandardOutput.BaseStream.CopyTo($Stream) } finally { $Stream.Dispose() }
            } else {
                $Stream = [IO.File]::OpenRead($Path)
                try { $Stream.CopyTo($Process.StandardInput.BaseStream) } finally { $Stream.Dispose(); $Process.StandardInput.Close() }
            }
            $Process.WaitForExit()
            if ($Process.ExitCode -ne 0) { throw 'State transfer failed; inspect the error above' }
        } finally { $Process.Dispose() }
    }
    function Save-Backup {
        $Backups = Join-Path $Dir 'backups'
        Assert-NotLink $Backups
        [IO.Directory]::CreateDirectory($Backups) | Out-Null
        $Output = Join-Path $Backups ("state-" + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N') + '.tar.gz')
        try { Invoke-Transfer 'backup' ($Output + '.part'); [IO.File]::Move(($Output + '.part'), $Output) }
        catch { if (Test-Path -LiteralPath ($Output + '.part')) { [IO.File]::Delete(($Output + '.part')) }; throw }
        Write-Output "Backup: $Output"
    }
    switch ($Command) {
        { $_ -in @('install','start') } {
            Invoke-Compose up --detach --no-build --wait --wait-timeout 120
            $SavedPort = ((Get-Content -LiteralPath (Join-Path $Dir 'docker.env') | Where-Object { $_ -like 'TERNELA_PORT=*' }) -split '=',2)[1]
            Write-Output "Ternela: http://127.0.0.1:$SavedPort`nFolder: $Dir`nManage: & `"$Dir\ternela.ps1`" status"
        }
        'stop' { Invoke-Compose stop }
        'status' { Invoke-Compose ps; Invoke-Control status }
        'logs' { Invoke-Compose logs --tail 100 }
        'update' { Invoke-Control update }
        'updates' { if (-not $Choice) { throw 'Use updates on or updates off' }; Invoke-Control updates $Choice }
        { $_ -in @('backup','restore') } {
            if ($Command -eq 'restore') {
                if (-not $File -or -not (Test-Path -LiteralPath $File -PathType Leaf)) { throw 'Use restore -File PATH with an existing backup' }
                Assert-NotLink $File
            }
            $WasRunning = @(Invoke-Compose ps --status running --quiet)
            Invoke-Compose stop
            try {
                Save-Backup
                if ($Command -eq 'restore') { Invoke-Transfer 'restore' $File }
            } finally { if ($WasRunning.Count) { Invoke-Compose up --detach --no-build --wait --wait-timeout 120 } }
        }
        'remove' {
            Invoke-Compose down
            if ($Purge) {
                $ImageId = @(Invoke-Docker image ls --quiet --filter "reference=ternela-local:$Identity")
                if ($ImageId.Count) { Invoke-Docker image rm "ternela-local:$Identity" }
                if ($VolumeExists.Count) { Invoke-Docker volume rm $Volume }
                # Windows refuses to remove a process's current working directory.
                $ParentDir = Split-Path -Parent $Dir
                $CurrentDir = (Get-Location).Path
                if ($CurrentDir -eq $Dir -or $CurrentDir.StartsWith($Dir + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { Set-Location -LiteralPath $ParentDir }
                $ProcessDir = [Environment]::CurrentDirectory
                if ($ProcessDir -eq $Dir -or $ProcessDir.StartsWith($Dir + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { [Environment]::CurrentDirectory = $ParentDir }
                [IO.Directory]::Delete($Dir, $true)
                Write-Output 'Removed the container, live volume, local image and marked install folder.'
            } else { Write-Output 'Container removed. Live volume, folder and image retained; start restores the app. Use remove -Purge to delete them.' }
        }
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
