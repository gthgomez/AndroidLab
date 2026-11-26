from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .common import LabError, digest, wait_until

RESUMED = re.compile(r"(?:topResumedActivity|mResumedActivity)[:=]\s*ActivityRecord\{[^\n]*?\bu\d+\s+([^\s/]+)/")
DISPLAY_HEADER = re.compile(r"(?m)^\s*Display #(\d+)\s+\(activities")


def resumed_packages(text):
    matches = list(DISPLAY_HEADER.finditer(text))
    return {int(m[1]): sorted(set(RESUMED.findall(text[m.end():(matches[i+1].start() if i+1 < len(matches) else len(text))])))
            for i, m in enumerate(matches)}


def display_identity(text, display_id):
    identities = set()
    for line in text.splitlines():
        if "DisplayInfo{" not in line or "scrcpy" not in line.lower():
            continue
        if not re.search(rf"\bdisplayId[ =]+{display_id}(?:[ ,}}]|$)", line):
            continue
        identity = re.search(r'\buniqueId\s*[=: ]\s*"([^"]+)"', line)
        if identity and "virtual:" in identity[1]:
            identities.add(identity[1])
    return next(iter(identities)) if len(identities) == 1 else None


def display_counters(text):
    return {int(n) for n in re.findall(r"(?m)^\s*Display (\d+):\s*$", text)}


def clean_ui(xml, package):
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as err:
        raise LabError("ui_unavailable", "Could not parse the UI hierarchy") from err
    attributes = {"text", "resource-id", "class", "content-desc", "bounds", "clickable", "enabled", "focused"}
    nodes = [{k: v for k, v in n.attrib.items() if k in attributes}
             for n in root.iter("node") if n.get("package") == package]
    if not nodes:
        raise LabError("wrong_hierarchy", "Hierarchy did not identify the session application")
    return nodes


