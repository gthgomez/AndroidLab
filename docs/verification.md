# Verification

## Run the checks

```powershell
.\scripts\Test-Release.ps1
python fixtures/build.py --sdk $env:ANDROID_HOME
android-lab devices list
android-lab run --project fixtures --suite ui
android-lab run --project fixtures --suite instrumentation
android-lab run --project fixtures --suite negative
python fixtures/rehearse.py --avd AVD_NAME
```

The negative UI suite must fail the false text assertion and still complete cleanup. The paired-display rehearsal needs the pinned scrcpy installer and a disposable, sufficiently resourced Android 14+ AVD.

## Evidence classes

- Host regressions simulate inventories and resource pressure. They cover available and unavailable emulators, qualified and unqualified fallback, partial capabilities, concurrent leases, stale sessions, revocation, ownership, invalidated firmware/adapter evidence, and CLI/MCP parity.
- The synthetic evaluator independent of the broker checks the primary and automation apps' own event streams. Negative controls cover wrong placement, stale markers, focus loss, missing concurrent input, and text mismatch.
- The managed AOSP Android 14 UI run passed display placement, screen marker, tap, verified counter change, relaunch, persistence and screenshot. Cleanup completed.
- The stock scrcpy 5.0.1 paired rehearsal on that AVD detected real primary focus loss during secondary app launch and swipe. Both packages reported the correct display and intended gestures. That result blocks the personal-device trial with this backend.
- Headroid v0.1.0a5 passed the same touch rehearsal on the same AVD. The primary retained focus and received input throughout the interleaved gestures. A separate Android 16 AVD attempt stopped at the lock-state gate before any display comparison.
- Real host journeys ran MonteCarloLedger (447 tests), RGRemote (108), and PrismLocal devDebug (504) through the Windows broker. Their JUnit results had zero failures. These results establish host testing, not real-network discovery or native inference.
- The Windows installer was exercised in a separate empty state directory, including uninstall. A fresh Codex App Server thread invoked the installed MCP doctor and planner. Cursor discovered the registered tools. Babel registration uses its supported local registry.
- Qualification requires a separate operator-reviewed record on the exact physical firmware/transport. Rehearsals and simulated inventories cannot create that record.

Every report records the route, functional outcome, safety status, evidence status and cleanup separately. Keep raw APKs, local configurations, device evidence and personal execution traces outside version control.
