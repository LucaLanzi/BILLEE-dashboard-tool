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


class TestDevcontainerCli(unittest.TestCase):
    def setUp(self):
        self.old_state, self.old_path = agent.STATE_DIR, os.environ["PATH"]
        agent.STATE_DIR = tempfile.mkdtemp()
        os.environ["PATH"] = "/usr/bin:/bin"  # no devcontainer, no node

    def tearDown(self):
        agent.STATE_DIR, os.environ["PATH"] = self.old_state, self.old_path

    def fake_cli(self):
        """tools/node (a script that prints its arguments) + tools/.../devcontainer.js."""
        tools = os.path.join(agent.STATE_DIR, "tools")
        js = os.path.join(tools, "node_modules", "@devcontainers", "cli", "devcontainer.js")
        node = os.path.join(tools, "node", "bin", "node")
        for path, body in ((js, ""), (node, '#!/bin/sh\necho "ARGS $*"\n')):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(body)
        os.chmod(node, 0o755)
        return node, js

    def repo(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, ".devcontainer"))
        with open(os.path.join(d, ".devcontainer", "devcontainer.json"), "w") as f:
            f.write('{"remoteUser": "ros_user"}')
        return d

    def test_finds_the_cli(self):
        self.assertIsNone(agent.devcontainer_cli())
        node, js = self.fake_cli()
        self.assertEqual(agent.devcontainer_cli(), [node, js])

    def test_rebuild_needs_the_cli(self):
        a = agent.Agent()
        p = a.container({"key": "rover", "repo": self.repo()}).rebuild()
        self.assertEqual((p["id"], p["tag"]), ("container-rover", "container"))
        self.assertEqual(wait_state(a, p["id"], {"failed"})["rc"], 1)
        self.assertTrue(any("devcontainer CLI" in t for t in text(a, p["id"])))

    def test_rebuild_runs_devcontainer_up(self):
        node, js = self.fake_cli()
        repo = self.repo()
        a = agent.Agent()
        dc = a.container({"key": "ground", "repo": repo})
        dc.find = lambda: None  # no docker needed
        p = dc.rebuild()
        with self.assertRaises(RuntimeError):  # Start and Rebuild share one id: one at a time
            dc.start()
        wait_state(a, p["id"], {"exited"})
        args = next(t for t in text(a, p["id"]) if t.startswith("ARGS "))
        self.assertEqual(args.split()[1:], [
            js, "up", "--workspace-folder", os.path.realpath(repo), "--config",
            os.path.join(os.path.realpath(repo), ".devcontainer/devcontainer.json"),
            "--remove-existing-container"])

    @unittest.skipUnless(os.path.isdir("/run/systemd/system"), "needs systemd")
    def test_missing_service_is_not_found(self):
        self.assertEqual(agent.Agent().service("billee-no-such-unit-xyz", "status")["active"],
                         "not-found")


class TestCommanderBuild(unittest.TestCase):
    def commander(self):
        from billee_dash import commander
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"dashboard": {"state_dir": tempfile.mkdtemp()},
                       "ground": {"mode": "container", "repo": tempfile.mkdtemp()}}, f)
        cfg = commander.load_config(f.name)
        os.unlink(f.name)
        return commander, commander.Commander(cfg)

    def test_build_preview(self):
        _, c = self.commander()
        out = c.build({"side": "both", "packages": "teleop", "deps": True, "preview": True})
        self.assertEqual(out["commands"], {
            "rover": "pixi run -e l4t build --parallel-workers 2 --executor sequential "
                     "--packages-up-to teleop",
            "ground": "pixi run -e default build --packages-up-to teleop"})
        with self.assertRaises(ValueError):
            c.build({"side": "moon", "preview": True})
        c.ground_enabled = False
        self.assertEqual(list(c.build({"side": "both", "preview": True})["commands"]), ["rover"])

    def test_foxglove_layouts(self):
        """The Foxglove link carries the layoutId of the mode's layout once it's configured."""
        from urllib.parse import parse_qs, urlparse
        _, c = self.commander()
        c.cfg["foxglove"]["open_with"] = "web"
        q = parse_qs(urlparse(c.foxglove_url(layout="arm")).query)
        self.assertNotIn("layoutId", q)  # not configured: Foxglove keeps its last layout
        self.assertEqual(q["ds.url"], [c.cfg["links"]["foxglove_ws"]])
        c.cfg["foxglove"]["layouts"] = {"rover": "lay_rover1", "arm": "lay_arm2"}
        for layout, lid in (("rover", "lay_rover1"), ("arm", "lay_arm2")):
            q = parse_qs(urlparse(c.link_url("foxglove", layout)).query)
            self.assertEqual(q["layoutId"], [lid])
        q = parse_qs(urlparse(c.link_url("foxglove_local", "rover")).query)
        self.assertEqual((q["ds.url"], q["layoutId"]), (["ws://localhost:8765"], ["lay_rover1"]))
        c.cfg["foxglove"]["open_with"] = "desktop"
        self.assertTrue(c.foxglove_url(layout="arm").startswith("foxglove://open?"))
        self.assertIn("layoutId=lay_arm2", c.foxglove_url(layout="arm"))

    def test_no_rebuild_while_a_mode_runs(self):
        commander, c = self.commander()
        c.job = {"title": "Teleop · Drive", "state": "running"}
        with self.assertRaises(commander.ClientError):
            c.rebuild_container("rover")

    def test_gds_services(self):
        from billee_dash import commander
        self.assertEqual(commander.load_config()["rover"]["services"],
                         ["billee-lan-gds", "billee-scm-lan-gds"])


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
