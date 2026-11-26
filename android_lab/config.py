from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .common import LabError, json_read


def default_home():
    return Path(os.environ.get("ANDROID_LAB_HOME") or
                Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/state")) / "AndroidLab")


@dataclass
class Config:
    home: Path
    settings: dict

    @classmethod
    def load(cls, home=None):
        root = Path(home or default_home()).resolve()
        policy = root / "config.json"
        return cls(root, json_read(policy) if policy.is_file() else {})

    @property
    def sdk(self):
        path = self.settings.get("sdk") or os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
        if not path:
            raise LabError("no_sdk", "Set ANDROID_HOME or configure the SDK in Android Lab")
        return Path(path)

    @property
    def avds(self):
        return Path(self.settings.get("avd_home") or os.environ.get("ANDROID_AVD_HOME") or Path.home() / ".android/avd")

    @property
    def tool_env(self):
        env = {**os.environ, "ANDROID_HOME": str(self.sdk), "ANDROID_SDK_ROOT": str(self.sdk),
               "ANDROID_AVD_HOME": str(self.avds)}
        for key in ("ADB_SERVER_SOCKET", "ANDROID_ADB_SERVER_ADDRESS", "ANDROID_ADB_SERVER_PORT", "ANDROID_SERIAL"):
            env.pop(key, None)
        return env

    def executable(self, folder, name):
        path = self.sdk / folder / (name + (".exe" if os.name == "nt" else ""))
        if not path.is_file():
            raise LabError("missing_tool", f"Install Android SDK {folder}/{name}")
        return path

    def build_tool(self, name, windows_suffix=".exe"):
        versions = sorted((p for p in (self.sdk / "build-tools").glob("*") if p.name.split(".")[0].isdigit()),
                          key=lambda p: tuple(int(n) for n in p.name.split(".") if n.isdigit()), reverse=True)
        for directory in versions:
            path = directory / (name + (windows_suffix if os.name == "nt" else ""))
            if path.is_file():
                return path
        raise LabError("missing_tool", f"Install Android SDK Build Tools containing {name}")

    def budget(self, key, default):
        value = self.settings.get("budget", {}).get(key, default)
        if type(value) is not int or value < 0:
            raise LabError("invalid_config", f"Budget {key} must be a nonnegative integer")
        return value
