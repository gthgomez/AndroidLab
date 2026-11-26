"""Owner-operated, touch-only qualification trial on an explicitly selected physical phone."""
from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from pathlib import Path

from rehearse import PACKAGES, ROOT, observations
from evaluate import evaluate
from android_lab.artifact import read_apk
from android_lab.broker import Broker
from android_lab.common import LabError, json_write, wait_until
from android_lab.config import Config
from android_lab.device import DeviceSession


def operator_trial(alias, rehearsal_id):
    broker = Broker(Config.load())
    platform = broker.platform
    backend = platform.scrcpy_identity()
    reviewed = broker.state.get(rehearsal_id)
    rehearsal = reviewed["data"]
    checks = next((event for event in reversed(broker.state.events(rehearsal_id))
                   if event["event"] == "paired_canary"), {})
    if (reviewed["status"] != "closed" or rehearsal.get("route") != "managed_emulator"
            or rehearsal.get("cleanup") != "complete" or rehearsal.get("functional") != "passed"
            or backend.get("profile") != "focus" or rehearsal.get("backend") != backend
            or not checks.get("passed")):
        raise LabError("rehearsal_required", "A passing paired AVD rehearsal of the exact focus-protected server is required")

    device = next((row for row in platform.devices(strict=True) if row["id"] == alias and row["state"] == "device"), None)
    if not device or re.fullmatch(r"emulator-\d+", device["serial"]):
        raise LabError("device_unavailable", "The authorized physical transport is unavailable")
    identity = platform.identity(device, "personal")
    device.update(kind="personal", api=identity["api"], identity=identity,
                  resources=["physical:" + identity["device_identity"]])
    if not platform.health(device, {"min_battery": 25, "max_thermal": 1}):
        raise LabError("device_health", "Battery or thermal state cannot safely admit the trial")
    if platform.adb("shell", "am", "get-current-user", device=device).strip() != "0":
        raise LabError("unsupported_user", "The trial requires Android user zero")

    marker = secrets.token_hex(12)
    data = {"project": "paired-canary", "suite": "operator-trial", "route": "personal", "required": [],
            "planned_device": alias, "device": device, "boot": platform.boot_identity(device), "backend": backend,
            "display": 0, "execution": "started", "functional": "unverified", "safety": "unverified",
            "evidence": "partial", "cleanup": "pending"}
    session, _ = broker.state.acquire(device["resources"], 180, data)
    directory = broker.directory(session)
    directory.mkdir(parents=True, exist_ok=True)
    work = DeviceSession(platform, broker.state, session, data, directory)
    result = {"passed": False}
    print(f"Trial session {session}", flush=True)
    with broker.state.operation(session):
        try:
            artifacts = {}
            paths = {}
            for role, package in PACKAGES.items():
                paths[role] = directory / ("app.apk" if role == "primary" else "test.apk")
                artifact = read_apk(ROOT / "fixtures/build" / (role + ".apk"), broker.config, stage=paths[role])
                if (artifact["package"] != package or artifact["permissions"] or artifact["instrumentation"]
                        or artifact["sha256"] != rehearsal.get("fixture_hashes", {}).get(role)):
                    raise LabError("invalid_fixture", "Trial APK differs from the independently observed rehearsal")
                artifacts[role] = artifact
            work.check_transport()
            work.check_lock()
            work.install(artifacts["primary"], paths["primary"])
            work.install(artifacts["automation"], paths["automation"])
            data["artifact"] = artifacts["automation"]
            print("Canaries installed. Open Android Lab Primary and tap Increment repeatedly until asked to stop.", flush=True)

            def primary_taps():
                if not work.installed(PACKAGES["primary"]):
                    raise LabError("fixture_removed", "Primary canary disappeared")
                code, output = work.shell("run-as", PACKAGES["primary"], "cat", "files/canary-events.jsonl", check=False)
                if code:
                    return 0
                if len(output.encode()) > 65536:
                    raise LabError("log_limit", "Synthetic evidence exceeded its bound")
                rows = [json.loads(line) for line in output.splitlines()]
                return sum(row.get("event") == "tap" and row.get("display") == 0 and row.get("marker") == "UNSET"
                           for row in rows)

            wait_until(primary_taps, 60, "Owner primary-canary input was not observed", work.cancelled)
            work.check_lock()
            work.start_secondary(80)
            work.launch(marker=marker)
            work.action({"op": "tap", "x": 250, "y": 375})
            previous = primary_taps()
            wait_until(lambda: primary_taps() > previous, 12, "Concurrent owner tap was not observed", work.cancelled)
            work.action({"op": "swipe", "x1": 250, "y1": 480, "x2": 250, "y2": 620, "ms": 250})
            previous = primary_taps()
            wait_until(lambda: primary_taps() > previous, 12, "Owner follow-up tap was not observed", work.cancelled)
            result = evaluate(observations(work, "primary"), observations(work, "automation"), marker, work.display,
                              fresh_operator_launch=True)
            data.update(execution="finished", functional="passed" if result["passed"] else "failed",
                        safety="paired_touch_observed" if result["passed"] else "failed", evidence="complete")
            if not result["passed"]:
                for resource in device["resources"]:
                    broker.state.invalidate(resource)
            broker.state.event(session, "paired_canary", **result)
            print("Primary input observation complete. The broker is removing the two synthetic canaries.", flush=True)
        except LabError as err:
            broker.record_error(session, data, err)
        except KeyboardInterrupt:
            broker.record_error(session, data, LabError("cancelled", "Owner cancelled the trial", uncertain=True))
        except (OSError, ValueError) as err:
            broker.record_error(session, data, LabError("canary_error", type(err).__name__, uncertain=True))
        finally:
            broker.state.update(session, data)
            broker.cleanup_locked(session, data)
    report = broker.report(session) | {"trial": result}
    json_write(directory / "trial.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True, help="The explicitly authorized physical alias")
    parser.add_argument("--rehearsal", required=True, help="The passing local AVD rehearsal session ID")
    parser.add_argument("--supervised", action="store_true", required=True, help="Confirm the owner is present and has chosen this trial window")
    args = parser.parse_args()
    try:
        report = operator_trial(args.device, args.rehearsal)
    except LabError as err:
        print(json.dumps({"error": err.code, "message": str(err)}))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["trial"]["passed"] and report["cleanup"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
