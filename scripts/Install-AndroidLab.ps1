[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'AndroidLab\install'),
    [string]$Sdk,
    [string]$AvdHome,
    [switch]$NoPath,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$stateRoot = if ($env:ANDROID_LAB_HOME) { $env:ANDROID_LAB_HOME } else { Join-Path $env:LOCALAPPDATA 'AndroidLab' }
$receiptPath = Join-Path $InstallRoot 'install-receipt.json'
$source = Split-Path -Parent $PSScriptRoot
$pathItems = @([Environment]::GetEnvironmentVariable('Path', 'User') -split ';' | Where-Object { $_ })
$old = if (Test-Path -LiteralPath $receiptPath) { Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json }
$files = @('run_lab.py', 'android-lab.cmd', 'LICENSE') + @(
    'common','config','project','state','planner','artifact','platform','device','broker','cli','mcp','__init__' |
    ForEach-Object { "android_lab\$_.py" }
)
if ($Uninstall) {
    if (!$old) { throw 'Android Lab installation receipt is missing.' }
    if ($old.addedToPath -and $pathItems -contains $InstallRoot) {
        [Environment]::SetEnvironmentVariable('Path', (($pathItems | Where-Object { $_ -ne $InstallRoot }) -join ';'), 'User')
    }
    foreach ($name in $files) {
        $entry = Join-Path $InstallRoot $name
        if (Test-Path -LiteralPath $entry -PathType Leaf) { Remove-Item -LiteralPath $entry }
    }
    Remove-Item -LiteralPath $receiptPath
    Write-Output "Android Lab command removed. Local data remains in $stateRoot"
    return
}
$python = (Get-Command python.exe -ErrorAction Stop).Source
$version = & $python -c "import sys; print(int(sys.version_info >= (3, 10)))"
if ($LASTEXITCODE -ne 0 -or $version -ne '1') { throw 'Python 3.10 or later is required.' }
if ($python -match '[%"\r\n]' -or $InstallRoot -match '[%"\r\n]') { throw 'Unsupported launcher path.' }
New-Item -ItemType Directory -Path (Join-Path $InstallRoot 'android_lab'),$stateRoot -Force | Out-Null
if (!$old) {
    foreach ($name in $files) {
        if (Test-Path -LiteralPath (Join-Path $InstallRoot $name)) { throw 'Install target contains a file without an Android Lab receipt.' }
    }
}
foreach ($name in $files | Where-Object { $_ -ne 'android-lab.cmd' }) {
    Copy-Item -LiteralPath (Join-Path $source $name) -Destination (Join-Path $InstallRoot $name) -Force
}
$cmd = @"
@echo off
"$python" -B "%~dp0run_lab.py" %*
exit /b %ERRORLEVEL%
"@
Set-Content -LiteralPath (Join-Path $InstallRoot 'android-lab.cmd') -Value $cmd -Encoding ascii
$added = [bool]($old -and $old.addedToPath)
if (!$NoPath -and $pathItems -notcontains $InstallRoot) {
    [Environment]::SetEnvironmentVariable('Path', (($pathItems + $InstallRoot) -join ';'), 'User')
    $added = $true
}
if (!$NoPath -and ($env:Path -split ';') -notcontains $InstallRoot) { $env:Path += ";$InstallRoot" }
$config = Join-Path $stateRoot 'config.json'
if (!(Test-Path -LiteralPath $config)) {
    $settings = @{}
    if ($Sdk) { $settings.sdk = [IO.Path]::GetFullPath($Sdk) }
    if ($AvdHome) { $settings.avd_home = [IO.Path]::GetFullPath($AvdHome) }
    $settings | ConvertTo-Json | Set-Content -LiteralPath $config -Encoding UTF8
}
@{python=$python; addedToPath=$added} | ConvertTo-Json | Set-Content -LiteralPath $receiptPath -Encoding UTF8
& $python -B (Join-Path $InstallRoot 'run_lab.py') --version
if ($LASTEXITCODE -ne 0) { throw "Launcher failed with exit $LASTEXITCODE" }
Write-Output (Join-Path $InstallRoot 'android-lab.cmd')
