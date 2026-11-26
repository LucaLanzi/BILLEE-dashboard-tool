"""Mode catalog: commands match docs/running.md, and bad values never reach a command line."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from billee_dash import modes  # noqa: E402


def cmds(mode, **opts):
    plan = modes.build(mode, opts)
    return [s["cmd"] for s in plan["pre"]], [s["cmd"] for s in plan["rover"]], \
        [s["cmd"] for s in plan["ground"]], plan["links"]


class TestModes(unittest.TestCase):
    def test_every_mode_has_known_options(self):
        for m in modes.MODES:
            for k in m["options"]:
                self.assertIn(k, modes.OPTIONS, f"{m['id']}: {k}")
            for k in m.get("defaults", {}):
                self.assertIn(k, m["options"], f"{m['id']}: default for missing option {k}")

    def test_teleop_drive_defaults(self):
        pre, rover, ground, links = cmds("teleop-drive")
        self.assertEqual(pre, ["tooling/can-up can2", "tooling/can-clear-faults --iface can2"])
        self.assertEqual(rover, ["ros2 launch chassis_bringup rover.launch.py mode:=real camera:=cloud "
                                 "arm_tf:=false status_light:=teleop"])
        self.assertEqual(ground, ["ros2 launch chassis_bringup ground_station.launch.py "
                                  "use_sim_time:=false view:=cloud"])
        self.assertEqual(links, ["foxglove"])

    def test_zed_choices(self):
        cam = lambda c: [s["cmd"] for s in modes.build("teleop-drive", {"camera": c})["rover"]  # noqa: E731
                         ][0].split()
        for c in ("video", "cloud", "openpose", "both"):
            self.assertIn(f"camera:={c}", cam(c))
            view = modes.build("teleop-drive", {"camera": c})["ground"][0]["cmd"]
            self.assertIn(f"view:={c}", view)
        self.assertFalse([a for a in cam("none") if a.startswith("camera:=")])

    def test_science_has_no_zed_and_opens_scm(self):
        _, rover, ground, links = cmds("teleop-science")
        self.assertNotIn("camera:=", rover[0])
        self.assertIn("view:=drivetrain", ground[0])
        self.assertEqual(links, ["foxglove", "scm_gds"])

    def test_sim_on_jetson(self):
        pre, rover, ground, _ = cmds("teleop-drive", drivetrain="sim", camera="cloud")
        self.assertEqual(pre, [])  # no CAN for the sim
        self.assertEqual(rover[0], "xvfb-run -a vglrun -d egl ros2 launch chassis_bringup "
                                   "rover.launch.py mode:=sim camera:=cloud arm_tf:=false "
                                   "status_light:=teleop")
        self.assertIn("use_sim_time:=true", ground[0])

    def test_real_options(self):
        _, rover, ground, _ = cmds("teleop-drive", can_interface="can3", fault_policy="fault",
                                   vio=True, gear_ratio="50", gamepad="rover", camera="none",
                                   can_up=False, clear_faults=False)
        self.assertEqual(rover[0], "ros2 launch chassis_bringup rover.launch.py mode:=real "
                                   "can_interface:=can3 fault_policy:=fault gear_ratio:=50 "
                                   "vio:=true arm_tf:=false joy_control:=true "
                                   "status_light:=teleop")
        self.assertIn("joystick:=false", ground[0])

    def test_arm(self):
        _, rover, ground, _ = cmds("teleop-arm", arm_hw=True, arm_jog=True)
        self.assertEqual(rover[1], "ros2 launch arm_bringup arm.launch.py")
        # The jog teleop runs on the rover, next to the arm driver and its arm_axes.yaml.
        self.assertEqual(rover[2], "ros2 launch arm_bringup arm_teleop.launch.py joy:=false")
        self.assertFalse([c for c in ground if "arm_teleop" in c])
        pre, _, _, _ = cmds("teleop-arm", arm_hw=True)
        self.assertEqual(pre, ["tooling/can-up can2", "tooling/can-clear-faults --iface can2",
                               "tooling/can-up can3"])

    def test_arm_defaults_on(self):
        # Teleop · Arm with nothing ticked: arm bus up, arm driver and jog teleop on the rover.
        pre, rover, ground, _ = cmds("teleop-arm")
        self.assertIn("tooling/can-up can3", pre)
        self.assertIn("ros2 launch arm_bringup arm.launch.py", rover)
        self.assertIn("ros2 launch arm_bringup arm_teleop.launch.py joy:=false", rover)

    def test_arm_deadzone(self):
        _, rover, _, _ = cmds("teleop-arm")
        self.assertIn("ros2 launch arm_bringup arm_teleop.launch.py joy:=false", rover)
        _, rover, _, _ = cmds("teleop-arm", arm_deadzone=0.25)
        self.assertIn("ros2 launch arm_bringup arm_teleop.launch.py joy:=false deadzone:=0.25", rover)
        with self.assertRaises(modes.OptionError):
            modes.build("teleop-arm", {"arm_deadzone": 0.9})
        with self.assertRaises(modes.OptionError):
            modes.build("teleop-arm", {"arm_deadzone": "0.2; rm"})

    def test_arm_can_interface(self):
        pre, rover, _, _ = cmds("teleop-arm", arm_hw=True, arm_can_interface="can4")
        self.assertIn("tooling/can-up can4", pre)
        self.assertEqual(rover[1], "ros2 launch arm_bringup arm.launch.py can_interface:=can4")
        pre, _, _, _ = cmds("teleop-arm", arm_hw=False)
        self.assertNotIn("tooling/can-up can3", pre)
        pre, _, _, _ = cmds("teleop-arm", arm_hw=True, can_up=False)
        self.assertEqual(pre, ["tooling/can-clear-faults --iface can2"])
        with self.assertRaises(modes.OptionError):
            modes.build("teleop-arm", {"arm_hw": True, "arm_can_interface": "can2"})
        with self.assertRaises(modes.OptionError):
            modes.build("teleop-arm", {"arm_hw": True, "arm_can_interface": "can3; rm"})

    def test_arm_sim_and_real(self):
        # Real drivetrain, real arm (the default): the arm's TF follows /arm/joint_states.
        pre, rover, _, _ = cmds("teleop-arm")
        self.assertTrue(rover[0].startswith("ros2 launch chassis_bringup rover.launch.py "
                                            "mode:=real camera:=cloud"))
        self.assertIn("arm_tf:=arm", rover[0])
        # Sim drivetrain (Gazebo on the Jetson), mock arm: no CAN at all, the mock arm drives TF.
        pre, rover, _, _ = cmds("teleop-arm", drivetrain="sim", arm_hw="mock")
        self.assertEqual(pre, [])
        self.assertTrue(rover[0].startswith("xvfb-run -a vglrun -d egl ros2 launch "
                                            "chassis_bringup rover.launch.py mode:=sim"))
        self.assertIn("arm_tf:=arm", rover[0])
        self.assertIn("ros2 launch arm_bringup arm.launch.py can_interface:=mock "
                      "roboclaw_port:=mock", rover)
        self.assertIn("ros2 launch arm_bringup arm_teleop.launch.py joy:=false", rover)
        # Sim, no arm (a RealSense on a testbed): joints held at 0, no arm driver.
        pre, rover, _, _ = cmds("teleop-arm", drivetrain="sim", arm_hw="off", arm_jog=False)
        self.assertEqual(pre, [])
        self.assertIn("arm_tf:=zero", rover[0])
        self.assertEqual(len(rover), 1)
        # Sim drivetrain with the real arm: only the arm's bus comes up.
        pre, rover, _, _ = cmds("teleop-arm", drivetrain="sim", arm_hw="real")
        self.assertEqual(pre, ["tooling/can-up can3"])
        self.assertIn("arm_tf:=arm", rover[0])
        # Joints at 0 next to an arm driver would be two /arm/joint_states publishers.
        with self.assertRaises(modes.OptionError):
            modes.build("teleop-arm", {"arm_hw": "mock", "arm_tf": "zero"})
        _, rover, _, _ = cmds("teleop-arm", arm_tf="false")
        self.assertIn("arm_tf:=false", rover[0])
        with self.assertRaises(modes.OptionError):
            modes.build("teleop-arm", {"arm_hw": "maybe"})
        # Other modes turn the arm's TF off, so the arm doesn't show in their viewers.
        _, rover, _, _ = cmds("teleop-drive")
        self.assertIn("arm_tf:=false", rover[0])

    def test_arm_hw_saved_as_bool(self):
        # Values saved when arm_hw was a checkbox still work.
        self.assertEqual(modes.build("teleop-arm", {"arm_hw": True})["options"]["arm_hw"], "real")
        self.assertEqual(modes.build("teleop-arm", {"arm_hw": "false"})["options"]["arm_hw"],
                         "off")

    def test_bench_and_sim(self):
        pre, rover, ground, _ = cmds("bench", bench_rviz=False)
        self.assertEqual(rover, ["ros2 launch chassis_bringup onboard_test.launch.py rviz:=false"])
        self.assertEqual(ground, [])
        _, rover, ground, links = cmds("sim-ground")
        self.assertEqual(rover, [])
        self.assertEqual(ground, ["ros2 launch chassis_bringup sim_gz.launch.py foxglove:=true "
                                  "gui:=true navigation:=true rviz:=false"])
        self.assertEqual(links, ["foxglove_local"])

    def test_todo_modes_refuse(self):
        for m in ("autonomous",):
            with self.assertRaises(modes.OptionError):
                modes.build(m, {})

    def test_foxglove_layout_per_mode(self):
        """Teleop · Arm opens rover_arm.json; every other mode the rover on its own."""
        for mode_id in ("teleop-drive", "teleop-science", "astronaut", "sim-ground", "bench"):
            values = {"arm_hw": "off"} if mode_id == "teleop-arm" else {}
            self.assertEqual(modes.build(mode_id, values)["foxglove_layout"], "rover", mode_id)
        self.assertEqual(modes.build("teleop-arm", {})["foxglove_layout"], "arm")
        self.assertEqual(modes.MODE_BY_ID["autonomous"].get("foxglove_layout", "rover"), "rover")
        self.assertEqual(set(modes.FOXGLOVE_LAYOUTS), {"rover", "arm"})

    def test_aruco_sizes(self):
        # Empty: the rover's config files decide, nothing is passed.
        _, rover, _, _ = cmds("teleop-arm", arm_hw="off")
        self.assertNotIn("marker_size", rover[0])
        _, rover, _, _ = cmds("teleop-arm", arm_hw="off", aruco_sizes="40")
        self.assertIn(" marker_size:=0.04", rover[0])
        self.assertNotIn("marker_sizes", rover[0])
        _, rover, _, _ = cmds("teleop-arm", arm_hw="off", aruco_sizes=" 10-13:30, 7:100  45.5")
        self.assertIn(" marker_size:=0.0455 marker_sizes:=10-13:0.03,7:0.1", rover[0])
        plan = modes.build("teleop-drive", {"aruco_sizes": "10-13:30,7:100"})
        self.assertEqual(plan["options"]["aruco_sizes"], "10-13:30 7:100")  # normalised
        # The arm camera off: no sizes for it.
        _, rover, _, _ = cmds("teleop-drive", arm_camera="false", aruco_sizes="40")
        self.assertNotIn("marker_size", rover[0])
        for bad in ("40 50", "13-10:30", "10:2", "10:5000", "1000:30", "10:30;rm", "a:30",
                    "10-13", "10:30:40", "$(id)"):
            with self.assertRaises(modes.OptionError, msg=bad):
                modes.build("teleop-arm", {"arm_hw": "off", "aruco_sizes": bad})

    def test_injection_refused(self):
        for bad in ({"can_interface": "can2; reboot"}, {"rover_extra": "a:=$(id)"},
                    {"ground_extra": "x:=1 && rm"}, {"camera": "cloud;"}, {"gear_ratio": "1e9"},
                    {"rover_extra": "a:=`id`"}):
            with self.assertRaises(modes.OptionError, msg=str(bad)):
                modes.build("teleop-drive", bad)

    def test_extra_args(self):
        _, rover, _, _ = cmds("teleop-drive", rover_extra="rviz:=false  world:=a/b.sdf")
        self.assertTrue(rover[0].endswith("rviz:=false world:=a/b.sdf"))

    def test_browser_gamepad(self):
        plan = modes.build("teleop-drive", {"gamepad": "browser"})
        self.assertTrue(plan["gamepad"])
        rover = [s["cmd"] for s in plan["rover"]]
        self.assertEqual(rover[1], "python3 -u /tmp/billee-dash/joy_relay.py 8771")
        self.assertEqual(rover[2], "ros2 run teleop joy_tank_drive --ros-args --params-file "
                                   "src/teleop/config/joystick.yaml "
                                   "-r /cmd_vel:=/diff_drive_controller/cmd_vel_unstamped")
        self.assertNotIn("joy_control", rover[0])
        self.assertIn("joystick:=false", plan["ground"][0]["cmd"])

    def test_no_ground_container(self):
        plan = modes.build("teleop-science", {}, ground=False)  # e.g. a Mac
        self.assertEqual(plan["ground"], [])
        self.assertEqual(plan["options"]["gamepad"], "browser")
        self.assertEqual([s["id"] for s in plan["rover"]],
                         ["mode-rover", "mode-joy-relay", "mode-joy-teleop"])
        self.assertEqual(plan["links"], ["foxglove", "scm_gds"])
        plan = modes.build("teleop-drive", {"gamepad": "rover"}, ground=False)
        self.assertIn("joy_control:=true", plan["rover"][0]["cmd"])
        for bad in (("teleop-drive", {"gamepad": "ground"}), ("sim-ground", {})):
            with self.assertRaises(modes.OptionError):
                modes.build(*bad, ground=False)
        cat = {m["id"]: m for m in modes.catalog(ground=False)["modes"]}
        self.assertTrue(cat["sim-ground"]["disabled"])
        self.assertEqual(cat["teleop-drive"]["values"]["gamepad"], "browser")

    def test_astronaut(self):
        plan = modes.build("astronaut", {})
        pre = [s["id"] for s in plan["pre"]]
        self.assertEqual(pre, ["can-up", "can-clear-faults", "astro-hotspot", "astro-server"])
        hotspot, server = plan["pre"][2], plan["pre"][3]
        self.assertEqual(hotspot["cwd"], "hardware_repo")
        self.assertTrue(hotspot["fatal"] and server["fatal"])
        self.assertIn('sudo -n bash "$(pwd -P)/astronaut-streamer/jetson/hotspot.sh" up',
                      hotspot["cmd"])
        self.assertIn("sudo -n systemctl restart astronaut-mediamtx astronaut-web", server["cmd"])
        rover = [s["id"] for s in plan["rover"]]
        self.assertEqual(rover[:2], ["mode-astro-watch", "mode-rover"])  # + the drive
        self.assertNotIn("camera:=", plan["rover"][1]["cmd"])
        self.assertEqual(plan["links"], ["astronaut", "foxglove"])
        self.assertEqual(plan["post"], [])

    def test_astronaut_only_and_stop(self):
        plan = modes.build("astronaut", {"astro_drive": False, "hotspot": "start",
                                         "astro_stop": True})
        self.assertEqual([s["id"] for s in plan["pre"]], ["astro-hotspot", "astro-server"])
        self.assertIn("hotspot.sh\" start", plan["pre"][0]["cmd"])
        self.assertEqual([s["id"] for s in plan["rover"]], ["mode-astro-watch", "mode-light"])
        self.assertIn("-p pattern:=autonomous", plan["rover"][1]["cmd"])
        self.assertEqual(plan["ground"], [])
        self.assertEqual(plan["links"], ["astronaut"])
        self.assertEqual([s["id"] for s in plan["post"]], ["astro-stop"])

    def test_mode_light(self):
        light = lambda m, **o: [a for s in modes.build(m, o)["rover"]  # noqa: E731
                                for a in s["cmd"].split() if "status_light:=" in a or "pattern:=" in a]
        self.assertEqual(light("teleop-drive"), ["status_light:=teleop"])
        self.assertEqual(light("teleop-arm"), ["status_light:=teleop"])
        self.assertEqual(light("teleop-science"), ["status_light:=teleop"])
        self.assertEqual(light("astronaut"), ["status_light:=autonomous"])
        self.assertEqual(light("astronaut", astro_drive=False), ["pattern:=autonomous"])
        self.assertEqual(light("teleop-drive", status_light=False), [])
        self.assertEqual(light("bench"), [])
        self.assertIn("pattern:=idle", modes.IDLE_LIGHT["cmd"])

    def test_bench_without_ground_container(self):
        plan = modes.build("bench", {"can_up": False, "clear_faults": False}, ground=False)
        self.assertEqual([s["cmd"] for s in plan["rover"]],
                         ["ros2 launch chassis_bringup onboard_test.launch.py"])

    def test_can_commands(self):
        c = lambda *a, **k: modes.can_command(*a, **k)["cmd"]  # noqa: E731
        self.assertEqual(c("up", "can2", "250000"), "tooling/can-up can2 250000")
        self.assertEqual(c("down", "can3"), "tooling/can-up down can3")
        self.assertEqual(c("status", "vcan0"), "tooling/can-up status vcan0")
        self.assertEqual(c("clear", "can2", nodes=[5, 3]),
                         "tooling/can-clear-faults --iface can2 --nodes 3,5")
        self.assertEqual(c("fault-status", "can2"), "tooling/can-clear-faults --iface can2 --status")
        self.assertEqual(c("reboot", "can2"), "tooling/can-clear-faults --iface can2 --reboot")
        self.assertEqual(c("force-reboot", "can2", nodes=[4]),
                         "tooling/can-clear-faults --iface can2 --nodes 4 --force-reboot")
        for bad in (("up", "eth0"), ("up", "can2", "fast"), ("clear", "can2", None, [99]),
                    ("explode", "can2")):
            with self.assertRaises(modes.OptionError):
                modes.can_command(*bad)

    def test_build_step(self):
        b = modes.build_step
        self.assertEqual(b()["cmd"], "build")
        self.assertEqual(b("teleop", build_args="--parallel-workers 2 --executor sequential")["cmd"],
                         "build --parallel-workers 2 --executor sequential --packages-select teleop")
        step = b("teleop, chassis_bringup teleop", deps=True)
        self.assertEqual(step["cmd"], "build --packages-up-to teleop chassis_bringup")
        self.assertEqual((step["id"], step["where"], step["ros"]), ("build", "container", True))
        for bad in ("teleop;reboot", "$(id)", "a/b", "-x", "../src"):
            with self.assertRaises(modes.OptionError):
                b(bad)


if __name__ == "__main__":
    unittest.main()
