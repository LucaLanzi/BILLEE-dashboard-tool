"""Minimal rclpy stand-in for testing joy_relay.py without ROS (tests and fake containers).
Published messages are printed as JSON lines ("PUB /joy {...}") and kept in PUBLISHED."""
import time

PUBLISHED = []
_nodes = []


def init(args=None):
    pass


def try_shutdown():
    pass


def spin_once(node, timeout_sec=0.0):
    node._run_due()
    time.sleep(timeout_sec)


def spin(node):
    while True:
        spin_once(node, 0.005)
