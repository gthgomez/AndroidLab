# Security model

Report sensitive findings privately through the repository's security contact or GitHub private vulnerability reporting when available. Use synthetic records and omit device serials, network addresses, APK signing material, tokens and personal screenshots.

Android Lab exposes local stdio tools to the trusted same-user host. It deliberately omits arbitrary ADB, an unrestricted Windows shell, device-wide logs, clipboard manipulation, reboot, global settings and arbitrary package management. The broker validates every requested operation against the authorized session. It will not install over a preexisting package. Personal operations require a matching firmware/backend record and reviewed artifact identity.

Agents with unrestricted access to the same Windows account can use its ADB server directly, change local files, or run executable Gradle build logic. There is no hardened same-user containment. Remote agents should use their own isolated worker and build environment. A deployment that gives a remote caller the local agent stdio transport, the ADB port or host execution authority defeats the security model.

A virtual display shares CPU, GPU, sensors and Android services. Apps can attempt to start other activities or use the network. Exact-artifact review and independently qualified operations are prerequisites. When a display, package, boot or transport cannot be reconciled, the broker blocks the route and keeps ownership quarantined.

Read [architecture](docs/architecture.md) and [qualification](docs/qualification.md).
