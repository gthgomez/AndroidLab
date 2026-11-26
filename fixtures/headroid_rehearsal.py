"""Run only the display-creation API of the pinned Headroid comparison server."""
import json
import re
import secrets
import shlex
import socket
import subprocess

from android_lab.common import HIDDEN, LabError, digest, wait_until
from android_lab.device import display_counters, display_identity, process_identity

VERSION = "v0.1.0a5"
SHA256 = "2c375e03a1436e5b549b388e178709d7fa14d3a4191ad1f4a45ae6aad92eb59c"


def create_display(work, jar):
    if work.device["kind"] != "emulator" or not work.rehearsal:
        raise LabError("emulator_only", "Headroid comparison is limited to the session-owned emulator")
    if not jar.is_file() or digest(jar) != SHA256:
        raise LabError("bad_headroid_artifact", f"Supply the verified Headroid {VERSION} server JAR")
    work.check_transport()
    work.check_lock()
    previous = display_counters(work.layout())
    name = "androidlab-" + secrets.token_hex(12)
    remote = "/data/local/tmp/" + name + ".jar"
    work.adb("push", jar, remote, timeout=30)
    adb = work.platform.config.executable("platform-tools", "adb")
    command = "CLASSPATH=" + remote + " " + shlex.join(
        ["app_process", "/", "dev.headroid.server.HeadroidServer", "socket=" + name, "grace_ms=2000"])
    logfile = work.directory / "headroid.log"
    with logfile.open("wb") as log:
        process = subprocess.Popen([str(adb), "-t", str(work.device["transport_id"]), "shell", command],
                                   env=work.platform.config.tool_env, creationflags=HIDDEN,
                                   stdout=log, stderr=subprocess.STDOUT)
    work.data["secondary"] = {"previous": sorted(previous), "pending": True,
                              "pid": process.pid, "process": process_identity(process.pid), "name": "headroid"}
    work.save()
    forwarded = None
    connection = None

    def ready():
        if process.poll() is not None:
            raise LabError("headroid_exited", "Headroid exited before creating its comparison socket")
        return f"listening on localabstract:{name}" in logfile.read_text(encoding="utf-8", errors="replace")

    def rpc(method, params):
        with connection.makefile("rwb") as stream:
            stream.write((json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}) + "\n").encode())
            stream.flush()
            reply = json.loads(stream.readline(1024 * 1024))
        if reply.get("id") != 1 or "error" in reply:
            raise LabError("headroid_error", f"Headroid rejected {method}")
        return reply["result"]

    try:
        wait_until(ready, 20, "Headroid did not start", work.cancelled)
        port = work.adb("forward", "tcp:0", "localabstract:" + name).strip()
        if not re.fullmatch(r"\d{1,5}", port) or not 1 <= int(port) < 65536:
            raise LabError("headroid_forward", "ADB did not return an assigned loopback port")
        forwarded = "tcp:" + port
        connection = socket.create_connection(("127.0.0.1", int(port)), timeout=15)
        output = rpc("display.create", {"width": 540, "height": 960, "dpi": 180})
        number = output.get("displayId")
        if type(number) is not int or number <= 0 or number in previous:
            raise LabError("wrong_display", "Headroid did not identify a fresh secondary display")
        identity = display_identity(work.layout(), number, "headroid")
        if not identity:
            raise LabError("wrong_display", "Android did not confirm the Headroid display identity")
        work.data["display"] = number
        work.data["secondary"].update(identity=identity, pending=False)
        work.save()
        work.state.event(work.session, "display_bound", display=number, backend="headroid")
        return connection, forwarded, remote, rpc
    except Exception:
        close_display(work, connection, forwarded, remote, None)
        raise


def close_display(work, connection, forwarded, remote, rpc):
    if connection:
        try:
            if rpc:
                rpc("server.shutdown", {})
        except (OSError, LabError, ValueError):
            pass
        finally:
            connection.close()
    if forwarded:
        work.adb("forward", "--remove", forwarded, check=False)
    work.shell("rm", "-f", remote, check=False)
