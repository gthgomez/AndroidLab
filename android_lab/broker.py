from __future__ import annotations

import os
import signal
import time

from .artifact import authorize, read_apk
from .common import LabError, json_write, run, wait_until
from .device import DeviceSession, display_counters, display_identity, process_identity
from .planner import choose
from .platform import Platform, host_sample
from .project import ACTIONS, ACTION_CAP, Project
from .state import State


class Broker:
    def __init__(self, config, platform=None):
        self.config = config
        self.state = State(config.home)
        self.platform = platform or Platform(config)

    def directory(self, session):
        return self.config.home / "runs" / session

    def doctor(self):
        checks = {"broker": "ready", "runtime": "Windows" if os.name == "nt" else os.name}
        for key, folder, command in (("adb", "platform-tools", "adb"), ("emulator", "emulator", "emulator")):
            try:
                self.config.executable(folder, command)
                checks[key] = "available"
            except LabError as err:
                checks[key] = str(err)
        try:
            checks["scrcpy"] = self.platform.scrcpy_identity()["version"]
        except LabError as err:
            checks["scrcpy"] = str(err)
        checks["host"] = host_sample(self.config, device=True)
        return checks

    def inventory(self):
        inventory, presence = self.platform.inventory()
        invalidated = self.state.invalidated()
        for route in inventory:
            if route["id"] in invalidated:
                route.update(ready=False, capabilities=[])
        for row in presence:
            if row["id"] in invalidated:
                row.update(qualified=False, reason="Previous safety or recovery failure invalidated qualification")
        return inventory, presence

    def devices(self):
        inventory, presence = self.inventory()
        return {"devices": presence, "routes": [self.public_device(d) for d in inventory]}

    @staticmethod
    def public_device(device):
        if device is None:
            return None
        return {k: device[k] for k in ("id", "kind", "api", "ready", "capabilities", "max_seconds") if k in device}

    def plan(self, suite):
        inventory, presence = self.inventory() if suite["kind"] == "device" else ([], [])
        host = dict(host_sample(self.config, device=suite["kind"] == "device"))
        host["emulator_count"] = max(host.get("emulator_count", 0), len({d["id"] for d in inventory if d["kind"] == "emulator"}))
        result = choose(suite, inventory, host, self.state.leased(), self.config.budget)
        if result["route"] == "blocked":
            for device in presence:
                if device.get("reason"):
                    result["reasons"].append(f"{device['id']}: {device['reason']}")
        return result

    def describe_plan(self, plan):
        return {**plan, "device": self.public_device(plan["device"])}

    def project(self, path):
        project = Project.discover(path)
        local = self.config.settings.get("projects", {}).get(os.path.normcase(str(project.root)))
        if local:
            project.manifest = {**project.manifest, **local,
                                "suites": {**project.manifest.get("suites", {}), **local.get("suites", {})}}
        return project

    def start(self, path, name, *, interactive=False):
        project = Project.discover(path)
        suite = project.suite(name)
        plan = self.plan(suite)
        route, device = plan["route"], plan["device"]
        resources = device["resources"].copy() if device else []
        if route == "host" or (device and not suite.get("prebuilt", False)):
            resources.append("build")
        data = {"project": project.root.name, "revision": project.revision(), "suite": name, "route": route,
                "plan": self.describe_plan(plan), "required": suite["requires"], "building": False,
                "execution": "started", "functional": "unverified", "safety": "unverified",
                "evidence": "partial", "cleanup": "pending"}
        try:
            session, token = self.state.acquire(resources, suite["timeout"], data)
        except LabError as err:
            if err.code != "resource_busy":
                raise
            session, token = self.state.acquire([], suite["timeout"], data)
            return self.blocked(session, data, "A resource changed ownership during admission")
        directory = self.directory(session)
        directory.mkdir(parents=True, exist_ok=True)
        self.state.event(session, "planned", plan=data["plan"])
        if route == "blocked" or (interactive and (route == "host" or "instrumentation" in suite["requires"])):
            return self.blocked(session, data, "No suitable runtime for this suite and session mode")
        with self.state.operation(session):
            try:
                self.prepare(session, project, suite, route, device, data, directory)
                if route == "host":
                    data.update(execution="finished", safety="host_only")
                elif interactive:
                    data["execution"] = "ready"
                    self.state.update(session, data)
                    return self.report(session) | {"session_token": token}
                elif "instrumentation" in suite["requires"]:
                    work = DeviceSession(self.platform, self.state, session, data, directory)
                    remaining = max(1, int(self.state.get(session)["expires"] - time.time()))
                    work.instrumentation(data["test_artifact"], remaining)
                    data.update(execution="finished", functional="passed", evidence="complete")
                else:
                    for step in suite["steps"]:
                        self.step_locked(session, data, step)
                    asserted = any(e.get("assertion") is True and e.get("operation") in ("assert_text", "assert_absent")
                                   for e in self.state.events(session))
                    data.update(execution="finished", functional="passed" if asserted else "unverified",
                                evidence="complete" if asserted else "partial")
            except LabError as err:
                self.record_error(session, data, err)
            except KeyboardInterrupt:
                self.record_error(session, data, LabError("cancelled", "Operator interrupted the run", uncertain=True))
            except Exception as err:
                self.record_error(session, data, LabError("internal_error", type(err).__name__, uncertain=True))
            finally:
                self.state.update(session, data)
                if data["execution"] != "ready":
                    self.cleanup_locked(session, data)
        return self.report(session)

    def blocked(self, session, data, reason):
        data.update(execution="blocked", error={"code": "no_route", "message": reason})
        self.state.event(session, "blocked", reason=reason)
        self.cleanup_locked(session, data)
        return self.report(session)

    def prepare(self, session, project, suite, route, device, data, directory):
        cancelled = lambda: self.state.cancelled(session)
        if cancelled():
            raise LabError("cancelled", "Session expired before preparation")
        if route == "host" or not suite.get("prebuilt", False):
            data["building"] = True
            self.state.update(session, data)
            self.state.event(session, "operation_started", operation="build")
            tasks = [suite["task"]]
            if "instrumentation" in suite["requires"]:
                title = suite["variant"][0].upper() + suite["variant"][1:]
                tasks.append(f"{suite['module']}:assemble{title}AndroidTest")
            started = time.time()
            try:
                project.gradle(tasks, directory / "gradle.log", suite["timeout"], self.config.settings, cancelled)
            finally:
                data["building"] = False
                self.state.update(session, data)
                self.state.release_resource(session, "build")
            self.state.event(session, "operation_finished", operation="build")
            if route == "host":
                self.host_assertions(session, project, suite, started, data)
                return
        if cancelled():
            raise LabError("cancelled", "Session expired before APK validation")
        staged = directory / "app.apk"
        artifact = read_apk(project.apk(suite), self.config, stage=staged)
        policy = self.config.settings.get("devices", {}).get(device["id"], {})
        authorize(artifact, route, policy, suite["requires"])
        data["artifact"] = artifact
        test = None
        test_path = directory / "test.apk"
        if "instrumentation" in suite["requires"]:
            test = read_apk(project.apk(suite, test=True), self.config, stage=test_path)
            if (len(test["instrumentation"]) != 1 or test["instrumentation"][0]["target"] != artifact["package"]
                    or test["package"] == artifact["package"] or test["signers"] != artifact["signers"]):
                raise LabError("test_target_mismatch", "Test APK must target the approved app and use the same signer")
            authorize(test, route, policy, suite["requires"])
            data["test_artifact"] = test
        if route == "managed_emulator":
            device = self.boot_avd(session, device, data, directory)
        else:
            self.platform.find(device)
            if route in ("personal", "exclusive"):
                if self.platform.identity(device, route) != device["identity"] or not self.platform.health(device, policy):
                    raise LabError("qualification_changed", "Device identity or health changed after planning")
        if route in ("personal", "exclusive") and self.platform.adb("shell", "am", "get-current-user", device=device).strip() != "0":
            raise LabError("unsupported_user", "Physical-device operations require Android user zero")
        data.update(device=device, boot=self.platform.boot_identity(device), display=0)
        minimum = max(artifact["min_sdk"], test["min_sdk"] if test else 1)
        if minimum > device["api"]:
            raise LabError("sdk_too_old", "APK requires a newer Android SDK")
        abis = self.platform.prop(device, "ro.product.cpu.abilist").split(",")
        if any(a["abis"] and not set(a["abis"]).intersection(abis) for a in (artifact, test) if a):
            raise LabError("unsupported_abi", "APK native ABI does not match the selected runtime")
        self.state.update(session, data)
        work = DeviceSession(self.platform, self.state, session, data, directory)
        if route == "personal":
            remaining = int(self.state.get(session)["expires"] - time.time())
            if remaining < 10:
                raise LabError("timeout", "Insufficient time to provision the display")
            work.start_secondary(remaining)
        if cancelled():
            raise LabError("cancelled", "Session expired before app installation")
        work.install(artifact, staged)
        if test:
            work.install(test, test_path)
        else:
            work.launch()
        data["safety"] = ("qualification_matched" if route == "personal" else "isolated_emulator"
                          if route in ("emulator", "managed_emulator") else "exclusive_device")

    def host_assertions(self, session, project, suite, started, data):
        import xml.etree.ElementTree as ET
        from .common import within

        folder = within(project.root, suite["module"][1:].replace(":", "/")) / "build/test-results" / suite["task"].split(":")[-1]
        counts = {"tests": 0, "errors": 0, "failures": 0, "skipped": 0}
        for file in folder.glob("TEST-*.xml"):
            if file.stat().st_mtime < started - 1:
                continue
            report = ET.parse(file).getroot()
            for key in counts:
                counts[key] += int(report.get(key, "0"))
        self.state.event(session, "host_assertions", **counts)
        if counts["errors"] or counts["failures"]:
            raise LabError("assertion_failed", "Host tests reported errors or failures")
        if counts["tests"] > counts["skipped"]:
            data.update(functional="passed", evidence="complete")

    def boot_avd(self, session, avd, data, directory):
        port = self.platform.choose_port()
        serial = f"emulator-{port}"
        data["emulator"] = {"serial": serial, "avd": avd["name"]}
        self.state.update(session, data)
        try:
            child = self.platform.start_avd(avd, port, directory)
        except OSError as err:
            data.pop("emulator")
            self.state.update(session, data)
            raise LabError("emulator_failed", "Could not spawn emulator process") from err
        data["emulator"].update(pid=child.pid, process=process_identity(child.pid))
        self.state.update(session, data)

        def ready():
            if child.poll() not in (None, 0):
                raise LabError("emulator_failed", "Managed emulator exited. See local emulator log.")
            for dev in self.platform.devices():
                if dev["serial"] != serial or dev["state"] != "device":
                    continue
                if self.platform.avd_name(dev) != avd["name"]:
                    raise LabError("emulator_mismatch", "Unexpected AVD at the reserved port", uncertain=True)
                if self.platform.prop(dev, "sys.boot_completed") == "1":
                    return dev | {"kind": "emulator", "api": avd["api"], "name": avd["name"]}
            return None

        dev = wait_until(ready, self.config.budget("boot_timeout", 240), "Managed emulator did not finish booting", lambda: self.state.cancelled(session))
        data["emulator"]["boot"] = self.platform.boot_identity(dev)
        self.state.update(session, data)
        self.state.event(session, "emulator_booted", avd=avd["name"])
        return dev

    def step_locked(self, session, data, step):
        if self.state.cancelled(session):
            raise LabError("stale_session", "Session has expired or was cancelled")
        if not isinstance(step, dict) or step.get("op") not in ACTIONS:
            raise LabError("invalid_operation", "Unknown broker operation")
        operation = step["op"]
        if ACTION_CAP.get(operation, operation) not in data["required"] or (operation == "tap" and "text" in step and "inspect" not in data["required"]):
            raise LabError("capability_denied", "Operation was not authorized in the session plan")
        if data["route"] in ("personal", "exclusive"):
            current = type(self.config).load(self.config.home)
            entry = current.settings.get("devices", {}).get(data["device"]["id"], {})
            evidence = entry.get("qualification", {})
            if (not entry.get("enabled") or evidence.get("identity") != data["device"].get("identity")
                    or evidence.get("expires_unix", 0) <= time.time() or evidence.get("passed") is not True
                    or data["device"]["id"] in self.state.invalidated()
                    or not set(data["required"]).issubset(evidence.get("capabilities", []))):
                raise LabError("device_revoked", "Physical-device qualification was revoked or changed")
            authorize(data["artifact"], data["route"], entry, data["required"])
        self.state.event(session, "operation_started", operation=operation)
        result = DeviceSession(self.platform, self.state, session, data, self.directory(session)).action(step)
        self.state.event(session, "operation_finished", operation=operation, assertion=result.get("assertion", False))
        return result

    def step(self, session, token, step):
        if not isinstance(token, str) or not token:
            raise LabError("invalid_session", "Session token required")
        self.state.get(session, token, active=True)
        with self.state.operation(session):
            data = self.state.get(session, token, active=True)["data"]
            if data["execution"] != "ready":
                raise LabError("invalid_session", "Session has no interactive device")
            try:
                return self.step_locked(session, data, step)
            except LabError as err:
                self.record_error(session, data, err)
                self.state.update(session, data)
                self.cleanup_locked(session, data)
                raise

    def record_error(self, session, data, err):
        data["error"] = {"code": err.code, "message": str(err), "outcome": "uncertain" if err.uncertain else "failed"}
        data["execution"] = "cancelled" if err.code in ("cancelled", "stale_session") else "failed"
        if err.code == "assertion_failed":
            data["functional"] = "failed"
        if err.code in ("primary_display_violation", "wrong_display", "display_lost", "placement_unverified"):
            data["safety"] = "failed" if err.code == "primary_display_violation" else "unverified"
        if data["route"] == "personal" and data.get("device") and err.code in ("primary_display_violation", "wrong_display", "display_lost", "placement_unverified", "wrong_hierarchy", "transport_lost"):
            self.state.invalidate(data["device"]["id"])
        self.state.event(session, "error", **data["error"])

    def stop_owned_process(self, record):
        if not record.get("pid") or not record.get("process"):
            raise LabError("unknown_process", "Process spawn interrupted before ownership could be verified", uncertain=True)
        if process_identity(record["pid"]) != record["process"]:
            return
        if os.name == "nt":
            run(["taskkill", "/PID", str(record["pid"]), "/T", "/F"], check=False, timeout=10)
        else:
            try:
                os.kill(record["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
        wait_until(lambda: process_identity(record["pid"]) != record["process"], 12, "Owned process could not be stopped")

    def cleanup_locked(self, session, data):
        self.state.stop(session)
        clean = False
        try:
            self.reconcile(session, data)
            clean = True
            data["cleanup"] = "complete"
        except (LabError, OSError) as err:
            data["cleanup"] = "quarantined"
            if data["route"] == "personal" and data.get("device"):
                self.state.invalidate(data["device"]["id"])
            self.state.event(session, "cleanup_failed", reason=str(err) if isinstance(err, LabError) else "Local file cleanup failed")
        self.state.update(session, data)
        self.state.event(session, "cleanup", status=data["cleanup"])
        self.state.release(session, clean)
        self.directory(session).mkdir(parents=True, exist_ok=True)
        json_write(self.directory(session) / "report.json", self.report(session))

    def reconcile(self, session, data):
        if data.get("building"):
            if host_sample(self.config, device=False).get("busy_java", True):
                raise LabError("build_recovery_pending", "Build interrupted; verify Gradle is idle before recovering")
            data["building"] = False
        work = DeviceSession(self.platform, self.state, session, data, self.directory(session))
        secondary = data.get("secondary")
        if secondary:
            self.stop_owned_process(secondary)
        if data.get("packages") or secondary:
            work.cleanup()
        if secondary:
            if secondary.get("pending"):
                gone = lambda: not display_counters(work.layout()).difference(secondary["previous"])
            else:
                gone = lambda: display_identity(work.layout(), work.display) is None
            wait_until(gone, 15, "Secondary display cleanup is unverified")
        emulator = data.get("emulator")
        if emulator:
            connected = [dev for dev in self.platform.devices(strict=True) if dev["serial"] == emulator["serial"]]
            if connected:
                dev = connected[0]
                if (not emulator.get("boot") or self.platform.avd_name(dev) != emulator["avd"]
                        or self.platform.boot_identity(dev) != emulator["boot"]):
                    raise LabError("emulator_recovery_pending", "Emulator at reserved port cannot be attributed safely")
                exe = self.config.executable("platform-tools", "adb")
                run([exe, "-s", emulator["serial"], "emu", "kill"], env=self.config.tool_env, timeout=10)
            self.stop_owned_process(emulator)
            wait_until(lambda: all(dev["serial"] != emulator["serial"] for dev in self.platform.devices(strict=True)),
                       15, "Managed emulator still appears in ADB inventory")
        for name in ("app.apk", "test.apk", "private-display.mkv"):
            (self.directory(session) / name).unlink(missing_ok=True)

    def close(self, session, token=None):
        row = self.state.get(session, token)
        if row["status"] == "closed":
            return self.report(session)
        self.state.stop(session)
        with self.state.operation(session):
            row = self.state.get(session, token)
            if row["status"] == "closed":
                return self.report(session)
            data = row["data"]
            if data["execution"] in ("started", "ready"):
                data["execution"] = "cancelled"
            self.cleanup_locked(session, data)
        return self.report(session)

    def recover(self):
        results = []
        for row in self.state.all():
            if row["status"] == "quarantined" or (row["status"] == "active" and (row["cancelled"] or row["expires"] <= time.time())):
                try:
                    results.append(self.close(row["id"]))
                except LabError as err:
                    results.append({"session": row["id"], "status": err.code})
        return results

    def report(self, session=None):
        if not session:
            rows = self.state.all()
            if not rows:
                raise LabError("no_reports", "No session evidence is available")
            session = rows[0]["id"]
        row = self.state.get(session)
        fields = ("project", "revision", "suite", "plan", "artifact", "test_artifact", "execution",
                  "functional", "safety", "evidence", "cleanup", "error")
        data = row["data"]
        return {"session": session, "state": row["status"], "expires": row["expires"],
                "evidence_directory": str(self.directory(session)), **{k: data[k] for k in fields if k in data},
                "events": self.state.events(session)}
