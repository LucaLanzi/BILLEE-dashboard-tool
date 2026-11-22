"""Operating modes: what each one runs on the rover and the ground station.

Every mode is a state of the rover state machine (docs: README.md). A mode has a set of options
with defaults; the dashboard shows them, the operator may change them, and `build()` turns them
into the commands from billee-software-2027/docs/running.md:

    rover  (l4t container,  pixi -e l4t)     ros2 launch chassis_bringup rover.launch.py ...
    ground (x86 container,  pixi -e default) ros2 launch chassis_bringup ground_station.launch.py ...

Values are checked against each option's type and pattern before they reach a command line, so
nothing typed in the page can inject shell syntax.
"""

import re

# --------------------------------------------------------------------------- options
# side: which machine the option affects (shown in the page as a tag).
# basic: shown on the mode card; the rest sit under "Advanced".
OPTIONS = {
    "drivetrain": {
        "label": "Drivetrain", "side": "rover", "type": "select", "basic": True,
        "choices": [["real", "Real (ODESCs on CAN)"], ["sim", "Sim (Gazebo on the Jetson)"]],
        "default": "real",
        "help": "rover.launch.py mode:=. Sim runs Gazebo headless (xvfb-run + vglrun).",
    },
    "camera": {
        "label": "ZED 2i", "side": "rover", "type": "select", "basic": True,
        "choices": [["none", "Off"], ["video", "Video"], ["cloud", "Video + point cloud"],
                    ["both", "Video + point cloud + OpenPose"],
                    ["openpose", "Video + OpenPose"]],
        "default": "none",
        "help": "rover.launch.py camera:=video|cloud|both|openpose. The video "
                "(/zed/image/compressed) shows the skeletons when OpenPose runs. OpenPose needs "
                "~3 GB free RAM on the Jetson.",
    },
    "gamepad": {
        "label": "Gamepad on", "side": "both", "type": "select", "basic": True,
        "choices": [["ground", "Ground station"], ["browser", "This browser"],
                    ["rover", "Rover (bench)"]],
        "default": "ground",
        "help": "Where the gamepad is read. Ground station: joy_node in the x86 container. "
                "This browser: the dashboard page reads it (works on a Mac, no container) and "
                "sends it to the rover. Rover: plugged into the Jetson. Teleop runs on exactly "
                "one machine, or every command is sent twice.",
    },
    "can_up": {
        "label": "Bring CAN up first", "side": "rover", "type": "bool", "default": True,
        "help": "tooling/can-up <iface> on the Jetson host before launching (real drivetrain).",
    },
    "clear_faults": {
        "label": "Clear ODESC faults first", "side": "rover", "type": "bool", "default": True,
        "help": "tooling/can-clear-faults on the Jetson host after CAN is up (real drivetrain).",
    },
    "can_interface": {
        "label": "CAN interface", "side": "rover", "type": "text", "default": "can2",
        "pattern": r"v?can[0-9]{1,2}", "placeholder": "can2",
        "help": "The drivetrain's bus. The USB-CAN adapter has also shown up as can3.",
    },
    "fault_policy": {
        "label": "Wheel fault policy", "side": "rover", "type": "select",
        "choices": [["warn", "Warn, keep driving"], ["fault", "Stop the drivetrain"]],
        "default": "warn", "help": "rover.launch.py fault_policy:= (real drivetrain).",
    },
    "vio": {
        "label": "ZED VIO + EKF", "side": "rover", "type": "bool", "default": False,
        "help": "rover.launch.py vio:=true: the EKF publishes odom (real drivetrain).",
    },
    "odom_tf": {
        "label": "Wheel-odometry TF", "side": "rover", "type": "bool", "default": True,
        "help": "rover.launch.py odom_tf:= (forced off by VIO).",
    },
    "gear_ratio": {
        "label": "Gear ratio", "side": "rover", "type": "number", "default": 48.0,
        "min": 1, "max": 500, "help": "rover.launch.py gear_ratio:= (motor turns per wheel turn).",
    },
    "arm_camera": {
        "label": "Arm RealSense", "side": "rover", "type": "select",
        "choices": [["auto", "If plugged in"], ["true", "Always"], ["false", "Never"]],
        "default": "auto", "help": "rover.launch.py arm_camera:=.",
    },
    "rover_extra": {
        "label": "Extra rover args", "side": "rover", "type": "args", "default": "",
        "placeholder": "name:=value ...", "help": "Appended to the rover launch.",
    },
    "view": {
        "label": "RViz layout", "side": "ground", "type": "select",
        "choices": [["auto", "Match the ZED"], ["drivetrain", "Drivetrain"], ["video", "Video"],
                    ["cloud", "Point cloud"], ["openpose", "OpenPose"], ["both", "Both"]],
        "default": "auto", "help": "ground_station.launch.py view:=.",
    },
    "rviz": {
        "label": "RViz on the ground station", "side": "ground", "type": "bool", "default": True,
        "help": "ground_station.launch.py rviz:=.",
    },
    "ground_foxglove": {
        "label": "Ground Foxglove bridge", "side": "ground", "type": "bool", "default": True,
        "help": "ground_station.launch.py foxglove:= (ws://localhost:8765). The dashboard opens "
                "Foxglove on the rover's bridge either way.",
    },
    "fixed_frame": {
        "label": "Fixed frame", "side": "ground", "type": "select",
        "choices": [["odom", "odom"], ["base_link", "base_link (ride along)"]],
        "default": "odom", "help": "ground_station.launch.py fixed_frame:=.",
    },
    "ground_extra": {
        "label": "Extra ground args", "side": "ground", "type": "args", "default": "",
        "placeholder": "name:=value ...", "help": "Appended to the ground station launch.",
    },
    "status_light": {
        "label": "Mode light (blink(1))", "side": "rover", "type": "bool", "default": True,
        "help": "Show the mode on the rover's blink(1): solid blue in Teleop modes, solid red in "
                "Autonomous / Astronaut (rover.launch.py status_light:=, package status_light). "
                "While nothing runs, the dashboard shows Idle as a slow yellow pulse.",
    },
    # Astronaut
    "astro_drive": {
        "label": "Drive the rover too", "side": "rover", "type": "bool", "basic": True,
        "default": True,
        "help": "Also run Teleop · Drive (CAN, rover.launch.py, gamepad) with the stream.",
    },
    "hotspot": {
        "label": "Hotspot", "side": "rover", "type": "select", "basic": True,
        "choices": [["up", "Recreate (hotspot.sh up)"], ["start", "Start the saved one"]],
        "default": "up",
        "help": "astronaut-streamer/jetson/hotspot.sh on the Jetson. up recreates it from "
                "config/astronaut.env (+ astronaut.local.env for the password).",
    },
    "astro_stop": {
        "label": "Stop server + hotspot on Stop", "side": "rover", "type": "bool", "default": False,
        "help": "Off: Stop leaves the astronaut server and hotspot running (they also start at "
                "boot). On: Stop also stops astronaut-web, astronaut-mediamtx and the hotspot.",
    },
    # Teleop-Arm
    "arm_hw": {
        "label": "Arm hardware (arm.launch.py)", "side": "rover", "type": "bool", "basic": True,
        "default": False,
        "help": "ros2 launch arm_bringup arm.launch.py on the rover. Needs the arm_bringup "
                "package (branch kevin/arm-bringup).",
    },
    "arm_jog": {
        "label": "Arm jog teleop", "side": "ground", "type": "bool", "basic": True,
        "default": False,
        "help": "ros2 launch arm_bringup arm_teleop.launch.py joy:=false on the ground station, "
                "reading the same gamepad.",
    },
    # Ground-station sim
    "gui": {
        "label": "Gazebo window", "side": "ground", "type": "bool", "basic": True, "default": True,
        "help": "sim_gz.launch.py gui:=.",
    },
    "navigation": {
        "label": "EKF + Nav2", "side": "ground", "type": "bool", "default": True,
        "help": "sim_gz.launch.py navigation:=. Off saves CPU but removes odom.",
    },
    "odesc_shadow": {
        "label": "ODESC shadow on vcan0", "side": "ground", "type": "bool", "default": False,
        "help": "sim_gz.launch.py odesc_shadow:=true (create vcan0 on the host first).",
    },
    "bench_rviz": {
        "label": "RViz on the Jetson", "side": "rover", "type": "bool", "default": True,
        "help": "onboard_test.launch.py rviz:= (needs a monitor on the Jetson).",
    },
}

