from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .artifact import read_apk
from .broker import Broker
from .common import LabError, json_read, json_write
from .config import Config


def parser():
    root = argparse.ArgumentParser(prog="android-lab", description="Android test planning and local device sessions")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--home", help="Local state/config directory")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("capabilities")
    commands.add_parser("mcp")
    devices = commands.add_parser("devices").add_subparsers(dest="device_command", required=True)
    devices.add_parser("list")
    for op in ("identity", "revoke"):
        devices.add_parser(op).add_argument("device")
    project = commands.add_parser("project").add_subparsers(dest="project_command", required=True)
    project.add_parser("inspect").add_argument("path", nargs="?", default=".")
    for name in ("plan", "run", "start"):
        sub = commands.add_parser(name)
        sub.add_argument("--project", default=".")
        sub.add_argument("--suite", default="ui")
    sub = commands.add_parser("step")
    sub.add_argument("session")
    sub.add_argument("operation_json")
    sub = commands.add_parser("qualify")
    sub.add_argument("--device", required=True)
    sub.add_argument("--evidence", required=True, help="Local operator-reviewed qualification record")
    sub = commands.add_parser("artifact")
    sub.add_argument("path")
    sessions = commands.add_parser("sessions").add_subparsers(dest="session_command", required=True)
    sessions.add_parser("list")
    sessions.add_parser("recover")
    sessions.add_parser("cancel").add_argument("session")
    sub = commands.add_parser("report")
    target = sub.add_mutually_exclusive_group()
    target.add_argument("--last", action="store_true")
    target.add_argument("--session")
    return root


def qualification(broker, alias, evidence_file):
    import time
    from .planner import EXCLUSIVE, PERSONAL

    config = broker.config
    entries = config.settings.get("devices", {})
    entry = entries.get(alias, {})
    mode = entry.get("mode")
    allowed = PERSONAL if mode == "personal" else EXCLUSIVE if mode == "exclusive" else None
    if allowed is None:
        raise LabError("no_enrollment", "Create a disabled owner enrollment before importing qualification")
    evidence = json_read(Path(evidence_file))
    capabilities = evidence.get("capabilities", [])
    if (not isinstance(capabilities, list) or not capabilities or not set(capabilities) <= allowed
            or evidence.get("passed") is not True or evidence.get("operator_reviewed") is not True
            or not isinstance(evidence.get("checks"), dict)
            or not all(evidence["checks"].get(c) == "passed" for c in capabilities)
            or not all(evidence["checks"].get(c) == "passed" for c in ("artifact_isolation", "cleanup", "stale_session", "disconnect"))
            or (mode == "personal" and not all(evidence["checks"].get(c) == "passed"
                                              for c in ("primary_canary", "placement", "lock", "external_activity")))
            or not time.time() < evidence.get("expires_unix", 0) <= time.time() + 30 * 86400):
        raise LabError("qualification_incomplete", "Require reviewed operation-specific checks, safety checks, and expiry within 30 days")
    identity = device_identity(broker, alias)["identity"]
    if evidence.get("identity") != identity:
        raise LabError("identity_changed", "Qualification must match current firmware, transport, and tool identity")
    entry["qualification"] = evidence
    entry["enabled"] = True
    json_write(config.home / "config.json", config.settings)
    broker.state.accept_qualification(alias)
    return {"device": alias, "capabilities": sorted(capabilities), "evidence": "operator_attested"}


def device_identity(broker, alias):
    policy = broker.config.settings.get("devices", {}).get(alias, {})
    mode = policy.get("mode")
    if mode not in ("personal", "exclusive"):
        raise LabError("no_enrollment", "Configure a disabled device enrollment first")
    for device in broker.platform.devices():
        if device["id"] == alias and device["state"] == "device" and not broker.platform.avd_name(device):
            return {"device": alias, "identity": broker.platform.identity(device, mode)}
    raise LabError("device_unavailable", "Enrolled physical transport is unavailable")


def execute(broker, args):
    command = args.command
    if command == "doctor":
        return broker.doctor()
    if command == "capabilities":
        return broker.devices()["routes"]
    if command == "devices":
        if args.device_command == "list":
            return broker.devices()
        if args.device_command == "identity":
            return device_identity(broker, args.device)
        from .common import json_write
        entries = broker.config.settings.get("devices", {})
        if args.device not in entries:
            raise LabError("no_enrollment", "Device enrollment was not found")
        entries[args.device]["enabled"] = False
        json_write(broker.config.home / "config.json", broker.config.settings)
        return {"device": args.device, "revoked": True}
    if command == "project":
        return broker.project(args.path).describe()
    if command == "plan":
        return broker.describe_plan(broker.plan(broker.project(args.project).suite(args.suite)))
    if command in ("run", "start"):
        return broker.start(args.project, args.suite, interactive=command == "start")
    if command == "step":
        try:
            operation = json.loads(args.operation_json)
        except ValueError as err:
            raise LabError("bad_json", "Operation must be a JSON object") from err
        return broker.step(args.session, os.environ.get("ANDROID_LAB_SESSION_TOKEN"), operation)
    if command == "qualify":
        return qualification(broker, args.device, args.evidence)
    if command == "artifact":
        return read_apk(Path(args.path), broker.config)
    if command == "sessions":
        if args.session_command == "list":
            return broker.state.all()
        if args.session_command == "recover":
            return broker.recover()
        return broker.close(args.session)
    return broker.report(args.session)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        broker = Broker(Config.load(args.home))
        if args.command == "mcp":
            from .mcp import serve
            return serve(broker)
        result = execute(broker, args)
        print(json.dumps(result, indent=2, ensure_ascii=True))
        if args.command in ("run", "start") and isinstance(result, dict) and (result.get("execution") in ("failed", "blocked", "cancelled")
                                         or result.get("cleanup") == "quarantined"):
            return 2
        return 0
    except LabError as err:
        print(json.dumps({"error": {"code": err.code, "message": str(err), "uncertain": err.uncertain}}))
        return 2
    except (KeyError, TypeError, ValueError, OSError) as err:
        print(json.dumps({"error": {"code": "invalid_request", "message": type(err).__name__}}))
        return 2
