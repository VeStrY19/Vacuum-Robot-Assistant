# Nexo — a voice assistant mounted on a robot vacuum

Nexo is a fully local voice assistant (no cloud service) running on an Intel NUC mounted on a Roborock S5 robot vacuum (Valetudo firmware). You talk to it (or type), it answers aloud, and it can move the robot to a room or a person so it can talk with them.

Examples: "go to Stevens", "go to room 2", "stop the robot", "start cleaning", "what is the battery level?".

---

## 1. Architecture

```
Mic ──► Whisper (OpenVINO, Intel Arc GPU) ──► text
                                                │
                                                ▼
                            Qwen2.5 7B (Ollama) + tool-calling
                                                │  decides to call a tool, or just answers
                                                ▼
                                MCP client (nexo_mcp_assistant.py)
                                                │
                                                ▼
                                MCP server (robot_mcp_server.py)
                                                │
                                                ▼
                                       robot_control.py
                                ┌───────────────┴────────────────┐
                                ▼                                ▼
                  python-miio (miio protocol)          Valetudo REST API
                  start / stop / pause / home / status  goto (move to a point)
                                └───────────────┬────────────────┘
                                                ▼
                                      Roborock S5 robot

Text reply ──► Markdown cleanup ──► Piper (English voice) ──► speaker
```

Role of each component:

| Component | Role |
|---|---|
| **Whisper** (via OpenVINO / `optimum-intel`) | Transcribes speech to text, accelerated on the Intel GPU |
| **Qwen2.5 7B** (via Ollama) | Understands the sentence, decides whether to call a robot tool or just chat |
| **MCP** (FastMCP) | Standard way to expose the robot's functions to the LLM as "tools" |
| **python-miio** | The only layer that actually talks to the robot over the miio protocol (start, stop, pause, home, status) |
| **Valetudo REST API** | Used to move to a point (`goto`), see §6 |
| **Piper** | Local text-to-speech |

MCP does not replace python-miio: MCP exposes the functions to the LLM, python-miio executes them on the robot.

`python-miio` is installed with `pip install python-miio`, which fetches the package from PyPI. This is the distribution of the same project as the GitHub repository `rytilahti/python-miio`: same code, only the installation channel differs.

---

## 2. Hardware and environment

- Intel NUC14RVS mini-PC (Intel Arc iGPU), Ubuntu 24.04, Python 3.12, mounted on the robot
- Roborock S5 robot (model `roborock.vacuum.s5`) running Valetudo
- Microphone and speaker plugged into the NUC
- Working folder on the NUC: `~/voice-assistant/` (with its `venv`)

---

## 3. Installation

### 3.1 Intel GPU drivers (to accelerate Whisper)

The packages `intel-level-zero-gpu` and `level-zero` do not exist in the standard Ubuntu 24.04 repositories. Use Intel's PPA:

```bash
sudo apt-get update
sudo apt-get install -y software-properties-common
sudo add-apt-repository -y ppa:kobuk-team/intel-graphics
sudo apt-get update
sudo apt-get install -y libze-intel-gpu1 libze1 intel-metrics-discovery intel-opencl-icd clinfo intel-gsc

sudo usermod -aG render $USER
sudo usermod -aG video $USER
sudo reboot
```

Check after rebooting:

```bash
clinfo | grep "Device Name"     # should show Intel(R) Arc(TM) Graphics
```

### 3.2 System packages

```bash
sudo apt install -y ffmpeg alsa-utils
```

If installing `pyaudio` fails: `sudo apt install portaudio19-dev`.

### 3.3 Python packages (inside the `~/voice-assistant` venv)

```bash
pip install "optimum[openvino]" openvino
pip install "transformers>=4.46,<5.0" "huggingface_hub>=0.34,<1.0"
pip install pyaudio numpy ollama python-miio fastmcp piper-tts requests
```

The `transformers` and `huggingface_hub` versions are deliberately capped: `transformers` 5.x is incompatible with `optimum-intel` and caused repetition loops in Whisper. A pip warning about `nncf` and `numpy` is harmless here.

Watch out for venvs: a package installed in one venv does not exist in another. A `No module named 'optimum'` or `No module named 'miio'` error almost always means the wrong venv.

### 3.4 Models

```bash
ollama pull qwen2.5:7b
python -m piper.download_voices en_US-lessac-medium
```

Piper does not download voices automatically: without the second command it fails with `Unable to find voice`.

---

## 4. Connecting to the robot (IP and token)

`python-miio` needs the robot's IP and its miio token. The token is never sent in clear over the network: `miiocli discover` only gives the IP and the device ID, with a fake token made only of `f`.

The robot runs Valetudo, so it is rooted and the token can be read directly on it. SSH uses a key (the `.id_rsa` file generated during flashing), not a password.