DRIVE_OPTIONS = ["drivetrain", "camera", "gamepad", "can_up", "clear_faults", "can_interface",
                 "fault_policy", "vio", "odom_tf", "gear_ratio", "arm_camera", "status_light",
                 "rover_extra", "view", "rviz", "ground_foxglove", "fixed_frame", "ground_extra"]

# The blink(1) mode light per state (status_light package in billee-software-2027): Teleop modes
# solid blue, Autonomous and Astronaut solid red. Idle (yellow pulse) is the dashboard's
# IDLE_LIGHT, shown while nothing runs; "arrived" (flashing green) is for the autonomous mode to
# publish on /status_light/pattern when it reaches its goal.
MODE_LIGHT = {"teleop-drive": "teleop", "teleop-arm": "teleop", "teleop-science": "teleop",
              "autonomous": "autonomous", "astronaut": "autonomous"}
IDLE_LIGHT = {"id": "status-light-idle", "label": "mode light (idle)", "where": "container",
              "ros": True, "cmd": "ros2 run status_light status_light --ros-args -p pattern:=idle"}


def light_step(pattern):
    """The light on its own, for a mode that runs no rover.launch.py."""
    return {"id": "mode-light", "label": f"mode light ({pattern})", "where": "container",
            "ros": True,
            "cmd": f"ros2 run status_light status_light --ros-args -p pattern:={pattern}"}