def process_identity(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        api.OpenProcess.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = (wintypes.HANDLE,)
        api.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            flags = [wintypes.FILETIME() for _ in range(4)]
            if api.GetProcessTimes(handle, *[ctypes.byref(f) for f in flags]):
                return f"{flags[0].dwHighDateTime}:{flags[0].dwLowDateTime}"
            return None
        finally:
            api.CloseHandle(handle)
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


class DeviceSession:
    def __init__(self, platform, state, session, data, directory):
        self.platform, self.state = platform, state
        self.session, self.data, self.directory = session, data, directory

    @property
    def device(self):
        return self.data["device"]

    @property
    def personal(self):
        return self.data["route"] == "personal"

    @property
    def display(self):
        return self.data.get("display", 0)

    @property
    def package(self):
        return self.data["artifact"]["package"]

    def save(self):
        self.state.update(self.session, self.data)

    def cancelled(self):
        return self.state.cancelled(self.session)

    def check_transport(self):
        self.platform.find(self.device)
        if self.platform.boot_identity(self.device) != self.data["boot"]:
            raise LabError("boot_changed", "Device rebooted; recovery required", uncertain=True)

    def adb(self, *args, **kwargs):
        return self.platform.adb(*args, device=self.device, **kwargs)

    def shell(self, *args, **kwargs):
        return self.adb("shell", *args, **kwargs)

    def layout(self):
        return self.shell("dumpsys", "display")

    def check_lock(self):
        window = self.shell("dumpsys", "window", "policy")
        awake = re.search(r"(?m)^\s*mAwake=true\b", window)
        locked = re.findall(r"(?m)^\s*(?:mShowingLockscreen|isStatusBarKeyguard|isKeyguardShowing|showing)\s*=\s*(true|false)\b", window)
        if not awake or not locked or "true" in locked:
            raise LabError("lock_state", "Display power or lock state does not allow a personal session")

    def start_secondary(self, duration):
        self.check_transport()
        features = self.shell("pm", "list", "features").splitlines()
        if "feature:android.software.activities_on_secondary_displays" not in features:
            raise LabError("no_secondary_display", "Android did not advertise secondary-display activities")
        self.check_lock()
        previous = display_counters(self.layout())
        self.data["secondary"] = {"previous": sorted(previous), "pending": True}
        self.save()
        child = self.platform.begin_secondary(self.device, self.directory, duration,
                                              recording="video" in self.data["required"])
        self.data["secondary"].update(pid=child.pid, process=process_identity(child.pid))
        self.save()

        def ready():
            if child.poll() is not None:
                raise LabError("display_failed", "scrcpy exited before the display could be bound")
            log = (self.directory / "display.log").read_text(encoding="utf-8", errors="replace")
            match = re.search(r"New display: [^\r\n]+\(id=(\d+)\)", log)
            if not match:
                return None
            number = int(match[1])
            if number == 0 or number in previous:
                raise LabError("wrong_display", "scrcpy did not create a fresh secondary display")
            value = display_identity(self.layout(), number)
            return (number, value) if value else None

        number, identity = wait_until(ready, 20, "Secondary display identity unavailable", self.cancelled)
        self.data["display"] = number
        self.data["secondary"].update(identity=identity, pending=False)
        self.save()
        self.state.event(self.session, "display_bound", display=number, backend="scrcpy")

    def assert_bound(self):
        self.check_transport()
        if self.personal:
            self.check_lock()
            record = self.data.get("secondary", {})
            if (self.display <= 0 or not record.get("identity") or not record.get("process")
                    or process_identity(record["pid"]) != record["process"]
                    or display_identity(self.layout(), self.display) != record["identity"]):
                raise LabError("display_lost", "Secondary display identity changed", uncertain=True)

    def placement(self):
        self.assert_bound()
        raw = self.shell("dumpsys", "activity", "activities")
        observed = resumed_packages(raw)
        if not self.personal and observed.get(self.display) != [self.package]:
            diagnostics = [line for line in raw.splitlines() if "Display #" in line or "ResumedActivity" in line]
            (self.directory / "placement.log").write_text("\n".join(diagnostics), encoding="utf-8")
        if self.personal and self.package in observed.get(0, ()):
            raise LabError("primary_display_violation", "Session app appeared on the primary display", uncertain=True)
        if (self.personal and 0 not in observed) or observed.get(self.display) != [self.package]:
            raise LabError("placement_unverified", "Session app is not the sole resumed app on the assigned display")
        return self.display

    def installed(self, package):
        lines = self.shell("pm", "list", "packages", "--user", "0", package).splitlines()
        return f"package:{package}" in lines

    def installed_bytes_match(self, artifact):
        lines = self.shell("pm", "path", "--user", "0", artifact["package"]).splitlines()
        if len(lines) != 1 or not re.fullmatch(r"package:/data/app/[A-Za-z0-9_=+~./-]+/base\.apk", lines[0]):
            return False
        output = self.shell("sha256sum", lines[0].removeprefix("package:"), timeout=25)
        return output.split()[:1] == [artifact["sha256"]]

    def install(self, artifact, staged):
        self.check_transport()
        package = artifact["package"]
        if self.installed(package):
            raise LabError("existing_package", "Package already exists; its data is preserved")
        if digest(staged) != artifact["sha256"]:
            raise LabError("artifact_changed", "Staged APK changed after validation")
        self.data.setdefault("packages", []).append(artifact)
        self.save()
        self.state.event(self.session, "operation_started", operation="install", package=package)
        output = self.adb("install", "-R", "--user", "0", "--no-streaming", *(["-t"] if artifact["instrumentation"] else []), staged, timeout=60)
        if "Success" not in output.splitlines() or not self.installed_bytes_match(artifact):
            raise LabError("install_unverified", "Installation result is uncertain", uncertain=True)
        self.state.event(self.session, "operation_finished", operation="install", package=package)

    def launch(self, *, restart=False):
        self.assert_bound()
        activity = self.data["artifact"]["activity"]
        if not activity:
            raise LabError("no_launcher", "APK does not expose a validated launcher")
        if self.cancelled():
            raise LabError("cancelled", "Session cancelled before launch")
        if restart:
            self.shell("am", "force-stop", "--user", "0", self.package)
        self.state.event(self.session, "operation_started", operation="launch")
        result = self.shell("am", "start", "--user", "0", "-W", "--display", str(self.display),
                            "-n", self.package + "/" + activity, timeout=20)
        if any(line.startswith(("Error", "Exception")) for line in result.splitlines()):
            raise LabError("launch_failed", "Android rejected the explicit activity launch")

        def placed():
            try:
                return self.placement() == self.display
            except LabError as err:
                if err.code != "placement_unverified":
                    raise
                return False

        wait_until(placed, 10, "Actual activity placement could not be verified", self.cancelled)
        self.state.event(self.session, "operation_finished", operation="launch", display=self.display)

    def tree(self):
        if self.personal:
            raise LabError("unsupported", "Personal-device hierarchy is unavailable")
        self.placement()
        path = f"/data/local/tmp/androidlab-{self.session}.xml"
        self.data["tree_path"] = path
        self.save()
        try:
            self.shell("uiautomator", "dump", path, timeout=40)
            self.placement()
            raw = self.adb("exec-out", "cat", path, binary=True)
            if len(raw) > 2_000_000:
                raise LabError("ui_too_large", "UI hierarchy exceeds the capture limit")
            return clean_ui(raw, self.package)
        finally:
            self.shell("rm", "-f", path)

    def instrumentation(self, test, timeout):
        self.check_transport()
        runner = test["instrumentation"][0]["runner"]
        self.state.event(self.session, "operation_started", operation="instrumentation")
        output = self.shell("am", "instrument", "--user", "0", "-w", "-r",
                            test["package"] + "/" + runner, timeout=timeout)
        (self.directory / "instrumentation.txt").write_text(output, encoding="utf-8")
        match = re.search(r"\bOK\s*\((\d+)\s+tests?\)", output)
        if not match or not int(match[1]) or "FAILURES!!!" in output or "INSTRUMENTATION_FAILED" in output:
            raise LabError("assertion_failed", "Instrumentation reported failure or zero executed tests")
        self.state.event(self.session, "operation_finished", operation="instrumentation", assertions=int(match[1]))

    def coordinates(self, step):
        if step["op"] == "tap" and "text" in step:
            nodes = [node for node in self.tree() if step["text"] in (node.get("text"), node.get("content-desc"))]
            bounds = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", nodes[0].get("bounds", "")) if len(nodes) == 1 else None
            if not bounds:
                raise LabError("ambiguous_selector", "Tap requires one visible node with exact text")
            x1, y1, x2, y2 = map(int, bounds.groups())
            return [(x1 + x2) // 2, (y1 + y2) // 2]
        keys = ("x", "y") if step["op"] == "tap" else ("x1", "y1", "x2", "y2", "ms")
        values = [step.get(key) for key in keys]
        width, height = (540, 960) if self.personal else (10000, 10000)
        if not all(type(n) is int and 0 <= n < limit for n, limit in zip(values, (width, height, width, height, 2001))):
            raise LabError("invalid_coordinates", "Coordinates or duration outside allowed bounds")
        return values

    def action(self, step):
        op = step["op"]
        if op == "assert_display":
            return {"display": self.placement(), "assertion": True}
        if op in ("inspect", "assert_text", "assert_absent"):
            if op == "inspect":
                return {"nodes": self.tree()[:150]}
            text = step.get("text")
            if not isinstance(text, str) or not text or len(text) > 500:
                raise LabError("invalid_assertion", "Text assertion requires a bounded string")
            found = any(text in (n.get("text"), n.get("content-desc")) for n in self.tree())
            if found != (op == "assert_text"):
                raise LabError("assertion_failed", "Expected application text assertion failed")
            return {"assertion": True}
        if op == "relaunch":
            self.launch(restart=True)
            return {}
        if op == "screenshot":
            if self.personal:
                raise LabError("unsupported", "Personal screenshots require frame identity qualification")
            self.placement()
            raw = self.adb("exec-out", "screencap", "-p", binary=True)
            self.placement()
            if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise LabError("invalid_frame", "Screenshot was not a PNG")
            name = f"screen-{time.time_ns()}.png"
            (self.directory / name).write_bytes(raw)
            return {"file": name}
        self.placement()
        if op in ("tap", "swipe"):
            args = [op, *map(str, self.coordinates(step))]
        elif op == "text" and not self.personal:
            text = step.get("text")
            if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9 .,_@-]{1,100}", text):
                raise LabError("unsupported_text", "Only bounded basic Latin input is available")
            args = ["text", text.replace(" ", "%s")]
        elif op == "back" and not self.personal:
            args = ["keyevent", "4"]
        else:
            raise LabError("unsupported", "Operation unavailable on this backend")
        self.placement()
        if self.cancelled():
            raise LabError("cancelled", "Session cancelled before input")
        self.shell("input", "-d", str(self.display), *args, timeout=8)
        self.placement()
        return {}

    def rebind_cleanup_transport(self):
        current = next((row for row in self.platform.devices(strict=True)
                        if row["serial"] == self.device["serial"] and row["state"] == "device"), None)
        if not current or self.platform.boot_identity(current) != self.data["boot"]:
            raise LabError("cleanup_transport_lost", "Original device boot is unavailable", uncertain=True)
        self.device["transport_id"] = current["transport_id"]
        self.save()

    def cleanup(self):
        self.check_transport()
        for artifact in reversed(self.data.get("packages", [])):
            package = artifact["package"]
            if self.installed(package):
                if not self.installed_bytes_match(artifact):
                    raise LabError("cleanup_identity_mismatch", "Installed APK differs from the owned artifact")
                self.shell("am", "force-stop", "--user", "0", package)
                outcome = self.adb("uninstall", "--user", "0", package, timeout=30)
                if "Success" not in outcome.splitlines() or self.installed(package):
                    raise LabError("cleanup_failed", "Owned package removal could not be verified", uncertain=True)
        if self.data.get("tree_path"):
            self.shell("rm", "-f", self.data["tree_path"])
