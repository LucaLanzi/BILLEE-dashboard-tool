"""The commander: the dashboard's web server, on the ground station.

It drives two agents (agent.py): its own, in this process, for the ground station, and the
rover's, over SSH (rover_link.py). The page talks only to this server:

    GET  /api/catalog            modes, options, links, CAN nodes
    GET  /api/state              links, containers, processes, the running mode
    GET  /api/logs?since=&wait=  output of every process on both machines (long poll)
    POST /api/mode/preview|start|stop
    POST /api/can                CAN tools on the rover host
    POST /api/container          start a dev container / Ctrl-C everything in it
    POST /api/proc/stop          stop one process
    POST /api/service            systemd services on the rover (the F' GDS units)
    POST /api/open               open a link (GDS, Foxglove) on this machine
    POST /api/rover/reconnect

POSTs must carry `X-Billee-Dash: 1` and JSON, which a page on another site can't send without
the browser asking this server first, so a random website can't drive the rover.
"""

import collections
import json
import mimetypes
import os
import re
import signal
import socket
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import agent as agent_mod
from . import modes
from .rover_link import ClientError, LocalClient, RoverLink

PKG_DIR = os.path.dirname(os.path.realpath(__file__))
TOOL_DIR = os.path.dirname(PKG_DIR)
WEB_DIR = os.path.join(TOOL_DIR, "web")
CONFIG_DIR = os.path.join(TOOL_DIR, "config")

SPLASH = r"""
	   ██████╗ ██╗██╗     ██╗     ███████╗███████╗
	   ██╔══██╗██║██║     ██║     ██╔════╝██╔════╝
	   ██████╔╝██║██║     ██║     █████╗  █████╗
	   ██╔══██╗██║██║     ██║     ██╔══╝  ██╔══╝
	   ██████╔╝██║███████╗███████╗███████╗███████╗
	   ╚═════╝ ╚═╝╚══════╝╚══════╝╚══════╝╚══════╝

          Rover Control Module  ·  Science Control Module
     Powered by ROS2 Humble and F` Flight Software (NASA/JPL)
"""


# --------------------------------------------------------------------------- config
def _merge(base, over):
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path=None):
    """config/dashboard.json, then config/local.json (written by `make setup-dash`, not in
    git), then the file given with --config."""
    with open(os.path.join(CONFIG_DIR, "dashboard.json")) as f:
        cfg = json.load(f)
    for extra in (os.path.join(CONFIG_DIR, "local.json"), path):
        if extra and os.path.exists(extra):
            with open(extra) as f:
                cfg = _merge(cfg, json.load(f))
    ground = cfg["ground"]
    if not ground.get("repo"):
        # The dashboard is a submodule of billee-software-2027: the repo is one level up.
        parent = os.path.dirname(TOOL_DIR)
        ground["repo"] = parent if os.path.isdir(os.path.join(parent, "ros2_ws")) else \
            os.path.expanduser("~/billee-software-2027")
    ground["repo"] = os.path.realpath(os.path.expanduser(ground["repo"]))
    return cfg


def can_nodes(repo):
    """Wheel nodes from ros2_ws/src/odesc/config/node_map.yaml (joint + its position comment)."""
    path = os.path.join(repo, "ros2_ws", "src", "odesc", "config", "node_map.yaml")
    try:
        text = open(path).read()
    except OSError:
        return [{"node": n, "joint": "", "where": ""} for n in (2, 3, 4, 5, 6, 7)]
    out = []
    for m in re.finditer(r"joint:\s*(\S+)\s*(?:#\s*([^\n]*?))?\s*\n\s*node_id:\s*(\d+)", text):
        out.append({"node": int(m.group(3)), "joint": m.group(1), "where": (m.group(2) or "").strip()})
    return out or [{"node": n, "joint": "", "where": ""} for n in (2, 3, 4, 5, 6, 7)]


def tcp_open(url, timeout=0.6):
    u = urlparse(url)
    port = u.port or (443 if u.scheme in ("https", "wss") else 80)
    try:
        with socket.create_connection((u.hostname, port), timeout=timeout):
            return True
    except (OSError, TypeError):
        return False


