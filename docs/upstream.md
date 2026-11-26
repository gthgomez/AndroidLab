# Upstream audit

These dispositions come from inspection of upstream documentation, selected source and release metadata. Feature claims are candidates for runtime qualification. This repository does not bundle upstream binaries.

| Component | License and disposition |
|---|---|
| [Android SDK, ADB and Emulator](https://developer.android.com/tools) | External tools under the Android SDK terms. Adopt installed SDK for artifact inspection, transport pinning and AVD lifecycle. |
| [AndroidX Test](https://github.com/android/android-test) | Apache-2.0. Run an existing project's explicitly targeted instrumentation APK on qualified isolated lanes. |
| [Gradle-managed devices](https://developer.android.com/studio/test/managed-devices) | Preserve as a project-native option. Android Lab owns the AVD lease for its own managed route. GMD and ATD rendering and scheduling need separate adapter validation. |
| [Robolectric](https://github.com/robolectric/robolectric) | MIT. Adopt when a project's existing host suite uses it. Shadow behavior cannot establish real hardware fidelity. |
| [Perfetto](https://github.com/google/perfetto) | Apache-2.0. Potential isolated performance adapter. Broad personal-device tracing exceeds the broker's scope. |
| [scrcpy v5.0.1](https://github.com/Genymobile/scrcpy/releases/tag/v5.0.1) | Apache-2.0. External pinned display adapter. The renderer creates a virtual display and headless mode requires a video sink. Explicitly disable control, clipboard automation and audio. Destroy virtual display content on close. |
| [PolyScreen MCP](https://github.com/Shenepoy/polyscreen-mcp) | MPL-2.0. Reference for logical versus physical display IDs and scoped hierarchy. Its raw package and display tools exceed this broker's lease. |
| [Appium UiAutomator2](https://github.com/appium/appium-uiautomator2-driver/blob/v10.4.0/lib/uiautomator2-server/session.ts) | Apache-2.0. Reference for isolated suite integration. Session setup can change hidden API policy, initialize a helper and attempt unlock. Its display selector does not create the display. |
| [Callstack Agent Device](https://github.com/callstack/agent-device) | MIT. Reference for hierarchy and input. The supported operations reviewed do not establish end-to-end secondary-display isolation. |
| [Agent VScreen](https://github.com/kilolonion/agent-vscreen) | MIT. Secondary-display research. It includes primary task handoff, audio controls and privileged onboarding. |
| [scrcpy-skilled](https://github.com/OpenMinis/scrcpy-skilled) | MIT with Apache-2.0 scrcpy. Reference for accessibility. Its phone overlay and service installation affect the primary experience. |
| [ATC](https://github.com/rock3r/atc) | Apache-2.0. Reference for Windows inventory, leasing and scheduling. Lab uses one ownership store to bind leases to exact artifacts and display operations. |
| [Maestro](https://github.com/mobile-dev-inc/maestro) | Apache-2.0. Good isolated flow runner. Device-wide focus and permissions need separate eligibility checks for personal use. |
| [Mobile MCP](https://github.com/mobile-next/mobile-mcp/blob/1.0.20/src/android.ts) | Apache-2.0. Default screen capture fallback, unscoped input and global clipboard typing exceed personal scope. |
| [Mobilewright](https://github.com/mobile-next/mobilewright) | Apache-2.0. Optional isolated suite adapter candidate. Current mobilecli transport leaves device policy to its backend. |
| [ShadowAuto](https://github.com/android-notes/ShadowAuto) | Apache-2.0. ImageReader and hidden display flags inform fixture cases; fallback flags can weaken focus isolation. |
| [Headless Android](https://github.com/HakaI963/headless-android) | Apache-2.0. Candidate for a like-for-like dedicated-device trial. Shizuku and optional APK repackaging add enrollment and signature changes. |
| [Droidance](https://github.com/ybblab/Droidance) | Apache-2.0. Installed-app data access and reported focus loss on some Android versions conflict with the personal-device contract. |
| [Shizuku](https://github.com/RikkaApps/Shizuku) | Apache-2.0. Defer. Additional privileged on-device service and enrollment are unnecessary for the existing Windows SDK. |

The handoff from scrcpy display creation to Android activity placement is verified independently. [Virtual-display behavior](https://github.com/Genymobile/scrcpy/blob/v5.0.1/doc/virtual-display.md) · [App launch restrictions](https://developer.android.com/reference/android/app/ActivityOptions#setLaunchDisplayId(int))

The launcher uses bundled Windows runtime libraries in the externally downloaded scrcpy archive. Review its included notices and upstream licenses. The application runtime itself uses Python's standard library and SQLite. The fixture uses Java and standard Android APIs.
