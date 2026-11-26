from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .common import HIDDEN, LabError, TASK, json_read, run, within

MODULE = re.compile(r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]*)+\Z")
VARIANT = re.compile(r"[A-Za-z][A-Za-z0-9]*\Z")
CAPABILITIES = frozenset({
    "launch", "placement", "tap", "swipe", "text", "back", "inspect", "screenshot", "video",
    "instrumentation", "real_network", "oem", "native_arm64", "exclusive", "tv",
})
ACTION_CAP = {"assert_text": "inspect", "assert_absent": "inspect", "assert_display": "placement", "relaunch": "launch"}
ACTIONS = set(ACTION_CAP) | {"tap", "swipe", "text", "back", "inspect", "screenshot"}


@dataclass
class Project:
    root: Path
    manifest: dict
    modules: list

    @classmethod
    def discover(cls, path):
        root = Path(path).resolve()
        if not root.is_dir():
            raise LabError("no_project", "Project is not a directory")
        names = ("android-lab.json", "settings.gradle.kts", "settings.gradle")
        while not any((root / name).is_file() for name in names):
            if root == root.parent:
                raise LabError("no_project", "No Gradle settings or android-lab.json found")
            root = root.parent
        manifest = json_read(root / names[0]) if (root / names[0]).is_file() else {}
        settings = next((root / name for name in names[1:] if (root / name).is_file()), None)
        names = {":app"} if (root / "app").is_dir() else set()
        if settings:
            for clause in re.findall(r"(?m)^\s*include\s*(\([^)]*\)|[^\n]*)", settings.read_text(encoding="utf-8-sig")):
                names.update(re.findall(r"""["'](:[A-Za-z0-9_.:-]+)["']""", clause))
        if isinstance(manifest.get("module"), str):
            names.add(manifest["module"])
        modules = []
        for name in sorted(names):
            if not MODULE.fullmatch(name):
                raise LabError("bad_module", "Expected a qualified Gradle :module path")
            directory = within(root, name[1:].replace(":", "/"))
            build = next((directory / file for file in ("build.gradle.kts", "build.gradle") if (directory / file).is_file()), None)
            if not build:
                continue
            source = build.read_text(encoding="utf-8-sig")
            fields = {}
            for field in ("applicationId", "testInstrumentationRunner", "minSdk", "compileSdk", "targetSdk"):
                pattern = r"(\d+)" if field.endswith("Sdk") else r"""["']([^"']+)["']"""
                match = re.search(rf"\b{field}(?:Version)?\s*(?:=\s*)?{pattern}", source)
                fields[field] = (int(match[1]) if field.endswith("Sdk") else match[1]) if match else None
            outputs = []
            for file in (directory / "build/outputs/apk").glob("**/output-metadata.json"):
                if file.resolve().is_relative_to(root):
                    info = json_read(file)
                    outputs.append({"variant": info.get("variantName"), "package": info.get("applicationId"),
                                    "metadata": str(file.relative_to(root))})
            modules.append({"module": name, **fields, "apk_outputs": outputs,
                            "unit_tests": (directory / "src/test").exists(),
                            "instrumentation_tests": (directory / "src/androidTest").exists()})
        return cls(root, manifest, modules)

    def describe(self):
        return {"project": self.root.name, "modules": self.modules,
                "gradle_wrapper": (self.root / "gradle/wrapper/gradle-wrapper.jar").is_file(),
                "docs": [name for name in ("README.md", "PROJECT_CONTEXT.md", "AGENTS.md") if (self.root / name).is_file()],
                "suites": sorted({"unit", "ui", "hardware", "instrumentation"} | self.manifest.get("suites", {}).keys())}

    def suite(self, name):
        defaults = {"unit": {"kind": "host"}, "ui": {"kind": "device"},
                    "hardware": {"kind": "device"}, "instrumentation": {"kind": "device"}}
        requested = self.manifest.get("suites", {}).get(name)
        if requested is None and name not in defaults:
            raise LabError("unknown_suite", "Unknown suite. Inspect the project for available suites.")
        suite = {**defaults.get(name, {"kind": "device"}), **(requested or {})}
        if suite["kind"] not in ("host", "device"):
            raise LabError("invalid_suite", "Suite kind must be host or device")
        if "workflow" in suite:
            suite["steps"] = json_read(within(self.root, suite["workflow"])).get("steps", [])
        steps = suite.get("steps", [])
        if not isinstance(steps, list) or len(steps) > 100:
            raise LabError("invalid_workflow", "A workflow can contain up to 100 operations")
        required = set(suite.get("requires", []))
        for step in steps:
            if not isinstance(step, dict) or step.get("op") not in ACTIONS:
                raise LabError("invalid_workflow", "Unknown workflow operation")
            op = step["op"]
            required.add(ACTION_CAP.get(op, op))
            if op == "tap" and "text" in step:
                required.add("inspect")
        if required - CAPABILITIES:
            raise LabError("invalid_capability", "Unknown requested capability")
        if name == "hardware":
            required.add("real_network")
        if name == "instrumentation":
            required.add("instrumentation")
        if suite["kind"] == "host" and (required or steps):
            raise LabError("invalid_suite", "Host-only suites cannot request device operations")
        if suite["kind"] == "device" and ("instrumentation" not in required or steps):
            required.update(("launch", "placement"))
        if "instrumentation" in required and steps:
            raise LabError("invalid_suite", "Use separate suites for instrumentation and interactive steps")
        duration = suite.get("timeout", 300)
        if type(duration) is not int or not 10 <= duration <= 3600:
            raise LabError("invalid_suite", "Duration must be between 10 and 3600 seconds")
        module = suite.get("module", self.manifest.get("module"))
        if not module:
            app_modules = [m["module"] for m in self.modules if m["applicationId"]]
            if len(app_modules) > 1:
                raise LabError("ambiguous_module", "Specify an application module in android-lab.json")
            module = next(iter(app_modules), ":app")
        variant = suite.get("variant", self.manifest.get("variant", "debug"))
        if not isinstance(module, str) or not MODULE.fullmatch(module) or not isinstance(variant, str) or not VARIANT.fullmatch(variant):
            raise LabError("invalid_variant", "Specify a valid module and variant")
        title = variant[0].upper() + variant[1:]
        task = suite.get("task", f"{module}:" + (f"test{title}UnitTest" if suite["kind"] == "host" else f"assemble{title}"))
        default_task = f"{module}:" + (f"test{title}UnitTest" if suite["kind"] == "host" else f"assemble{title}")
        if not isinstance(task, str) or not TASK.fullmatch(task) or task != default_task:
            raise LabError("invalid_task", "Suite must use its module and variant's standard Gradle task")
        if type(suite.get("prebuilt", False)) is not bool:
            raise LabError("invalid_suite", "prebuilt must be true or false")
        info = next((m for m in self.modules if m["module"] == module), {})
        minimum = suite.get("min_sdk", info.get("minSdk") or 1)
        if type(minimum) is not int or minimum < 1:
            raise LabError("invalid_suite", "min_sdk must be a positive integer")
        return {**suite, "name": name, "module": module, "variant": variant, "task": task,
                "requires": sorted(required), "steps": steps, "timeout": duration, "min_sdk": minimum}

    def apk(self, suite, *, test=False):
        field = "test_apk" if test else "apk"
        if suite.get(field):
            return within(self.root, suite[field])
        directory = within(self.root, suite["module"][1:].replace(":", "/")) / "build/outputs/apk"
        candidates = []
        for file in directory.glob("**/output-metadata.json"):
            if not file.resolve().is_relative_to(self.root):
                continue
            if ("androidTest" in file.relative_to(directory).parts) != test:
                continue
            info = json_read(file)
            variant = suite["variant"]
            if info.get("variantName") not in ((variant, variant + "AndroidTest") if test else (variant,)):
                continue
            elements = info.get("elements", [])
            if len(elements) == 1 and not elements[0].get("filters"):
                candidates.append(within(self.root, str(file.parent / elements[0]["outputFile"])))
        if len(candidates) != 1:
            raise LabError("apk_ambiguous", f"Expected one universal APK. Specify {field} for this suite.")
        return candidates[0]

    def revision(self):
        try:
            code, out = run(["git", "rev-parse", "HEAD"], cwd=self.root, check=False)
            if not code and re.fullmatch(r"[0-9a-f]{40,64}", out.strip()):
                status, dirty = run(["git", "status", "--porcelain"], cwd=self.root, check=False)
                return {"commit": out.strip(), "dirty": None if status else bool(dirty.strip())}
        except LabError:
            pass
        return {"commit": None, "dirty": None}

    def gradle(self, tasks, log, timeout, settings, cancelled):
        if not (self.root / "gradle/wrapper/gradle-wrapper.jar").is_file():
            raise LabError("no_gradle", "Project has no Gradle wrapper JAR")
        java_home = settings.get("java_home") or os.environ.get("JAVA_HOME")
        executable = Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java") if java_home else shutil.which("java")
        if not executable:
            raise LabError("missing_java", "Configure a compatible JDK")
        env = dict(os.environ)
        if settings.get("sdk"):
            env.update(ANDROID_HOME=settings["sdk"], ANDROID_SDK_ROOT=settings["sdk"])
        for key in ("ANDROID_SERIAL", "ADB_SERVER_SOCKET", "ANDROID_ADB_SERVER_ADDRESS", "ANDROID_ADB_SERVER_PORT"):
            env.pop(key, None)
        args = [str(executable), "-classpath", "gradle/wrapper/gradle-wrapper.jar",
                "org.gradle.wrapper.GradleWrapperMain", *tasks, "--no-daemon", "--max-workers=1",
                *settings.get("gradle_args", [])]
        with log.open("wb") as output:
            try:
                child = subprocess.Popen(args, cwd=self.root, env=env, stdout=output,
                                         stderr=subprocess.STDOUT, creationflags=HIDDEN)
            except OSError as err:
                raise LabError("tool_unavailable", "Could not launch Gradle wrapper") from err
            deadline = time.monotonic() + timeout
            try:
                while child.poll() is None:
                    if cancelled() or time.monotonic() >= deadline:
                        raise LabError("cancelled" if cancelled() else "timeout", "Gradle run interrupted. See local build log.")
                    time.sleep(0.3)
            finally:
                if child.poll() is None:
                    if os.name == "nt":
                        run(["taskkill", "/PID", str(child.pid), "/T", "/F"], check=False, timeout=10)
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=10)
        if child.returncode:
            raise LabError("build_failed", f"Gradle returned {child.returncode}. See local build log.")
