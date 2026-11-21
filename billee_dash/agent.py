#!/usr/bin/env python3
"""BILLEE dashboard agent: the listener that runs commands on one machine.

One copy runs on the rover's host (the Jetson, outside the dev container). The dashboard on
the ground station (the commander) uploads this file over SSH, starts it and talks to it through
an SSH tunnel. The commander also imports this module and runs an in-process copy for the
ground station itself, so both machines are driven by the same code.

The agent:
  - starts and tracks processes, on the host or inside a dev container (`docker exec`),
    each in its own process group so Stop reaches every child (Ctrl-C first);
  - keeps the last lines of every process's output in a ring buffer that the commander
    long-polls, so a dropped radio link loses no output and stops nothing;
  - finds a dev container by the labels VS Code / the devcontainer CLI put on it, starts it and
    runs its postStartCommand, like "Reopen in Container" does;
  - reports and restarts systemd services (the F' GDS units).

Standard library only (Python 3.8+), one file, so it can run on any host as-is.

    python3 agent.py --port 8770            # serve in the foreground
    python3 agent.py --ensure --port 8770   # start in the background unless the same version runs;
                                            # prints {"port", "token", "version"} as JSON

It listens on 127.0.0.1 only and every call needs the token from ~/.billee-dash/token, so only
someone who can already log in to this machine can use it.
"""

import argparse
import collections
import hashlib
import json
import os
import re
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

API = 1  # bump on an incompatible API change
STATE_DIR = os.path.expanduser(os.environ.get("BILLEE_DASH_STATE", "~/.billee-dash"))
PIDFILE_DIR_IN_CONTAINER = "/tmp/billee-dash"
STOP_ESCALATE_S = (10.0, 15.0)  # SIGINT, then SIGTERM after 10 s, SIGKILL after 15 s
ANSI_CR = re.compile(r"\r(?!\n)")


