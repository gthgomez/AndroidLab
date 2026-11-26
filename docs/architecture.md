# Architecture

Android Lab is a Python modular monolith. Its Windows installer copies Python source and adds a command shim. It uses the installed SDK and JDK. Local agents can also run the same broker over MCP stdio. There is no HTTP listener, dependency on Docker, or bundled ADB server.

| Component | Responsibility |
|---|---|
| Project discovery | Find the nearest Gradle settings, module hints, existing test sources and AGP APK metadata. Resolve the exact package, permissions, instrumentation, signing certificate and native ABIs from the staged APK. |
| Planner | Evaluate requested capabilities, minimum SDK, duration, current host resources and leases. Prefer host tests, a borrowable live AVD, then a managed AVD. Select eligible physical devices when emulation cannot meet the request. |
| State and policy | A local SQLite database owns device and build leases. A per-session OS lock serializes operations across CLI and MCP processes. Cancellation and expiry fence stale sessions. Personal-device policy and artifact approval live outside the project. |
| Supervisor | Stage the APK, bind an ADB transport and boot identity, journal mutations, run scoped steps, verify assertions and reconcile owned resources before releasing leases. |
| Adapters | Gradle wrapper for host tasks, Android SDK tools for emulators and exclusive devices, pinned scrcpy for experimental secondary-display provisioning. |
| Reports | Record routing, source revision, operation boundaries, APK identity, assertions and cleanup. Execution, functional result, safety, evidence and cleanup appear separately. |

## Routing

Hardware, ARM64 and OEM requests exclude standard emulator routes. A local Wi-Fi protocol trial must declare `real_network`. Host resource sampling uses physical memory and commit headroom, CPU load, disk availability, running emulators, and active Java as a conservative signal for outside builds on Windows. The planner reserves incremental emulator RAM and build headroom. The broker serializes its own builds. The configurable defaults are starting estimates for development machines and should be tuned against measured peak usage.

```json
{
  "budget": {
    "reserve_mib": 768,
    "build_mib": 1536,
    "emulator_ram_mib": 2560,
    "emulator_overhead_mib": 640,
    "max_cpu_percent": 80,
    "min_disk_mib": 2048,
    "max_emulators": 1
  },
  "borrow_avds": [],
  "devices": {}
}
```

The managed emulator adapter runs existing profiles headlessly with a read-only base and no snapshots. It validates the console name for the selected port, waits for Android boot completion and stores the boot identity. Set `borrow_avds` to a list of AVD names whose foreground and guest resources may be used. Android Lab does not shut down a borrowed AVD.

## Device boundaries

The personal route requires an owner-enabled record, a matching current firmware and backend identity, verified battery and thermal readings, qualification for every requested operation and the exact approved APK. Scope includes package ID, full APK SHA-256, signer set, declared permissions, instrumentation identity and operations. An approved separate QA package must be absent before installation. An APK can run arbitrary code with its Android privileges, including starting activities and sending network traffic. Approval needs review of behavior and eligibility on an isolated test target.

The secondary adapter uses a fresh nonzero display, binds its logical ID to a live scrcpy process and a framework virtual-display identity, then asks Android to launch the validated component on that display. It checks actual resumed activity placement and repeats checks around operations. It disables audio and scrcpy control, keeps the owner's primary display power settings, and applies the virtual-display IME hide policy. It never takes a primary screenshot for a personal session. A display can be lost between a check and an operation. Live qualification must test OEM behavior, including concurrent focus, locked states and transport failures.

Personal text, back, hierarchy, screenshots, instrumentation and scoped application logs remain unavailable. The current adapter has a bounded virtual-display recording sink. Recording is subject to qualification of what the display shows throughout the session. Personal operation support is experimental until independently qualified on the specific firmware and transport. Exclusive-device use requires an owner-designated dedicated device. A virtual display shares device hardware and services, including CPU, battery and network.

## Ownership, recovery and trust

A run proceeds through planning, lease acquisition, artifact validation, provisioning, actions, and cleanup. Mutations are journaled before execution. A timed-out mutation is uncertain. The supervisor does not repeat it automatically. Release requires verified cleanup; disconnects and unresolved process identity leave a quarantined lease. Owner commands are `sessions cancel`, `sessions recover` and `devices revoke`.

Local same-user shell agents retain access to ADB, APKs and the local state. Android Lab constrains its own interface. ADB device authority in the same account remains a cooperative boundary. Restrict ADB and enrollment files at the OS/account boundary if agents must be treated as hostile. Run Gradle only for trusted local projects. Project scripts are executable build code. Device intent is never inferred from a package suffix alone.

The stdio MCP server is for trusted local clients. Remote workers and authenticated cloud dispatch require separate transport and execution isolation. There is no public ADB listener, remote shell or remote job ingestion in this release.

Read [security](../SECURITY.md), [qualification](qualification.md) and the [upstream audit](upstream.md).
