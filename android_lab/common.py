from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+\Z")
TASK = re.compile(r":?[A-Za-z0-9_][A-Za-z0-9_.-]*(?::[A-Za-z0-9_][A-Za-z0-9_.-]*)*\Z")
ACTIVITY = re.compile(r"[A-Za-z_.$][A-Za-z0-9_.$]*\Z")
HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class LabError(Exception):
    def __init__(self, code, message, *, uncertain=False):
        super().__init__(message)
        self.code = code
        self.uncertain = uncertain


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def json_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def json_read(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("expected object")
        return data
    except (ValueError, OSError) as err:
        raise LabError("invalid_json", f"Cannot read JSON object {path.name}") from err


def within(root, value):
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise LabError("outside_project", "Path escapes the project")
    return path


def run(args, *, timeout=15, cwd=None, binary=False, env=None, check=True):
    try:
        result = subprocess.run(list(map(str, args)), capture_output=True, timeout=timeout,
                                cwd=cwd, env=env, creationflags=HIDDEN)
    except subprocess.TimeoutExpired as err:
        raise LabError("timeout", "Tool timed out; operation outcome may be uncertain", uncertain=True) from err
    except OSError as err:
        raise LabError("tool_unavailable", "Cannot start required tool") from err
    if binary:
        if result.returncode:
            raise LabError("tool_failed", f"Tool failed with exit {result.returncode}")
        return result.stdout
    out = (result.stdout + result.stderr).decode("utf-8", "replace").replace("\r\n", "\n")
    if check and result.returncode:
        raise LabError("tool_failed", f"Tool failed with exit {result.returncode}")
    return out if check else (result.returncode, out)


def wait_until(predicate, seconds, message, cancelled=lambda: False):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if cancelled():
            raise LabError("cancelled", "Session cancelled")
        value = predicate()
        if value:
            return value
        time.sleep(0.4)
    raise LabError("timeout", message, uncertain=True)
