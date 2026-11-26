"""Rehearse paired-canary placement and gestures on a session-owned AVD."""
from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from android_lab.artifact import read_apk
from android_lab.broker import Broker
from android_lab.common import LabError, json_write
from android_lab.config import Config
from android_lab.device import DeviceSession
from android_lab.planner import choose
from android_lab.platform import host_sample
from evaluate import evaluate

PACKAGES = {
    "primary": "com.androidlab.canary.primary.lab",
    "automation": "com.androidlab.canary.automation.lab",
}


def observations(work, role):
    package = PACKAGES[role]
    raw = work.shell("run-as", package, "cat", "files/canary-events.jsonl")
    if len(raw.encode()) > 65536:
        raise LabError("log_limit", "Synthetic evidence exceeded its bound")
    (work.directory / (role + ".jsonl")).write_text(raw, encoding="utf-8")
    return [json.loads(line) for line in raw.splitlines()]


def rehearse(avd_name):
    broker = Broker(Config.load())
    platform = broker.platform
    platform.scrcpy_identity()
    inventory, _ = broker.inventory()
    inventory = [d for d in inventory if d["kind"] == "avd" and d["name"] == avd_name]
    requested = {"kind": "device", "requires": ["launch", "placement", "tap", "swipe"],
                 "prebuilt": True, "min_sdk": 34, "timeout": 720}
    plan = choose(requested, inventory, host_sample(broker.config, device=True), broker.state.leased(), broker.config.budget)
    if plan["route"] != "managed_emulator":
        raise LabError("no_route", " | ".join(plan["reasons"]))
    avd = plan["device"]
    data = {"project": "paired-canary", "suite": "rehearse", "route": "managed_emulator", "required": [],
            "plan": broker.describe_plan(plan), "execution": "started", "functional": "unverified",
            "safety": "emulator_only", "evidence": "partial", "cleanup": "pending"}
    session, _ = broker.state.acquire(avd["resources"], requested["timeout"], data)
    directory = broker.directory(session)
    directory.mkdir(parents=True, exist_ok=True)
    result = {"passed": False}
    marker = secrets.token_hex(12)
    with broker.state.operation(session):
        try:
            artifacts = {}
            paths = {}
            for role, package in PACKAGES.items():
                paths[role] = directory / ("app.apk" if role == "primary" else "test.apk")
                artifact = read_apk(ROOT / "fixtures/build" / (role + ".apk"), broker.config, stage=paths[role])
                if artifact["package"] != package or artifact["permissions"] or artifact["instrumentation"]:
                    raise LabError("invalid_fixture", "Expected the permission-free synthetic canary")
                artifacts[role] = artifact
            device = broker.boot_avd(session, avd, data, directory)
            data.update(device=device, boot=platform.boot_identity(device), display=0, artifact=artifacts["primary"])
            work = DeviceSession(platform, broker.state, session, data, directory)
            work.save()
            size = re.search(r"Physical size: (\d+)x(\d+)", work.shell("wm", "size"))
            if not size:
                raise LabError("unknown_size", "AVD did not identify display dimensions")
            x_center, y_center = int(size[1]) // 2, int(size[2]) // 2
            work.install(artifacts["primary"], paths["primary"])
            work.launch(marker=marker)
            primary_tap = ["input", "-d", "0", "tap", str(x_center), str(y_center)]
            work.placement()
            work.shell(*primary_tap, timeout=12)
            work.install(artifacts["automation"], paths["automation"])
            virtual = DeviceSession(platform, broker.state, session, data, directory, rehearsal=True)
            virtual.start_secondary(180)
            data["artifact"] = artifacts["automation"]
            virtual.launch(marker=marker)
            virtual.action({"op": "tap", "x": 250, "y": 375})
            # Exercise the two displays interleaved. The primary swipe/tap is allowed only on this AVD.
            work.shell(*primary_tap, timeout=12)
            virtual.action({"op": "swipe", "x1": 250, "y1": 480, "x2": 250, "y2": 620, "ms": 250})
            work.shell(*primary_tap, timeout=12)
            result = evaluate(observations(work, "primary"), observations(virtual, "automation"), marker, virtual.display)
            data.update(execution="finished", functional="passed" if result["passed"] else "failed", evidence="complete")
            broker.state.event(session, "paired_canary", **result)
        except LabError as err:
            broker.record_error(session, data, err)
        except KeyboardInterrupt:
            broker.record_error(session, data, LabError("cancelled", "Operator cancelled the rehearsal", uncertain=True))
        except (OSError, ValueError) as err:
            broker.record_error(session, data, LabError("rehearsal_error", type(err).__name__, uncertain=True))
        finally:
            broker.state.update(session, data)
            broker.cleanup_locked(session, data)
    report = broker.report(session)
    report["rehearsal"] = result
    json_write(directory / "rehearsal.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--avd", required=True, help="The AVD to provision exclusively for this rehearsal")
    args = parser.parse_args()
    try:
        report = rehearse(args.avd)
    except LabError as err:
        print(json.dumps({"error": err.code, "message": str(err)}))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["rehearsal"]["passed"] and report["cleanup"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
