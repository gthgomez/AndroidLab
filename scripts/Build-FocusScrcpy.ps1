[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = if ($env:ANDROID_LAB_HOME) { $env:ANDROID_LAB_HOME } else { Join-Path $env:LOCALAPPDATA 'AndroidLab' }
$root = [IO.Path]::GetFullPath($root)
$commit = 'a60891aea193d92e7e5c3942700eca63f9d19a5f'
$checksum = '20faa424046b3c47b3251a1e84480d94e9cfe92d1d4dd00d582fc526624269f1'
$repo = Split-Path -Parent $PSScriptRoot
$status = & python (Join-Path $repo 'run_lab.py') doctor | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Android Lab doctor failed.' }
if ($status.host.emulator_count -ne 0 -or $status.host.busy_java -or $status.host.free_mib -lt 3072 -or $status.host.commit_free_mib -lt 3072 -or $status.host.cpu_percent -ge 80) {
    throw 'Focus server build needs an idle build slot and 3 GiB of RAM and commit headroom.'
}
if ($status.scrcpy -ne '5.0.1') { throw 'Install the pinned scrcpy Windows client first.' }
$build = Join-Path $root ('tools/focus-build-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $build -Force | Out-Null
$zip = Join-Path $build 'scrcpy-source.zip'
Invoke-WebRequest -UseBasicParsing -Uri "https://codeload.github.com/Genymobile/scrcpy/zip/$commit" -OutFile $zip -TimeoutSec 45
if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $checksum) {
    throw 'scrcpy source archive digest mismatch.'
}
Expand-Archive -LiteralPath $zip -DestinationPath $build
$source = Join-Path $build "scrcpy-$commit"
$file = Join-Path $source 'server/src/main/java/com/genymobile/scrcpy/video/NewDisplayCapture.java'
$code = [IO.File]::ReadAllText($file)
$anchor = '| VIRTUAL_DISPLAY_FLAG_DEVICE_DISPLAY_GROUP;'
if ([regex]::Matches($code, [regex]::Escape($anchor)).Count -ne 1) { throw 'The pinned source no longer matches the focus patch.' }
$updated = $code.Replace($anchor, '| VIRTUAL_DISPLAY_FLAG_DEVICE_DISPLAY_GROUP' + [Environment]::NewLine + '                            | (1 << 16); // VIRTUAL_DISPLAY_FLAG_STEAL_TOP_FOCUS_DISABLED')
[IO.File]::WriteAllText($file, $updated, (New-Object Text.UTF8Encoding($false)))
$configPath = Join-Path $root 'config.json'
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$sdk = if ($config.sdk) { $config.sdk } else { $env:ANDROID_HOME }
if (!$sdk) { throw 'Configure ANDROID_HOME before building the server.' }
$env:ANDROID_HOME = $sdk
$env:ANDROID_SDK_ROOT = $sdk
$server = Join-Path $source 'server/build/outputs/apk/release/server-release-unsigned.apk'
$gradleArgs = @('-classpath', 'gradle/wrapper/gradle-wrapper.jar', 'org.gradle.wrapper.GradleWrapperMain',
    ':server:assembleRelease', '--no-daemon', '--max-workers=1', '-Dorg.gradle.jvmargs=-Xmx1152m -XX:MaxMetaspaceSize=512m')
Push-Location $source
try {
    & java.exe @gradleArgs
    if ($LASTEXITCODE -ne 0) { throw "Focus server build failed with exit $LASTEXITCODE" }
} finally {
    Pop-Location
}
if (!(Test-Path -LiteralPath $server -PathType Leaf)) { throw 'Compiled scrcpy server artifact missing.' }
$installed = Join-Path $build 'scrcpy-server-focus.jar'
Copy-Item -LiteralPath $server -Destination $installed
$script = @'
import sys
from pathlib import Path
from android_lab.common import digest, json_read, json_write
config, server = map(Path, sys.argv[1:3])
data = json_read(config)
data["scrcpy"].update(server_path=str(server), server_sha256=digest(server), profile="focus")
json_write(config, data)
'@
Push-Location $repo
try {
    & python -c $script $configPath $installed
    if ($LASTEXITCODE -ne 0) { throw 'Focus server configuration failed.' }
} finally {
    Pop-Location
}
Write-Output $installed
