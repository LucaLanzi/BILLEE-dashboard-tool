"""Clients for the two agents, and the SSH link that reaches the rover's.

LocalClient  - the ground station's agent, in this process.
RemoteClient - the rover's agent, over HTTP through an SSH tunnel.
RoverLink    - keeps that tunnel up: uploads agent.py over SSH, starts it (`--ensure`), forwards a
               local port to it, and reconnects after a dropped link. Processes on the rover keep
               running while the link is down; their output is buffered there and fetched on
               reconnect.
"""

import json
import os
import shlex
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

from . import agent as agent_mod

AGENT_SRC = os.path.realpath(agent_mod.__file__)


class ClientError(RuntimeError):
    pass


class LocalClient:
    def __init__(self, agent):
        self.agent = agent

    def health(self):
        return {"ok": True, "version": agent_mod.VERSION}

    def status(self, containers=(), services=()):
        return self.agent.status(containers, services)

    def procs(self):
        return self.agent.proc_list()

    def start(self, spec):
        try:
            return self.agent.start(spec)
        except RuntimeError as e:
            raise ClientError(str(e))

    def stop(self, proc_id=None, tag=None):
        return self.agent.stop(proc_id, tag)

    def stop_all(self):
        return self.agent.stop_all()

    def container_start(self, spec):
        try:
            return self.agent.container(spec).start()
        except RuntimeError as e:
            raise ClientError(str(e))

    def container_rebuild(self, spec):
        try:
            return self.agent.container(spec).rebuild()
        except RuntimeError as e:
            raise ClientError(str(e))

    def container_stop_ros(self, spec):
        return self.agent.container(spec).stop_ros()

    def service(self, name, action):
        return self.agent.service(name, action)

    def logs(self, since, wait=0.0):
        return self.agent.hub.since(since, wait=wait)