# --------------------------------------------------------------------------- modes
MODES = [
    {
        "id": "teleop-drive", "group": "mission", "title": "Teleop · Drive", "short": "Drive",
        "summary": "5 cameras · ZED 2i video + point cloud · Xbox drives the rover",
        "options": DRIVE_OPTIONS, "defaults": {"camera": "cloud"},
        "links": ["foxglove"],
    },
    {
        "id": "teleop-arm", "group": "mission", "title": "Teleop · Arm", "short": "Arm",
        "summary": "Teleop · Drive plus the arm. Xbox mode toggle (drive / arm)",
        "options": ["arm_hw", "arm_jog"] + DRIVE_OPTIONS, "defaults": {"camera": "cloud"},
        "links": ["foxglove"],
        "todo": "Xbox drive/arm toggle and motion planning are not built yet.",
    },
    {
        "id": "teleop-science", "group": "mission", "title": "Teleop · Science", "short": "Science",
        "summary": "Teleop · Drive without the ZED 2i · opens the Science GDS",
        "options": DRIVE_OPTIONS, "defaults": {"camera": "none"},
        "links": ["foxglove", "scm_gds"],
    },
    {
        "id": "autonomous", "group": "mission", "title": "Autonomous", "short": "Auto",
        "summary": "Autonomous navigation", "options": [], "links": ["foxglove"],
        "todo": "Not defined yet.", "disabled": True,
    },
    {
        "id": "astronaut", "group": "mission", "title": "Astronaut", "short": "Astro",
        "summary": "Jetson hotspot + astronaut server; the Pi joins and streams by itself",
        "options": ["astro_drive", "hotspot", "astro_stop"] + DRIVE_OPTIONS,
        "defaults": {"camera": "none"},
        "links": ["astronaut", "foxglove"],
    },
    {
        "id": "sim-ground", "group": "test", "title": "Sim · Ground station only", "short": "Sim",
        "summary": "Gazebo, rover and teleop on this machine. No Jetson needed",
        "options": ["gui", "navigation", "rviz", "odesc_shadow", "ground_extra"],
        "defaults": {"rviz": False}, "links": ["foxglove_local"],
    },
    {
        "id": "bench", "group": "test", "title": "Bench test · Jetson only", "short": "Bench",
        "summary": "Drivetrain, teleop and RViz all on the Jetson (gamepad + monitor on it)",
        "options": ["can_up", "clear_faults", "can_interface", "fault_policy", "gear_ratio",
                    "bench_rviz", "rover_extra"],
        "links": [],
    },
]

MODE_BY_ID = {m["id"]: m for m in MODES}

ARGS_RE = re.compile(r"^(\s*[A-Za-z_][A-Za-z0-9_]*:=[A-Za-z0-9_.,:/+@%=-]+)*\s*$")
LAUNCH_DEFAULTS = {  # rover.launch.py defaults: only differing values are passed
    "can_interface": "can2", "fault_policy": "warn", "odom_tf": True, "gear_ratio": 48.0,
    "arm_camera": "auto", "vio": False,
}


