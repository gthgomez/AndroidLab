"""MCP stdio transport for local trusted coding agents."""
from __future__ import annotations

import json
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event

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
    "lab_job": ("Get the result of a submitted Android Lab operation", {"job": STRING}, ["job"]),
    "lab_cancel_job": ("Request cancellation of an in-progress Android Lab operation", {"job": STRING}, ["job"]),
}
SUPPORTED = {"2024-11-05", "2025-03-26", "2025-06-18"}
BACKGROUND = {"lab_run", "lab_start", "lab_step", "lab_close"}


def tools():
    return [{"name": name, "description": description,
             "inputSchema": {"type": "object", "properties": properties, "required": required,
                             "additionalProperties": False}}
            for name, (description, properties, required) in SCHEMAS.items()]


def validate(name, arguments):
    schema = SCHEMAS.get(name)
    if not schema:
        raise LabError("unknown_tool", "Unknown Android Lab operation")
    _, props, required = schema
    if not isinstance(arguments, dict) or not set(required) <= arguments.keys() or not arguments.keys() <= props.keys():
        raise LabError("invalid_args", "Invalid tool arguments")
    for key, value in arguments.items():
        if not isinstance(value, str if props[key]["type"] == "string" else dict):
            raise LabError("invalid_args", "Tool argument type mismatch")


def invoke(broker, name, arguments, *, on_session=None):
    validate(name, arguments)
    if name == "lab_doctor":
        return broker.doctor()
    if name == "lab_devices":
        return broker.devices()
    if name == "lab_project":
        return broker.project(arguments["project"]).describe()
    if name == "lab_plan":
        return broker.describe_plan(broker.plan(broker.project(arguments["project"]).suite(arguments["suite"])))
    if name in ("lab_run", "lab_start"):
        return broker.start(arguments["project"], arguments["suite"], interactive=name == "lab_start",
                            on_session=on_session)
    if name == "lab_step":
        return broker.step(arguments["session"], arguments["token"], arguments["operation"])
    return broker.close(arguments["session"], arguments["token"])


class Jobs:
    def __init__(self, broker):
        self.broker = broker
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="android-lab")
        self.records = {}
        self.sessions = {}

    def submit(self, name, arguments):
        validate(name, arguments)
        if any(not item["future"].done() for item in self.records.values()):
            raise LabError("job_busy", "Wait for the current Android Lab job or cancel it")
        if len(self.records) >= 32:
            self.records.pop(next(iter(self.records)))
        key = secrets.token_hex(12)
        item = {"cancel_requested": Event(), "session": None, "tool": name}

        def bind(session):
            item["session"] = session
            if item["cancel_requested"].is_set():
                self.broker.state.stop(session)

        def work():
            if item["cancel_requested"].is_set():
                raise LabError("cancelled", "Job cancelled before execution")
            config = type(self.broker.config).load(self.broker.config.home)
            worker = type(self.broker)(config)
            if name in ("lab_step", "lab_close"):
                worker.state.get(arguments["session"], arguments["token"])
                bind(arguments["session"])
            try:
                result = invoke(worker, name, arguments, on_session=bind)
            except LabError:
                if item["cancel_requested"].is_set() and item["session"]:
                    return worker.close(item["session"])
                raise
            if (item["cancel_requested"].is_set() and item["session"]
                    and worker.state.get(item["session"])["status"] != "closed"):
                return worker.close(item["session"])
            if name == "lab_start" and "session_token" in result:
                self.sessions[result["session"]] = result["session_token"]
            if name == "lab_close":
                self.sessions.pop(arguments["session"], None)
            return result

        item["future"] = self.pool.submit(work)
        self.records[key] = item
        return {"job": key, "state": "submitted"}

    def status(self, key):
        item = self.records.get(key)
        if item is None:
            raise LabError("unknown_job", "Unknown Android Lab job in this client session")
        future = item["future"]
        if not future.done():
            return {"job": key, "state": "running", "session": item["session"]}
        try:
            return {"job": key, "state": "finished", "tool": item["tool"], "result": future.result()}
        except LabError as err:
            return {"job": key, "state": "failed", "error": {"code": err.code, "message": str(err)}}
        except Exception as err:
            return {"job": key, "state": "failed", "error": {"code": "invalid_job", "message": type(err).__name__}}

    def cancel(self, key):
        item = self.records.get(key)
        if item is None:
            raise LabError("unknown_job", "Unknown Android Lab job in this client session")
        item["cancel_requested"].set()
        if item["session"] and not item["future"].done():
            self.broker.state.stop(item["session"])
        return {"job": key, "state": "cancellation_requested" if not item["future"].done() else "finished"}

    def close(self):
        for key, item in self.records.items():
            if not item["future"].done():
                self.cancel(key)
        self.pool.shutdown()
        for session, token in self.sessions.items():
            try:
                self.broker.close(session, token)
            except (LabError, OSError):
                pass


def serve(broker, source=None, output=None):
    source = source or sys.stdin.buffer
    output = output or sys.stdout.buffer
    initialized = False
    jobs = Jobs(broker)
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
                              "instructions": "Plan first. Run/start/step/close return a job ID. Poll lab_job for results. Respect blocked routes."}
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
                        validate(name, arguments)
                        if name in BACKGROUND:
                            payload = jobs.submit(name, arguments)
                        elif name == "lab_job":
                            payload = jobs.status(arguments["job"])
                        elif name == "lab_cancel_job":
                            payload = jobs.cancel(arguments["job"])
                        else:
                            broker.reload_policy()
                            payload = invoke(broker, name, arguments)
                        target = payload.get("tool") if name == "lab_job" else name
                        report = payload.get("result", payload)
                        failed = payload.get("state") == "failed" or (
                            isinstance(report, dict) and (report.get("cleanup") == "quarantined" or
                            (target != "lab_close" and report.get("execution") in ("blocked", "failed", "cancelled")))
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
        jobs.close()
    return 0
