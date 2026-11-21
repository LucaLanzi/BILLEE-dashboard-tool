"""joy_relay.py with a stub rclpy: newest state wins, stale input zeroes /joy."""
import json
import os
import socket
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "rosstub"))
sys.path.insert(0, os.path.dirname(HERE))
sys._called_from_test = True
import rclpy  # noqa: E402  (the stub)
from billee_dash import joy_relay  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestJoyRelay(unittest.TestCase):
    def spin(self, node, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            rclpy.spin_once(node, 0.005)

    def test_relay(self):
        port = free_port()
        node = joy_relay.JoyRelay(port)
        rclpy.PUBLISHED.clear()
        self.spin(node, 0.15)
        self.assertEqual(rclpy.PUBLISHED, [], "nothing published before any input")

        c = socket.create_connection(("127.0.0.1", port))
        old = {"axes": [0, 0.1, 1, 0, 0.1, 1], "buttons": [0, 0, 0, 0, 1]}
        new = {"axes": [0, 0.9, 1, 0, 0.9, 1], "buttons": [0, 0, 0, 0, 1]}
        c.sendall((json.dumps(old) + "\n" + "garbage\n" + json.dumps(new) + "\n").encode())
        self.spin(node, 0.12)
        self.assertTrue(rclpy.PUBLISHED)
        first = rclpy.PUBLISHED[0]
        self.assertEqual(first["axes"][1], 0.9, "the newest state in a burst wins")
        self.assertEqual(len(first["axes"]), 8)
        self.assertEqual(len(first["buttons"]), 11)
        self.assertEqual(first["buttons"][4], 1)

        rclpy.PUBLISHED.clear()
        self.spin(node, joy_relay.STALE_S + 0.2)  # input stops
        last = rclpy.PUBLISHED[-1]
        self.assertEqual(last["buttons"][4], 0, "deadman released after input stops")
        self.assertEqual(set(last["axes"]), {0.0})
        n = len(rclpy.PUBLISHED)
        self.spin(node, 0.2)
        self.assertEqual(len(rclpy.PUBLISHED), n, "only one zero message, then quiet")
        c.close()
        node.srv.close()


if __name__ == "__main__":
    unittest.main()