def file_version():
    try:
        with open(os.path.realpath(__file__), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return "unknown"


VERSION = file_version()


# --------------------------------------------------------------------------- output buffer
class Hub:
    """Ring buffer of output lines from every process, with a sequence number per line."""

    def __init__(self, maxlen=40000):
        self.lines = collections.deque(maxlen=maxlen)
        self.seq = 0
        self.cond = threading.Condition()
        self.listeners = []

    def add(self, proc_id, text, stream="out"):
        with self.cond:
            self.seq += 1
            entry = (self.seq, round(time.time(), 3), proc_id, stream, text)
            self.lines.append(entry)
            self.cond.notify_all()
        for listener in list(self.listeners):
            try:
                listener(entry)
            except Exception:  # a broken listener must not break output capture
                pass

    def since(self, seq, proc_id=None, wait=0.0, limit=2000):
        deadline = time.monotonic() + max(0.0, wait)
        with self.cond:
            while True:
                out = [e for e in self.lines if e[0] > seq and (proc_id is None or e[2] == proc_id)]
                remaining = deadline - time.monotonic()
                if out or remaining <= 0:
                    break
                self.cond.wait(remaining)
            last = self.seq
            gap = seq > 0 and bool(self.lines) and self.lines[0][0] > seq + 1
        if len(out) > limit:
            out, gap = out[-limit:], True
        return {"lines": out, "last": last, "gap": gap}


# --------------------------------------------------------------------------- JSONC
def strip_jsonc(text):
    """devcontainer.json allows // and /* */ comments and trailing commas."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def load_jsonc(path):
    with open(path) as f:
        return json.loads(strip_jsonc(f.read()))


# --------------------------------------------------------------------------- processes
class Proc:
    def __init__(self, pid_, label, kind, tag=None, container=None, cmd=None):
        self.id = pid_
        self.label = label
        self.kind = kind  # host | container | task
        self.tag = tag
        self.container = container  # container key for kind == container
        self.cmd = cmd
        self.state = "starting"  # starting | running | stopping | exited | failed | stopped
        self.rc = None
        self.started = time.time()
        self.ended = None
        self.popen = None
        self.container_id = None
        self.stop_requested = False

    def view(self):
        return {
            "id": self.id, "label": self.label, "kind": self.kind, "tag": self.tag,
            "container": self.container, "cmd": self.cmd, "state": self.state, "rc": self.rc,
            "started": self.started, "ended": self.ended,
        }

    @property
    def alive(self):
        return self.state in ("starting", "running", "stopping")


def _pump(stream, hub, proc_id):
    """Copy a pipe into the hub line by line. A bare \\r (progress bars) also ends a line."""
    pending = b""
    while True:
        chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(4096)
        if not chunk:
            break
        pending += chunk
        *complete, pending = re.split(rb"\r\n|\n|\r", pending)
        for raw in complete:
            hub.add(proc_id, raw.decode("utf-8", "replace"))
    if pending:
        hub.add(proc_id, pending.decode("utf-8", "replace"))


class Agent:
    def __init__(self, name="host"):
        self.name = name
        self.hub = Hub()
        self.procs = collections.OrderedDict()
        self.lock = threading.RLock()
        self.containers = {}  # key -> Devcontainer
        self.counter = 0
        self.started = time.time()

    # ---- bookkeeping
    def log(self, text, proc_id="agent"):
        self.hub.add(proc_id, text, "meta")

    def _new_id(self, wanted, label):
        """A caller-chosen id is reused run after run (unless that run is still alive);
        otherwise a fresh id is made from the label."""
        with self.lock:
            if wanted:
                pid_ = re.sub(r"[^A-Za-z0-9_.-]+", "-", wanted).strip("-") or "proc"
                existing = self.procs.get(pid_)
                if existing is not None and existing.alive:
                    raise RuntimeError(f"'{existing.label}' is already running")
                return pid_
            base = re.sub(r"[^A-Za-z0-9_.-]+", "-", label or "proc").strip("-")[:40] or "proc"
            self.counter += 1
            return f"{base}-{self.counter}"

    def _register(self, proc):
        with self.lock:
            self.procs[proc.id] = proc
            self.procs.move_to_end(proc.id)
            # Keep the 200 newest finished records.
            finished = [p for p in self.procs.values() if not p.alive]
            for old in finished[:-200]:
                self.procs.pop(old.id, None)

    def proc_list(self):
        with self.lock:
            return [p.view() for p in self.procs.values()]

    # ---- containers
    def container(self, spec):
        key = spec.get("key") or "default"
        with self.lock:
            dc = self.containers.get(key)
            if dc is None or dc.spec != spec:
                dc = Devcontainer(self, spec)
                self.containers[key] = dc
            return dc

    # ---- starting
    def start(self, spec):
        """spec: {id?, label, target: host|container, container?: {...}, cmd: str | [argv],
        cwd?, env?: {}, tag?}. cmd as a string runs in bash -c."""
        label = spec.get("label") or (spec["cmd"] if isinstance(spec["cmd"], str) else " ".join(spec["cmd"]))
        target = spec.get("target", "host")
        proc_id = self._new_id(spec.get("id"), label)
        cmd = spec["cmd"]
        env = {str(k): str(v) for k, v in (spec.get("env") or {}).items()}
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("RCUTILS_LOGGING_BUFFERED_STREAM", "1")
        env.setdefault("RCUTILS_COLORIZED_OUTPUT", "1")

        if target == "host":
            argv = ["bash", "-c", cmd] if isinstance(cmd, str) else list(cmd)
            proc = Proc(proc_id, label, "host", spec.get("tag"), cmd=_show(cmd))
            self._register(proc)
            self.log(f"$ {_show(cmd)}", proc_id)
            full_env = dict(os.environ, **env)
            cwd = os.path.expanduser(spec.get("cwd") or "~")
            try:
                proc.popen = subprocess.Popen(
                    argv, cwd=cwd, env=full_env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as e:
                return self._failed(proc, f"could not start: {e}")
        elif target == "container":
            dc = self.container(spec["container"])
            proc = Proc(proc_id, label, "container", spec.get("tag"), dc.key, cmd=_show(cmd))
            self._register(proc)
            self.log(f"[{dc.key} container] $ {_show(cmd)}", proc_id)
            info = dc.status()
            if info.get("state") != "running":
                return self._failed(proc, f"{dc.key} container is not running ({info.get('state')})")
            proc.container_id = info["id"]
            script = cmd if isinstance(cmd, str) else " ".join(shlex.quote(a) for a in cmd)
            wrapper = (
                f"mkdir -p {PIDFILE_DIR_IN_CONTAINER}; set -m; "
                f'bash -c "$BILLEE_CMD" & child=$!; set +m; '
                f"echo $child > {PIDFILE_DIR_IN_CONTAINER}/{proc_id}.pid; "
                f"wait $child; rc=$?; rm -f {PIDFILE_DIR_IN_CONTAINER}/{proc_id}.pid; exit $rc"
            )
            argv = ["docker", "exec", "-u", dc.user]
            workdir = spec.get("cwd") or dc.workspace(info)
            if workdir:
                argv += ["-w", workdir]
            for k, v in dict(env, BILLEE_CMD=script).items():
                argv += ["-e", f"{k}={v}"]
            argv += [info["id"], "bash", "-c", wrapper]
            try:
                proc.popen = subprocess.Popen(
                    argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as e:
                return self._failed(proc, f"could not run docker: {e}")
        else:
            raise ValueError(f"unknown target {target!r}")

        proc.state = "running"
        threading.Thread(target=self._watch, args=(proc,), daemon=True).start()
        return proc.view()

    def task(self, label, fn, proc_id=None, tag=None):
        """Run fn(log) in a thread and track it like a process. fn returns an exit code."""
        proc = Proc(self._new_id(proc_id, label), label, "task", tag)
        self._register(proc)
        proc.state = "running"

        def run():
            try:
                rc = fn(lambda text: self.hub.add(proc.id, text))
            except Exception as e:  # report, don't kill the agent
                self.hub.add(proc.id, f"ERROR: {e}", "err")
                rc = 1
            self._finish(proc, rc if isinstance(rc, int) else 0)

        threading.Thread(target=run, daemon=True).start()
        return proc.view()

    def _failed(self, proc, message):
        self.hub.add(proc.id, f"ERROR: {message}", "err")
        self._finish(proc, 127)
        return proc.view()

    def _finish(self, proc, rc):
        proc.rc = rc
        proc.ended = time.time()
        if proc.stop_requested:
            proc.state = "stopped"
        else:
            proc.state = "exited" if rc == 0 else "failed"
        self.log(f"[{proc.label}] {proc.state} (exit {rc})", proc.id)

    def _watch(self, proc):
        _pump(proc.popen.stdout, self.hub, proc.id)
        rc = proc.popen.wait()
        self._finish(proc, rc)

    # ---- stopping
    def stop(self, proc_id=None, tag=None):
        with self.lock:
            targets = [p for p in self.procs.values() if p.alive and
                       (p.id == proc_id or (tag is not None and p.tag == tag))]
        for p in targets:
            self._stop_one(p)
        return [p.id for p in targets]

    def stop_all(self):
        with self.lock:
            targets = [p for p in self.procs.values() if p.alive]
        for p in targets:
            self._stop_one(p)
        return [p.id for p in targets]

    def _stop_one(self, proc):
        if proc.kind == "task" or proc.popen is None:
            proc.stop_requested = True
            return
        proc.stop_requested = True
        proc.state = "stopping"
        self.log(f"[{proc.label}] stopping (Ctrl-C)", proc.id)
        threading.Thread(target=self._escalate, args=(proc,), daemon=True).start()

    def _signal(self, proc, sig):
        if proc.kind == "container":
            # Killing the `docker exec` client would leave the process running in the
            # container, so signal its process group in there.
            name = signal.Signals(sig).name.replace("SIG", "")
            pidfile = f"{PIDFILE_DIR_IN_CONTAINER}/{proc.id}.pid"
            subprocess.run(
                ["docker", "exec", proc.container_id, "bash", "-c",
                 f'p=$(cat {pidfile} 2>/dev/null) && kill -{name} -- -"$p" 2>/dev/null'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        else:
            try:
                os.killpg(proc.popen.pid, sig)
            except ProcessLookupError:
                pass

    def _escalate(self, proc):
        steps = [(signal.SIGINT, 0.0), (signal.SIGTERM, STOP_ESCALATE_S[0]),
                 (signal.SIGKILL, STOP_ESCALATE_S[1])]
        t0 = time.monotonic()
        for sig, at in steps:
            while time.monotonic() - t0 < at:
                if proc.popen.poll() is not None:
                    return
                time.sleep(0.2)
            if proc.popen.poll() is not None:
                return
            if sig != signal.SIGINT:
                self.log(f"[{proc.label}] still running, sending {signal.Signals(sig).name}", proc.id)
            try:
                self._signal(proc, sig)
            except Exception as e:
                self.log(f"[{proc.label}] signal failed: {e}", proc.id)
        time.sleep(3)
        if proc.popen.poll() is None:
            try:
                os.killpg(proc.popen.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    # ---- services
    def service(self, name, action):
        if not re.fullmatch(r"[A-Za-z0-9@_.-]+", name or ""):
            raise ValueError("bad service name")
        if action == "status":
            res = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True)
            text = (res.stdout.strip() or res.stderr.strip() or "unknown").splitlines()[0]
            return {"name": name, "active": text[:80]}
        if action not in ("start", "stop", "restart"):
            raise ValueError("action must be status|start|stop|restart")
        return self.start({
            "id": f"service-{action}-{name}", "label": f"systemctl {action} {name}",
            "target": "host", "cmd": ["sudo", "-n", "systemctl", action, name]})

    # ---- status
    def host_info(self):
        info = {"hostname": socket.gethostname(), "agent_version": VERSION, "api": API,
                "uptime": round(time.time() - self.started)}
        try:
            info["load"] = os.getloadavg()[0]
        except OSError:
            pass
        try:
            with open("/proc/meminfo") as f:
                mem = dict(re.findall(r"^(\w+):\s+(\d+)", f.read(), re.M))
            info["mem_total_mb"] = int(mem["MemTotal"]) // 1024
            info["mem_avail_mb"] = int(mem["MemAvailable"]) // 1024
        except (OSError, KeyError):
            pass
        return info

    def status(self, containers=(), services=()):
        out = {"host": self.host_info(), "procs": self.proc_list(), "seq": self.hub.seq}
        out["containers"] = {}
        for spec in containers:
            try:
                out["containers"][spec.get("key", "default")] = self.container(spec).status()
            except Exception as e:
                out["containers"][spec.get("key", "default")] = {"state": "error", "error": str(e)}
        out["services"] = {}
        for name in services:
            try:
                out["services"][name] = self.service(name, "status")["active"]
            except Exception as e:
                out["services"][name] = f"error: {e}"
        return out


def _show(cmd):
    return cmd if isinstance(cmd, str) else " ".join(shlex.quote(a) for a in cmd)


# --------------------------------------------------------------------------- dev containers
class Devcontainer:
    """A dev container defined by <repo>/<config>, found by its devcontainer labels."""

    def __init__(self, agent, spec):
        self.agent = agent
        self.spec = dict(spec)
        self.key = spec.get("key", "default")
        self.repo = os.path.realpath(os.path.expanduser(spec["repo"]))
        self.config_rel = spec.get("config", ".devcontainer/devcontainer.json")
        self.config_path = os.path.join(self.repo, self.config_rel)
        self.name_hint = spec.get("name")  # optional docker container name
        self._config = None
        self._ws_cache = None

    @property
    def config(self):
        if self._config is None:
            try:
                self._config = load_jsonc(self.config_path)
            except (OSError, ValueError):
                self._config = {}
        return self._config

    @property
    def user(self):
        return self.spec.get("user") or self.config.get("remoteUser") or \
            self.config.get("containerUser") or "root"

    def _docker(self, *args, timeout=20):
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)

    def find(self):
        if self.name_hint:
            res = self._docker("ps", "-a", "--filter", f"name=^{self.name_hint}$",
                               "--format", "{{.ID}}\t{{.State}}\t{{.Names}}\t")
        else:
            res = self._docker(
                "ps", "-a", "--filter", f"label=devcontainer.local_folder={self.repo}",
                "--format", '{{.ID}}\t{{.State}}\t{{.Names}}\t{{.Label "devcontainer.config_file"}}')
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or "docker ps failed")
        rows = [r.split("\t") for r in res.stdout.splitlines() if r.strip()]
        if not rows:
            return None
        # Prefer the container made from this exact config (a repo can have several).
        best = [r for r in rows if len(r) > 3 and r[3] and
                os.path.realpath(r[3]) == os.path.realpath(self.config_path)]
        best = best or [r for r in rows if len(r) <= 3 or not r[3]] or rows
        running = [r for r in best if r[1] == "running"]
        row = (running or best)[0]
        return {"id": row[0], "state": row[1], "name": row[2]}

    def workspace(self, info=None):
        if self._ws_cache:
            return self._ws_cache
        info = info or self.find()
        if not info:
            return None
        res = self._docker("inspect", "--format", "{{json .Mounts}}", info["id"])
        try:
            for m in json.loads(res.stdout or "[]"):
                if os.path.realpath(m.get("Source", "")) == self.repo:
                    self._ws_cache = m["Destination"]
                    return self._ws_cache
        except ValueError:
            pass
        return self.config.get("workspaceFolder") or f"/workspaces/{os.path.basename(self.repo)}"

    def status(self):
        try:
            info = self.find()
        except FileNotFoundError:
            return {"state": "no-docker", "error": "docker is not installed on this machine"}
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            return {"state": "error", "error": str(e)}
        if not info:
            return {"state": "missing", "config": self.config_rel,
                    "repo_exists": os.path.isdir(self.repo)}
        info["config"] = self.config_rel
        if info["state"] == "running":
            info["workspace"] = self.workspace(info)
        return info

    def start(self):
        return self.agent.task(f"start {self.key} dev container", self._start_task,
                               proc_id=f"container-{self.key}", tag="container")

    def _run_logged(self, log, argv, cwd=None):
        log("$ " + _show(argv))
        p = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL)
        for raw in iter(p.stdout.readline, b""):
            log(raw.decode("utf-8", "replace").rstrip("\r\n"))
        return p.wait()

    def _lifecycle(self, name):
        """A devcontainer lifecycle command as a list of shell strings."""
        cmd = self.config.get(name)
        if not cmd:
            return []
        if isinstance(cmd, str):
            return [cmd]
        if isinstance(cmd, list):
            return [" ".join(shlex.quote(a) for a in cmd)]
        if isinstance(cmd, dict):  # named commands (run in parallel by VS Code; in order here)
            return [c if isinstance(c, str) else " ".join(shlex.quote(a) for a in c)
                    for c in cmd.values()]
        return []

    def _start_task(self, log):
        if not os.path.isdir(self.repo):
            log(f"ERROR: {self.repo} does not exist on {socket.gethostname()}")
            return 1
        info = self.find()
        if info and info["state"] == "running":
            log(f"{self.key} container {info['name']} is already running")
            return 0
        for cmd in self._lifecycle("initializeCommand"):
            if "xhost" in cmd and not os.environ.get("DISPLAY"):
                log(f"skipping initializeCommand ({cmd}): no DISPLAY")
                continue
            rc = self._run_logged(log, ["bash", "-c", cmd], cwd=self.repo)
            if rc != 0:
                log(f"initializeCommand exited {rc}; continuing")
        if info is None:
            cli = shutil_which("devcontainer") or os.path.join(STATE_DIR, "tools", "node_modules", ".bin", "devcontainer")
            if not os.path.exists(cli):
                log(f"ERROR: no dev container for {self.config_rel} in {self.repo} yet, and the "
                    "devcontainer CLI isn't installed. Open the repo in VS Code once (Dev Containers: "
                    "Reopen in Container), or run `make setup-dash` to install the CLI.")
                return 1
            log("no container yet: creating it with the devcontainer CLI (the first build can take a while)")
            return self._run_logged(log, [cli, "up", "--workspace-folder", self.repo,
                                          "--config", self.config_path])
        log(f"starting {self.key} container {info['name']} ({info['id'][:12]})")
        rc = self._run_logged(log, ["docker", "start", info["id"]])
        if rc != 0:
            return rc
        self._ws_cache = None
        ws = self.workspace(info)
        for cmd in self._lifecycle("postStartCommand"):
            log("running postStartCommand (as VS Code does at every container start)")
            rc = self._run_logged(log, ["docker", "exec", "-u", self.user, "-w", ws or "/",
                                        info["id"], "bash", "-c", cmd])
            if rc != 0:
                log(f"postStartCommand exited {rc}")
                return rc
        log(f"{self.key} container is up")
        return 0

    def stop_ros(self):
        """Ctrl-C every process the agent started in this container, even if the agent that
        started it is gone (pidfiles in the container's /tmp)."""
        info = self.find()
        if not info or info["state"] != "running":
            return "not running"
        script = (f"for f in {PIDFILE_DIR_IN_CONTAINER}/*.pid; do [ -e \"$f\" ] || continue; "
                  f"p=$(cat \"$f\"); kill -INT -- -\"$p\" 2>/dev/null && echo \"stopped $(basename $f .pid)\"; done")
        res = subprocess.run(["docker", "exec", info["id"], "bash", "-c", script],
                             capture_output=True, text=True, timeout=20)
        return res.stdout.strip() or "nothing to stop"


def shutil_which(name):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


# --------------------------------------------------------------------------- HTTP API
def make_handler(agent, token):
    class Handler(BaseHTTPRequestHandler):
        server_version = "billee-agent"

        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _auth(self):
            if not secrets.compare_digest(self.headers.get("X-Billee-Token", ""), token):
                self._send(401, {"error": "bad token"})
                return False
            return True

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            if url.path == "/api/health":
                return self._send(200, {"ok": True, "version": VERSION, "api": API})
            if not self._auth():
                return
            try:
                if url.path == "/api/logs":
                    return self._send(200, agent.hub.since(
                        int(q.get("since", 0)), q.get("proc"), min(float(q.get("wait", 0)), 25.0)))
                if url.path == "/api/procs":
                    return self._send(200, {"procs": agent.proc_list()})
                return self._send(404, {"error": "not found"})
            except Exception as e:
                return self._send(500, {"error": str(e)})

        def do_POST(self):
            if not self._auth():
                return
            path = urlparse(self.path).path
            try:
                body = self._body()
                if path == "/api/status":
                    return self._send(200, agent.status(body.get("containers", []),
                                                        body.get("services", [])))
                if path == "/api/start":
                    return self._send(200, agent.start(body))
                if path == "/api/stop":
                    return self._send(200, {"stopped": agent.stop(body.get("id"), body.get("tag"))})
                if path == "/api/stop_all":
                    return self._send(200, {"stopped": agent.stop_all()})
                if path == "/api/container/start":
                    return self._send(200, agent.container(body["container"]).start())
                if path == "/api/container/stop_ros":
                    return self._send(200, {"result": agent.container(body["container"]).stop_ros()})
                if path == "/api/service":
                    return self._send(200, agent.service(body.get("name"), body.get("action")))
                return self._send(404, {"error": "not found"})
            except RuntimeError as e:
                return self._send(409, {"error": str(e)})
            except (ValueError, KeyError, TypeError) as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    return Handler


def read_token():
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, "token")
    try:
        with open(path) as f:
            tok = f.read().strip()
        if tok:
            return tok
    except OSError:
        pass
    tok = secrets.token_hex(16)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok)
    return tok


