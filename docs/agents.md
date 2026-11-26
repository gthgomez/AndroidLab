# Coding-agent integration

Set Android Lab as the default for runtime Android testing. Ask the broker for a plan, then run the suite or open a scoped exploratory session. A ready emulator takes precedence when it meets the requirements. A qualified physical device is an automatic fallback. Honor blocked operations and preserve the difference between device setup and verified behavior. Keep raw ADB away from unsupported or denied operations. Primary-display interaction on a personal phone needs distinct authorization.

The MCP entry runs the installed Python executable with arguments `-B`, an absolute path to the installed `run_lab.py`, and `mcp`. It shares state and qualification with the CLI.

```powershell
$runner = Join-Path $env:LOCALAPPDATA 'AndroidLab/install/run_lab.py'
$python = (Get-Command python.exe).Source
codex mcp add android-lab -- $python -B $runner mcp
babel mcp add android-lab python $runner mcp
```

For Cursor add one entry at `mcpServers.android-lab` in the user's MCP configuration. Set `command` to the Python executable and `args` to the array described above. Enable that server only. Inspect the entry with `cursor-agent mcp list-tools android-lab`. See the [Cursor MCP guide](https://cursor.com/docs/mcp).

Invoke `lab_doctor` and `lab_plan` in a fresh session as an integration check. Use `lab_run` or `lab_start` and `lab_step`. Use `lab_close` when finished. The owner may stop any session from the CLI. A stdio client can take time to learn the result of a running tool. Use the CLI cancel/recover path if the client exits or a device disconnects.

Remove a registration with `codex mcp remove android-lab`, `babel mcp remove android-lab`, or by removing only the added Cursor entry. Keep unrelated MCP registrations intact.

Host/cloud agents with no personal-device authority can install the runner against their own SDK. The local MCP entry does not bridge cloud agents to personal hardware.
