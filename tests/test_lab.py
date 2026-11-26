import io
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from android_lab.artifact import authorize
from android_lab.broker import Broker
from android_lab.cli import execute, parser
from android_lab.common import LabError, digest, json_write
from android_lab.config import Config
from android_lab.device import DeviceSession, clean_ui, display_counters, display_identity, focused_package, resumed_packages
from android_lab.mcp import invoke, serve
from android_lab.planner import EMULATOR, PERSONAL, choose
from android_lab.platform import Platform, parse_devices
from android_lab.project import Project
from android_lab.state import State

HOST = {"free_mib": 7000, "commit_free_mib": 7000, "cpu_percent": 8, "disk_mib": 50000,
        "busy_java": False, "accelerated": True, "emulator_count": 0}
SUITE = {"kind": "device", "requires": ["launch", "placement"], "prebuilt": True,
         "min_sdk": 26, "timeout": 60}
AVD = {"id": "avd:fixture", "kind": "avd", "ready": True, "api": 36, "resources": ["avd"],
       "capabilities": EMULATOR, "memory_mib": 4096}
EMU = AVD | {"id": "running", "kind": "emulator", "resources": ["emu"]}
PHONE = {"id": "phone", "kind": "personal", "ready": True, "api": 36,
         "resources": ["phone"], "capabilities": PERSONAL}


class PlannerTests(unittest.TestCase):
    def route(self, devices, *, suite=SUITE, host=HOST, leases=()):
        return choose(suite, devices, host, set(leases), lambda key, default: default)["route"]

    def test_host_first(self):
        self.assertEqual("host", self.route([EMU, PHONE], suite={"kind": "host", "requires": []}))

    def test_ready_emulator_first(self):
        self.assertEqual("emulator", self.route([AVD, PHONE, EMU]))

    def test_managed_when_budget_fits(self):
        self.assertEqual("managed_emulator", self.route([PHONE, AVD]))

    def test_fallback_when_emulator_absent(self):
        self.assertEqual("personal", self.route([PHONE]))

    def test_fallback_when_emulator_exceeds_ram(self):
        self.assertEqual("personal", self.route([AVD, PHONE], host=HOST | {"free_mib": 2100}))

    def test_hardware_routes_to_qualified_device(self):
        self.assertEqual("personal", self.route([AVD, EMU, PHONE],
                                               suite=SUITE | {"requires": ["real_network", "placement"]}))

    def test_unqualified_unavailable_or_leased_is_blocked(self):
        self.assertEqual("blocked", self.route([PHONE | {"ready": False}]))
        self.assertEqual("blocked", self.route([PHONE], leases=["phone"]))
        self.assertEqual("blocked", self.route([]))

    def test_partial_capabilities_blocked(self):
        self.assertEqual("blocked", self.route([PHONE], suite=SUITE | {"requires": ["launch", "text"]}))

    def test_no_host_stats_or_external_build(self):
        self.assertEqual("blocked", self.route([AVD, PHONE], host={}))
        self.assertEqual("blocked", self.route([PHONE], suite=SUITE | {"prebuilt": False},
                                               host=HOST | {"busy_java": True}))


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.state = State(self.home)

    def test_two_writers_contend(self):
        def acquire():
            try:
                return self.state.acquire(["device"], 60, {})[0]
            except LabError as err:
                return err.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: acquire(), range(2)))
        self.assertEqual(1, results.count("resource_busy"))

    def test_expired_token_and_quarantine_hold_lease(self):
        first, old_token = self.state.acquire(["device"], -1, {})
        with self.assertRaisesRegex(LabError, "expired"):
            self.state.get(first, old_token, active=True)
        self.state.release(first, False)
        with self.assertRaisesRegex(LabError, "leased"):
            self.state.acquire(["device"], 60, {})
        self.state.release(first, True)
        second, new_token = self.state.acquire(["device"], 60, {})
        with self.assertRaises(LabError):
            self.state.get(second, old_token, active=True)
        self.assertEqual(second, self.state.get(second, new_token, active=True)["id"])

    def test_cancel_fences_future_operations_and_live_lock(self):
        session, token = self.state.acquire(["device"], 60, {})
        with self.state.operation(session):
            with self.assertRaisesRegex(LabError, "current operation"):
                with self.state.operation(session):
                    self.fail("A second operation acquired the lock")
            self.state.stop(session)
            with self.assertRaises(LabError):
                self.state.get(session, token, active=True)

    def test_safety_invalidation_persists(self):
        self.state.invalidate("phone")
        self.assertEqual({"phone"}, set(State(self.home).invalidated()))
        with self.assertRaisesRegex(LabError, "latest invalidation"):
            self.state.accept_qualification("phone", time.time() - 1)
        self.state.accept_qualification("phone", time.time())
        self.assertIn("phone", self.state.invalidated())

    def test_two_transports_share_a_lease_and_invalidation(self):
        self.state.acquire(["physical:handset"], 60, {"transport": "usb"})
        with self.assertRaises(LabError):
            self.state.acquire(["physical:handset"], 60, {"transport": "wireless"})
        self.state.invalidate("physical:handset")
        with self.assertRaises(LabError):
            self.state.accept_qualification("wireless", time.time() - 1, "physical:handset")

    def test_recovery_distinguishes_absent_backend_from_failure(self):
        broker = Broker(Config(self.home, {}))
        state = {"route": "personal", "device": {"id": "safety-fixture"}, "execution": "started"}
        session, token = self.state.acquire(["phone"], -1, state)
        with patch.object(broker, "reconcile", side_effect=LabError("transport_lost", "not present")):
            self.assertEqual("quarantined", broker.recover()[0]["cleanup"])
        self.assertEqual({"phone"}, self.state.leased())
        self.assertIn("safety-fixture", self.state.invalidated())
        with patch.object(broker, "reconcile"):
            self.assertEqual("complete", broker.recover()[0]["cleanup"])
        self.assertEqual(set(), self.state.leased())


