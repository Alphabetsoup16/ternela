# SPDX-License-Identifier: Apache-2.0
#requires -Version 5.1
# Filesystem/control probe only. Docker is mocked; no real containers are touched.
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ProbeRoot = Join-Path ([IO.Path]::GetTempPath()) ('ternela-probe-' + [Guid]::NewGuid().ToString('N'))
$InstallDir = Join-Path $ProbeRoot 'install with spaces'
$MockEngine = 'engine-a'
$MockVolumeOwner = ''
$ProbeCalls = New-Object 'System.Collections.Generic.List[object]'
$OriginalPort = [Environment]::GetEnvironmentVariable('TERNELA_PORT', 'Process')
function docker {
    $DockerArguments = @($args)
    if ($DockerArguments[0] -eq '--context') { $DockerArguments = $DockerArguments[2..($DockerArguments.Count - 1)] }
    $global:LASTEXITCODE = 0
    $ProbeCalls.Add([pscustomobject]@{ Arguments = @($DockerArguments); Port = [Environment]::GetEnvironmentVariable('TERNELA_PORT', 'Process') })
    if ($DockerArguments[0] -eq 'context' -and $DockerArguments[1] -eq 'show') { 'desktop-linux' }
    elseif ($DockerArguments[0] -eq 'info') {
        if ($DockerArguments -contains '{{.OSType}}') { 'linux' } else { $MockEngine }
    } elseif ($DockerArguments[0] -eq 'volume' -and $DockerArguments[1] -eq 'ls' -and $MockVolumeOwner) { "ternela-$MockVolumeOwner-state" }
    elseif ($DockerArguments[0] -eq 'volume' -and $DockerArguments[1] -eq 'inspect') { $MockVolumeOwner }
    elseif ($DockerArguments[0] -eq 'volume' -and $DockerArguments[1] -eq 'rm') { $script:MockVolumeOwner = '' }
    elseif ($DockerArguments[0] -eq 'compose' -and $DockerArguments -contains 'up') { $script:MockVolumeOwner = [IO.File]::ReadAllText((Join-Path $InstallDir '.ternela-install')).Trim() }
    elseif ($DockerArguments[0] -eq 'compose' -and $DockerArguments -contains 'version') { 'Mock Compose v2' }
    elseif (($DockerArguments -join ' ') -like '*local_control.py*') { '{"settings":{"automatic_updates":false}}' }
}
try {
    [Environment]::SetEnvironmentVariable('TERNELA_PORT', '9999', 'Process')
    & (Join-Path $RepoRoot 'scripts/ternela.ps1') install -Dir $InstallDir -NoAutoUpdate -Port 8769
    if ($LASTEXITCODE -ne 0) { throw 'Install failed' }
    $EnvFile = Join-Path $InstallDir 'docker.env'
    $Contents = [IO.File]::ReadAllText($EnvFile)
    if ($Contents -notmatch 'TERNELA_AUTO_UPDATE=false' -or $Contents -notmatch 'TERNELA_PORT=8769') { throw 'Saved preferences differ from install arguments' }
    if ([IO.File]::ReadAllBytes($EnvFile)[0] -eq 239) { throw 'BOM in Compose environment file' }
    $ComposeStarts = @($ProbeCalls | Where-Object { $_.Arguments[0] -eq 'compose' -and $_.Arguments -contains 'up' })
    if ($ComposeStarts.Count -ne 1 -or $ComposeStarts[0].Port) { throw 'Parent environment overrode saved configuration' }
    if ([Environment]::GetEnvironmentVariable('TERNELA_PORT', 'Process') -ne '9999') { throw 'Caller environment was not restored' }
    $InstalledScript = Join-Path $InstallDir 'ternela.ps1'
    & $InstalledScript status
    & $InstalledScript updates on
    & $InstalledScript update
    $MockEngine = 'engine-b'
    & $InstalledScript remove -Purge
    if ($LASTEXITCODE -eq 0 -or -not (Test-Path -LiteralPath $InstallDir)) { throw 'Engine mismatch did not protect the install folder' }
    if (@($ProbeCalls | Where-Object { $_.Arguments -contains 'down' }).Count) { throw 'Engine mismatch reached Docker removal' }
    $MockEngine = 'engine-a'
    & $InstalledScript remove
    if (-not (Test-Path -LiteralPath $InstallDir)) { throw 'Normal removal deleted data' }
    Set-Location -LiteralPath $InstallDir
    & $InstalledScript remove -Purge
    if ($LASTEXITCODE -ne 0 -or (Test-Path -LiteralPath $InstallDir)) { throw 'Purge failed' }
    Write-Output 'POWERSHELL_FLOW_PASS: mocked Docker, engine guard, environment restoration and filesystem lifecycle'
} finally {
    [Environment]::SetEnvironmentVariable('TERNELA_PORT', $OriginalPort, 'Process')
    if (Test-Path -LiteralPath $ProbeRoot) {
        Set-Location -LiteralPath ([IO.Path]::GetTempPath())
        [IO.Directory]::Delete($ProbeRoot, $true)
    }
}