class OptionError(ValueError):
    pass


def defaults(mode):
    out = {}
    for key in mode["options"]:
        out[key] = mode.get("defaults", {}).get(key, OPTIONS[key]["default"])
    return out


def validate(mode, values):
    """Merge the submitted values over the defaults; raise OptionError on a bad value."""
    out = defaults(mode)
    for key, raw in (values or {}).items():
        if key not in out:
            continue  # options of another mode: ignore
        spec = OPTIONS[key]
        kind = spec["type"]
        if kind == "bool":
            if isinstance(raw, str):
                raw = raw.lower() in ("1", "true", "yes", "on")
            out[key] = bool(raw)
        elif kind == "select":
            allowed = [c[0] for c in spec["choices"]]
            if str(raw) not in allowed:
                raise OptionError(f"{spec['label']}: '{raw}' is not one of {', '.join(allowed)}")
            out[key] = str(raw)
        elif kind == "number":
            try:
                num = float(raw)
            except (TypeError, ValueError):
                raise OptionError(f"{spec['label']}: '{raw}' is not a number")
            if not spec.get("min", float("-inf")) <= num <= spec.get("max", float("inf")):
                raise OptionError(f"{spec['label']}: {num} is out of range")
            out[key] = num
        elif kind == "text":
            text = str(raw).strip()
            if not re.fullmatch(spec["pattern"], text):
                raise OptionError(f"{spec['label']}: '{text}' is not valid (e.g. {spec['placeholder']})")
            out[key] = text
        elif kind == "args":
            text = " ".join(str(raw).split())
            if not ARGS_RE.match(text):
                raise OptionError(f"{spec['label']}: use name:=value pairs separated by spaces "
                                  "(letters, digits and . , : / + @ % = - _ only)")
            out[key] = text
    return out


def _b(v):
    return "true" if v else "false"


def _fmt_num(v):
    return str(int(v)) if float(v).is_integer() else str(v)


JOY_RELAY_PORT = 8771
NO_GROUND_MSG = "needs the x86 ground-station container, which this machine doesn't use"


def joy_steps(port=JOY_RELAY_PORT):
    """Rover processes for "Gamepad on: This browser": the relay that turns the dashboard's
    gamepad stream into /joy, and the same tank-drive node teleop.launch.py runs."""
    return [
        {"id": "mode-joy-relay", "label": "gamepad relay", "where": "container", "ros": True,
         "prep": 'mkdir -p /tmp/billee-dash && printf "%s" "$BILLEE_JOY_RELAY" > '
                 '/tmp/billee-dash/joy_relay.py',
         "embed": "joy_relay.py", "embed_env": "BILLEE_JOY_RELAY",
         "cmd": f"python3 -u /tmp/billee-dash/joy_relay.py {int(port)}"},
        {"id": "mode-joy-teleop", "label": "joy_tank_drive", "where": "container", "ros": True,
         "cmd": "ros2 run teleop joy_tank_drive --ros-args --params-file "
                "src/teleop/config/joystick.yaml "
                "-r /cmd_vel:=/diff_drive_controller/cmd_vel_unstamped"},
    ]


