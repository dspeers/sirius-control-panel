#!/usr/bin/env python3
"""Contract smoke tests for the Sirius REST API.

Verifies that the responses the control panel and voice_direct depend on still have the
expected SHAPE (the keys the code reads) — the thing a robot firmware update tends to
break. Run it after any firmware update, or whenever the UI/voice acts up.

READ-ONLY: only GETs — no movement, no writes, safe to run anytime.

    python3 test_api.py [robot-ip]      # default 192.168.4.134

Exit 0 if all pass, 1 otherwise.

NOTE: this checks contract/shape, not values. It confirms motor/temperature still has the
per-leg keys, not that they're non-zero (an idle robot can legitimately report low/0).
"""
import sys, json, urllib.request

ROBOT = sys.argv[1] if len(sys.argv) > 1 else "192.168.4.134"
BASE = f"http://{ROBOT}:8088/api/v1"

# (label, GET path, [required dotted key-paths], who depends on it)
TESTS = [
    ("ping",             "system/ping",            ["success"],                                                        "liveness / firmware 2.4.5+ REST API"),
    ("battery status",   "battery/status",         ["data.percentage", "data.voltage", "data.is_charging"],            "panel battery %, voltage, charge"),
    ("battery charging", "battery/charging",        ["data.is_charging", "data.current"],                               "panel charging→desktop-mode rule (current)"),
    ("motor temps",      "motor/temperature",      ["data.front_left", "data.front_right", "data.back_left", "data.back_right"], "panel motor °C (per-leg keys)"),
    ("action list",      "action/list?limit=1",    ["data.action_base_path", "data.actions"],                          "voice_direct + panel resolve actions from these"),
    ("action status",    "action/status",          ["data.is_playing", "data.file_path"],                              "panel pose/stance derivation"),
    ("transform status", "transform/status",       ["data.body", "data.head"],                                         "panel pose derivation"),
    ("robot mode",       "user/robot-mode",        ["data.robot_mode"],                                                "panel desktop/ground toggle"),
    ("tof sensor",       "sensor/tof",             ["data.distances_2d"],                                              "spatial sensing (4x4 grid)"),
    ("imu sensor",       "sensor/imu",             ["data.orientation"],                                               "attitude"),
    ("ai credentials",   "ai/credentials/status",  ["data.llm", "data.asr"],                                           "voice pipeline config"),
]

def get(path):
    with urllib.request.urlopen(f"{BASE}/{path}", timeout=6) as r:
        return json.loads(r.read().decode())

def has_path(d, dotted):
    for k in dotted.split("."):
        if not isinstance(d, dict) or k not in d:
            return False
        d = d[k]
    return True

def main():
    print(f"Sirius API contract tests → {ROBOT}\n")
    fails = 0
    for label, path, required, why in TESTS:
        try:
            r = get(path)
            missing = [p for p in required if not has_path(r, p)]
            if missing:
                print(f"  FAIL  {label:16} — missing {missing}   ({why})"); fails += 1
            else:
                flag = "" if r.get("success", True) else "   [note: success=false]"
                print(f"  ok    {label:16} — {', '.join(required)}{flag}")
        except Exception as e:
            print(f"  FAIL  {label:16} — {type(e).__name__}: {e}   ({why})"); fails += 1
    print(f"\n{len(TESTS)-fails}/{len(TESTS)} passed")
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
