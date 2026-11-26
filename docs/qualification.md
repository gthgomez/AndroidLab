# Physical-device qualification

Physical-device access starts disabled. An owner must authorize a distinct qualification session. Keep synthetic artifacts and logs under local state. Do not use the primary canary during personal activity. A dedicated device or emulator is the target for deliberately disruptive scenarios.

Use the same paired fixtures, marker, firmware, transport, session dimensions and actions for each backend under comparison. Record the precise backend and SDK versions and compare the event results independently of backend exit codes. Follow the [canary instructions](../fixtures/README.md).

## Required observations

| Check | Observable result |
|---|---|
| Placement and identity | Fresh nonzero display is owned by the active session. Actual activity resumes there. Only the automation marker is visible on captures from that display. |
| Gesture and focus | Both canaries receive their intended gestures while the primary records no extra input or focus loss. Confirm coordinates after resizing or orientation change. |
| Typing and IME | Distinct input is received by each canary and the primary IME remains usable. Run separate ASCII and Unicode trials. |
| External activities and permission dialogs | Secondary code cannot make an unapproved primary surface appear. Exercise deliberately disruptive controls on isolated equipment. |
| Lock and idle | Operations stop when the expected unlocked state becomes unknown. No wake, unlock, power or main display input is issued. |
| Disconnect and crash | Old session ID and transport cannot operate after disconnect. Reconnection reconciles the original resources or retains quarantine. |
| Cancellation, conflicts and expiry | One lease owns the device. Cancellation and timeout prevent later input. Cleanup removes the test app and virtual display only. |
| Artifact isolation | Approval covers exact APK bytes, signing identity, manifest and capabilities. Production app and data remain untouched. |
| Change detection | Firmware, backend hash, SDK/ADB version and transport changes invalidate the applicable outcome. |

The core regression suite uses simulated inventories and deliberately invalid inputs. It can establish routing and policy behavior. Hardware claims require live evidence. Running the fixtures on an emulator establishes synthetic app behavior there.

Owner enrollment is in the local `config.json`. Use the alias from `android-lab devices list`, bind `mode` to `personal` or a verified dedicated `exclusive` device, and set `enabled` to false initially. Specify conservative `max_seconds`, `min_battery`, and `max_thermal`. Collect the current identity with `android-lab devices identity DEVICE_ALIAS`. The explicit operator-only `qualify` command imports an externally reviewed record. It does not perform qualification or permit a live trial on its own.

A local reviewed record contains `identity`, `passed: true`, `operator_reviewed: true`, `expires_unix` within 30 days, `capabilities`, and `checks` keyed by operation with value `passed`. Additional check names are `artifact_isolation`, `cleanup`, `stale_session` and `disconnect`, plus `primary_canary`, `placement`, `lock` and `external_activity` for personal devices. Retain per-check evidence alongside the record and review before import.

Use `android-lab artifact APP.apk` to inspect exact identities. The owner's device entry lists reviewed `artifacts` with fields `package`, `sha256`, `signers`, `permissions`, `instrumentation`, and `allows`. Plans can request an operation. Only owner policy can permit it. Firmware changes and safety failures require review and fresh evidence before re-enabling.

```powershell
android-lab qualify --device DEVICE_ALIAS --evidence .local/qualification.json
android-lab devices revoke DEVICE_ALIAS
android-lab sessions recover
```

Keep enrollment, screenshots, video, logs, device identities and APKs out of public repositories.