def _drive(plan, o, real, iface, cfg, light=None):
    """rover.launch.py + ground_station.launch.py (+ the browser-gamepad relay)."""
    args = [f"mode:={o['drivetrain']}"]
    if o["camera"] != "none":
        args.append(f"camera:={o['camera']}")
    if real:
        if iface != LAUNCH_DEFAULTS["can_interface"]:
            args.append(f"can_interface:={iface}")
        if o["fault_policy"] != LAUNCH_DEFAULTS["fault_policy"]:
            args.append(f"fault_policy:={o['fault_policy']}")
        if float(o["gear_ratio"]) != LAUNCH_DEFAULTS["gear_ratio"]:
            args.append(f"gear_ratio:={_fmt_num(o['gear_ratio'])}")
        if o["vio"]:
            args.append("vio:=true")
        if not o["odom_tf"] and not o["vio"]:
            args.append("odom_tf:=false")
    if o["arm_camera"] != LAUNCH_DEFAULTS["arm_camera"]:
        args.append(f"arm_camera:={o['arm_camera']}")
    if o["gamepad"] == "rover":
        args.append("joy_control:=true")
    if light:
        args.append(f"status_light:={light}")
    if o["rover_extra"]:
        args.append(o["rover_extra"])
    prefix = "" if real else "xvfb-run -a vglrun -d egl "
    plan["rover"].append({
        "id": "mode-rover", "label": "rover.launch.py", "where": "container", "ros": True,
        "cmd": f"{prefix}ros2 launch chassis_bringup rover.launch.py {' '.join(args)}"})
    if o["gamepad"] == "browser":
        port = (cfg or {}).get("rover", {}).get("joy_relay_port", JOY_RELAY_PORT)
        plan["rover"] += joy_steps(port)
        plan["gamepad"] = True

    view = o["view"] if o["view"] != "auto" else (
        o["camera"] if o["camera"] != "none" else "drivetrain")
    gargs = [f"use_sim_time:={_b(not real)}", f"view:={view}"]
    if o["fixed_frame"] != "odom":
        gargs.append(f"fixed_frame:={o['fixed_frame']}")
    if not o["rviz"]:
        gargs.append("rviz:=false")
    if not o["ground_foxglove"]:
        gargs.append("foxglove:=false")
    if o["gamepad"] != "ground":
        gargs.append("joystick:=false")
    if o["ground_extra"]:
        gargs.append(o["ground_extra"])
    plan["ground"].append({
        "id": "mode-ground", "label": "ground_station.launch.py", "where": "container",
        "ros": True,
        "cmd": f"ros2 launch chassis_bringup ground_station.launch.py {' '.join(gargs)}"})



def build(mode_id, values, cfg=None, ground=True):
    """-> {"mode", "options", "pre": [step], "rover": [proc], "ground": [proc], "links": [...]}.

    A step/proc is {"id", "label", "cmd", "where": "host"|"container", "ros": bool}.
    "ros" commands run through pixi in the container's ros2_ws.
    ground=False: this machine has no ground-station container (e.g. a Mac): nothing runs
    on the ground side, and the gamepad comes from the browser."""
    mode = MODE_BY_ID.get(mode_id)
    if mode is None:
        raise OptionError(f"unknown mode {mode_id!r}")
    if mode.get("disabled"):
        raise OptionError(f"{mode['title']} is not available yet: {mode.get('todo', '')}")
    if not ground and mode_id == "sim-ground":
        raise OptionError(f"{mode['title']} {NO_GROUND_MSG}")
    o = validate(mode, ground_defaults(mode, values) if not ground else values)
    if not ground and o.get("gamepad") == "ground":
        raise OptionError(f"Gamepad on: Ground station {NO_GROUND_MSG}. Pick This browser.")
    plan = {"mode": mode_id, "options": o, "pre": [], "rover": [], "ground": [], "post": [],
            "links": list(mode.get("links", []))}

    real = o.get("drivetrain", "real") == "real"
    iface = o.get("can_interface", "can2")
    drive = mode_id in ("teleop-drive", "teleop-arm", "teleop-science") or \
        (mode_id == "astronaut" and o["astro_drive"])
    if (drive or mode_id == "bench") and real:
        if o.get("can_up"):
            plan["pre"].append(can_up_step(iface))
        if o.get("clear_faults"):
            plan["pre"].append(clear_faults_step(iface))
    if mode_id == "astronaut":
        plan["pre"] += astronaut_steps(o["hotspot"])
        port = (cfg or {}).get("astronaut", {}).get("web_port", 8080)
        plan["rover"].append({
            "id": "mode-astro-watch", "label": "astronaut stream", "where": "host",
            "cwd": "hardware_repo", "embed": "astro_watch.py", "embed_env": "BILLEE_ASTRO_WATCH",
            "show": f"(dashboard) astro_watch.py {int(port)}: reports the Pi and the feed",
            "cmd": f'exec python3 -u -c "$BILLEE_ASTRO_WATCH" {int(port)}'})
        if o["astro_stop"]:
            plan["post"] = astronaut_stop_steps()
        if not o["astro_drive"]:
            plan["links"] = ["astronaut"]

    light = MODE_LIGHT.get(mode_id) if o.get("status_light", True) else None
    if drive:
        _drive(plan, o, real, iface, cfg, light)
    elif light:  # no rover.launch.py to carry it (Astronaut without driving)
        plan["rover"].append(light_step(light))
    if mode_id == "teleop-arm":
        if o["arm_hw"]:
            plan["rover"].append({
                "id": "mode-arm", "label": "arm.launch.py", "where": "container", "ros": True,
                "cmd": "ros2 launch arm_bringup arm.launch.py"})
        if o["arm_jog"]:
            plan["ground"].append({
                "id": "mode-arm-jog", "label": "arm_teleop.launch.py", "where": "container",
                "ros": True, "cmd": "ros2 launch arm_bringup arm_teleop.launch.py joy:=false"})

    if mode_id == "sim-ground":
        args = ["foxglove:=true", f"gui:={_b(o['gui'])}", f"navigation:={_b(o['navigation'])}",
                f"rviz:={_b(o['rviz'])}"]
        if o["odesc_shadow"]:
            args.append("odesc_shadow:=true")
        if o["ground_extra"]:
            args.append(o["ground_extra"])
        plan["ground"].append({
            "id": "mode-ground", "label": "sim_gz.launch.py", "where": "container", "ros": True,
            "cmd": f"ros2 launch chassis_bringup sim_gz.launch.py {' '.join(args)}"})

    elif mode_id == "bench":
        args = []
        if iface != "can2":
            args.append(f"can_interface:={iface}")
        if o["fault_policy"] != "warn":
            args.append(f"fault_policy:={o['fault_policy']}")
        if float(o["gear_ratio"]) != 48.0:
            args.append(f"gear_ratio:={_fmt_num(o['gear_ratio'])}")
        if not o["bench_rviz"]:
            args.append("rviz:=false")
        if o["rover_extra"]:
            args.append(o["rover_extra"])
        plan["rover"].append({
            "id": "mode-rover", "label": "onboard_test.launch.py", "where": "container",
            "ros": True,
            "cmd": f"ros2 launch chassis_bringup onboard_test.launch.py {' '.join(args)}".strip()})
    if not ground:
        plan["ground"] = []
    return plan


