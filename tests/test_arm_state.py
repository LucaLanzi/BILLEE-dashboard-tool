"""The dashboard's Teleop · Arm line: arm_joy_teleop log lines -> selected joint state."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from billee_dash.commander import Output, arm_teleop_update  # noqa: E402

PFX = "[arm_joy_teleop.py-1] \x1b[0m[INFO] [1795333349.687107859] [arm_joy_teleop]: "


class TestArmState(unittest.TestCase):
    def test_lines(self):
        arm = {}
        arm_teleop_update(arm, PFX + "selected [4/6] joint_arm_4 (roboclaw) pos +0.000 rad, limits [-6.28, 6.28]")
        self.assertEqual((arm["joint"], arm["index"], arm["count"], arm["driver"], arm["pos"]),
                         ("joint_arm_4", 4, 6, "roboclaw", "+0.000"))
        arm_teleop_update(arm, PFX + "jogging joint_arm_4 from +0.000 rad")
        self.assertTrue(arm["jogging"])
        arm_teleop_update(arm, PFX + "joint_arm_4 stopped at +0.120 rad")
        self.assertEqual((arm["jogging"], arm["pos"]), (False, "+0.120"))
        arm_teleop_update(arm, PFX + "solenoid ON")
        self.assertTrue(arm["solenoid"])
        arm_teleop_update(arm, PFX + "drive deadman held: arm locked out until LT is released")
        self.assertTrue(arm["locked"])
        arm_teleop_update(arm, PFX + "selected [1/6] joint_arm_1 (pxx) pos ? rad, limits [-6.28, 6.28]")
        self.assertEqual((arm["joint"], arm["pos"]), ("joint_arm_1", "?"))

    def test_only_the_arm_teleop_counts(self):
        out = Output()
        out.add("rover", "mode-rover", "selected [2/6] joint_arm_2 (pxx) pos +1.000 rad")
        self.assertEqual(out.arm, {})
        out.add("rover", "mode-arm-jog", "selected [2/6] joint_arm_2 (pxx) pos +1.000 rad")
        self.assertEqual(out.arm["joint"], "joint_arm_2")


if __name__ == "__main__":
    unittest.main()
