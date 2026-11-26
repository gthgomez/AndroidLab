"""MCP stdio transport for local trusted coding agents."""
from __future__ import annotations

import json
import sys

from .common import LabError

STRING = {"type": "string"}
PROJECT = {"project": STRING}
PLAN = PROJECT | {"suite": STRING}
SESSION = {"session": STRING, "token": STRING}
SCHEMAS = {
    "lab_doctor": ("Inspect SDK and host readiness", {}, []),
    "lab_devices": ("List available testing environments", {}, []),
    "lab_project": ("Inspect a local Android project", PROJECT, ["project"]),
    "lab_plan": ("Explain deterministic test routing", PLAN, ["project", "suite"]),
    "lab_run": ("Run a declared suite on the selected environment", PLAN, ["project", "suite"]),
    "lab_start": ("Open a scoped exploratory device session", PLAN, ["project", "suite"]),
    "lab_step": ("Run a declared operation on an open session", SESSION | {"operation": {"type": "object"}}, ["session", "token", "operation"]),
    "lab_close": ("Cancel and clean up an open session", SESSION, ["session", "token"]),
}
SUPPORTED = {"2024-11-05", "2025-03-26", "2025-06-18"}


def tools():
    return [{"name": name, "description": description,
             "inputSchema": {"type": "object", "properties": properties, "required": required,
                             "additionalProperties": False}}
            for name, (description, properties, required) in SCHEMAS.items()]


def invoke(broker, name, arguments):
    schema = SCHEMAS.get(name)
    if not schema:
        raise LabError("unknown_tool", "Unknown Android Lab operation")
    _, props, required = schema
    if not isinstance(arguments, dict) or not set(required) <= arguments.keys() or not arguments.keys() <= props.keys():
        raise LabError("invalid_args", "Invalid tool arguments")
    for key, value in arguments.items():
        if not isinstance(value, str if props[key]["type"] == "string" else dict):
            raise LabError("invalid_args", "Tool argument type mismatch")
    if name == "lab_doctor":
        return broker.doctor()
    if name == "lab_devices":
        return broker.devices()
    if name == "lab_project":
        return broker.project(arguments["project"]).describe()
    if name == "lab_plan":
        return broker.describe_plan(broker.plan(broker.project(arguments["project"]).suite(arguments["suite"])))
    if name in ("lab_run", "lab_start"):
        return broker.start(arguments["project"], arguments["suite"], interactive=name == "lab_start")
    if name == "lab_step":
        return broker.step(arguments["session"], arguments["token"], arguments["operation"])
    return broker.close(arguments["session"], arguments["token"])


def serve(broker, source=None, output=None):
    source = source or sys.stdin.buffer
    output = output or sys.stdout.buffer
    initialized = False
    sessions = {}
    def send(record):
        output.write((json.dumps(record, ensure_ascii=True) + "\n").encode())
        output.flush()

    try:
        for line in source:
            request = {}
            try:
                if len(line) > 2**20:
                    raise ValueError("request too large")
                request = json.loads(line)
                if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                    raise ValueError("invalid request")
                if "id" not in request:
                    continue
                method, params = request.get("method"), request.get("params", {})
                response = {"jsonrpc": "2.0", "id": request["id"]}
                if method == "initialize":
                    version = params.get("protocolVersion")
                    initialized = True
                    result = {"protocolVersion": version if version in SUPPORTED else "2025-06-18",
                              "capabilities": {"tools": {}},
                              "serverInfo": {"name": "android-lab", "version": "0.1.0"},
                              "instructions": "Use lab_plan then lab_run or lab_start. Respect blocked routes."}
                elif not initialized:
                    send(response | {"error": {"code": -32002, "message": "Initialize first"}})
                    continue
                elif method == "ping":
                    result = {}
                elif method == "tools/list":
                    result = {"tools": tools()}
                elif method == "tools/call":
                    try:
                        name, arguments = params["name"], params.get("arguments", {})
                        broker.reload_policy()
                        payload = invoke(broker, name, arguments)
                        if name == "lab_start" and "session_token" in payload:
                            sessions[payload["session"]] = payload["session_token"]
                        if name == "lab_close":
                            sessions.pop(arguments["session"], None)
                        failed = isinstance(payload, dict) and (
                            payload.get("cleanup") == "quarantined" or
                            (name != "lab_close" and payload.get("execution") in ("blocked", "failed", "cancelled"))
                        )
                    except LabError as err:
                        failed = True
                        payload = {"error": {"code": err.code, "message": str(err), "uncertain": err.uncertain}}
                    result = {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": failed}
                else:
                    send(response | {"error": {"code": -32601, "message": "Unknown method"}})
                    continue
                send(response | {"result": result})
            except (ValueError, KeyError, TypeError, OSError, AttributeError):
                send({"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                      "error": {"code": -32600, "message": "Invalid MCP request"}})
    finally:
        for session, token in sessions.items():
            try:
                broker.close(session, token)
            except (LabError, OSError):
                pass
    return 0
