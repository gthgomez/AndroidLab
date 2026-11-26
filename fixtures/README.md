# Android canaries

Build the two app fixtures and their emulator instrumentation with a JDK and the Android SDK. Install Android SDK Platform 33 or newer and a matching stable Build Tools package. From the repository root run the following.

```powershell
python fixtures/build.py --sdk $env:ANDROID_HOME
python -m unittest discover -s fixtures -p test_evaluate.py
android-lab run --project fixtures --suite ui
android-lab run --project fixtures --suite instrumentation
android-lab run --project fixtures --suite negative
```

The last command must fail its deliberately false assertion. The UI journey checks a rendered marker, verifies activity placement, increments an app-private counter, relaunches, and checks the counter again. The instrumentation fixture tests synthetic app-private storage without starting an activity. The simulator and physical qualification results carry different meanings.

Each invocation of the build signs all three APKs with one newly generated fixture key. A rebuild changes signing and hash identities. The key is temporary. The APKs and signatures live in the ignored build directory.

## Paired-display qualification fixture

Use the primary and automation apps together on a dedicated device or inside an explicitly authorized personal-device qualification window. The primary fixture is the only activity whose focus, IME and input are observed. Use synthetic input.

Pass a fresh `lab_marker` string to both fixture activities when launching them. The primary opens on display zero. The automation launch must explicitly target the broker-owned secondary display. Verify that the rendered marker and the reported display match on the automation capture before sending input. Both fixtures record their own events in app-private `files/canary-events.jsonl`. Keep the logs in local evidence. The broker or a dedicated qualification operator may export only these named files from the two fixture identities.

For the basic coexistence journey, touch the primary canary before automation starts. Launch the automation canary, then tap and swipe in it while also tapping the primary canary. Tap the primary again after the final automation gesture. Verify that the primary retains focus throughout the automation interval, with both apps continuing to receive their intended input. Test text separately where the display backend qualifies it. Give each fixture distinct synthetic text, including Unicode where the backend supports it. Export logs before teardown.

```powershell
python fixtures/evaluate.py $primaryLog $automationLog --marker $marker --display $secondaryId
```

Add `--primary-text` and `--automation-text` with the expected synthetic values for the input trial. The evaluator checks same-run display identity, focus and lifecycle observations, input progress, and any supplied text expectations. It emits assertions only. It does not create an enrollment or certify a phone.

For a complete device evaluation, also record independent screenshot marker and input-coordinate readbacks, IME visibility and keyboard effects, rotation and resize, back and relaunch, app storage, external activity and permission handling, idle or lock behavior, disconnect and cancellation, and teardown. Use emulators or dedicated hardware for disruptive controls such as wrong-display launches, conflicting touches, forced lock, or a killed host. Check the device and backend versions against the result before enabling a capability. A successful synthetic run or a recording alone cannot qualify personal-phone operation.
