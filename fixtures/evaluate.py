"""Evaluate synthetic paired-canary observations independently of the device backend."""

import argparse
import json
import re
from pathlib import Path

MAX_LOG_SIZE = 64 * 1024
MARKER = re.compile(r"[A-Za-z0-9-]{1,40}\Z")


def read_events(file):
    with Path(file).open("rb") as stream:
        data = stream.read(MAX_LOG_SIZE + 1)
    if len(data) > MAX_LOG_SIZE:
        raise ValueError("Canary log exceeds the fixture limit.")
    return [json.loads(line) for line in data.decode("utf-8").splitlines()]


def evaluate(events, other, marker, display, expected_primary=None, expected_automation=None, *,
             fresh_operator_launch=False):
    if not isinstance(marker, str) or not MARKER.fullmatch(marker) or marker == "UNSET":
        raise ValueError("Supply a unique nondefault fixture marker.")
    if type(display) is not int or display < 1:
        raise ValueError("A distinct secondary display is required.")

    def select(rows, expected_display, expected_marker):
        result = [row for row in rows if isinstance(row, dict) and row.get("marker") == expected_marker]
        valid = all(type(row.get("display")) is int and row["display"] == expected_display
                    and type(row.get("uptime_ms")) is int and row["uptime_ms"] >= 0
                    and isinstance(row.get("event"), str) for row in result)
        if not valid:
            return result, False
        times = [row["uptime_ms"] for row in result]
        return result, bool(result) and times == sorted(times)

    primary, primary_placed = select(events, 0, "UNSET" if fresh_operator_launch else marker)
    secondary, secondary_placed = select(other, display, marker)
    checks = {
        "primary_identity": primary_placed,
        "automation_identity": secondary_placed,
    }
    if not (primary_placed and secondary_placed):
        return {"passed": False, "checks": checks}

    def focused_at(rows, when):
        focus = [row["focused"] for row in rows if row["event"] == "focus"
                 and "focused" in row and row["uptime_ms"] <= when]
        return bool(focus) and focus[-1] is True

    def gestures(rows):
        return [row for row in rows if row["event"] in ("tap", "swipe")]

    automation_gestures = gestures(secondary)
    primary_gestures = gestures(primary)
    checks["automation_tap_and_swipe"] = {row["event"] for row in automation_gestures} == {"tap", "swipe"}
    resumes = [row for row in secondary if row["event"] == "resume"]
    if resumes and automation_gestures:
        start, end = resumes[0]["uptime_ms"], automation_gestures[-1]["uptime_ms"]
        primary_mid = [row for row in primary if start <= row["uptime_ms"] <= end]
        checks["primary_retains_focus"] = focused_at(primary, start) and not any(
            row["event"] in ("pause", "stop", "destroy", "create")
            or (row["event"] == "focus" and row.get("focused") is not True)
            for row in primary_mid
        )
        checks["primary_input_brackets_automation"] = (
            any(row["uptime_ms"] < start for row in primary_gestures)
            and any(start < row["uptime_ms"] < end for row in primary_gestures)
            and any(row["uptime_ms"] > end for row in primary_gestures)
        )
    else:
        checks["primary_retains_focus"] = False
        checks["primary_input_brackets_automation"] = False

    def last_text(rows):
        values = [row.get("value") for row in rows if row["event"] == "text"]
        return values[-1] if values else None

    if expected_primary is not None:
        checks["primary_text"] = last_text(primary) == expected_primary
    if expected_automation is not None:
        checks["automation_text"] = last_text(secondary) == expected_automation
    return {"passed": all(checks.values()), "checks": checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("primary", type=Path)
    parser.add_argument("automation", type=Path)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--display", type=int, required=True)
    parser.add_argument("--primary-text")
    parser.add_argument("--automation-text")
    args = parser.parse_args()
    try:
        report = evaluate(read_events(args.primary), read_events(args.automation), args.marker,
                          args.display, args.primary_text, args.automation_text)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"Qualification data error: {exc}\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