# --------------------------------------------------------------------------- combined output
class Output:
    """Every line from both machines plus the dashboard's own, in one sequence."""

    def __init__(self, maxlen=60000):
        self.lines = collections.deque(maxlen=maxlen)
        self.seq = 0
        self.cond = threading.Condition()

    def add(self, side, proc, text, stream="out", ts=None):
        with self.cond:
            self.seq += 1
            self.lines.append([self.seq, ts or round(time.time(), 3), side, proc, stream, text])
            self.cond.notify_all()

    def since(self, seq, wait=0.0, limit=3000):
        deadline = time.monotonic() + wait
        with self.cond:
            if seq > self.seq:  # the server restarted: start over
                seq = 0
            while True:
                out = [e for e in self.lines if e[0] > seq] if self.seq > seq else []
                left = deadline - time.monotonic()
                if out or left <= 0:
                    break
                self.cond.wait(left)
            last = self.seq
        return {"lines": out[-limit:], "last": last}


# --------------------------------------------------------------------------- commander
class Commander:
    def __init__(self, cfg):
        self.cfg = cfg
        self.state_dir = os.path.expanduser(cfg["dashboard"].get("state_dir", "~/.billee-dash"))
        self.out = Output()
        self.ground_agent = agent_mod.Agent("ground")
        self.ground = LocalClient(self.ground_agent)
        self.rover_link = RoverLink(cfg["rover"], self.state_dir, self.note)
        self.job = None
        self.job_lock = threading.Lock()
        self.cache = {"rover": None, "ground": None, "links": {}, "updated": 0}
        self.nodes = can_nodes(cfg["ground"]["repo"])

    # ---- logging
    def note(self, text, proc="dashboard", stream="meta"):
        self.out.add("dash", proc, text, stream)

    def client(self, side):
        if side == "ground":
            return self.ground
        c = self.rover_link.client
        if c is None:
            raise ClientError(f"rover link is {self.rover_link.state}"
                              f"{': ' + self.rover_link.message if self.rover_link.message else ''}")
        return c

    def container_spec(self, side):
        c = self.cfg[side]
        spec = {"key": side, "repo": c["repo"], "config": c["devcontainer"]}
        if c.get("container_name"):
            spec["name"] = c["container_name"]
        return spec

    # ---- background loops
    def start(self):
        self.rover_link.start()
        for side in ("ground", "rover"):
            threading.Thread(target=self._pull_output, args=(side,), daemon=True).start()
        threading.Thread(target=self._poll_status, daemon=True).start()

    def _pull_output(self, side):
        since, epoch = 0, None
        while True:
            try:
                client = self.client(side)
            except ClientError:
                time.sleep(1.0)
                continue
            if side == "rover" and epoch is not self.rover_link.client:
                epoch = self.rover_link.client  # new connection: the agent may be new too
            try:
                r = client.logs(since, wait=20.0)
            except (ConnectionError, ClientError):
                time.sleep(1.0)
                continue
            if r["last"] < since:  # the agent restarted
                since = 0
                continue
            for seq, ts, proc, stream, text in r["lines"]:
                self.out.add(side, proc, text, stream, ts)
            if r.get("gap"):
                self.out.add(side, "dashboard", "... (older output dropped)", "meta")
            since = r["last"]

    def _poll_status(self):
        services = self.cfg["rover"].get("services", [])
        while True:
            try:
                self.cache["ground"] = self.ground.status([self.container_spec("ground")])
            except Exception as e:
                self.cache["ground"] = {"error": str(e)}
            try:
                self.cache["rover"] = self.client("rover").status(
                    [self.container_spec("rover")], services)
            except (ClientError, ConnectionError) as e:
                self.cache["rover"] = {"error": str(e)}
            links = self.cfg["links"]
            self.cache["links"] = {k: tcp_open(v) for k, v in links.items()}
            self.cache["updated"] = time.time()
            self._adopt_job()
            self._check_job()
            time.sleep(1.5)

    # ---- links
    def foxglove_url(self, ws=None):
        ws = ws or self.cfg["links"]["foxglove_ws"]
        how = self.cfg.get("foxglove", {}).get("open_with", "auto")
        q = urllib.parse.urlencode({"ds": "foxglove-websocket", "ds.url": ws})
        desktop = f"foxglove://open?{q}"
        web = f"https://app.foxglove.dev/~/view?{q}"
        if how == "desktop":
            return desktop
        if how == "web":
            return web
        # auto: the desktop app if it registered its foxglove:// handler, else the web app
        try:
            res = subprocess.run(["xdg-mime", "query", "default", "x-scheme-handler/foxglove"],
                                 capture_output=True, text=True, timeout=3)
            if res.stdout.strip():
                return desktop
        except (OSError, subprocess.TimeoutExpired):
            pass
        return web

    def link_url(self, name):
        links = self.cfg["links"]
        if name == "foxglove":
            return self.foxglove_url()
        if name == "foxglove_local":
            return self.foxglove_url("ws://localhost:8765")
        if name in links:
            return links[name]
        raise ValueError(f"unknown link {name!r}")

    def open_link(self, name):
        url = self.link_url(name)
        self.note(f"opening {name}: {url}")
        if url.startswith("foxglove://"):
            try:
                subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                return url
            except OSError:
                url = self.link_url("foxglove").replace("foxglove://open?",
                                                        "https://app.foxglove.dev/~/view?")
        webbrowser.open_new_tab(url)
        return url

    # ---- commands
    def proc_spec(self, side, step, tag=None):
        """A plan step -> an agent start spec."""
        c = self.cfg[side]
        cmd = step["cmd"]
        spec = {"id": step["id"], "label": step["label"], "tag": tag}
        if step["where"] == "container":
            spec["target"] = "container"
            spec["container"] = self.container_spec(side)
            if step.get("ros"):
                pixi = c.get("pixi_run", "pixi run -e {env}").format(env=c["pixi_env"])
                cmd = f"cd ros2_ws && exec {pixi} {cmd}"
            env = {"ROS_DOMAIN_ID": str(self.cfg.get("ros_domain_id", 42))}
            if side == "ground" and os.environ.get("DISPLAY"):
                env["DISPLAY"] = os.environ["DISPLAY"]
            spec["env"] = env
        else:
            spec["target"] = "host"
            spec["cwd"] = c["repo"]
        spec["cmd"] = cmd
        return spec

    def run_step(self, side, step, tag=None):
        return self.client(side).start(self.proc_spec(side, step, tag))

    def wait_proc(self, side, proc_id, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for p in self.client(side).procs():
                if p["id"] == proc_id and p["state"] not in ("starting", "running", "stopping"):
                    return p
            if self.job and self.job.get("cancel"):
                return None
            time.sleep(0.5)
        return None

    def ensure_container(self, side, timeout=900):
        client = self.client(side)
        st = client.status([self.container_spec(side)])["containers"][side]
        if st.get("state") == "running":
            return True, "running"
        if st.get("state") in ("no-docker", "error"):
            return False, st.get("error", st["state"])
        self.note(f"{side} container is {st.get('state')}: starting it")
        p = client.container_start(self.container_spec(side))
        done = self.wait_proc(side, p["id"], timeout)
        if not done:
            return False, "timed out (or cancelled) starting the container"
        return done["rc"] == 0, f"exit {done['rc']}"

    def can_action(self, body):
        step = modes.can_command(body.get("action"), body.get("iface", "can2"),
                                 body.get("bitrate"), body.get("nodes"))
        # one CAN tool at a time: reuse its fixed id so a second click while running is refused
        return self.run_step("rover", step, tag="can")

    # ---- modes
    def job_view(self):
        j = self.job
        if not j:
            return None
        return {k: j[k] for k in ("mode", "title", "state", "message", "steps", "started",
                                  "options", "plan", "links")}

    def start_mode(self, mode_id, options):
        plan = modes.build(mode_id, options, self.cfg)
        with self.job_lock:
            if self.job and self.job["state"] in ("starting", "running", "degraded", "stopping"):
                raise ClientError(f"{self.job['title']} is {self.job['state']}: stop it first")
            mode = modes.MODE_BY_ID[mode_id]
            steps = []
            if plan["rover"] or plan["pre"]:
                steps += [{"name": "Rover link", "state": "pending"},
                          {"name": "Rover container", "state": "pending"}]
            if plan["ground"]:
                steps.append({"name": "Ground container", "state": "pending"})
            steps += [{"name": s["label"], "state": "pending"} for s in plan["pre"]]
            steps += [{"name": s["label"], "state": "pending"} for s in plan["rover"]]
            steps += [{"name": s["label"], "state": "pending"} for s in plan["ground"]]
            if plan["links"]:
                steps.append({"name": "Open links", "state": "pending"})
            self.job = {"mode": mode_id, "title": mode["title"], "state": "starting",
                        "message": "", "steps": steps, "started": time.time(),
                        "options": plan["options"], "plan": plan, "links": plan["links"],
                        "cancel": False, "procs": []}
        threading.Thread(target=self._run_job, args=(self.job,), daemon=True).start()
        return self.job_view()

    def _step(self, job, name, state, message=""):
        for s in job["steps"]:
            if s["name"] == name:
                s["state"] = state
                if message:
                    s["message"] = message
        if state in ("failed", "warn"):
            self.note(f"{job['title']}: {name}: {state}{' - ' + message if message else ''}")

    def _fail(self, job, name, message):
        if job["cancel"]:  # Stop was pressed while starting: not a failure
            self._step(job, name, "cancelled")
            return
        self._step(job, name, "failed", message)
        job["state"], job["message"] = "failed", f"{name}: {message}"
        self.note(f"{job['title']} not started: {name}: {message}", stream="err")
        self._stop_job_procs(job)

    def _run_job(self, job):
        plan = job["plan"]
        self.note(f"=== {job['title']} ===")
        try:
            if plan["rover"] or plan["pre"]:
                self._step(job, "Rover link", "active")
                for _ in range(60):
                    if self.rover_link.client or job["cancel"]:
                        break
                    time.sleep(0.5)
                if not self.rover_link.client:
                    return self._fail(job, "Rover link", self.rover_link.message or "not connected")
                self._step(job, "Rover link", "done")
                self._step(job, "Rover container", "active")
                ok, msg = self.ensure_container("rover")
                if not ok:
                    return self._fail(job, "Rover container", msg)
                self._step(job, "Rover container", "done")
            if plan["ground"]:
                self._step(job, "Ground container", "active")
                ok, msg = self.ensure_container("ground")
                if not ok:
                    return self._fail(job, "Ground container", msg)
                self._step(job, "Ground container", "done")
            for step in plan["pre"]:
                if job["cancel"]:
                    return
                self._step(job, step["label"], "active")
                p = self.run_step("rover", step, tag="can")
                done = self.wait_proc("rover", p["id"], 120)
                if done is None:
                    return self._fail(job, step["label"], "did not finish")
                if done["rc"] != 0:
                    if step["id"] == "can-up":
                        return self._fail(job, step["label"],
                                          f"exit {done['rc']} (see Rover output; sudo rights? "
                                          "`make setup-dash`)")
                    # can-clear-faults exits 1 when a node keeps an error or is missing; the
                    # drivetrain's fault_policy decides what that means, so carry on.
                    self._step(job, step["label"], "warn", f"exit {done['rc']}: see Rover output")
                else:
                    self._step(job, step["label"], "done")
            for side in ("rover", "ground"):
                for i, step in enumerate(plan[side]):
                    if job["cancel"]:
                        return
                    self._step(job, step["label"], "active")
                    p = self.run_step(side, step, tag=f"mode:{job['mode']}")
                    job["procs"].append((side, p["id"]))
                    if p["state"] not in ("running", "starting"):
                        return self._fail(job, step["label"], f"{p['state']} (exit {p['rc']})")
                    self._step(job, step["label"], "done")
                if side == "rover" and plan["rover"] and plan["ground"]:
                    # Let the rover's controllers and bridge come up before the ground side.
                    delay = float(self.cfg.get("mode", {}).get("ground_delay_s", 5))
                    end = time.monotonic() + delay
                    while time.monotonic() < end and not job["cancel"]:
                        time.sleep(0.25)
            if plan["links"]:
                self._step(job, "Open links", "active")
                for name in plan["links"]:
                    try:
                        self.open_link(name)
                    except Exception as e:
                        self.note(f"could not open {name}: {e}", stream="err")
                self._step(job, "Open links", "done")
            if not job["cancel"]:
                job["state"] = "running"
                self.note(f"{job['title']} running")
        except (ClientError, ConnectionError) as e:
            current = next((s["name"] for s in job["steps"] if s["state"] == "active"), "start")
            self._fail(job, current, str(e))

    def _adopt_job(self):
        """After a dashboard restart, pick the running mode back up from the processes the
        agents still run (the rover's survive; tags say which mode started them)."""
        if self.job is not None:
            return
        found = []
        for side in ("rover", "ground"):
            for p in (self.cache.get(side) or {}).get("procs", []):
                tag = p.get("tag") or ""
                if tag.startswith("mode:") and p["state"] in ("starting", "running"):
                    found.append((side, p["id"], tag[5:]))
        if not found:
            return
        mode_id = found[0][2]
        mode = modes.MODE_BY_ID.get(mode_id, {"title": mode_id})
        with self.job_lock:
            if self.job is not None:
                return
            self.job = {"mode": mode_id, "title": mode["title"], "state": "running",
                        "message": "picked up after a dashboard restart", "steps": [],
                        "started": time.time(), "options": {}, "plan": None, "links": [],
                        "cancel": False, "procs": [(s, i) for s, i, _ in found]}
        self.note(f"{mode['title']} is already running: picked it up")

    def _check_job(self):
        job = self.job
        if not job or job["state"] not in ("running", "degraded", "stopping"):
            return
        alive, dead = [], []
        for side, pid in job["procs"]:
            info = self.cache.get(side) or {}
            procs = {p["id"]: p for p in info.get("procs", [])}
            p = procs.get(pid)
            if p is None:
                if "error" in info:
                    alive.append(pid)  # link down: assume still running
                continue
            (alive if p["state"] in ("starting", "running", "stopping") else dead).append(
                f"{side}:{p['label']}")
        if job["state"] == "stopping":
            if not alive:
                job["state"], job["message"] = "stopped", ""
                self.note(f"{job['title']} stopped")
        elif dead:
            job["state"] = "degraded"
            job["message"] = "exited: " + ", ".join(dead)

    def _stop_job_procs(self, job):
        for side, pid in reversed(job.get("procs", [])):
            try:
                self.client(side).stop(pid)
            except (ClientError, ConnectionError) as e:
                self.note(f"could not stop {side}:{pid}: {e}", stream="err")

    def stop_mode(self):
        job = self.job
        if not job:
            return None
        job["cancel"] = True
        if job["state"] in ("starting", "running", "degraded"):
            job["state"] = "stopping"
            self.note(f"stopping {job['title']}")
            self._stop_job_procs(job)
        return self.job_view()

    # ---- state for the page
    def state(self):
        rover = self.cache.get("rover") or {}
        ground = self.cache.get("ground") or {}
        return {
            "rover": {"link": self.rover_link.state, "message": self.rover_link.message,
                      "ssh": self.rover_link.ssh_command_text(), **rover},
            "ground": ground,
            "links": self.cache.get("links", {}),
            "job": self.job_view(),
            "updated": self.cache.get("updated"),
        }

    def catalog(self):
        cat = modes.catalog()
        cfg = self.cfg
        cat["links"] = {
            "rcm_gds": {"label": "RCM GDS", "url": cfg["links"]["rcm_gds"]},
            "scm_gds": {"label": "Science GDS", "url": cfg["links"]["scm_gds"]},
            "foxglove": {"label": "Foxglove", "url": self.foxglove_url(),
                         "ws": cfg["links"]["foxglove_ws"]},
        }
        cat["hosts"] = {"rover": cfg["rover"].get("host"), "ground": cfg["ground"].get("host"),
                        "rover_user": cfg["rover"].get("user")}
        cat["services"] = cfg["rover"].get("services", [])
        cat["can_nodes"] = self.nodes
        cat["can_default_iface"] = cfg.get("can", {}).get("iface", "can2")
        cat["can_default_bitrate"] = cfg.get("can", {}).get("bitrate", 500000)
        return cat

    # ---- startup
    def bring_up(self):
        """Start both dev containers as soon as they're reachable (startup.start_containers)."""
        def run():
            ok, msg = self.ensure_container("ground")
            self.note(f"ground container: {'ready' if ok else 'NOT ready - ' + msg}")
            for _ in range(240):
                if self.rover_link.client:
                    break
                time.sleep(0.5)
            if not self.rover_link.client:
                self.note("rover not reachable yet: start its container from the page once it is")
                return
            try:
                ok, msg = self.ensure_container("rover")
                self.note(f"rover container: {'ready' if ok else 'NOT ready - ' + msg}")
            except (ClientError, ConnectionError) as e:
                self.note(f"rover container: {e}", stream="err")
        threading.Thread(target=run, daemon=True).start()


# --------------------------------------------------------------------------- HTTP
def make_handler(cmd):
    class Handler(BaseHTTPRequestHandler):
        server_version = "billee-dash"

        def log_message(self, fmt, *args):
            pass

        def _send(self, code, obj=None, body=None, ctype="application/json"):
            body = body if body is not None else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/api/catalog":
                    return self._send(200, cmd.catalog())
                if url.path == "/api/state":
                    return self._send(200, cmd.state())
                if url.path == "/api/logs":
                    return self._send(200, cmd.out.since(int(q.get("since", 0)),
                                                         min(float(q.get("wait", 0)), 25.0)))
            except Exception as e:
                return self._send(500, {"error": str(e)})
            path = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
            full = os.path.realpath(os.path.join(WEB_DIR, path))
            if not full.startswith(os.path.realpath(WEB_DIR) + os.sep) or not os.path.isfile(full):
                return self._send(404, {"error": "not found"})
            with open(full, "rb") as f:
                data = f.read()
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            return self._send(200, body=data, ctype=ctype)

        def do_POST(self):
            if self.headers.get("X-Billee-Dash") != "1":
                return self._send(403, {"error": "missing X-Billee-Dash header"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                path = urlparse(self.path).path
                if path == "/api/mode/preview":
                    return self._send(200, modes.build(body["mode"], body.get("options"), cmd.cfg))
                if path == "/api/mode/start":
                    return self._send(200, cmd.start_mode(body["mode"], body.get("options")))
                if path == "/api/mode/stop":
                    return self._send(200, {"job": cmd.stop_mode()})
                if path == "/api/can":
                    return self._send(200, cmd.can_action(body))
                if path == "/api/container":
                    side, action = body.get("side"), body.get("action")
                    if side not in ("rover", "ground"):
                        raise ValueError("side must be rover or ground")
                    spec = cmd.container_spec(side)
                    if action == "start":
                        return self._send(200, cmd.client(side).container_start(spec))
                    if action == "stop_ros":
                        return self._send(200, {"result": cmd.client(side).container_stop_ros(spec)})
                    raise ValueError("action must be start or stop_ros")
                if path == "/api/proc/stop":
                    side = body.get("side")
                    if side not in ("rover", "ground"):
                        raise ValueError("side must be rover or ground")
                    return self._send(200, {"stopped": cmd.client(side).stop(body.get("id"))})
                if path == "/api/service":
                    name = body.get("name")
                    if name not in cmd.cfg["rover"].get("services", []):
                        raise ValueError(f"unknown service {name!r}")
                    return self._send(200, cmd.client("rover").service(name, body.get("action")))
                if path == "/api/open":
                    return self._send(200, {"url": cmd.open_link(body.get("link"))})
                if path == "/api/rover/reconnect":
                    cmd.rover_link.reconnect()
                    return self._send(200, {"ok": True})
                return self._send(404, {"error": "not found"})
            except (modes.OptionError, ValueError, KeyError, TypeError) as e:
                return self._send(400, {"error": str(e)})
            except ClientError as e:
                return self._send(409, {"error": str(e)})
            except ConnectionError as e:
                return self._send(503, {"error": f"rover unreachable: {e}"})
            except Exception as e:
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    return Handler


def serve(cfg, open_browser=True, autostart=True):
    dash = cfg["dashboard"]
    cmd = Commander(cfg)
    server = ThreadingHTTPServer((dash.get("bind", "127.0.0.1"), int(dash.get("port", 7780))),
                                 make_handler(cmd))
    server.daemon_threads = True

    def interrupt(*_):
        raise KeyboardInterrupt
    # Also when started in the background (SIGINT ignored there) or stopped with SIGTERM.
    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    url = f"http://localhost:{server.server_address[1]}"
    print(SPLASH)
    print(f"  Dashboard     {url}")
    print(f"  Rover         {cfg['rover'].get('user') or '?'}@{cfg['rover'].get('host')}"
          f"  (repo {cfg['rover'].get('repo')})")
    print(f"  Ground repo   {cfg['ground']['repo']}")
    print("  Ctrl-C stops the dashboard and what it started on this machine;"
          " the rover keeps running.\n", flush=True)
    cmd.note(f"dashboard up at {url}")
    cmd.start()
    startup = cfg.get("startup", {})
    if autostart and startup.get("start_containers", True):
        cmd.bring_up()
    if open_browser:
        webbrowser.open_new_tab(url)
        if startup.get("open_rcm_gds", True):
            cmd.open_link("rcm_gds")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        signal.signal(signal.SIGINT, signal.SIG_DFL)  # a second Ctrl-C quits at once
        try:
            print("\nstopping: Ctrl-C to everything the dashboard started on the ground station "
                  "(Ctrl-C again to quit now)", flush=True)
        except OSError:
            pass
        cmd.ground_agent.stop_all()
        for _ in range(60):
            if not any(p["state"] in ("running", "stopping") for p in cmd.ground_agent.proc_list()):
                break
            time.sleep(0.25)
        cmd.rover_link.close()
    return cmd
