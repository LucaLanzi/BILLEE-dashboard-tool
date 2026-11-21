import json
import sys
import time

import rclpy


class _Logger:
    def info(self, m): print("[INFO]", m, flush=True)
    def warn(self, m): print("[WARN]", m, flush=True)


class _Time:
    def __init__(self):
        self.nanoseconds = int(time.monotonic() * 1e9)
    def to_msg(self):
        return self.nanoseconds


class _Clock:
    def now(self):
        return _Time()


class _Pub:
    def __init__(self, topic):
        self.topic = topic
    def publish(self, msg):
        entry = {"axes": list(msg.axes), "buttons": list(msg.buttons)}
        rclpy.PUBLISHED.append(entry)
        if "--print" in sys.argv or not hasattr(sys, "_called_from_test"):
            print("PUB", self.topic, json.dumps(entry), flush=True)


class Node:
    def __init__(self, name):
        self._timers = []
    def create_publisher(self, typ, topic, depth):
        return _Pub(topic)
    def create_timer(self, period, cb):
        self._timers.append([period, cb, time.monotonic() + period])
    def get_logger(self):
        return _Logger()
    def get_clock(self):
        return _Clock()
    def destroy_node(self):
        pass
    def _run_due(self):
        now = time.monotonic()
        for t in self._timers:
            if now >= t[2]:
                t[2] = now + t[0]
                t[1]()
