"""Gamepad relay: publishes /joy on the rover from a gamepad read by the dashboard page.

The dashboard page reads the controller with the browser's Gamepad API (no USB passthrough or
local ROS needed, so it works from a Mac) and posts its state to the commander, which streams it
as JSON lines over the dashboard's SSH tunnel to this node, inside the rover's dev container:

    browser --HTTP--> commander --SSH tunnel (TCP)--> joy_relay (127.0.0.1:8771) --/joy-->
        joy_tank_drive --/diff_drive_controller/cmd_vel_unstamped--> drivetrain

The commander sends the gamepad in the Linux joy_node layout (axes LX LY LT RX RY RT, buttons
A B X Y LB RB back start guide LS RS), so teleop/config/joystick.yaml applies unchanged.

Safety: /joy is published only while fresh input arrives. After STALE_S without any, one
all-zero /joy goes out (deadman released -> joy_tank_drive sends zero velocity) and publishing
stops; diff_drive_controller's cmd_vel_timeout (0.25 s) stops the wheels in any case. When a
backlog arrives at once after a stall (TCP over a lossy link), only the newest state is used.

Run (the dashboard does this):  python3 joy_relay.py [port]    (needs rclpy: inside pixi)
"""

import json
import select
import socket
import sys

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Joy

STALE_S = 0.3
RATE_HZ = 20.0
N_AXES, N_BUTTONS = 8, 11


class JoyRelay(Node):
    def __init__(self, port):
        super().__init__("dashboard_joy_relay")
        self.pub = self.create_publisher(Joy, "/joy", 1)
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", port))
        self.srv.listen(2)
        self.srv.setblocking(False)
        self.conn, self.buf = None, b""
        self.latest, self.last_rx, self.active = None, 0.0, False
        self.create_timer(1.0 / RATE_HZ, self.tick)
        self.get_logger().info(f"gamepad relay on 127.0.0.1:{port} -> /joy")

    def now(self):
        return self.get_clock().now().nanoseconds / 1e9

    def poll(self):
        while True:
            ready, _, _ = select.select([self.srv] + ([self.conn] if self.conn else []), [], [], 0)
            if not ready:
                return
            if self.srv in ready:
                new, addr = self.srv.accept()
                new.setblocking(False)
                if self.conn:
                    self.conn.close()  # the newest dashboard connection wins
                self.conn, self.buf = new, b""
                self.get_logger().info("dashboard connected")
                continue
            try:
                data = self.conn.recv(65536)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                data = b""
            if not data:
                self.conn.close()
                self.conn = None
                self.get_logger().info("dashboard disconnected")
                return
            self.buf += data
            *lines, self.buf = self.buf.split(b"\n")
            for line in reversed(lines):  # newest complete state only
                try:
                    msg = json.loads(line)
                    self.latest = ([float(a) for a in msg["axes"]][:16],
                                   [1 if b else 0 for b in msg["buttons"]][:32])
                    self.last_rx = self.now()
                    break
                except (ValueError, KeyError, TypeError):
                    continue

    def publish(self, axes, buttons):
        msg = Joy()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "dashboard"
        msg.axes = list(axes) + [0.0] * max(0, N_AXES - len(axes))
        msg.buttons = list(buttons) + [0] * max(0, N_BUTTONS - len(buttons))
        self.pub.publish(msg)

    def tick(self):
        self.poll()
        if self.latest and self.now() - self.last_rx < STALE_S:
            self.publish(*self.latest)
            self.active = True
        elif self.active:
            self.publish([], [])  # deadman released
            self.active = False
            self.get_logger().warn("gamepad input stopped: sent zero")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8771
    rclpy.init()
    node = JoyRelay(port)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            node.publish([], [])
        except Exception:
            pass
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
