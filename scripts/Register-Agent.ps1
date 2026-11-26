[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Codex','Cursor','Babel')][string]$Agent,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'AndroidLab\install'),
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$runner = Join-Path $InstallRoot 'run_lab.py'
$receipt = Join-Path $InstallRoot 'install-receipt.json'
if (!(Test-Path -LiteralPath $receipt -PathType Leaf)) { throw 'Install Android Lab before registering its agent server.' }
$python = (Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json).python
switch ($Agent) {
    'Codex' {
        if ($Uninstall) {
            & codex mcp remove android-lab
        } else {
            & codex mcp get android-lab --json 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { throw 'The android-lab Codex entry already exists.' }
            & codex mcp add android-lab -- $python -B $runner mcp
        }
        if ($LASTEXITCODE -ne 0) { throw "Codex registration returned $LASTEXITCODE" }
    }
    'Babel' {
        if ($Uninstall) {
            & babel mcp remove android-lab
        } else {
            & babel mcp add android-lab python $runner mcp
        }
        if ($LASTEXITCODE -ne 0) { throw "Babel registration returned $LASTEXITCODE" }
    }
    'Cursor' {
        $folder = Join-Path $env:USERPROFILE '.cursor'
        $target = Join-Path $folder 'mcp.json'
        $config = if (Test-Path -LiteralPath $target) {
            Get-Content -LiteralPath $target -Raw | ConvertFrom-Json -AsHashtable
        } else {
            @{}
        }
        if (!$config.Contains('mcpServers')) { $config.mcpServers = @{} }
        $entry = $config.mcpServers['android-lab']
        if ($Uninstall) {
            if (!$entry -or $entry.command -ne $python -or $entry.args.Count -ne 3 -or $entry.args[1] -ne $runner) {
                throw 'The Cursor entry does not match this installation.'
            }
            $config.mcpServers.Remove('android-lab')
        } else {
            if ($entry) { throw 'The android-lab Cursor entry already exists.' }
            $config.mcpServers['android-lab'] = @{command=$python; args=@('-B',$runner,'mcp')}
        }
        New-Item -ItemType Directory -Path $folder -Force | Out-Null
        $tmp = Join-Path $folder ('android-lab-' + [Guid]::NewGuid().ToString('N') + '.json')
        try {
            $config | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $tmp -Encoding utf8NoBOM
            [IO.File]::Move($tmp, $target, $true)
        } finally {
            if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp }
        }
    }
}