# --------------------------------------------------------------------------- astronaut
# billee-hardware-2027's astronaut-streamer on the Jetson host (cwd: rover.hardware_repo). Its
# hotspot script and two systemd services are installed once with `make astronaut jetson`; the
# Pi then joins the hotspot and streams by itself. sudo runs without a password through the rule
# `make setup-dash` installs, which names the hotspot script by its absolute path ($(pwd -P)).
HOTSPOT_SH = '"$(pwd -P)/astronaut-streamer/jetson/hotspot.sh"'
ASTRO_UNITS = "astronaut-mediamtx astronaut-web"
_NO_ASTRO = ('{ echo "astronaut-streamer is not set up in $(pwd -P): in ~/billee-hardware-2027 run '
             'make rover, then make astronaut jetson"; exit 1; }')


def astronaut_steps(hotspot="up"):
    return [
        {"id": "astro-hotspot", "label": f"hotspot {hotspot}", "where": "host",
         "cwd": "hardware_repo", "fatal": True,
         "show": f"sudo bash astronaut-streamer/jetson/hotspot.sh {hotspot}",
         "cmd": f"[ -f astronaut-streamer/jetson/hotspot.sh ] || {_NO_ASTRO}; "
                f"sudo -n bash {HOTSPOT_SH} {hotspot} && bash {HOTSPOT_SH} status"},
        {"id": "astro-server", "label": "astronaut server", "where": "host",
         "cwd": "hardware_repo", "fatal": True,
         "show": f"sudo systemctl restart {ASTRO_UNITS}",
         "cmd": f"[ -f /etc/systemd/system/astronaut-web.service ] || {_NO_ASTRO}; "
                f"sudo -n systemctl restart {ASTRO_UNITS} && systemctl is-active {ASTRO_UNITS}"},
    ]


def astronaut_stop_steps():
    return [{"id": "astro-stop", "label": "stop astronaut server + hotspot", "where": "host",
             "cwd": "hardware_repo",
             "show": "sudo systemctl stop astronaut-web astronaut-mediamtx; "
                     "sudo bash astronaut-streamer/jetson/hotspot.sh stop",
             "cmd": f"sudo -n systemctl stop astronaut-web astronaut-mediamtx; "
                    f"sudo -n bash {HOTSPOT_SH} stop"}]