```bash
chmod 600 <key>.id_rsa        # otherwise SSH ignores the key (permissions too open)
ssh -i <key>.id_rsa root@<ROBOT_IP> "cat /mnt/data/miio/device.token" > device.token
python3 -c "print(open('device.token','rb').read().hex()[:32])"
```

Things to know:
- The robot's BusyBox has neither `xxd` nor `od`, and `scp` fails (`sftp-server: not found`). Work around it with `ssh ... "cat ..." > file`, then convert to hex on the NUC.
- The file holds 16 bytes plus a newline (`0a`). The valid token is 32 hex characters, hence the `[:32]`.
- The private key and the token give control of the robot. Do not publish them (public Git repo, project submission, etc.).

Check:

```bash
miiocli device --ip <ROBOT_IP> --token <TOKEN> info
miiocli roborockvacuum --ip <ROBOT_IP> --token <TOKEN> status
```

The first should show `Model: roborock.vacuum.s5`, the second the state (`state=Charging`, battery, errors).

Where to set the IP and token: the `ROBOT_IP` and `ROBOT_TOKEN` environment variables, e.g. `export ROBOT_IP=<ROBOT_IP> ROBOT_TOKEN=<TOKEN>` before launching the assistant.

---

## 5. Project files

| File | Role |
|---|---|
| `nexo_mcp_assistant.py` | **Main script.** Voice → Whisper → Qwen with MCP tools → Piper |
| `robot_mcp_server.py` | MCP server exposing the robot's 6 tools. Started automatically by the main script |
| `robot_control.py` | Robot controller (python-miio + Valetudo API) |
| `rooms_config.json` | Named places (rooms, people) and their coordinates |
| `nexo_robot_assistant.py` | Older version without MCP (hand-made JSON intent classification). Kept for reference |
| `nexo_assistant_openvino.py` | Minimal version: voice + Qwen + Piper, no robot |
| `voice_control.py`, `requirements.txt` | Early drafts (openWakeWord wake word), unused and untested |

Exposed MCP tools:

| Tool | Effect |
|---|---|
| `start_cleaning` | Starts a full automatic cleaning cycle |
| `stop_cleaning` | Stops the robot |
| `pause_cleaning` | Pauses cleaning |
| `return_to_dock` | Sends the robot back to its dock |
| `go_to_location` | Moves the robot, without cleaning, to a place or person from `rooms_config.json` |
| `get_status` | Returns battery, state and errors |

`start_cleaning` launches the robot's automatic cleaning cycle. It is not manual driving (forward, back, turn), which is not implemented.

---

## 6. Moving to a point: coordinates and the Valetudo API

This is the part that took the most trial and error.

- python-miio's `goto` command (`app_goto_target`) uses the robot's raw coordinate frame.
- Valetudo's map uses its own coordinates, in **centimetres**, with a different origin.
- The two systems cannot be converted with a simple ×10. Sending Valetudo coordinates to `vac.goto()` sends the robot off the map.

The chosen solution is to call Valetudo's API, which does the conversion itself:

```bash
curl -X PUT http://<ROBOT_IP>/api/v2/robot/capabilities/GoToLocationCapability \
  -H "Content-Type: application/json" \
  -d '{"action": "goto", "coordinates": {"x": 2413, "y": 2544}}'
```

- The `"action": "goto"` field is mandatory. Without it the API answers `Bad Request`.
- The robot must not be busy: otherwise the UI shows `Cannot go to point while the robot is busy`. Wait until it is stopped (`Idle` or `Charging`).
- `robot_control.py` makes this call in `goto_point()`.

---

## 7. Places and people: `rooms_config.json`

The file maps a name to a map point (Valetudo coordinates in cm):

```json
{
  "room 2": {"x": 2413, "y": 2544},
  "main room": {"x": 2196, "y": 2236},
  "stevens": {"x": 2300, "y": 2400},
  "david": {"x": 2500, "y": 2100}
}
```

The `stevens` and `david` values above are examples to replace.

### Adding a place or a person

1. In the Valetudo UI (`http://<ROBOT_IP>`), on the main map, place the "Go To" marker where the robot should stop. Put it a little in front of the person, not on their chair.
2. **Long-press** "GO TO LOCATION". A window shows the exact payload, with `x` and `y`.
3. Add the line to `rooms_config.json`, with the name in lowercase, i.e. the one you will say aloud.
4. Restart `python nexo_mcp_assistant.py`: the MCP server re-reads the file on each start.

JSON rules: a comma after every entry except the last, double quotes only, coordinates as numbers without quotes. An invalid file crashes startup and points to the faulty line.

### Name matching

- The `go_to_location` tool description is built at startup from the file, so Qwen knows the exact list of available names.
- Names are matched approximately (`difflib`, cutoff 0.6) to absorb transcription errors: "Stephens" or "Steven" resolve to `stevens`, "Davit" to `david`. A name that resembles nothing returns "Unknown location".

---

## 8. Valetudo map and segments (for the record)

