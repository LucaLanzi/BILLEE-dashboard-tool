# BILLEE Dashboard

The easy way to operate the BILLEE rover: one web page on the ground station that replaces the
SSH sessions, dev-container terminals and `ros2 launch` commands. Pick a mode, press **Run**, and
the dashboard does the rest:
- starts the rover's and the ground station's dev containers;
- brings CAN up and clears ODESC faults;
- launches the rover and the ground station;
- opens the F´ GDS pages and Foxglove.

Everything both machines print is one click away.

It lives in [billee-software-2027](https://github.com/BILLEE-BroncoSTAR/billee-software-2027) as
the `BILLEE-dashboard-tool/` submodule.

## Quick start

On the ground station, from billee-software-2027's root:

```bash
make setup-dash
```

```bash
make start
```

`make setup-dash` asks for the **Jetson's IP address**, the SSH user (default `billee`) and the
repo paths on the rover, then sets up SSH and checks the rover (details in
[Setup](#setup-make-setup-dash)). Run both in a terminal on the ground station itself, not inside
a dev container: the dashboard starts the containers.

`make start` opens the dashboard at `http://localhost:7780` and the RCM GDS. Ctrl-C in that
terminal stops the dashboard and what it started on the ground station; the rover keeps running.

## Using it

- **State machine** (top): the rover state machine, live.
  - **Highlighting:** whichever mode runs lights up (blue while it starts, green running, amber if
    a process died), and so does the edge from Idle into it. Idle is lit when nothing runs.
  - **Devcontainers:** their boxes follow the real containers.
  - **Using it:** hover a state for its note, click one to select that mode (it doesn't run it),
    and collapse the strip from its header.
- **Modes** (left): pick one, check its settings, press **Run**. **Stop** shuts it down with
  Ctrl-C. Pressing **Run** on another mode while one is running offers to switch.
- **Settings** (middle): the common ones on the card, everything else under **Advanced**.
  **Commands** shows exactly what will run, and where.
- **Links** (top right): RCM GDS, Science GDS, Foxglove and the Astronaut page. The status pills
  next to them show what is reachable.
- **Systems** (right):
  - the rover link and the free RAM on each machine;
  - each dev container, with **Start** and **Stop all**;
  - the GDS services, with **Restart**.
- **CAN** (right): every `can-up` and `can-clear-faults` command, per node ([below](#can-panel)).
- **Output** (bottom bar): the verbose terminal output of the **Rover**, the **Ground** station,
  the **Dashboard** itself (what it did and why), or **All** of it. Pick a process on the left to
  see only its output, or stop it. ROS log colours are kept.

## Modes

The modes follow the rover state machine. Each starts from sensible defaults that you can change
before **Run**. The commands are the ones in billee-software-2027's `docs/running.md`.

| Mode | Rover | Ground station | Opens |
|---|---|---|---|
| **Teleop · Drive** | `can-up` → `can-clear-faults` → `rover.launch.py mode:=real camera:=both` | `ground_station.launch.py use_sim_time:=false view:=both` | Foxglove |
| **Teleop · Arm** | Drive, + `arm_bringup arm.launch.py` (option) | Drive, + `arm_teleop.launch.py joy:=false` (option) | Foxglove |
| **Teleop · Science** | Drive without the ZED 2i (`camera:=none`) | `view:=drivetrain` | Foxglove, Science GDS |
| Autonomous | TODO | | |
| **Astronaut** | hotspot up → astronaut server → stream watcher, + Drive (option) | Drive's (option) | Astronaut page, Foxglove |
| Sim · Ground station only | nothing | `sim_gz.launch.py foxglove:=true` | Foxglove (local) |
| Bench test · Jetson only | `can-up` → `can-clear-faults` → `onboard_test.launch.py` | nothing | |

The settings cover every argument in `running.md`:
- **drivetrain:** real ODESCs, or Gazebo on the Jetson (`xvfb-run -a vglrun -d egl`);
- **ZED:** `camera:=none|cloud|openpose|both`;
- **gamepad:** where it's read ([below](#gamepad));
- **CAN:** `can_interface`, `fault_policy`, `gear_ratio`, `vio`, `odom_tf`, `arm_camera`;
- **ground station:** the RViz `view`, `fixed_frame`, RViz and Foxglove-bridge toggles;
- **extra:** free `name:=value` arguments for either side.

Values are checked before they reach a command line, so nothing typed in the page can inject
shell syntax.

**Run order:**
1. the rover link, then the dev containers the mode needs;
2. the CAN or astronaut steps (a failed `can-up` or hotspot stops the run);
3. the rover launch, then 5 s later the ground launch;
4. the links.

### Astronaut

Runs billee-hardware-2027's astronaut-streamer on the Jetson host, in `~/billee-hardware-2027`
(`rover.hardware_repo`):
1. **Hotspot:** `sudo bash astronaut-streamer/jetson/hotspot.sh up` (or `start`, the saved one).
2. **Server:** `sudo systemctl restart astronaut-mediamtx astronaut-web`.
3. **The Pi** joins the hotspot and starts streaming by itself. A watcher in the Rover output
   reports when it does: the Pi's address, signal and temperature, and the feed going LIVE.
4. Opens the astronaut page (`https://<jetson>:8443`, with talk-back).

**Drive the rover too** (on by default) runs Teleop · Drive alongside it. **Stop** leaves the
server and hotspot running (they also start at boot) unless **Stop server + hotspot on Stop** is
on.

The streamer must be installed on the Jetson once, with its hotspot password: see
astronaut-streamer's `docs/RUN_GUIDE.md` (`make astronaut jetson`).

## Links

Every page the dashboard opens uses the **Jetson IP address entered in `make setup-dash`**.
Enter `192.168.4.97` and the dashboard opens:

| Link | Address | Opened |
|---|---|---|
| RCM GDS | `http://192.168.4.97:5000` | once, at `make start` |
| Science GDS | `http://192.168.4.97:5001` | with Teleop · Science |
| Foxglove | `ws://192.168.4.97:8765` (the rover's bridge) | with every mode |
| Astronaut | `https://192.168.4.97:8443` | with Astronaut |

Run `make setup-dash` again to change the address; every link follows.

Foxglove uses the desktop app if it's installed (a `foxglove://` link), otherwise
app.foxglove.dev (`foxglove.open_with`). The layout for every mode is billee-software-2027's
`ros2_ws/src/chassis_bringup/foxglove/rover_dashboard.json`: import it once
(**Layouts → Import from file**) and Foxglove keeps it.

## Gamepad

**Gamepad on** picks where the controller is read. Hold **LB** to drive in every case
(`teleop/config/joystick.yaml`):

| Setting | Reads it | Use when |
|---|---|---|
| **Ground station** | `joy_node` in the ground station's x86 container | the normal setup |
| **This browser** | the dashboard page (Gamepad API: Xbox or PS5, USB or Bluetooth) | the computer running the dashboard has no ground-station container, or `joy_node` can't see the pad |
| **Rover (bench)** | `joy_node` on the Jetson | bench testing with the pad plugged into the Jetson |

**This browser** streams the pad's state at 20 Hz through the dashboard's SSH tunnel to
[`joy_relay.py`](billee_dash/joy_relay.py) on the rover. The relay publishes `/joy` for the usual
`joy_tank_drive`. The mode card shows the pad, both sticks and LB live. Press any button once
after plugging it in: browsers only show a gamepad after that.

It stops on its own:
- the page stops sending when its tab is hidden or the pad disconnects;
- the relay publishes a zero `/joy` (deadman released) 0.3 s after input stops;
- `diff_drive_controller` stops the wheels 0.25 s after its last command, whatever happens (a
  dropped link, a crashed browser).

## CAN panel

Every command of `tooling/can-up` and `tooling/can-clear-faults`, run on the Jetson host:

| Button | Runs |
|---|---|
| Up / Status / Down | `tooling/can-up <iface> [bitrate]`, `can-up status <iface>`, `can-up down <iface>` (`vcan0` creates a virtual bus) |
| Read errors | `can-clear-faults --iface <iface> [--nodes …] --status` |
| Clear faults | `can-clear-faults --iface <iface> [--nodes …]` |
| Clear + reboot stuck | `… --reboot` (reboots boards whose error a clear can't fix) |
| Reboot | `… --force-reboot` (reboots the selected boards, or all; asks first) |

Pick nodes with the chips (named from `ros2_ws/src/odesc/config/node_map.yaml`); none selected
means every wheel node.

## Setup (`make setup-dash`)

[`scripts/setup.sh`](scripts/setup.sh), run on the ground station host (not in a container). It
is safe to run again at any time:
1. installs `python3`, `ssh`, `curl` and `xdg-utils` if missing, and checks Docker access;
2. asks for the Jetson's IP address, the SSH user (default `billee`), and the billee-software-2027 and
   billee-hardware-2027 paths on the rover, and saves them in `config/local.json`;
3. makes an SSH key if you have none and copies it to the rover (you type the password once);
4. checks the rover:
   - python3, docker, the repo and `can-up`;
   - the l4t container and the GDS services;
   - the astronaut-streamer and its services;
5. offers `/etc/sudoers.d/billee-dash` on the rover, checked with `visudo -c`. Without a password
   it only allows:
   - `ip` and `modprobe vcan` (for `can-up`);
   - the astronaut `hotspot.sh up|start|stop`;
   - start, stop and restart of the GDS and astronaut units.

   The hotspot script lives in your home directory, so this lets your user run that script as
   root. That is fine on the team's Jetson, but it's worth knowing;
6. optionally installs the devcontainer CLI. It's only needed to *create* a container that was
   never built.

Non-interactive: `YES=1 ROVER_HOST=192.168.4.97 make setup-dash` (`ROVER_USER=` for a user other
than `billee`).

**Which computer.** The ground station is the x86 Linux machine with the ground-station dev
container, and the dashboard runs the ground side (RViz, the gamepad, a local Foxglove bridge)
there. Any other computer with Python 3 and `ssh` can run the dashboard without a ground-station
container: every mode then runs on the rover only, and the gamepad is read by the browser.
- **macOS:** the dashboard switches to this by itself, with no Docker needed.
- **Any other machine:** set `ground.mode` to `none`.

## Configuration

`config/dashboard.json` holds the defaults. `config/local.json` (written by setup, not in git)
overrides any of them:

| Key | Default | |
|---|---|---|
| `rover.host` / `rover.user` | `10.110.0.230` / (setup) | the Jetson; every link uses this address |
| `rover.repo` / `rover.hardware_repo` | `~/billee-software-2027` / `~/billee-hardware-2027` | on the Jetson host |
| `rover.devcontainer` / `ground.devcontainer` | `.devcontainer/l4t/…`, `.devcontainer/devcontainer.json` | which container |
| `*.container_name` | empty | use a fixed container name instead of the labels |
| `rover.services` | the two GDS units | shown under Systems |
| `rover.joy_relay_port` | `8771` | the gamepad relay on the rover (reached through the SSH tunnel) |
| `ground.mode` | `auto` | `container` (runs the ground side here), `none` (rover only); `auto` = `none` on macOS |
| `links.*` | `http://{rover}:5000`, `:5001`, `ws://{rover}:8765`, `https://{rover}:8443` | `{rover}` is `rover.host` |
| `astronaut.web_port` | `8080` | the astronaut server's status API, on the Jetson |
| `foxglove.open_with` | `auto` | `desktop`, `web` or `auto` |
| `startup.open_rcm_gds` / `startup.start_containers` | `true` | at `make start` |
| `mode.ground_delay_s` | `5` | wait between the rover and ground launches |

Options: `make start ARGS="--help"`, e.g. `make start ARGS="--no-browser --port 7781"`
(`--no-browser`, `--port`, `--no-autostart`, `--config extra.json`).

## How it works

```
 Ground station                                         Rover (Jetson)
 ┌──────────────────────────────────────┐   SSH         ┌──────────────────────────────────┐
 │ browser ── commander (billee-dash)   │══ tunnel ════▶│ agent.py (listener, host)        │
 │              │ in-process agent      │               │   ├─ host: can-up, clear-faults, │
 │              ├─ docker exec ────────▶│ x86 container │   │   astronaut, GDS services    │
 │              │   pixi -e default     │               │   └─ docker exec ─▶ l4t container│
 │              └─ opens GDS, Foxglove  │               │        pixi -e l4t  ros2 launch  │
 └──────────────────────────────────────┘               └──────────────────────────────────┘
```

- **One devtool, two roles.** The commander (this repo, on the ground station) serves the page
  and decides what to run. The listener, [`billee_dash/agent.py`](billee_dash/agent.py), runs the
  commands. On the ground station it runs inside the commander. On the rover the commander uploads
  it over SSH on connect and starts it, so there is nothing to install or keep in sync there.
- **The same steps you'd type.** A ROS launch runs as
  `docker exec <devcontainer> bash -c "cd ros2_ws && pixi run -e l4t ros2 launch …"`, which is
  what SSH-ing in, opening the dev container, `pixi shell -e l4t` and launching would do.
- **Dev containers.** The dashboard finds them by the labels VS Code puts on them, starts them
  with `docker start` and runs their `postStartCommand`, as **Reopen in Container** does.
- **Radio-link safe.**
  - The rover's agent and its processes keep running when the link drops. Their output is
    buffered there and fetched on reconnect.
  - Ctrl-C on the dashboard stops what it started on the ground station only.
  - A restarted dashboard picks up the mode still running on the rover.
- **Stop is Ctrl-C.** Each process runs in its own process group. Stop sends SIGINT to the whole
  group (so `ros2 launch` shuts its nodes down cleanly), then SIGTERM after 10 s and SIGKILL after
  15 s.
- **Standard library only.** Python 3.8+, `ssh` and `docker`; no pip packages and no web CDNs, so
  it works offline in the field.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `make setup-dash`: "This is a dev container" | Run it in a terminal on the ground station itself, not in a dev container (a prompt like `ros_user@rover:/workspaces/…` is the rover's container). |
| `make start`: "the dashboard is already running" | One is already open from another terminal: it opens that one. To restart it, Ctrl-C in that terminal (`lsof -nP -iTCP:7780 -sTCP:LISTEN` finds it). |
| Rover link `down: Permission denied` | `make setup-dash` (copies your SSH key). |
| Rover link `down: No route to host` | The rover is off or the radio link is down. It reconnects by itself. |
| `can-up` or the hotspot: `a password is required` | Install the sudo rule: `make setup-dash`, step 5. |
| Astronaut: `astronaut-streamer is not set up` | On the Jetson, in `~/billee-hardware-2027`: `make rover`, then `make astronaut jetson`. |
| Astronaut: `HOTSPOT_PSK is empty` | Set the hotspot password in `astronaut-streamer/config/astronaut.local.env` on the Jetson. |
| Astronaut page warns about the certificate | It's the Jetson's own; accept it once (needed for talk-back). |
| Container `missing` | Build it once in VS Code (**Reopen in Container**), or install the devcontainer CLI in setup. |
| Docker `permission denied` | `sudo usermod -aG docker $USER`, then log out and in (on that machine). |
| Foxglove opens the web app instead of the desktop app | Install the desktop app, or set `foxglove.open_with` to `desktop`. The web app can't reach `ws://` addresses on the LAN in every browser. |
| Leftover ROS processes after the dashboard was killed | **Systems → Stop all** on that container. |
| Gamepad box says "No gamepad" | Press a button on it: browsers hide a pad until then. |
| Gamepad box says "gamepad relay not reachable" | The relay is still starting, or it died: see Rover output, process **gamepad relay**. |

## Development

```bash
python3 -m unittest discover -s tests       # = make test-dash in billee-software-2027
./billee-dash --rover-local --no-browser    # "rover" agent in-process, no SSH (testing)
```

The rover's agent logs to `~/.billee-dash/agent.log` on the Jetson.