class RemoteClient:
    def __init__(self, port, token):
        self.base = f"http://127.0.0.1:{port}"
        self.token = token

    def _call(self, method, path, body=None, timeout=10.0):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "X-Billee-Token": self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", str(e))
            except ValueError:
                msg = str(e)
            raise ClientError(msg)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ConnectionError(str(e))

    def health(self):
        return self._call("GET", "/api/health", timeout=3)

    def status(self, containers=(), services=()):
        return self._call("POST", "/api/status",
                          {"containers": list(containers), "services": list(services)}, 15)

    def procs(self):
        return self._call("GET", "/api/procs")["procs"]

    def start(self, spec):
        return self._call("POST", "/api/start", spec, 20)

    def stop(self, proc_id=None, tag=None):
        return self._call("POST", "/api/stop", {"id": proc_id, "tag": tag})["stopped"]

    def stop_all(self):
        return self._call("POST", "/api/stop_all", {})["stopped"]

    def container_start(self, spec):
        return self._call("POST", "/api/container/start", {"container": spec})

    def container_rebuild(self, spec):
        return self._call("POST", "/api/container/rebuild", {"container": spec})

    def container_stop_ros(self, spec):
        return self._call("POST", "/api/container/stop_ros", {"container": spec}, 30)["result"]

    def service(self, name, action):
        return self._call("POST", "/api/service", {"name": name, "action": action})

    def logs(self, since, wait=0.0):
        return self._call("GET", f"/api/logs?since={int(since)}&wait={wait}", timeout=wait + 10)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RoverLink:
    """state: off | connecting | up | down. `client` is set while up."""

    def __init__(self, rover_cfg, state_dir, log):
        self.cfg = rover_cfg
        self.state_dir = state_dir
        self.log = log
        self.state = "off"
        self.message = ""
        self.client = None
        self.tunnel = None
        self.local_agent = None
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.version = None
        self.joy_port = None  # local port that reaches the rover's gamepad relay
        self._thread = None
        os.makedirs(state_dir, exist_ok=True)

    # ---- ssh
    @property
    def local_mode(self):
        return self.cfg.get("host") in ("local", "localhost-agent")

    @property
    def target(self):
        user = self.cfg.get("user")
        return f"{user}@{self.cfg['host']}" if user else self.cfg["host"]

    def ssh_base(self, master=True):
        opts = ["ssh", "-p", str(self.cfg.get("ssh_port", 22)),
                "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
                "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3",
                "-o", "StrictHostKeyChecking=accept-new"]
        if self.cfg.get("ssh_key"):
            opts += ["-i", os.path.expanduser(self.cfg["ssh_key"])]
        if master:
            opts += ["-o", "ControlMaster=auto", "-o",
                     f"ControlPath={self.state_dir}/ssh-%C", "-o", "ControlPersist=120"]
        else:
            opts += ["-o", "ControlMaster=no", "-o", "ControlPath=none"]
        return opts

    def ssh(self, remote_cmd, stdin=None, timeout=30):
        return subprocess.run(self.ssh_base() + [self.target, remote_cmd], input=stdin,
                              capture_output=True, timeout=timeout)

    def ssh_command_text(self):
        """For the page: how to open a shell on the rover yourself."""
        port = self.cfg.get("ssh_port", 22)
        return f"ssh {'-p %s ' % port if port != 22 else ''}{self.target}"

    # ---- lifecycle
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def reconnect(self):
        self._drop("reconnect requested")
        self.wake.set()

    def _set(self, state, message=""):
        if (state, message) != (self.state, self.message):
            self.state, self.message = state, message
            self.log(f"rover link: {state}{' - ' + message if message else ''}")

    def _drop(self, why):
        with self.lock:
            self.client = None
            self.joy_port = None
            if self.tunnel and self.tunnel.poll() is None:
                self.tunnel.terminate()
            self.tunnel = None
        if self.state == "up":
            self._set("down", why)

    def _loop(self):
        backoff = 2.0
        while True:
            if self.client is not None:
                try:
                    if self.tunnel is not None and self.tunnel.poll() is not None:
                        raise ConnectionError("SSH tunnel closed")
                    self.client.health()
                    backoff = 2.0
                    self.wake.wait(3.0)
                    self.wake.clear()
                    continue
                except (ConnectionError, ClientError) as e:
                    self._drop(str(e))
            try:
                self._connect()
                backoff = 2.0
            except Exception as e:  # keep trying: the radio link may come back
                self._set("down", str(e))
                self.wake.wait(backoff)
                self.wake.clear()
                backoff = min(backoff * 1.5, 15.0)

    def _connect(self):
        self._set("connecting", self.target if not self.local_mode else "local agent")
        if self.local_mode:  # testing / a dashboard running on the rover itself
            if self.local_agent is None:
                self.local_agent = agent_mod.Agent("rover")
            self.client = LocalClient(self.local_agent)
            self.joy_port = int(self.cfg.get("joy_relay_port", 8771))
            self.version = agent_mod.VERSION
            self._set("up", "local agent (no SSH)")
            return
        if not self.cfg.get("host"):
            raise ConnectionError("no rover host configured (config/local.json)")
        port = int(self.cfg.get("agent_port", 8770))
        # 1. upload agent.py unless the rover already has this exact version
        remote_py = "~/.billee-dash/agent.py"
        res = self.ssh(f"python3 {remote_py} --version 2>/dev/null || true", timeout=20)
        if res.returncode == 255:
            raise ConnectionError(_ssh_error(res))
        if res.stdout.decode().strip() != agent_mod.VERSION:
            with open(AGENT_SRC, "rb") as f:
                src = f.read()
            res = self.ssh(f"mkdir -p ~/.billee-dash && cat > {remote_py}.tmp && "
                           f"mv {remote_py}.tmp {remote_py}", stdin=src)
            if res.returncode != 0:
                raise ConnectionError("could not upload the agent: " + _ssh_error(res))
            self.log("rover link: uploaded agent " + agent_mod.VERSION)
        # 2. start it (no-op if this version already runs)
        res = self.ssh(f"python3 {remote_py} --ensure --port {port}", timeout=30)
        try:
            info = json.loads(res.stdout.decode().strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise ConnectionError("agent did not start: " + _ssh_error(res))
        if "error" in info:
            raise ConnectionError(info["error"])
        # 3. tunnel a local port to it, and one to the gamepad relay (listening only while a
        #    mode with "Gamepad on: This browser" runs; ssh connects it on demand)
        lport, jport = free_port(), free_port()
        relay = int(self.cfg.get("joy_relay_port", 8771))
        log = self._tunnel_log()
        tunnel = subprocess.Popen(
            self.ssh_base(master=False) + ["-N", "-o", "ExitOnForwardFailure=yes",
                                           "-L", f"127.0.0.1:{lport}:127.0.0.1:{port}",
                                           "-L", f"127.0.0.1:{jport}:127.0.0.1:{relay}",
                                           self.target],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
        log.close()  # the ssh process keeps its own copy
        client = RemoteClient(lport, info["token"])
        for _ in range(60):
            if tunnel.poll() is not None:
                raise ConnectionError("SSH tunnel failed: " + (self._tunnel_log_tail() or
                                                                f"exit {tunnel.returncode}"))
            try:
                health = client.health()
                break
            except ConnectionError:
                time.sleep(0.25)
        else:
            tunnel.terminate()
            raise ConnectionError("rover agent did not answer through the tunnel")
        with self.lock:
            self.tunnel, self.client = tunnel, client
            self.joy_port = jport
            self.version = health.get("version")
        self._set("up", f"{self.target} (agent {self.version})")

    # ssh writes a line per refused forward (e.g. the gamepad relay not running yet). A file,
    # not a pipe nobody reads, so it can never fill up and stall the tunnel.
    def _tunnel_log(self):
        path = os.path.join(self.state_dir, "ssh-tunnel.log")
        if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
            os.replace(path, path + ".1")
        return open(path, "ab")

    def _tunnel_log_tail(self):
        try:
            with open(os.path.join(self.state_dir, "ssh-tunnel.log"), "rb") as f:
                lines = f.read()[-2000:].decode(errors="replace").strip().splitlines()
            return lines[-1] if lines else ""
        except OSError:
            return ""

    def close(self):
        self._drop("dashboard closing")
        if not self.local_mode:
            try:  # end the shared SSH connection; the rover agent keeps running
                subprocess.run(self.ssh_base() + ["-O", "exit", self.target],
                               capture_output=True, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass


def _ssh_error(res):
    err = (res.stderr or b"").decode(errors="replace").strip().splitlines()
    text = err[-1] if err else f"exit {res.returncode}"
    if "Permission denied" in text:
        text += " (run `make setup-dash` to install your SSH key on the rover)"
    return text


def quote(s):
    return shlex.quote(s)
