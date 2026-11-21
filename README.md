# BILLEE Dashboard

One web page to run the BILLEE rover from the ground station: pick a mode, press **Run**, and the
dashboard starts both dev containers and launches the rover and the ground station. It also
brings CAN up, clears ODESC faults and opens the F´ GDS pages and Foxglove for you.

It lives in [billee-software-2027](https://github.com/BILLEE-BroncoSTAR/billee-software-2027) as
the `dashboard/` submodule. From that repo's root, on the ground station:

```bash
make setup-dash
```

```bash
make start
```

`make start` opens `http://localhost:7780` and the RCM GDS (`http://10.110.0.230:5000`).

## On a Mac

A Mac can be the ground station for **controlling** the rover (not for running sims). Run
`make setup-dash` and `make start` on the Mac itself, not in a container: a Docker container on
macOS can't see a USB or Bluetooth gamepad, and ROS traffic doesn't cross Docker Desktop's VM.
On macOS the dashboard switches itself to `ground.mode: none`:
- nothing runs on the Mac but the dashboard (Python 3 and `ssh` ship with macOS, so no Docker);
- every mode runs on the rover only, and the gamepad is read by the browser (below);
- Foxglove: install the desktop app; the dashboard opens it on the rover's bridge;
- Sim · Ground station only isn't available.

## Gamepad from the browser

**Gamepad on: This browser** (the default on a Mac; also on Linux) reads the controller with the
browser's Gamepad API (Xbox or PS5, USB or Bluetooth) and drives the rover with it:

```
page (20 Hz, Linux joy_node layout) → dashboard → SSH tunnel → joy_relay → /joy
    → joy_tank_drive → /diff_drive_controller/cmd_vel_unstamped
```

- The rover runs [`billee_dash/joy_relay.py`](billee_dash/joy_relay.py) (written into its
  container when the mode starts) and the same `joy_tank_drive` node, with the same
  `teleop/config/joystick.yaml`. Hold **LB** to drive, as with a gamepad on the ground station.
- The mode card shows the pad, the two sticks and LB live. Press any button once after plugging
  it in: browsers only show a gamepad after that.
- **It stops on its own.** Sending stops when the tab is hidden or the pad disconnects. The relay
  publishes a zero `/joy` (deadman released) 0.3 s after input stops, and
  `diff_drive_controller` stops the wheels 0.25 s after its last command whatever happens (a
  dropped link, a crashed browser). After a stall, only the newest gamepad state is used.

## How it works

```
 Ground station (x86, 10.110.0.231)                     Rover (Jetson, 10.110.0.230)
 ┌──────────────────────────────────────┐   SSH         ┌──────────────────────────────────┐
 │ browser ── commander (billee-dash)   │══ tunnel ════▶│ agent.py (listener, host)        │
 │              │ in-process agent      │               │   ├─ host: can-up, clear-faults, │
 │              ├─ docker exec ────────▶│ x86 container │   │        systemctl (GDS units)  │
 │              │   pixi -e default     │               │   └─ docker exec ─▶ l4t container│
 │              └─ opens GDS, Foxglove  │               │        pixi -e l4t  ros2 launch  │
 └──────────────────────────────────────┘               └──────────────────────────────────┘
```

- **One devtool, two roles.** The commander (this repo, on the ground station) serves the page
  and decides what to run. The listener, [`billee_dash/agent.py`](billee_dash/agent.py), runs the
  commands. The ground station runs it inside the commander. On the rover the commander uploads
  it over SSH on connect and starts it, so there is nothing to install or keep in sync there.
- **The same steps you'd type.** A ROS launch runs as
  `docker exec <devcontainer> bash -c "cd ros2_ws && pixi run -e l4t ros2 launch …"`, which is
  the same as SSH-ing in, opening the dev container, running `pixi shell -e l4t` and launching.
  The commands come from `docs/running.md`.
- **Dev containers.** The dashboard finds them by the labels VS Code puts on them, starts them
  with `docker start` and runs their `postStartCommand`, as **Reopen in Container** does. A
  container that was never built needs one VS Code build first (or the devcontainer CLI, see
  setup).
- **Radio-link safe.** The rover's agent keeps running when the link drops. Its processes keep
  running and their output is buffered there, then fetched on reconnect. Ctrl-C on the dashboard
  stops what it started on the **ground station** only. When the dashboard restarts, it picks up
  the mode still running on the rover.
- **Stop is Ctrl-C.** Each process runs in its own process group inside the container. Stop sends
  SIGINT to the whole group (so `ros2 launch` shuts its nodes down cleanly), then SIGTERM after
  10 s and SIGKILL after 15 s.
- **Standard library only.** Python 3.8+, `ssh` and `docker`; no pip packages and no web CDNs, so
  it works offline in the field.

## Modes

The modes follow the rover state machine. Each has defaults; change any of them on the mode's card
(the basic ones) or under **Advanced**. **Commands** shows exactly what will run.

| Mode | Rover (l4t) | Ground station (x86) | Opens |
|---|---|---|---|
| **Teleop · Drive** | `can-up` → `can-clear-faults` → `rover.launch.py mode:=real camera:=both` (+ gamepad relay, with **This browser**) | `ground_station.launch.py use_sim_time:=false view:=both` | Foxglove |
| **Teleop · Arm** | Drive, + `arm_bringup arm.launch.py` (option) | Drive, + `arm_teleop.launch.py joy:=false` (option) | Foxglove |
| **Teleop · Science** | Drive without the ZED 2i (`camera:=none`) | `view:=drivetrain` | Foxglove, Science GDS |
| Autonomous | TODO | | |
| Astronaut | TODO | | |
| Sim · Ground station only | nothing | `sim_gz.launch.py foxglove:=true` | Foxglove (local) |
| Bench test · Jetson only | `can-up` → `can-clear-faults` → `onboard_test.launch.py` | nothing | |

Options cover every argument in `running.md`:
- **drivetrain:** real ODESCs, or Gazebo on the Jetson (`xvfb-run -a vglrun -d egl`);
- **ZED:** `camera:=none|cloud|openpose|both`;
- **gamepad:** on the ground station, in this browser, or on the rover;
- **CAN:** `can_interface`, `fault_policy`, `gear_ratio`, `vio`, `odom_tf`, `arm_camera`;
- **ground station:** the RViz `view`, `fixed_frame`, RViz and Foxglove-bridge toggles;
- **extra:** free `name:=value` arguments for either side.

Values are checked before they reach a command line, so nothing typed in the page can inject
shell syntax.

Run order: rover link → rover container → ground container → CAN steps → rover launch → (5 s)
→ ground launch → links. Pressing **Run** on another mode while one is running offers to switch.

**Links.**
- The **RCM GDS** opens once when the dashboard starts.
- **Foxglove** opens on every mode, connected straight to the rover's bridge at
  `ws://10.110.0.230:8765`. With `foxglove.open_with: auto`, the desktop app is used if it is
  installed (a `foxglove://` link), otherwise app.foxglove.dev.
  The layout for every mode is billee-software-2027's
  `ros2_ws/src/chassis_bringup/foxglove/rover_dashboard.json`: import it once (**Layouts → Import
  from file**) and Foxglove keeps it.
- **Teleop · Science** also opens the **Science GDS** (`:5001`).

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
means every wheel node. `can-up` needs sudo on the rover without a password: `make setup-dash`
offers a narrow sudo rule for that.

## Output

**Output** (bottom bar) shows the verbose terminal output of both machines:
- **Rover** (l4t container and host), **Ground** (x86 container) or **Dashboard** (what it did and
  why), or **All** interleaved;
- filter to one process from the list on the left, and stop any process from there.

ROS log colours are kept.

**Systems** shows:
- the rover link, the free RAM on each machine and each dev container's state, with buttons to
  start it or Ctrl-C everything the dashboard started in it;
- the two GDS services (`billee-rcm-lan-gds`, `billee-scm-lan-gds`), with a restart button.

## Setup (`make setup-dash`)

[`scripts/setup.sh`](scripts/setup.sh), run on the ground station host:
1. installs `python3`, `ssh`, `curl`, `xdg-utils` if missing, and checks Docker access;
2. asks for the rover's address, SSH user and repo path, and writes `config/local.json`;
3. makes an SSH key if you have none and copies it to the rover (you type the password once);
4. checks the rover: python3, docker, the repo, `can-up`, the l4t container, the GDS services;
5. offers `/etc/sudoers.d/billee-dash` on the rover. It is checked with `visudo -c` and only allows
   `ip`, `modprobe vcan` and start/stop/restart of the two GDS units, without a password;
6. optionally installs the devcontainer CLI, which is only needed to *create* a container that
   was never built.

Non-interactive: `YES=1 ROVER_USER=billee make setup-dash`.

## Configuration

`config/dashboard.json` holds the defaults. `config/local.json` (written by setup, not in git)
overrides any of them:

| Key | Default | |
|---|---|---|
| `rover.host` / `rover.user` | `10.110.0.230` / (setup) | SSH target |
| `rover.repo` | `~/billee-software-2027` | on the rover host |
| `rover.devcontainer` / `ground.devcontainer` | `.devcontainer/l4t/…`, `.devcontainer/devcontainer.json` | which container |
| `*.container_name` | empty | use a fixed container name instead of the labels |
| `rover.services` | the two GDS units | shown under Systems |
| `rover.joy_relay_port` | `8771` | the gamepad relay on the rover (reached through the SSH tunnel) |
| `ground.mode` | `auto` | `container` (x86 ground station), `none` (rover only, e.g. a Mac); `auto` = `none` on macOS |
| `links.*` | `:5000`, `:5001`, `ws://…:8765` | GDS and Foxglove |
| `foxglove.open_with` | `auto` | `desktop`, `web` or `auto` |
| `startup.open_rcm_gds` / `startup.start_containers` | `true` | at `make start` |
| `mode.ground_delay_s` | `5` | wait between the rover and ground launches |

Command-line options: `./billee-dash --help` (`--no-browser`, `--port`, `--no-autostart`,
`--config extra.json`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| Rover link `down: Permission denied` | `make setup-dash` (copies your SSH key). |
| Rover link `down: No route to host` | The rover is off or the radio link is down. It reconnects by itself. |
| `can-up`: `a password is required` | Install the sudo rule: `make setup-dash`, step 5. |
| Container `missing` | Build it once in VS Code (**Reopen in Container**), or install the devcontainer CLI in setup. |
| Docker `permission denied` | `sudo usermod -aG docker $USER`, then log out and in (on that machine). |
| Foxglove opens the web app instead of the desktop app | Install the desktop app, or set `foxglove.open_with` to `desktop`. The web app can't reach `ws://` addresses on the LAN in every browser. |
| Leftover ROS processes after the dashboard was killed | **Systems → Stop all** on that container. |
| Gamepad box says "No gamepad" | Press a button on it: browsers hide a pad until then. Use Chrome or Safari on the Mac. |
| Gamepad box says "gamepad relay not reachable" | The relay is still starting, or it died: see Rover output, process **gamepad relay**. |

## Development

```bash
python3 -m unittest discover -s tests       # modes, agent, HTTP auth, gamepad relay (stub rclpy)
./billee-dash --rover-local --no-browser    # "rover" agent in-process, no SSH (testing)
```

The rover's agent logs to `~/.billee-dash/agent.log` on the Jetson.
