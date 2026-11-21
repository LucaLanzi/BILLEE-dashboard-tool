"""Agent: processes, Ctrl-C to the whole group, output buffer, JSONC, HTTP auth."""
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["BILLEE_DASH_STATE"] = tempfile.mkdtemp()
from billee_dash import agent  # noqa: E402


def wait_state(a, pid, states, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        p = next(p for p in a.proc_list() if p["id"] == pid)
        if p["state"] in states:
            return p
        time.sleep(0.05)
    raise AssertionError(f"{pid} never reached {states}: {p}")


def text(a, pid):
    return [e[4] for e in a.hub.since(0)["lines"] if e[2] == pid]


class TestAgent(unittest.TestCase):
    def test_runs_and_captures(self):
        a = agent.Agent()
        p = a.start({"id": "t1", "label": "echo", "cmd": "echo one; printf 'a\\rb\\n'; exit 3"})
        p = wait_state(a, "t1", {"failed"})
        self.assertEqual(p["rc"], 3)
        self.assertEqual([t for t in text(a, "t1") if not t.startswith(("$", "["))], ["one", "a", "b"])

    def test_stop_reaches_children(self):
        a = agent.Agent()
        with tempfile.TemporaryDirectory() as d:
            marker = os.path.join(d, "child-got-int")
            # The child (a grandchild of the agent, like ros2 launch under pixi) gets the Ctrl-C too.
            script = f"bash -c 'trap \"touch {marker}; exit 0\" INT; while :; do sleep 0.1; done'; true"
            a.start({"id": "t2", "label": "tree", "cmd": script})
            wait_state(a, "t2", {"running"})
            time.sleep(0.5)
            self.assertEqual(a.stop("t2"), ["t2"])
            p = wait_state(a, "t2", {"stopped"}, timeout=15)
            time.sleep(0.2)
            self.assertTrue(os.path.exists(marker))
            self.assertEqual(p["state"], "stopped")

    def test_id_reuse_and_conflict(self):
        a = agent.Agent()
        a.start({"id": "same", "label": "x", "cmd": "sleep 5"})
        with self.assertRaises(RuntimeError):
            a.start({"id": "same", "label": "x", "cmd": "true"})
        a.stop("same")
        wait_state(a, "same", {"stopped"})
        a.start({"id": "same", "label": "x", "cmd": "true"})  # reusable once finished
        wait_state(a, "same", {"exited"})

    def test_hub_long_poll(self):
        h = agent.Hub(maxlen=5)
        t0 = time.monotonic()
        self.assertEqual(h.since(0, wait=0.3)["lines"], [])
        self.assertGreater(time.monotonic() - t0, 0.25)
        for i in range(8):
            h.add("p", str(i))
        r = h.since(1)
        self.assertTrue(r["gap"])  # lines 2 and 3 fell out of the ring
        self.assertEqual([e[4] for e in r["lines"]], ["3", "4", "5", "6", "7"][-5:])

    def test_jsonc(self):
        src = '{\n // c\n "a": "x // not a comment", /* b */ "b": [1, 2,],\n}'
        self.assertEqual(json.loads(agent.strip_jsonc(src)), {"a": "x // not a comment", "b": [1, 2]})
        here = os.path.dirname(os.path.abspath(__file__))
        for cfg in (".devcontainer/devcontainer.json", ".devcontainer/l4t/devcontainer.json"):
            path = os.path.join(here, "..", "..", cfg)  # as a submodule of billee-software-2027
            if os.path.exists(path):
                self.assertIn("remoteUser", agent.load_jsonc(path))

    def test_http_needs_token(self):
        import threading
        from http.server import ThreadingHTTPServer
        a = agent.Agent()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), agent.make_handler(a, "tok"))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        self.assertTrue(json.load(urllib.request.urlopen(base + "/api/health"))["ok"])
        req = urllib.request.Request(base + "/api/start", data=b'{"cmd": "true"}', method="POST")
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 401)
        req.add_header("X-Billee-Token", "tok")
        self.assertEqual(json.load(urllib.request.urlopen(req))["kind"], "host")
        srv.shutdown()


class TestLinks(unittest.TestCase):
    def test_links_follow_the_rover_ip(self):
        from billee_dash import commander
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"rover": {"host": "192.168.4.97"}}, f)
        links = commander.load_config(f.name)["links"]
        os.unlink(f.name)
        self.assertEqual(links, {"rcm_gds": "http://192.168.4.97:5000",
                                 "scm_gds": "http://192.168.4.97:5001",
                                 "foxglove_ws": "ws://192.168.4.97:8765",
                                 "astronaut": "https://192.168.4.97:8443"})


if __name__ == "__main__":
    unittest.main()
