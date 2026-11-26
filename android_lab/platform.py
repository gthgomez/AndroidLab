from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

from .common import HIDDEN, LabError, digest, run
from .planner import EMULATOR, EXCLUSIVE, PERSONAL

EMU_SERIAL = re.compile(r"emulator-(\d{4,5})\Z")
AVD_NAME = re.compile(r"[A-Za-z0-9_.-]+\Z")
SCRCPY_VERSION = "5.0.1"


def windows_sample():
    script = """
    $ErrorActionPreference = 'Stop'
    $m = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
    $cpu = Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average
    $before = @{}
    Get-Process java,javaw -ErrorAction SilentlyContinue | ForEach-Object { $before[$_.Id] = $_.CPU }
    Start-Sleep -Milliseconds 300
    $busy = @(Get-Process java,javaw -ErrorAction SilentlyContinue | Where-Object {
        !$before.ContainsKey($_.Id) -or ($_.CPU - $before[$_.Id]) -gt 0.12
    }).Count -gt 0
    [pscustomobject]@{
        free_mib = [int]$m.AvailableMBytes
        commit_free_mib = [int](($m.CommitLimit - $m.CommittedBytes) / 1MB)
        cpu_percent = $cpu.Average
        busy_java = $busy
        emulator_count = @(Get-Process qemu-system* -ErrorAction SilentlyContinue).Count
    } | ConvertTo-Json -Compress
    """
    try:
        values = json.loads(run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]))
        if values.get("cpu_percent") is None or not isinstance(values.get("free_mib"), int):
            return {}
        return values
    except (LabError, ValueError):
        return {}


def host_sample(config, *, device):
    if os.name == "nt":
        values = windows_sample()
    else:
        values = {}
        if Path("/proc/meminfo").is_file():
            memory = {key: int(value) for key, value in re.findall(r"^(\w+):\s+(\d+) kB", Path("/proc/meminfo").read_text(), re.M)}
            values = {"free_mib": memory.get("MemAvailable", 0) // 1024,
                      "commit_free_mib": memory.get("MemAvailable", 0) // 1024,
                      "cpu_percent": min(100, round(100 * os.getloadavg()[0] / (os.cpu_count() or 1))),
                      "busy_java": False, "emulator_count": 0}
    values["disk_mib"] = shutil.disk_usage(config.home).free // (2 ** 20)
    if device:
        try:
            parent = next(p for p in (config.avds, *config.avds.parents) if p.exists())
            values["disk_mib"] = min(values["disk_mib"], shutil.disk_usage(parent).free // (2 ** 20))
            exe = config.executable("emulator", "emulator")
            code, output = run([exe, "-accel-check"], check=False, timeout=10, env=config.tool_env)
            values["accelerated"] = code == 0 and bool(re.search(r"(?m)^0\s*$", output))
        except (OSError, LabError, StopIteration):
            values["accelerated"] = False
    return values


def properties(path):
    if not path.is_file():
        return {}
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                if "=" in line and not line.startswith("#"))


def parse_devices(output):
    rows = []
    for line in output.splitlines():
        fields = line.strip().split()
        if len(fields) < 2 or fields[1] not in {"device", "offline", "unauthorized", "recovery", "sideload"}:
            continue
        meta = dict(part.split(":", 1) for part in fields[2:] if ":" in part)
        transport = meta.get("transport_id", "")
        if transport.isdecimal():
            serial = fields[0]
            alias = "device-" + hashlib.sha256(serial.encode()).hexdigest()[:12]
            rows.append({"serial": serial, "state": fields[1], "transport_id": int(transport), "id": alias,
                         "transport": "usb" if "usb" in meta else "tcp"})
    return rows


