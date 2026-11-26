"""Build signed Android Lab canaries using a JDK and official Android SDK tools."""

import argparse
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_SDK = 26
AUTOMATION = "com.androidlab.canary.automation.lab"
PRIMARY = "com.androidlab.canary.primary.lab"
RUNNER = "com.androidlab.fixture.StorageTest"


def command(*args, env=None, capture=False):
    result = subprocess.run([str(item) for item in args], env=env, check=True, text=True,
                            capture_output=capture)
    return (result.stdout + result.stderr) if capture else ""


def jdk(path):
    if not path:
        java = shutil.which("java")
        if not java:
            raise ValueError("Install JDK 17+ or pass --java-home.")
        settings = command(java, "-XshowSettings:properties", "-version", capture=True)
        match = re.search(r"^\s*java\.home\s*=\s*(.+)$", settings, re.MULTILINE)
        if not match:
            raise ValueError("Could not determine Java home. Pass --java-home.")
        path = match.group(1).strip()
    directory = Path(path) / "bin"
    tools = tuple(executable(directory, name) for name in ("java", "javac", "keytool"))
    if not all(tool.is_file() for tool in tools):
        raise ValueError("JDK java, javac and keytool are required.")
    return tools


def executable(directory, name):
    return directory / (name + (".exe" if os.name == "nt" else ""))


def version_key(path):
    return tuple(int(part) for part in path.name.split("."))


def installed_sdk(sdk, requested_tools, requested_api):
    platform = sdk / "platforms"
    tools = sdk / "build-tools"
    if not platform.is_dir() or not tools.is_dir():
        raise ValueError("Install at least one Android SDK platform and Build Tools package.")
    if requested_api:
        apis = [requested_api]
    else:
        apis = sorted((int(path.name.removeprefix("android-")) for path in platform.iterdir()
                       if re.fullmatch(r"android-\d+", path.name)), reverse=True)
    for api in apis:
        android = platform / f"android-{api}" / "android.jar"
        if api >= 33 and android.is_file():
            break
    else:
        raise ValueError("Install an Android SDK platform of API 33 or later.")
    if requested_tools:
        versions = [tools / requested_tools]
    else:
        versions = sorted((path for path in tools.iterdir()
                           if re.fullmatch(r"\d+(?:\.\d+)+", path.name)),
                          key=version_key, reverse=True)
    for version in versions:
        aapt = executable(version, "aapt2")
        zipalign = executable(version, "zipalign")
        d8 = version / "lib" / "d8.jar"
        signer = version / "lib" / "apksigner.jar"
        if all(tool.is_file() for tool in (aapt, zipalign, d8, signer)):
            return android, api, aapt, zipalign, d8, signer
    raise ValueError("Android SDK Build Tools must include aapt2, zipalign, d8 and apksigner.")


def manifest(package, label, is_test):
    if is_test:
        content = f'''<application android:label="{label}" android:debuggable="true"
            android:allowBackup="false" />
        <instrumentation android:name="{RUNNER}" android:targetPackage="{AUTOMATION}"
            android:functionalTest="true" />'''
    else:
        content = f'''<application android:label="{label}" android:debuggable="true"
            android:allowBackup="false" android:usesCleartextTraffic="false"
            android:enableOnBackInvokedCallback="false"
            android:theme="@android:style/Theme.Material.Light.NoActionBar">
            <activity android:name="com.androidlab.fixture.CanaryActivity"
                android:exported="true" android:resizeableActivity="true">
                <intent-filter>
                    <action android:name="android.intent.action.MAIN"/>
                    <category android:name="android.intent.category.LAUNCHER"/>
                </intent-filter>
            </activity>
        </application>'''
    return f'''<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{package}" android:versionCode="1" android:versionName="1">
    {content}
</manifest>
'''


def compile_dex(stage, source, java, javac, android, d8):
    stage.mkdir()
    classes = stage / "classes"
    classes.mkdir()
    command(javac, "-encoding", "UTF-8", "--release", "8", "-classpath", android,
            "-d", classes, ROOT / "src" / source)
    command(java, "-cp", d8, "com.android.tools.r8.D8", "--min-api", MIN_SDK, "--lib",
            android, "--output", stage, *sorted(classes.rglob("*.class")))
    return stage / "classes.dex"


def assemble(stage, filename, package, label, dex, sdk_tools, key, env, java, is_test=False):
    android, api, aapt, zipalign, _, signer = sdk_tools
    work = stage / filename
    work.mkdir()
    xml = work / "AndroidManifest.xml"
    xml.write_text(manifest(package, label, is_test), encoding="utf-8")
    unaligned = work / "unaligned.apk"
    aligned = work / "aligned.apk"
    output = stage / (filename + ".apk")
    command(aapt, "link", "-I", android, "--manifest", xml, "--min-sdk-version", MIN_SDK,
            "--target-sdk-version", api, "-o", unaligned)
    with zipfile.ZipFile(unaligned, mode="a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(dex, arcname="classes.dex")
    command(zipalign, "-f", "4", unaligned, aligned)
    command(java, "-jar", signer, "sign", "--ks", key, "--ks-key-alias", "androidlab-fixture",
            "--ks-pass", "env:ANDROID_LAB_FIXTURE_KEY_PASS", "--out", output, aligned, env=env)
    command(java, "-jar", signer, "verify", output)
    return output


def build(args):
    if not args.sdk:
        raise ValueError("Set ANDROID_HOME or pass --sdk.")
    sdk_tools = installed_sdk(Path(args.sdk), args.build_tools, args.api)
    android, api, _, _, d8, _ = sdk_tools
    java, javac, keytool = jdk(args.java_home)
    output = ROOT / "build"
    output.mkdir(exist_ok=True)
    print(f"Compiling synthetic fixtures with Android API {api}.", flush=True)
    with tempfile.TemporaryDirectory(dir=output) as temp:
        stage = Path(temp)
        key = stage / "synthetic.p12"
        env = {**os.environ, "ANDROID_LAB_FIXTURE_KEY_PASS": secrets.token_urlsafe(32)}
        command(keytool, "-genkeypair", "-keystore", key, "-storetype", "PKCS12",
                "-storepass:env", "ANDROID_LAB_FIXTURE_KEY_PASS", "-alias", "androidlab-fixture",
                "-dname", "CN=Android Lab Synthetic Fixtures", "-keyalg", "RSA", "-keysize", "2048",
                "-validity", "365", env=env)
        canary_dex = compile_dex(stage / "canary-code", "CanaryActivity.java", java, javac, android, d8)
        test_dex = compile_dex(stage / "instrumentation-code", "StorageTest.java", java, javac, android, d8)
        products = [
            assemble(stage, "automation", AUTOMATION, "Android Lab Automation Canary",
                     canary_dex, sdk_tools, key, env, java),
            assemble(stage, "primary", PRIMARY, "Android Lab Primary Canary",
                     canary_dex, sdk_tools, key, env, java),
            assemble(stage, "instrumentation", AUTOMATION + ".test", "Android Lab Storage Test",
                     test_dex, sdk_tools, key, env, java, is_test=True),
        ]
        for apk in products:
            target = output / apk.name
            apk.replace(target)
            print(f"build/{target.name} sha256={hashlib.sha256(target.read_bytes()).hexdigest()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", default=os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT"))
    parser.add_argument("--java-home", default=os.environ.get("JAVA_HOME"))
    parser.add_argument("--api", type=int)
    parser.add_argument("--build-tools")
    try:
        build(parser.parse_args())
    except (OSError, subprocess.CalledProcessError, ValueError) as err:
        print(f"Cannot build Android Lab fixtures. {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
