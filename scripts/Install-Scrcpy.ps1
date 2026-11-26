$ErrorActionPreference = 'Stop'
$root = if ($env:ANDROID_LAB_HOME) { $env:ANDROID_LAB_HOME } else { Join-Path $env:LOCALAPPDATA 'AndroidLab' }
if (![Environment]::Is64BitOperatingSystem) { throw 'This installer requires 64-bit Windows.' }
$name = 'scrcpy-win64-v5.0.1'
$sha = 'b12a2c4ee8be317422451fc7dcf8ee20a71b5ea7ef9ad73ddd825a25316227a5'
$tools = Join-Path $root 'tools'
New-Item -ItemType Directory -Path $tools -Force | Out-Null
$destination = Join-Path $tools $name
if (Test-Path -LiteralPath $destination) { throw 'The pinned scrcpy folder already exists.' }
$archive = Join-Path $tools "$name.zip"
if (Test-Path -LiteralPath $archive) { throw 'The download target already exists.' }
try {
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/Genymobile/scrcpy/releases/download/v5.0.1/$name.zip" -OutFile $archive
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant() -ne $sha) { throw 'scrcpy archive hash mismatch.' }
    Expand-Archive -LiteralPath $archive -DestinationPath $tools
} finally {
    if (Test-Path -LiteralPath $archive -PathType Leaf) { Remove-Item -LiteralPath $archive }
}
$exe = Join-Path $destination 'scrcpy.exe'
$server = Join-Path $destination 'scrcpy-server'
& $exe --version
if ($LASTEXITCODE -ne 0) { throw "scrcpy version check failed with exit $LASTEXITCODE" }
$code = @'
import sys
from pathlib import Path
from android_lab.common import digest, json_read, json_write
config, client, server = map(Path, sys.argv[1:])
data = json_read(config) if config.is_file() else {}
data["scrcpy"] = {"path": str(client), "client_sha256": digest(client), "server_sha256": digest(server)}
json_write(config, data)
'@
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    & python -c $code (Join-Path $root 'config.json') $exe $server
    if ($LASTEXITCODE -ne 0) { throw "scrcpy configuration failed with exit $LASTEXITCODE" }
} finally {
    Pop-Location
}