class Platform:
    def __init__(self, config):
        self.config = config

    def adb(self, *args, device=None, **kwargs):
        command = [self.config.executable("platform-tools", "adb")]
        if device:
            command.extend(["-t", str(device["transport_id"])])
        try:
            return run([*command, *args], env=self.config.tool_env, **kwargs)
        except LabError as err:
            raise LabError(err.code, f"ADB {args[0]}: {err}", uncertain=err.uncertain) from err

    def devices(self, *, strict=False):
        try:
            output = self.adb("devices", "-l")
            if "List of devices attached" not in output.splitlines():
                raise LabError("adb_inventory_unknown", "ADB did not return a recognized device inventory")
            return parse_devices(output)
        except LabError:
            if strict:
                raise
            return []

    def find(self, saved):
        for current in self.devices():
            if (current["serial"], current["transport_id"], current["state"]) == (saved["serial"], saved["transport_id"], "device"):
                return current
        raise LabError("transport_lost", "The leased device transport disappeared or was replaced", uncertain=True)

    def prop(self, device, name):
        return self.adb("shell", "getprop", name, device=device).strip()

    def boot_identity(self, device):
        value = self.adb("shell", "cat", "/proc/sys/kernel/random/boot_id", device=device).strip()
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", value):
            raise LabError("unknown_boot", "No device boot identity available")
        return value

    def avd_name(self, device):
        if not EMU_SERIAL.fullmatch(device["serial"]):
            return None
        # Emulator console commands require a specific emulator serial.
        command = [self.config.executable("platform-tools", "adb"), "-s", device["serial"], "emu", "avd", "name"]
        code, output = run(command, env=self.config.tool_env, check=False, timeout=5)
        name = output.splitlines()[0].strip() if output.splitlines() else ""
        return name if code == 0 and AVD_NAME.fullmatch(name) else None

    def avds(self):
        try:
            exe = self.config.executable("emulator", "emulator")
            names = run([exe, "-list-avds"], env=self.config.tool_env, timeout=10).splitlines()
        except LabError:
            return {}
        results = {}
        for name in names:
            name = name.strip()
            if not AVD_NAME.fullmatch(name):
                continue
            declared = properties(self.config.avds / (name + ".ini"))
            info = properties(Path(declared.get("path", self.config.avds / (name + ".avd"))) / "config.ini")
            version = re.search(r"android-(\d+)", declared.get("target", "") + info.get("image.sysdir.1", ""))
            caps = set(EMULATOR)
            if "tv" in info.get("tag.id", "").lower():
                caps.add("tv")
            configured_ram = re.fullmatch(r"(\d+)\s*([MG])?", info.get("hw.ramSize", "2048"), re.I)
            ram = (int(configured_ram[1]) * (1024 if (configured_ram[2] or "").upper() == "G" else 1)
                   if configured_ram else 2048)
            ram = self.config.budget("emulator_ram_mib", ram)
            estimated = ram + self.config.budget("emulator_overhead_mib", 640)
            results[name] = {"id": "avd:" + name, "name": name, "kind": "avd", "api": int(version[1]) if version else 0,
                             "ready": bool(info), "capabilities": sorted(caps), "ram": ram,
                             "memory_mib": estimated, "resources": ["avd:" + name, "emulator-provision"]}
        return results

    def scrcpy_identity(self):
        record = self.config.settings.get("scrcpy", {})
        exe = Path(record.get("path", ""))
        server = exe.parent / "scrcpy-server"
        files = [exe, server]
        if not all(p.is_file() for p in files):
            raise LabError("missing_scrcpy", f"Configure the official scrcpy {SCRCPY_VERSION} Windows release")
        if not all(digest(p) == record.get(key) for p, key in zip(files, ("client_sha256", "server_sha256"))):
            raise LabError("scrcpy_changed", "Configured scrcpy binary or server hash changed")
        version = run([exe, "--version"], cwd=exe.parent)
        if not re.search(rf"(?m)^scrcpy {re.escape(SCRCPY_VERSION)}(?:\s|$)", version):
            raise LabError("wrong_scrcpy", "Unsupported scrcpy version")
        return {"version": SCRCPY_VERSION, "client": record["client_sha256"], "server": record["server_sha256"]}

    def identity(self, device, mode):
        api = self.prop(device, "ro.build.version.sdk")
        fingerprint = self.prop(device, "ro.build.fingerprint")
        serial = self.prop(device, "ro.boot.serialno") or self.prop(device, "ro.serialno")
        if (not api.isdecimal() or not fingerprint or len(serial) < 5
                or serial.lower() in ("unknown", "0123456789abcdef")):
            raise LabError("unknown_device", "Android firmware or SDK could not be identified")
        sources = [Path(__file__).with_name(name) for name in ("platform.py", "device.py", "broker.py", "artifact.py", "planner.py", "state.py", "cli.py")]
        adb_version = self.adb("version").splitlines()
        return {"api": int(api), "fingerprint": fingerprint, "transport": device["transport"],
                "device_identity": hashlib.sha256(("android-lab:physical:" + serial).encode()).hexdigest(),
                "adb": adb_version[:2], "adb_sha256": digest(self.config.executable("platform-tools", "adb")),
                "adapter_sha256": hashlib.sha256("".join(map(digest, sources)).encode()).hexdigest(),
                "backend": self.scrcpy_identity() if mode == "personal" else {"version": "adb"}}

    def health(self, device, policy):
        battery = self.adb("shell", "dumpsys", "battery", device=device)
        thermal = self.adb("shell", "dumpsys", "thermalservice", device=device)
        level = re.search(r"(?mi)^\s*level:\s*(\d+)\s*$", battery)
        status = re.search(r"(?mi)^\s*Thermal Status:\s*(\d+)\s*$", thermal)
        return bool(level and status and int(level[1]) >= policy.get("min_battery", 25)
                    and int(status[1]) <= policy.get("max_thermal", 1))

    def inventory(self):
        avds = self.avds()
        results = []
        available = self.devices()
        presence = []
        for dev in available:
            public = {k: dev[k] for k in ("id", "state", "transport")}
            if dev["state"] != "device":
                presence.append(public | {"reason": "ADB transport not authorized or ready"})
                continue
            try:
                name = self.avd_name(dev)
                if name:
                    avd = avds.get(name)
                    if avd:
                        avd["ready"] = False
                    allow = self.config.settings.get("borrow_avds", [])
                    api = self.prop(dev, "ro.build.version.sdk")
                    booted = self.prop(dev, "sys.boot_completed") == "1"
                    caps = avd["capabilities"] if avd else sorted(EMULATOR)
                    results.append(dev | {"id": "emu:" + name, "name": name, "kind": "emulator",
                                          "api": int(api) if api.isdecimal() else 0,
                                          "ready": booted and name in allow, "capabilities": caps,
                                          "resources": ["avd:" + name, dev["id"]]})
                    presence.append(public | {"kind": "emulator", "name": name, "borrow_allowed": name in allow})
                    continue
                if EMU_SERIAL.fullmatch(dev["serial"]):
                    presence.append(public | {"reason": "Emulator console identity not verified"})
                    continue
                policy = self.config.settings.get("devices", {}).get(dev["id"], {})
                mode = policy.get("mode")
                if mode not in ("personal", "exclusive") or not policy.get("enabled"):
                    presence.append(public | {"kind": "physical", "reason": "No enabled owner enrollment"})
                    continue
                identity = self.identity(dev, mode)
                evidence = policy.get("qualification", {})
                exact = (evidence.get("identity") == identity and evidence.get("passed") is True
                         and evidence.get("device") == dev["id"] and evidence.get("mode") == mode)
                fresh = time.time() < evidence.get("expires_unix", 0)
                caps = (PERSONAL if mode == "personal" else EXCLUSIVE).intersection(evidence.get("capabilities", []))
                if "native_arm64" in caps and "arm64-v8a" not in self.prop(dev, "ro.product.cpu.abilist").split(","):
                    caps = caps - {"native_arm64"}
                healthy = exact and fresh and self.health(dev, policy)
                presence.append(public | {"kind": mode, "qualified": healthy,
                                          "reason": None if healthy else "Device identity, qualification, or battery/thermal condition requires attention"})
                results.append(dev | {"kind": mode, "api": identity["api"], "identity": identity, "ready": healthy,
                                      "capabilities": sorted(caps) if healthy else [],
                                      "qualified_at": evidence.get("observed_unix", 0),
                                      "max_seconds": policy.get("max_seconds", 120),
                                      "resources": ["physical:" + identity["device_identity"]]})
            except LabError as err:
                presence.append(public | {"reason": str(err)})
        return results + list(avds.values()), presence

    def choose_port(self):
        used = {d["serial"] for d in self.devices()}
        for port in range(5554, 5586, 2):
            if f"emulator-{port}" in used:
                continue
            try:
                with socket.socket() as first, socket.socket() as second:
                    first.bind(("127.0.0.1", port))
                    second.bind(("127.0.0.1", port + 1))
                return port
            except OSError:
                continue
        raise LabError("no_port", "No free emulator console port")

    def start_avd(self, avd, port, directory):
        exe = self.config.executable("emulator", "emulator")
        gpu = self.config.settings.get("emulator_gpu", "auto")
        args = [exe, "-avd", avd["name"], "-port", str(port), "-memory", str(avd["ram"]), "-cores", "2",
                "-no-window", "-no-audio", "-read-only", "-no-snapshot", "-no-boot-anim", "-gpu", gpu]
        with (directory / "emulator.log").open("wb") as log:
            return subprocess.Popen(list(map(str, args)), env=self.config.tool_env, creationflags=HIDDEN,
                                    stdout=log, stderr=subprocess.STDOUT)

    def begin_secondary(self, dev, directory, duration, *, recording):
        self.scrcpy_identity()
        exe = Path(self.config.settings["scrcpy"]["path"])
        env = self.config.tool_env | {"ADB": str(self.config.executable("platform-tools", "adb"))}
        env.pop("SCRCPY_SERVER_PATH", None)
        output = directory / ("display.mkv" if recording else "private-display.mkv")
        args = [exe, "--serial", dev["serial"], "--new-display=540x960/180", "--no-window", "--no-audio",
                "--no-control", "--no-power-on", "--no-clipboard-autosync", "--no-vd-system-decorations",
                "--display-ime-policy=hide", "--max-fps=15", "--time-limit", str(duration), "--record", output]
        with (directory / "display.log").open("wb") as log:
            return subprocess.Popen(list(map(str, args)), cwd=exe.parent, env=env, creationflags=HIDDEN,
                                    stdout=log, stderr=subprocess.STDOUT)
