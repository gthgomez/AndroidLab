from __future__ import annotations

EMULATOR = frozenset({"launch", "placement", "tap", "swipe", "text", "back", "inspect",
                      "screenshot", "instrumentation"})
PERSONAL = frozenset({"launch", "placement", "tap", "swipe", "video", "real_network", "oem", "native_arm64"})
EXCLUSIVE = EMULATOR | {"exclusive", "real_network", "oem", "native_arm64", "tv"}


def choose(suite, inventory, host, leases, budget):
    reasons = []
    requirements = set(suite["requires"])
    evidence = {"required": sorted(requirements), "host": host, "reasons": reasons}
    building = suite["kind"] == "host" or not suite.get("prebuilt", False)
    reserve = budget("reserve_mib", 768)
    build_need = budget("build_mib", 1536) if building else 0
    max_cpu = budget("max_cpu_percent", 80)

    def result(route, device=None):
        return {"route": route, "device": device, **evidence}

    def host_ok(additional_memory):
        return (
            host.get("free_mib", -1) >= build_need + additional_memory + reserve
            and host.get("commit_free_mib", -1) >= build_need + additional_memory + reserve
            and host.get("cpu_percent", 101) < max_cpu
            and host.get("disk_mib", -1) >= budget("min_disk_mib", 2048)
            and (not building or ("build" not in leases and not host.get("busy_java", True)))
        )

    if suite["kind"] == "host":
        if host_ok(0):
            reasons.append("Host-only suite with capacity for a serialized build")
            return result("host")
        reasons.append("Host build budget unavailable")
        return result("blocked")

    for kind, route in (("emulator", "emulator"), ("avd", "managed_emulator"),
                        ("personal", "personal"), ("exclusive", "exclusive")):
        for item in sorted(inventory, key=lambda d: d["id"]):
            if item["kind"] != kind:
                continue
            label = item["id"]
            if not item.get("ready") or leases.intersection(item["resources"]):
                reasons.append(f"{label}: unavailable, leased, or awaiting recovery")
                continue
            missing = requirements.difference(item["capabilities"])
            if missing:
                reasons.append(f"{label}: missing {', '.join(sorted(missing))}")
                continue
            if item.get("api", 0) < suite["min_sdk"]:
                reasons.append(f"{label}: Android SDK below {suite['min_sdk']}")
                continue
            if suite["timeout"] > item.get("max_seconds", 3600):
                reasons.append(f"{label}: requested duration exceeds device policy")
                continue
            if kind == "avd" and (not host.get("accelerated") or host.get("emulator_count", 1)
                                  >= budget("max_emulators", 1) or "emulator-provision" in leases):
                reasons.append(f"{label}: emulator provisioning or acceleration unavailable")
                continue
            if not host_ok(item["memory_mib"] if kind == "avd" else 0):
                reasons.append(f"{label}: host RAM, commit, CPU, disk, or build budget unavailable")
                continue
            reasons.append(f"{label}: satisfies all requested capabilities and current budgets")
            return result(route, item)
    if not inventory:
        reasons.append("No eligible Android runtime discovered")
    if all(d["kind"] not in ("personal", "exclusive") for d in inventory):
        reasons.append("No enrolled and currently qualified physical-device route")
    return result("blocked")
