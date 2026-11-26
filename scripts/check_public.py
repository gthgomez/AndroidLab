"""Scan tracked source for common secret and local-evidence indicators."""
import re
import subprocess
import sys
from pathlib import PurePosixPath

INDICATORS = [
    rb"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
    rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|ASIA[A-Z0-9]{16}|AKIA[A-Z0-9]{16})\b",
    rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}\b",
    rb"\b(?:10(?:\.[0-9]{1,3}){3}|192\.168(?:\.[0-9]{1,3}){2})\b",
    rb"(?i)[A-Z]:[\\/](?:Users|AgentData|Workspace|Dev)[\\/]",
]
BINARY = {".apk", ".aab", ".jks", ".keystore", ".aar", ".zip", ".db", ".sqlite", ".log", ".png", ".mkv", ".pem"}


def scan():
    files = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
    failures = []
    for path in filter(None, files):
        if PurePosixPath(path).suffix.lower() in BINARY or "/.env" in "/" + path:
            failures.append(path)
            continue
        if path == "scripts/check_public.py":
            continue
        data = subprocess.check_output(["git", "show", ":" + path])
        if any(re.search(pattern, data) for pattern in INDICATORS):
            failures.append(path)
    return failures


if __name__ == "__main__":
    failures = scan()
    for path in failures:
        print("Review staged public-source privacy indicators:", path, file=sys.stderr)
    if not failures:
        print("Tracked index passed public-source privacy indicator scan")
    sys.exit(bool(failures))
