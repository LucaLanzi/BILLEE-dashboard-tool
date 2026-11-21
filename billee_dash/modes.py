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
        "choices": [["none", "Off"], ["cloud", "Point cloud"],
                    ["openpose", "Video + OpenPose"], ["both", "Point cloud + video"]],
        "default": "none",
        "help": "rover.launch.py camera:=. The ZED video feed comes with OpenPose "
                "(openpose / both), which needs ~3 GB free RAM on the Jetson.",
    },
    "gamepad": {
        "label": "Gamepad on", "side": "both", "type": "select", "basic": True,
        "choices": [["ground", "Ground station"], ["rover", "Rover (bench)"]],
        "default": "ground",
        "help": "Teleop must run on exactly one machine, or every command is sent twice.",
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
        "choices": [["auto", "Match the ZED"], ["drivetrain", "Drivetrain"], ["cloud", "Point cloud"],
                    ["openpose", "OpenPose"], ["both", "Both"]],
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
                 "fault_policy", "vio", "odom_tf", "gear_ratio", "arm_camera", "rover_extra",
                 "view", "rviz", "ground_foxglove", "fixed_frame", "ground_extra"]

# --------------------------------------------------------------------------- modes
MODES = [
    {
        "id": "teleop-drive", "group": "mission", "title": "Teleop · Drive", "short": "Drive",
        "summary": "5 cameras · ZED 2i point cloud + video · Xbox drives the rover",
        "options": DRIVE_OPTIONS, "defaults": {"camera": "both"},
        "links": ["foxglove"],
    },
    {
        "id": "teleop-arm", "group": "mission", "title": "Teleop · Arm", "short": "Arm",
        "summary": "Teleop · Drive plus the arm. Xbox mode toggle (drive / arm)",
        "options": ["arm_hw", "arm_jog"] + DRIVE_OPTIONS, "defaults": {"camera": "both"},
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
        "summary": "Astronaut module (overlaps with billee-hardware-2027)", "options": [],
        "links": ["foxglove"], "todo": "Not defined yet.", "disabled": True,
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


def build(mode_id, values, cfg=None):
    """-> {"mode", "options", "pre": [step], "rover": [proc], "ground": [proc], "links": [...]}.

    A step/proc is {"id", "label", "cmd", "where": "host"|"container", "ros": bool}.
    "ros" commands run through pixi in the container's ros2_ws."""
    mode = MODE_BY_ID.get(mode_id)
    if mode is None:
        raise OptionError(f"unknown mode {mode_id!r}")
    if mode.get("disabled"):
        raise OptionError(f"{mode['title']} is not available yet: {mode.get('todo', '')}")
    o = validate(mode, values)
    plan = {"mode": mode_id, "options": o, "pre": [], "rover": [], "ground": [],
            "links": list(mode.get("links", []))}

    real = o.get("drivetrain", "real") == "real"
    iface = o.get("can_interface", "can2")
    if mode_id in ("teleop-drive", "teleop-arm", "teleop-science", "bench") and real:
        if o.get("can_up"):
            plan["pre"].append(can_up_step(iface))
        if o.get("clear_faults"):
            plan["pre"].append(clear_faults_step(iface))

    if mode_id in ("teleop-drive", "teleop-arm", "teleop-science"):
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
        if o["rover_extra"]:
            args.append(o["rover_extra"])
        prefix = "" if real else "xvfb-run -a vglrun -d egl "
        plan["rover"].append({
            "id": "mode-rover", "label": "rover.launch.py", "where": "container", "ros": True,
            "cmd": f"{prefix}ros2 launch chassis_bringup rover.launch.py {' '.join(args)}"})

        view = o["view"] if o["view"] != "auto" else (
            o["camera"] if o["camera"] != "none" else "drivetrain")
        gargs = [f"use_sim_time:={_b(not real)}", f"view:={view}"]
        if o["fixed_frame"] != "odom":
            gargs.append(f"fixed_frame:={o['fixed_frame']}")
        if not o["rviz"]:
            gargs.append("rviz:=false")
        if not o["ground_foxglove"]:
            gargs.append("foxglove:=false")
        if o["gamepad"] == "rover":
            gargs.append("joystick:=false")
        if o["ground_extra"]:
            gargs.append(o["ground_extra"])
        plan["ground"].append({
            "id": "mode-ground", "label": "ground_station.launch.py", "where": "container",
            "ros": True,
            "cmd": f"ros2 launch chassis_bringup ground_station.launch.py {' '.join(gargs)}"})

        if mode_id == "teleop-arm":
            if o["arm_hw"]:
                plan["rover"].append({
                    "id": "mode-arm", "label": "arm.launch.py", "where": "container", "ros": True,
                    "cmd": "ros2 launch arm_bringup arm.launch.py"})
            if o["arm_jog"]:
                plan["ground"].append({
                    "id": "mode-arm-jog", "label": "arm_teleop.launch.py", "where": "container",
                    "ros": True, "cmd": "ros2 launch arm_bringup arm_teleop.launch.py joy:=false"})

    elif mode_id == "sim-ground":
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
    return plan


# --------------------------------------------------------------------------- CAN tools
def can_up_step(iface, bitrate=None):
    cmd = f"tooling/can-up {iface}" + (f" {int(bitrate)}" if bitrate else "")
    return {"id": "can-up", "label": f"can-up {iface}", "where": "host", "cmd": cmd,
            "ros": False}


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


def catalog():
    """What the page needs to draw the modes."""
    return {"options": OPTIONS, "modes": [dict(m, values=defaults(m)) for m in MODES]}