# --------------------------------------------------------------------------- CAN tools
def can_up_step(iface, bitrate=None):
    cmd = f"tooling/can-up {iface}" + (f" {int(bitrate)}" if bitrate else "")
    return {"id": "can-up", "label": f"can-up {iface}", "where": "host", "cmd": cmd,
            "ros": False, "fatal": True}


def clear_faults_step(iface, nodes=None, action="clear"):
    cmd = f"tooling/can-clear-faults --iface {iface}"
    if nodes:
        cmd += " --nodes " + ",".join(str(n) for n in nodes)
    flag = {"clear": "", "status": " --status", "reboot": " --reboot",
            "force-reboot": " --force-reboot"}[action]
    return {"id": "can-clear-faults", "label": f"can-clear-faults{flag}", "where": "host",
            "cmd": cmd + flag, "ros": False}


IFACE_RE = re.compile(r"v?can[0-9]{1,2}")


def can_command(action, iface="can2", bitrate=None, nodes=None):
    """A CAN tool command from the CAN panel. -> step dict (runs on the rover host)."""
    if not IFACE_RE.fullmatch(iface or ""):
        raise OptionError(f"bad CAN interface '{iface}'")
    if bitrate not in (None, ""):
        try:
            bitrate = int(bitrate)
        except (TypeError, ValueError):
            raise OptionError("bitrate must be a whole number")
        if not 10000 <= bitrate <= 1000000:
            raise OptionError("bitrate must be 10000-1000000")
    else:
        bitrate = None
    node_list = None
    if nodes:
        try:
            node_list = sorted({int(n) for n in nodes})
        except (TypeError, ValueError):
            raise OptionError("nodes must be numbers")
        if any(n < 0 or n > 63 for n in node_list):
            raise OptionError("node IDs are 0-63")
    if action == "up":
        return can_up_step(iface, bitrate)
    if action == "down":
        return {"id": "can-up", "label": f"can-up down {iface}", "where": "host",
                "cmd": f"tooling/can-up down {iface}", "ros": False}
    if action == "status":
        return {"id": "can-up", "label": f"can-up status {iface}", "where": "host",
                "cmd": f"tooling/can-up status {iface}", "ros": False}
    if action in ("clear", "fault-status", "reboot", "force-reboot"):
        return clear_faults_step(iface, node_list,
                                 "status" if action == "fault-status" else action)
    raise OptionError(f"unknown CAN action {action!r}")


# --------------------------------------------------------------------------- build
PKG_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def build_step(packages="", deps=False, build_args=""):
    """`pixi run build` (colcon build --symlink-install over src/, ros2_ws/pixi.toml) in a dev
    container. packages: names separated by spaces or commas, none = the whole workspace;
    deps: --packages-up-to (with their dependencies) instead of --packages-select.
    build_args: extra colcon flags from the config (the Jetson's low-RAM ones)."""
    names = []
    for n in re.split(r"[\s,]+", str(packages or "").strip()):
        if not n:
            continue
        if not PKG_RE.fullmatch(n):
            raise OptionError(f"'{n}' is not a package name (letters, digits and _)")
        if n not in names:
            names.append(n)
    args = str(build_args or "").split()
    if names:
        args += ["--packages-up-to" if deps else "--packages-select"] + names
    label = "pixi run build" + (f" ({' '.join(names)})" if names else "")
    return {"id": "build", "label": label, "where": "container", "ros": True,
            "cmd": " ".join(["build"] + args)}


def ground_defaults(mode, values):
    """Without a ground container the gamepad defaults to the browser."""
    out = dict(values or {})
    if "gamepad" in mode["options"] and "gamepad" not in out:
        out["gamepad"] = "browser"
    return out


def catalog(ground=True):
    """What the page needs to draw the modes."""
    out = []
    for m in MODES:
        m = dict(m, values=defaults(m))
        if not ground:
            m["values"] = validate(m, ground_defaults(m, {})) if not m.get("disabled") else m["values"]
            if m["id"] == "sim-ground":
                m.update(disabled=True, todo=f"This mode {NO_GROUND_MSG}.")
        out.append(m)
    return {"options": OPTIONS, "modes": out, "ground": ground}
