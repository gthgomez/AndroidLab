import unittest

from evaluate import evaluate


def event(name, uptime, display, marker="synthetic", **fields):
    return {"event": name, "uptime_ms": uptime, "display": display, "marker": marker, **fields}


class QualifiedPairTests(unittest.TestCase):
    def setUp(self):
        self.primary = [
            event("focus", 1, 0, focused=True),
            event("tap", 2, 0),
            event("tap", 6, 0),
            event("text", 7, 0, value="Primary ø"),
            event("tap", 10, 0),
        ]
        self.automation = [
            event("resume", 3, 12),
            event("tap", 5, 12),
            event("text", 8, 12, value="Automation 🧪"),
            event("swipe", 9, 12),
        ]

    def report(self):
        return evaluate(self.primary, self.automation, "synthetic", 12, "Primary ø", "Automation 🧪")

    def test_good_pair(self):
        self.assertTrue(self.report()["passed"])

    def test_primary_focus_loss_fails_despite_automation_success(self):
        self.primary.insert(3, event("focus", 7, 0, focused=False))
        self.assertFalse(self.report()["passed"])
        self.assertTrue(self.report()["checks"]["automation_tap_and_swipe"])

    def test_automation_primary_display_fails(self):
        self.automation[1]["display"] = 0
        self.assertFalse(self.report()["checks"]["automation_identity"])

    def test_reused_display_marker_fails(self):
        for row in self.automation:
            row["marker"] = "previous-run"
        self.assertFalse(self.report()["checks"]["automation_identity"])

    def test_expected_text_mismatch_fails(self):
        self.automation[2]["value"] = "Automation ?"
        self.assertFalse(self.report()["checks"]["automation_text"])

    def test_missing_concurrent_primary_progress_fails(self):
        self.primary.pop(2)
        self.assertFalse(self.report()["checks"]["primary_input_brackets_automation"])


if __name__ == "__main__":
    unittest.main()
