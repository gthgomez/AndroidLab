from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

from .common import ACTIVITY, PACKAGE, LabError, digest, run


def read_apk(path, config, *, stage=None):
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != ".apk":
        raise LabError("no_apk", "The selected artifact is not an APK file")
    if stage is not None:
        shutil.copyfile(path, stage)
        path = stage
    info = run([config.build_tool("aapt"), "dump", "badging", path], timeout=30)
    package = re.search(r"^package: name='([^']+)'", info, re.M)
    if not package or not PACKAGE.fullmatch(package[1]):
        raise LabError("bad_package", "APK has an invalid application identity")
    activity = re.search(r"^launchable-activity: name='([^']+)'", info, re.M)
    activity = activity[1] if activity and ACTIVITY.fullmatch(activity[1]) else None
    minimum = re.search(r"^sdkVersion:'(\d+)'", info, re.M)
    java_home = config.settings.get("java_home")
    build_tools = config.build_tool("aapt").parent
    if (build_tools / "lib/apksigner.jar").is_file():
        exe = "java.exe" if (build_tools / "aapt.exe").exists() else "java"
        java = Path(java_home) / "bin" / exe if java_home else exe
        signer = [java, "-Xmx256m", "-jar", build_tools / "lib/apksigner.jar"]
    else:
        signer = [config.build_tool("apksigner", ".bat")]
    verified = run([*signer, "verify", "--print-certs", path], timeout=30)
    certs = sorted(re.findall(r"^Signer #\d+ certificate SHA-256 digest:\s*([0-9a-f]{64})$", verified, re.M))
    if not certs:
        raise LabError("bad_signature", "APK signature identity could not be verified")
    manifest = run([config.build_tool("aapt"), "dump", "xmltree", path, "AndroidManifest.xml"], timeout=30)
    # aapt badging can omit instrumentation declared in the binary manifest.
    instruments = []
    for record in re.split(r"(?m)^\s*E: instrumentation\b[^\n]*\n", manifest)[1:]:
        chunk = re.split(r"(?m)^\s*E:", record, maxsplit=1)[0]
        target = re.search(r'android:targetPackage.*?="([^"]+)"', chunk)
        runner = re.search(r'android:name.*?="([^"]+)"', chunk)
        if not target or not runner or not PACKAGE.fullmatch(target[1]) or not ACTIVITY.fullmatch(runner[1]):
            raise LabError("invalid_instrumentation", "Instrumentation identity is ambiguous")
        instruments.append({"target": target[1], "runner": runner[1]})
    with zipfile.ZipFile(path) as archive:
        abis = sorted({name.split("/")[1] for name in archive.namelist()
                       if name.startswith("lib/") and name.endswith(".so") and len(name.split("/")) == 3})
    return {"package": package[1], "activity": activity, "sha256": digest(path), "signers": certs,
            "min_sdk": int(minimum[1]) if minimum else 1, "abis": abis,
            "permissions": sorted(set(re.findall(r"^uses-permission(?:-sdk-\d+)?: name='([^']+)'", info, re.M))),
            "instrumentation": instruments}


def authorize(artifact, route, policy, requires):
    if route not in ("personal", "exclusive"):
        return
    package = artifact["package"]
    if route == "personal" and not package.endswith((".qa", ".connectionqa", ".lab")):
        raise LabError("qa_identity_required", "Personal-device APK must have a separate QA application identity")
    if route == "personal" and artifact["instrumentation"]:
        raise LabError("personal_instrumentation", "Instrumentation is disabled on personal-device sessions")
    entries = policy.get("artifacts", [])
    for approval in entries:
        if all(approval.get(k) == artifact[k] for k in ("package", "sha256", "signers", "permissions", "instrumentation")):
            if set(requires) <= set(approval.get("allows", [])):
                return
    raise LabError("artifact_denied", "Owner approval of this exact APK and its requested operations is required")