The map was split into rooms, then we finally chose to navigate by points (§6-7) rather than by segments. What was learned:

- Valetudo only offers editable segments if some exist. A fully blue map has no segments.
- You must enable "Persistent maps", then let the robot do a **full, uninterrupted cleaning run** and return to its dock on its own.
- If the map stays stuck as one block or in an inconsistent state: **Map Reset** (Options → Robot-managed Map Features), then a new full cycle.
- Manual splitting (Segment Management) requires the robot to be on its dock. The cut line must cross the whole width of the area, edge to edge.
- `segmentId`s **change** when you edit or rename a segment. Do not hard-code them in a file before segmentation is finished.
- A segment cannot be deleted, only merged into another.
- To clean a specific segment from the command line: `miiocli roborockvacuum ... segment_clean <id>` (with an underscore; `cleansegment` does not exist).
- List the segments on the robot side:

```bash
curl -s http://<ROBOT_IP>/api/v2/robot/state/map | python3 -c "
import json, sys
d = json.load(sys.stdin)
for l in d.get('layers', []):
    print(l.get('type'), l.get('metaData'))
"
```

- In that output, `active: False` does not signal a problem: it only means the robot is not cleaning that segment right now.

---

## 9. Running the assistant

```bash
cd ~/voice-assistant
source venv/bin/activate
python nexo_mcp_assistant.py
```

Do not run `robot_mcp_server.py` by hand: the main script starts it as a subprocess.

At startup, the line `MCP tools available: [...]` should list the 6 tools. Each turn has two modes:

- **Enter** (typing nothing): turns the microphone on. Recording stops after 2 seconds of silence, and noises shorter than 0.5 s are ignored.
- **Type a sentence** then Enter: sent directly as text, no microphone. Handy for testing without disturbing anyone.

Saying (or typing) `quit` stops the assistant.

Language: everything is in English (Whisper forced to English, Piper voice `en_US-lessac-medium`, English system prompt). To change language, edit those three places.

---

## 10. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ModuleNotFoundError: optimum` or `miio` | Wrong venv. Reinstall the package in the venv you use |
| `FileNotFoundError: ffmpeg` | `sudo apt install ffmpeg` |
| `no supported devices found` (GPU) | Intel drivers missing: §3.1, `render`/`video` groups, reboot. Fallback: `OV_DEVICE = "CPU"` |
| Whisper repeats "Testine Testine…" forever | `transformers` 5.x is incompatible. Version capped at `<5.0`. The script also forces the language, penalises repetition and ignores recordings that are too short |
| `huggingface-hub` conflict | `pip install "transformers>=4.46,<5.0" "huggingface_hub>=0.34,<1.0"` |
| ALSA / JACK warnings at launch | Cosmetic. They are hidden in the script |
| `Unable to find voice` (Piper) | `python -m piper.download_voices en_US-lessac-medium` |
| The voice reads out asterisks | Qwen was answering in Markdown. The prompt forbids it and `strip_markdown()` cleans up as well |
| Qwen slow to answer | Ollama doesn't use the Arc iGPU natively, so the 7B runs on CPU. `keep_alive="30m"` avoids reloading the model. Faster fallback: `qwen2.5:3b` |
| `DeviceException: Unable to discover the device` | Robot powered off, or IP changed. Check with `ping` and `miiocli ... status` |
| `Bad Request` on goto | Missing `"action": "goto"` field in the request body |
| `Cannot go to point while the robot is busy` | Wait until the robot is stopped before sending another goto |
| Robot drives off the map | Valetudo coordinates were sent to `vac.goto()`: use the REST API (§6) |
| `miiocli` not found | The active venv doesn't have `python-miio` (the `miiocli` test was done in another venv) |

Errors returned by a tool (robot busy, off, etc.) no longer crash the assistant: they are passed to Qwen, which explains them aloud.

---

## 11. Project status

**Verified**
- Full voice chain: mic → Whisper (OpenVINO, GPU) → Qwen → Piper
- Robot connection: token retrieved, `info` and `status` work
- MCP tool-calling: Qwen correctly calls `start_cleaning` and the command reaches python-miio
- Movement through the REST API: validated from the dock to the "room 2" point
- Map split into 7 zones on the Valetudo side

**To test or to do**
- End-to-end `go_to_location` test by voice (the REST goto was validated with `curl` and the tool logic was tested in simulation, but not yet with the real robot through the assistant)
- Test the "main room" point, which was obtained by another method, and calibrate "room 3" and the people (§7)
- Wake word ("Hey Nexo"): for now you must press Enter or type
- Move the token out of the code (environment variable) before any sharing
- The `~/flasher` venv runs Python 3.8, which is end-of-life: not urgent, but should be migrated
- Optional: change the robot's own voice (Valetudo → Voice packs). The packs are known to be finicky on the S5 and unrelated to Nexo
- The robot has no camera. If you add one, a vision model such as `qwen2.5vl` could be added later
