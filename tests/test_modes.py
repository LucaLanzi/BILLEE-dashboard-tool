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
        self.assertEqual(rover, ["ros2 launch chassis_bringup rover.launch.py mode:=real camera:=both"])
        self.assertEqual(ground, ["ros2 launch chassis_bringup ground_station.launch.py "
                                  "use_sim_time:=false view:=both"])
        self.assertEqual(links, ["foxglove"])

    def test_science_has_no_zed_and_opens_scm(self):
        _, rover, ground, links = cmds("teleop-science")
        self.assertNotIn("camera:=", rover[0])
        self.assertIn("view:=drivetrain", ground[0])
        self.assertEqual(links, ["foxglove", "scm_gds"])

    def test_sim_on_jetson(self):
        pre, rover, ground, _ = cmds("teleop-drive", drivetrain="sim", camera="cloud")
        self.assertEqual(pre, [])  # no CAN for the sim
        self.assertEqual(rover[0], "xvfb-run -a vglrun -d egl ros2 launch chassis_bringup "
                                   "rover.launch.py mode:=sim camera:=cloud")
        self.assertIn("use_sim_time:=true", ground[0])

    def test_real_options(self):
        _, rover, ground, _ = cmds("teleop-drive", can_interface="can3", fault_policy="fault",
                                   vio=True, gear_ratio="50", gamepad="rover", camera="none",
                                   can_up=False, clear_faults=False)
        self.assertEqual(rover[0], "ros2 launch chassis_bringup rover.launch.py mode:=real "
                                   "can_interface:=can3 fault_policy:=fault gear_ratio:=50 "
                                   "vio:=true joy_control:=true")
        self.assertIn("joystick:=false", ground[0])

    def test_arm(self):
        _, rover, ground, _ = cmds("teleop-arm", arm_hw=True, arm_jog=True)
        self.assertEqual(rover[1], "ros2 launch arm_bringup arm.launch.py")
        self.assertEqual(ground[1], "ros2 launch arm_bringup arm_teleop.launch.py joy:=false")

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
        for m in ("autonomous", "astronaut"):
            with self.assertRaises(modes.OptionError):
                modes.build(m, {})

    def test_injection_refused(self):
        for bad in ({"can_interface": "can2; reboot"}, {"rover_extra": "a:=$(id)"},
                    {"ground_extra": "x:=1 && rm"}, {"camera": "cloud;"}, {"gear_ratio": "1e9"},
                    {"rover_extra": "a:=`id`"}):
            with self.assertRaises(modes.OptionError, msg=str(bad)):
                modes.build("teleop-drive", bad)

    def test_extra_args(self):
        _, rover, _, _ = cmds("teleop-drive", rover_extra="rviz:=false  world:=a/b.sdf")
        self.assertTrue(rover[0].endswith("rviz:=false world:=a/b.sdf"))

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


if __name__ == "__main__":
    unittest.main()