def serve(port, bind="127.0.0.1"):
    token = read_token()
    agent = Agent()
    server = ThreadingHTTPServer((bind, port), make_handler(agent, token))
    server.daemon_threads = True
    with open(os.path.join(STATE_DIR, "agent.pid"), "w") as f:
        f.write(str(os.getpid()))

    def shutdown(*_):
        agent.stop_all()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    print(f"billee agent {VERSION} on {bind}:{port}", flush=True)
    server.serve_forever()


def _get_health(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5) as s:
            s.sendall(b"GET /api/health HTTP/1.0\r\nHost: localhost\r\n\r\n")
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
        return json.loads(data.split(b"\r\n\r\n", 1)[1])
    except (OSError, ValueError, IndexError):
        return None


def ensure(port, bind):
    """Leave a running agent of this version alone; replace an older one when it is idle."""
    token = read_token()
    health = _get_health(port)
    if health and health.get("version") == VERSION:
        print(json.dumps({"port": port, "token": token, "version": VERSION, "started": False}))
        return 0
    if health:
        try:
            with open(os.path.join(STATE_DIR, "agent.pid")) as f:
                old = int(f.read().strip())
            os.kill(old, signal.SIGTERM)
            for _ in range(50):
                time.sleep(0.1)
                if _get_health(port) is None:
                    break
        except (OSError, ValueError):
            pass
    log_path = os.path.join(STATE_DIR, "agent.log")
    with open(log_path, "ab") as log:
        subprocess.Popen([sys.executable, os.path.realpath(__file__), "--port", str(port),
                          "--bind", bind], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True, cwd=os.path.expanduser("~"))
    for _ in range(50):
        time.sleep(0.1)
        if _get_health(port):
            print(json.dumps({"port": port, "token": token, "version": VERSION, "started": True}))
            return 0
    print(json.dumps({"error": f"agent did not start; see {log_path}"}))
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--ensure", action="store_true", help="start in the background if needed")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args()
    if args.version:
        print(VERSION)
        return 0
    if args.ensure:
        return ensure(args.port, args.bind)
    serve(args.port, args.bind)
    return 0


if __name__ == "__main__":
    sys.exit(main())
