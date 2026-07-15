# Sirius Control Panel

A single-file, dependency-free local web UI for the **[Hengbot Sirius](https://hengbot.com)** robot dog.
Run it on any machine on the same network as your Sirius and control the robot from your browser —
live camera, driving, poses, and all of its built-in actions.

It's a tiny Python reverse-proxy that serves a self-contained control page **and** forwards API + camera
traffic to the robot, so the browser only ever talks to `localhost`. That means **no CORS headaches and no
mixed-content blocking** — the usual pain of pointing an `https://` page at an `http://`-only robot on your LAN.

> ⚠️ **Unofficial.** Not affiliated with or endorsed by Hengbot. Uses the robot's own local HTTP/WebSocket
> API. Use at your own risk.

## Features

- 📷 **Live camera** (MJPEG) — auto-enables the robot's video stream, with an on/off toggle
- 🎮 **Drive** — hold-to-move pad (forward / back / strafe / turn), speed slider, and gait modes
  (default / slow / walk / fast / precision / climb)
- 🧍 **Pose** — head yaw/pitch and body pitch/roll/yaw sliders, plus a reset
- 🎭 **Actions** — every built-in motion on the robot (200+), searchable, with a torque slider
- 🔋 **Live status** — battery %, charge current, charging state, and motor temperatures
- 🤖 **Manual / Autonomous toggle** — pauses the robot's autonomous behavior so your commands aren't
  overridden. Also drops into manual automatically the moment you drive, play an action, or move a pose slider.

## Requirements

- **Python 3** (standard library only — nothing to `pip install`)
- A Hengbot Sirius on the **same LAN**, and its **IP address**
- Robot firmware **2.4.5 or newer** (this exposes the v4.0.0 REST API on port `8088`).
  Older firmware runs a different API and is **not** supported — update the robot first.

## Quick start

```bash
# default: connects to 192.168.4.134, serves on http://localhost:8777
python3 sirius-panel.py

# or specify your robot's IP (and optionally a port)
python3 sirius-panel.py 192.168.1.50
python3 sirius-panel.py 192.168.1.50 9000
```

Then open **http://localhost:8777** in your browser.

Find your robot's IP from the Sirius face-screen menu (swipe to open it and look for the IP), or from your
router's client list.

## How it works

```
browser  ──►  http://localhost:8777        (this script)
                 ├─ /              → the control page (HTML/JS, inlined)
                 ├─ /api/v1/...    → proxied to  http://<robot>:8088   (REST API)
                 └─ /stream        → proxied to  http://<robot>:8080/video_stream  (MJPEG)
```

Because every request is same-origin (`localhost`), the browser never blocks it. The robot never needs to
send CORS headers, and you never hit the `https → http` mixed-content wall.

## Notes & troubleshooting

- **Camera is black?** The robot only emits video frames when vision detection is on. This panel turns it on
  automatically; the on/off toggle in the Camera header controls it (`POST /api/v1/vision/detection`).
- **Commands get overridden?** That's the robot's autonomous behavior re-asserting control. Use the
  **Autonomous/Manual** toggle in the header (it also auto-switches to manual when you issue any command).
- **`online` never shows / everything errors?** Confirm the IP is right and the robot is on firmware 2.4.5+
  (`curl http://<robot>:8088/api/v1/system/ping` should return a `pong`).
- **Nothing on port 8088?** Your robot is likely on older firmware without the REST API. Update it via the
  robot's on-device OTA first.

## License

[MIT](LICENSE) — do whatever you like, no warranty.