class PolicyTests(unittest.TestCase):
    artifact = {"package": "org.example.qa", "sha256": "sha", "permissions": ["internet"],
                "signers": ["cert"], "instrumentation": []}

    def test_pins_bytes_permissions_certificate_and_operation(self):
        policy = {"artifacts": [self.artifact | {"allows": ["launch"]}]}
        authorize(self.artifact, "personal", policy, ["launch"])
        for new in ({"sha256": "other"}, {"permissions": []}, {"signers": ["different"]}):
            with self.assertRaises(LabError):
                authorize(self.artifact | new, "personal", policy, ["launch"])
        with self.assertRaises(LabError):
            authorize(self.artifact, "personal", policy, ["launch", "tap"])

    def test_reject_production_and_instrumentation(self):
        with self.assertRaisesRegex(LabError, "separate QA"):
            authorize(self.artifact | {"package": "org.example.prod"}, "personal", {}, ["launch"])
        with self.assertRaisesRegex(LabError, "Instrumentation"):
            authorize(self.artifact | {"instrumentation": [{}]}, "personal", {}, ["launch"])

    def test_deny_arbitrary_gradle_device_task(self):
        project = Project(Path("."), {"suites": {"unit": {"task": ":app:connectedDebugAndroidTest"}}}, [])
        with self.assertRaisesRegex(LabError, "standard Gradle task"):
            project.suite("unit")

    def test_workflow_text_selector_needs_inspection(self):
        project = Project(Path("."), {"suites": {"ui": {"steps": [{"op": "tap", "text": "Increment"}]}}}, [])
        self.assertIn("inspect", project.suite("ui")["requires"])

    def test_definite_install_refusal_cannot_own_other_packages(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            apk = work / "fixture.apk"
            apk.write_bytes(b"qa-fixture")
            artifact = self.artifact | {"sha256": digest(apk)}
            data = {}
            device = DeviceSession(Mock(), Mock(), "session", data, work)
            with patch.object(device, "check_transport"), patch.object(device, "installed", return_value=False), patch.object(device, "cancelled", return_value=False):
                with patch.object(device, "adb", return_value=(1, "Failure [INSTALL_FAILED_ALREADY_EXISTS]")) as adb:
                    with self.assertRaisesRegex(LabError, "INSTALL_FAILED_ALREADY_EXISTS"):
                        device.install(artifact, apk)
                    self.assertIn("-R", adb.call_args.args)
            self.assertEqual([], data["packages"])

    def test_lost_install_response_is_not_cleanup_authority(self):
        data = {"packages": [self.artifact | {"install_state": "pending"}]}
        device = DeviceSession(Mock(), Mock(), "session", data, Path("."))
        with patch.object(device, "check_transport"), patch.object(device, "adb") as adb:
            with self.assertRaisesRegex(LabError, "owner reconciliation"):
                device.cleanup()
            adb.assert_not_called()


class DisplayTests(unittest.TestCase):
    def test_resumed_packages_are_display_scoped(self):
        example = """Display #2 (activities from top to bottom):
          topResumedActivity=ActivityRecord{abc u0 org.example.qa/.Main t4}
        Display #0 (activities from top to bottom):
          mResumedActivity: ActivityRecord{xyz u0 org.example.canary/.Main t6}
        """
        self.assertEqual({0: ["org.example.canary"], 2: ["org.example.qa"]}, resumed_packages(example))

    def test_display_identity_must_match_id_and_kind(self):
        text = 'mInfo=DisplayInfo{"scrcpy", displayId 3, uniqueId "virtual:owner:id"}'
        self.assertEqual("virtual:owner:id", display_identity(text, 3))
        self.assertIsNone(display_identity(text, 13))
        self.assertIsNone(display_identity(text.replace("scrcpy", "Built-in Screen"), 3))

    def test_unknown_inventory_cannot_prove_display_absence(self):
        self.assertEqual({0, 4}, display_counters("Logical displays:\n Display 0:\n Display 4:\n"))
        with self.assertRaisesRegex(LabError, "could not be verified"):
            display_counters("Unexpected OEM format")

    def test_focused_window_must_match_display_and_owner(self):
        text = "Display: mDisplayId=0\n  mCurrentFocus=Window{a u0 org.example.app/.Main}\n"
        text += "Display: mDisplayId=3\n  mCurrentFocus=Window{b u12 org.example.qa/.Main}\n"
        self.assertEqual("org.example.app", focused_package(text, 0))
        self.assertIsNone(focused_package(text, 3))

    def test_adb_inventory_failure_cannot_prove_device_absence(self):
        backend = Platform(Config(Path("."), {}))
        with patch.object(backend, "adb", side_effect=LabError("timeout", "ADB unavailable")):
            self.assertEqual([], backend.devices())
            with self.assertRaisesRegex(LabError, "unavailable"):
                backend.devices(strict=True)

    def test_hierarchy_strips_foreign_nodes_and_rejects_wrong_app(self):
        raw = b'<hierarchy><node package="org.example.qa" text="marker"/><node package="android" text="foreign"/></hierarchy>'
        self.assertEqual([{"text": "marker"}], clean_ui(raw, "org.example.qa"))
        with self.assertRaisesRegex(LabError, "identify"):
            clean_ui(raw, "org.other.qa")

    def test_original_failure_never_sends_primary_input(self):
        state, platform = Mock(), Mock()
        data = {"route": "personal", "artifact": {"package": "org.example.qa"}, "display": 4}
        session = DeviceSession(platform, state, "session", data, Path("."))
        with patch.object(session, "assert_bound"), patch.object(session, "shell", return_value=(
            "Display #0 (activities from top to bottom):\n"
            "topResumedActivity=ActivityRecord{a u0 org.example.qa/.Main t1}"
        )) as shell:
            with self.assertRaises(LabError) as caught:
                session.action({"op": "tap", "x": 10, "y": 10})
            self.assertEqual("primary_display_violation", caught.exception.code)
            shell.assert_called_once_with("dumpsys", "activity", "activities")

    def test_transport_inventory_alias(self):
        result = parse_devices("List of devices attached\nprivate-id device product:x usb:abc transport_id:17\n")
        self.assertEqual("usb", result[0]["transport"])
        self.assertNotIn("private-id", result[0]["id"])

    def test_recovery_rebinds_only_the_original_device_boot(self):
        device = {"serial": "fixture", "transport_id": 2, "state": "device"}
        route = {"device": device, "boot": "original"}
        platform = Mock()
        platform.devices.return_value = [device | {"transport_id": 19}]
        platform.boot_identity.return_value = "original"
        work = DeviceSession(platform, Mock(), "session", route, Path("."))
        work.rebind_cleanup_transport()
        self.assertEqual(19, route["device"]["transport_id"])
        platform.boot_identity.return_value = "different"
        with self.assertRaisesRegex(LabError, "Original device boot"):
            work.rebind_cleanup_transport()

    def test_partial_boot_process_is_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            platform = Mock()
            platform.devices.side_effect = [[{"serial": "emulator-5560"}], []]
            broker = Broker(Config(Path(tmp), {}), platform)
            owned = {"serial": "emulator-5560", "avd": "Fixture", "pid": 90, "process": "creation"}
            with patch("android_lab.broker.process_identity", return_value="creation"), patch.object(broker, "stop_owned_process") as stop:
                broker.reconcile("test", {"emulator": owned})
                stop.assert_called_once_with(owned)


class InterfaceTests(unittest.TestCase):
    def test_local_project_overrides_apply_to_run(self):
        import os
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            fixture = Path("fixtures").resolve()
            override = {"suites": {"hardware": {"requires": ["oem"], "prebuilt": True, "apk": "sample.apk"}}}
            json_write(home / "config.json", {"projects": {os.path.normcase(str(fixture)): override}})
            broker = Broker(Config.load(home))
            with patch.object(broker, "plan", return_value={"route": "blocked", "device": None, "reasons": []}) as plan:
                broker.start(fixture, "hardware")
            suite = plan.call_args.args[0]
            self.assertTrue(suite["prebuilt"])
            self.assertEqual("sample.apk", suite["apk"])
            self.assertIn("oem", suite["requires"])

    def test_cli_mcp_plan_parity_and_strict_arguments(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Config(Path(temp), {})
            broker = Broker(config)
            args = parser().parse_args(["plan", "--project", "fixtures", "--suite", "ui"])
            with patch("android_lab.broker.host_sample", return_value=HOST), patch.object(broker, "inventory", return_value=([EMU], [])):
                self.assertEqual(execute(broker, args), invoke(broker, "lab_plan", {"project": "fixtures", "suite": "ui"}))
            with self.assertRaises(LabError):
                invoke(broker, "lab_plan", {"project": "fixtures", "suite": "ui", "serial": "anything"})

    def test_stdio_protocol(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "lab_doctor"}},
        ]
        incoming = io.BytesIO(("\n".join(map(json.dumps, requests)) + "\n").encode())
        outgoing = io.BytesIO()
        mock = Mock()
        mock.doctor.return_value = {"broker": "ready"}
        serve(mock, incoming, outgoing)
        messages = list(map(json.loads, outgoing.getvalue().splitlines()))
        self.assertEqual([1, 2, 3], [reply["id"] for reply in messages])
        self.assertIn("lab_doctor", [tool["name"] for tool in messages[1]["result"]["tools"]])
        self.assertFalse(messages[2]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
