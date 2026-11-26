# Android Lab

A Windows-native test runner and local Android device broker for developers and coding agents. One planner and policy implementation serve both the CLI and local MCP. Host tests and emulator tests are the primary routes. Physical fallback is conditional on capability qualification and explicit owner enrollment.

## Install on Windows

You need PowerShell 7, Python 3.10+, a project-compatible JDK, Android SDK Platform Tools and Build Tools, and an installed AVD for runtime tests. Enable Windows emulator acceleration separately through the normal Android SDK setup. Existing SDKs and AVDs are reused.

```powershell
git clone https://github.com/gthgomez/AndroidLab.git
cd AndroidLab
.\scripts\Install-AndroidLab.ps1 -Sdk $env:ANDROID_HOME
android-lab doctor
android-lab devices list
```

Use `-AvdHome` for a custom AVD store. For a terminal that was open before installation, re-open it or run the installed shim directly under `%LOCALAPPDATA%\AndroidLab\install`. No third-party Python packages are required. Optional `scripts/Install-Scrcpy.ps1` verifies and installs the pinned Windows release without running a device session.

## Test a project

```powershell
android-lab project inspect .
android-lab plan --project . --suite unit
android-lab run --project . --suite unit
android-lab plan --project . --suite ui
android-lab run --project . --suite ui
android-lab report --last
```

The defaults use the recognized app module and the debug variant. The unit suite runs the variant's Gradle unit tests. The UI suite stages a universal APK and verifies launch and placement. To verify behavior, declare independent assertions. The instrumentation suite builds the app and test APKs separately and validates the instrumentation target and signer before it uses a single leased transport. It does not run Gradle's multi-device connected task.

Use `android-lab.json` when a project has multiple variants or needs assertions. For a prebuilt APK, set `prebuilt` and specify an APK within the project. Artifact metadata and signature take precedence over the project hint. Example with a standard debug build.

```json
{
  "module": ":app",
  "variant": "debug",
  "suites": {
    "ui": {
      "kind": "device",
      "timeout": 300,
      "steps": [
        {"op": "assert_display"},
        {"op": "assert_text", "text": "Welcome"},
        {"op": "tap", "text": "Continue"},
        {"op": "assert_text", "text": "Home"},
        {"op": "screenshot"}
      ]
    }
  }
}
```

The broker supports exploratory `start`, `step` and session cancellation. Declare the required operations before starting. Pass the returned one-time session token to CLI steps through `ANDROID_LAB_SESSION_TOKEN` or use the MCP tool schema. Store the token in memory. `sessions list`, `sessions cancel SESSION`, `sessions recover` and `devices revoke ALIAS` are owner controls. A completed launch with no independent functional assertion reports `functional: unverified`.

## Capabilities

| Route | Scope |
|---|---|
| Host | Variant Gradle unit suite, local build log, fresh JUnit evidence when present |
| Emulator | Managed no-window AVD or explicitly borrowable live AVD, scoped UI hierarchy and screenshots, exact text assertions, touch/swipe/basic Latin input, targeted instrumentation |
| Dedicated physical device | Eligible through a local owner enrollment and independent operation qualification |
| Personal secondary display | Experimental integration with scrcpy. Owner approval and independent device qualification required. Launch checks and scoped coordinate touch/swipe are candidate operations. Keyboard, UI tree, screenshots and instrumentation are unavailable on the personal lane. |
| Cloud | Public source and host or emulator testing on a worker's own SDK. No remote dispatch to a personal device. |

The default state directory is `%LOCALAPPDATA%\AndroidLab`. Do not commit local policies, device evidence or screenshots. See [architecture](docs/architecture.md), [security and trust boundaries](SECURITY.md), [physical qualification](docs/qualification.md), [upstream review](docs/upstream.md), [agent integration](docs/agents.md), and the [synthetic canary fixture](fixtures/README.md).

An owner can add private project overrides in the local configuration under `projects`. Use the fully resolved project directory, normalized to lowercase with Windows path separators, as the key. The entry uses the same manifest fields. This is useful for existing flavors and prebuilt APK workflows without editing another repository.

## Verify and uninstall

```powershell
.\scripts\Test-Release.ps1
python fixtures/build.py --sdk $env:ANDROID_HOME
android-lab run --project fixtures --suite ui
android-lab run --project fixtures --suite instrumentation
android-lab run --project fixtures --suite negative
```

The negative suite must return a functional failure with successful cleanup. Run `scripts/Install-AndroidLab.ps1 -Uninstall` from the same install source to remove the CLI and its PATH entry. Remove agent registration with the corresponding agent command. Local evidence and SDKs are preserved. [Apache-2.0](LICENSE)
