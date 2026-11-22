#!/usr/bin/env bash
# setup.sh — get the ground station ready to run the BILLEE dashboard (`make setup-dash`).
#
# Runs on the ground station HOST (not in a container). Idempotent: run it again any time.
# (On a computer without the ground-station container, e.g. a Mac, the dashboard runs every mode
# on the rover only; see README "Which computer".)
#
#   1. host tools: python3, ssh, curl, xdg-utils (Linux: apt, asks for sudo) + checks Docker
#   2. config/local.json: the rover's address, SSH user and repo paths (asks; Enter keeps the
#      default). Every page the dashboard opens (RCM/Science GDS, Foxglove, astronaut) uses
#      this address, e.g. 192.168.4.97 -> http://192.168.4.97:5000, ws://192.168.4.97:8765
#   3. an SSH key, installed on the rover (asks for the rover password once)
#   4. checks the rover: python3, docker, the repo, the CAN tools
#   5. optional: a sudo rule on the rover so the dashboard can run can-up, the astronaut hotspot
#      and server, and restart the GDS services without a password prompt
#   6. optional: the devcontainer CLI, here and on the rover (for Rebuild in the dashboard, and to
#      create a container that was never built; existing containers start with plain docker)
#
# Options (environment):
#   ROVER_HOST=10.110.0.230 ROVER_USER=billee ROVER_REPO=~/billee-software-2027   (defaults)
#   ROVER_HW_REPO=~/billee-hardware-2027
#   YES=1            accept every default and every optional step, no questions
#   SKIP_APT=1       don't install host packages
#   SKIP_ROVER=1     don't touch the rover (no key copy, no checks, no sudo rule)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
local_cfg="$here/config/local.json"
state="${BILLEE_DASH_STATE:-$HOME/.billee-dash}"

b() { printf '\033[1m%s\033[0m\n' "$*"; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die() { printf '  \033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
ask() {  # ask <question> <default> -> answer
  local q="$1" d="$2" a
  if [[ -n "${YES:-}" || ! -t 0 ]]; then echo "$d"; return; fi
  read -r -p "  $q [$d]: " a </dev/tty || true
  echo "${a:-$d}"
}
yesno() {  # yesno <question> <default y|n>
  local a
  if [[ -n "${YES:-}" ]]; then [[ "$2" == y ]]; return; fi
  if [[ ! -t 0 ]]; then [[ "$2" == y ]]; return; fi
  read -r -p "  $1 [$([[ $2 == y ]] && echo Y/n || echo y/N)]: " a </dev/tty || true
  a="${a:-$2}"; [[ "$a" =~ ^[Yy] ]]
}

if [[ $EUID -eq 0 ]]; then die "run this as your normal user (it asks for sudo when it needs it)"; fi
# The dashboard runs on the ground station's host: it starts the dev containers itself (with
# docker) and reaches the rover over SSH. Inside a dev container it can do neither.
if [[ -f /.dockerenv || -f /run/.containerenv ]] && [[ -z "${ALLOW_CONTAINER:-}" ]]; then
  echo "  This is a dev container (e.g. ros_user@rover:/workspaces/...)." >&2
  echo "  Run make setup-dash on the ground station itself, outside any container:" >&2
  echo "  a terminal on the laptop/desktop you operate the rover from, in its billee-software-2027 clone." >&2
  echo "  (ALLOW_CONTAINER=1 make setup-dash to continue anyway.)" >&2
  exit 1
fi
me="$(id -un 2>/dev/null || echo "uid $(id -u)")"   # not $USER: a `docker exec` shell doesn't set it

# ---------------------------------------------------------------------------- 1. host tools
b "1/6 Ground station tools"
os="$(uname -s)"
arch="$(uname -m)"
if [[ "$os" == Darwin ]]; then
  ok "macOS ($arch): no ground-station container here; modes run on the rover only"
  command -v python3 >/dev/null || die "python3 is missing: run  xcode-select --install  then make setup-dash again"
elif [[ "$arch" == x86_64 ]]; then
  ok "x86_64 Linux host"
else
  warn "this is $arch, not the x86 ground station; continuing"
fi
need=()
command -v python3 >/dev/null || need+=(python3)
command -v ssh >/dev/null || need+=(openssh-client)
command -v curl >/dev/null || need+=(curl)
[[ "$os" == Darwin ]] || command -v xdg-open >/dev/null || need+=(xdg-utils)
if [[ ${#need[@]} -gt 0 ]]; then
  if [[ -n "${SKIP_APT:-}" ]]; then
    warn "missing ${need[*]} (SKIP_APT=1: not installing)"
  elif command -v apt-get >/dev/null; then
    echo "  installing ${need[*]} (sudo)"
    sudo apt-get update -qq && sudo apt-get install -y -qq "${need[@]}"
  else
    die "install ${need[*]} with your package manager, then run make setup-dash again"
  fi
fi
python3 - <<'EOF' || die "python3 3.8 or newer is needed"
import sys; sys.exit(sys.version_info < (3, 8))
EOF
ok "python3 $(python3 -c 'import platform; print(platform.python_version())') (standard library only, nothing to pip install)"
command -v ssh >/dev/null || die "ssh is missing: install openssh-client, then run make setup-dash again"
ok "ssh $(ssh -V 2>&1 | cut -d, -f1)"
if [[ "$os" == Darwin ]]; then
  ok "no Docker needed on a Mac (ground.mode auto -> none)"
elif ! command -v docker >/dev/null; then
  warn "docker is missing: install Docker + the NVIDIA toolkit first (billee-software-2027 README §1)"
elif ! docker info >/dev/null 2>&1; then
  warn "docker needs sudo for $me. Fix: sudo usermod -aG docker $me, then log out and in"
else
  ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null)"
fi
mkdir -p "$state"

# ---------------------------------------------------------------------------- 2. config
b "2/6 Rover address"
read_cfg() { python3 -c "import json,sys; print(json.load(open(sys.argv[1]))$1)" "$local_cfg" 2>/dev/null || true; }
cur_host="$(read_cfg "['rover']['host']")"; cur_user="$(read_cfg "['rover']['user']")"
cur_repo="$(read_cfg "['rover']['repo']")"; cur_hw="$(read_cfg "['rover']['hardware_repo']")"
host="${ROVER_HOST:-$(ask "Rover (Jetson) IP address" "${cur_host:-10.110.0.230}")}"
user="${ROVER_USER:-$(ask "SSH user on the rover" "${cur_user:-billee}")}"
repo="${ROVER_REPO:-$(ask "billee-software-2027 path on the rover" "${cur_repo:-~/billee-software-2027}")}"
hwrepo="${ROVER_HW_REPO:-$(ask "billee-hardware-2027 path on the rover (astronaut-streamer)" "${cur_hw:-~/billee-hardware-2027}")}"
python3 - "$local_cfg" "$host" "$user" "$repo" "$hwrepo" <<'PYEOF'
import json, re, sys
path, host, user, repo, hwrepo = sys.argv[1:]
try:
    cfg = json.load(open(path))
except (OSError, ValueError):
    cfg = {}
cfg.setdefault("rover", {}).update(host=host, user=user, repo=repo, hardware_repo=hwrepo)
# The links follow rover.host (config/dashboard.json: http://{rover}:5000, ...). Drop links an
# older setup saved with a fixed address on the standard ports, so they follow it too.
links = cfg.get("links", {})
for key in list(links):
    if re.fullmatch(r"(https?|wss?)://[^/:]+:(5000|5001|8765|8443)/?", str(links[key])):
        del links[key]
if not links:
    cfg.pop("links", None)
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
PYEOF
ok "saved $local_cfg"
ok "the dashboard opens http://$host:5000 (RCM GDS), http://$host:5001 (Science GDS),"
ok "  ws://$host:8765 (Foxglove) and https://$host:8443 (astronaut)"
target="$user@$host"

if [[ -n "${SKIP_ROVER:-}" ]]; then
  warn "SKIP_ROVER=1: skipping the rover steps"
else
# ---------------------------------------------------------------------------- 3. ssh key
b "3/6 SSH key for $target"
if [[ ! -f "$HOME/.ssh/id_ed25519" && ! -f "$HOME/.ssh/id_rsa" ]]; then
  ssh-keygen -q -t ed25519 -N "" -f "$HOME/.ssh/id_ed25519" -C "billee-dash@$(hostname)"
  ok "made ~/.ssh/id_ed25519"
fi
ssh_ok() { ssh -o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new "$target" true 2>/dev/null; }
if ssh_ok; then
  ok "key login to $target works"
else
  echo "  copying your key to the rover: enter $user's password on the Jetson when asked"
  if command -v ssh-copy-id >/dev/null; then
    ssh-copy-id -o StrictHostKeyChecking=accept-new "$target" || warn "ssh-copy-id failed"
  else  # older macOS: no ssh-copy-id
    pub="$(ls "$HOME"/.ssh/id_ed25519.pub "$HOME"/.ssh/id_rsa.pub 2>/dev/null | head -1)"
    ssh -o StrictHostKeyChecking=accept-new "$target" \
      'mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys' \
      < "$pub" || warn "copying the key failed"
  fi
  ssh_ok && ok "key login to $target works" || die "can't log in to $target with a key; is the rover on and reachable?"
fi

# ---------------------------------------------------------------------------- 4. rover checks
b "4/6 Rover checks"
# The GDS units shown under Systems (rover.services in config/dashboard.json, local.json can
# override): checked here, and named in the sudo rule (step 5).
services="$(python3 - "$here/config" <<'PY'
import json, os, sys
d = sys.argv[1]
svc = json.load(open(os.path.join(d, "dashboard.json")))["rover"].get("services", [])
try:
    svc = json.load(open(os.path.join(d, "local.json"))).get("rover", {}).get("services", svc)
except (OSError, ValueError):
    pass
print(" ".join(s for s in svc if s.replace("-", "").replace("_", "").replace(".", "").replace("@", "").isalnum()))
PY
)"
# shellcheck disable=SC2016
ssh -o BatchMode=yes "$target" "REPO=$repo HWREPO=$hwrepo SERVICES='$services' bash -s" <<'EOF' || true
r="${REPO/#\~/$HOME}"
p() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
w() { printf '  \033[33m!\033[0m %s\n' "$*"; }
command -v python3 >/dev/null && p "python3 on the rover" || w "no python3 on the rover host: sudo apt install python3"
u="$(id -un)"
if docker info >/dev/null 2>&1; then p "docker usable by $u"; else w "docker needs sudo for $u on the rover: sudo usermod -aG docker $u"; fi
[ -d "$r/ros2_ws" ] && p "repo at $r" || w "no billee-software-2027 at $r (clone it there or set ROVER_REPO)"
[ -x "$r/tooling/can-up" ] && p "tooling/can-up" || w "no $r/tooling/can-up"
h="${HWREPO/#\~/$HOME}"
if [ -f "$h/astronaut-streamer/jetson/hotspot.sh" ]; then p "astronaut-streamer in $h"
else w "no astronaut-streamer in $h (Astronaut mode): cd $h && make rover"; fi
if [ -f /etc/systemd/system/astronaut-web.service ]; then p "astronaut services installed"
else w "astronaut services not installed (Astronaut mode): cd $h && make astronaut jetson"; fi
if docker ps -a --filter "label=devcontainer.local_folder=$(realpath "$r" 2>/dev/null || echo "$r")" --format '{{.Names}} ({{.State}})' 2>/dev/null | grep -q .; then
  p "l4t dev container: $(docker ps -a --filter "label=devcontainer.local_folder=$(realpath "$r")" --format '{{.Names}} ({{.State}})' | head -1)"
else
  w "no l4t dev container yet: build it once (VS Code: Reopen in Container -> rover-roshumble_l4t-aarch64)"
fi
for s in $SERVICES; do
  if [ "$(systemctl show -p LoadState --value "$s" 2>/dev/null)" = not-found ]; then
    w "$s: no such systemd unit on the rover (fix rover.services in config/local.json)"; continue
  fi
  st=$(systemctl is-active "$s" 2>/dev/null); [ "$st" = active ] && p "$s active" || w "$s: ${st:-unknown}"
done
if sudo -n true 2>/dev/null; then p "passwordless sudo"; else
  sudo -n -l /usr/sbin/ip link >/dev/null 2>&1 && p "sudo rule for ip (can-up)" || w "can-up needs sudo without a password (step 5)"
fi
EOF

# ---------------------------------------------------------------------------- 5. sudo rule
b "5/6 Rover sudo rule (can-up, astronaut hotspot + server, GDS restarts)"
# The hotspot script is named by its real path: sudo matches the command line exactly.
hotspot="$(ssh -o BatchMode=yes "$target" "realpath -m $hwrepo/astronaut-streamer/jetson/hotspot.sh" 2>/dev/null)"
sudo_ok() {
  ssh -o BatchMode=yes "$target" "sudo -n true 2>/dev/null || { sudo -n -l /usr/sbin/ip link &&
    sudo -n -l /usr/bin/bash $hotspot up &&
    sudo -n -l /usr/bin/systemctl restart astronaut-mediamtx astronaut-web$(
    for sv in $services; do printf ' && sudo -n -l /usr/bin/systemctl restart %s' "$sv"; done); } >/dev/null 2>&1"
}
if sudo_ok; then
  ok "already allowed"
elif yesno "Install /etc/sudoers.d/billee-dash on the rover (ip, modprobe vcan, the astronaut hotspot script, systemctl for the GDS and astronaut units)?" y; then
  sc=/usr/bin/systemctl
  rule="$user ALL=(root) NOPASSWD: /usr/sbin/ip, /sbin/ip, /usr/sbin/modprobe vcan, /sbin/modprobe vcan"
  for a in restart start stop; do for sv in $services; do rule+=", $sc $a $sv"; done; done
  rule+=", $sc restart astronaut-mediamtx astronaut-web, $sc stop astronaut-web astronaut-mediamtx"
  for a in up start stop; do rule+=", /usr/bin/bash $hotspot $a, /bin/bash $hotspot $a"; done
  echo "  enter $user's sudo password on the rover when asked"
  # visudo -c checks the file before it goes live, so a typo can't lock sudo.
  if ssh -t "$target" "echo '$rule' > /tmp/billee-dash.sudoers && sudo visudo -cf /tmp/billee-dash.sudoers && sudo install -m 0440 -o root -g root /tmp/billee-dash.sudoers /etc/sudoers.d/billee-dash; rm -f /tmp/billee-dash.sudoers"; then
    ok "sudo rule installed"
  else
    warn "sudo rule not installed: can-up and the astronaut hotspot will fail with 'a password is required'"
  fi
else
  warn "skipped: can-up, the astronaut hotspot/server and GDS restarts from the dashboard need it"
fi
fi  # SKIP_ROVER

# ---------------------------------------------------------------------------- 6. devcontainer CLI
# The dashboard's Rebuild (Systems -> a container's Rebuild, like VS Code's Rebuild Container) runs
# `devcontainer up --remove-existing-container`, and creating a container that was never built
# runs `devcontainer up`; both on the machine that has the container. Starting an existing one
# needs only docker. The CLI is plain JavaScript (no native modules), so the rover gets a copy of
# this machine's, plus a Node binary if it has none: the Jetson needs no internet for it.
b "6/6 devcontainer CLI (to rebuild containers from the dashboard)"
tools="$state/tools"
cli_js="node_modules/@devcontainers/cli/devcontainer.js"
node_major() { "${1:-node}" -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0; }
ground_cli() {  # the CLI in $tools on this machine (installs it with npm if needed)
  [[ -f "$tools/$cli_js" ]] && return 0
  command -v npm >/dev/null && [[ "$(node_major)" -ge 20 ]] || return 1
  npm install --silent --prefix "$tools" @devcontainers/cli >/dev/null && [[ -f "$tools/$cli_js" ]]
}
if [[ "$os" == Darwin ]]; then
  ok "ground: not needed on a Mac"
elif command -v devcontainer >/dev/null || [[ -f "$tools/$cli_js" ]]; then
  ok "ground: devcontainer CLI present"
elif ! command -v npm >/dev/null || [[ "$(node_major)" -lt 20 ]]; then
  warn "ground: skipped, no Node 20+ (Rebuild needs it; existing containers still start with docker)"
elif yesno "Install the devcontainer CLI on the ground station (npm, into $tools)?" y; then
  ground_cli && ok "ground: installed $tools/$cli_js" || warn "ground: npm install @devcontainers/cli failed"
else
  ok "ground: skipped"
fi

if [[ -z "${SKIP_ROVER:-}" ]]; then
  # shellcheck disable=SC2016
  rover_has="$(ssh -o BatchMode=yes "$target" 'n=~/.billee-dash/tools/node/bin/node; [ -x "$n" ] || n=$(command -v node || true)
    m=$([ -n "$n" ] && "$n" -p "process.versions.node.split(\".\")[0]" 2>/dev/null || echo 0)
    c=no; [ -f ~/.billee-dash/tools/'"$cli_js"' ] || command -v devcontainer >/dev/null && c=yes
    echo "$m $c $(uname -m)"' 2>/dev/null || echo "0 no ?")"
  read -r r_node r_cli r_arch <<<"$rover_has"
  if [[ "$r_node" -ge 20 && "$r_cli" == yes ]]; then
    ok "rover: devcontainer CLI present (Node $r_node)"
  elif ! yesno "Install the devcontainer CLI on the rover (into ~/.billee-dash/tools; for Rebuild)?" y; then
    ok "rover: skipped (the dashboard can't rebuild the rover's container without it)"
  else
    if [[ "$r_cli" != yes ]]; then
      if ground_cli; then
        tar -C "$tools" -cf - node_modules/@devcontainers | ssh -o BatchMode=yes "$target" \
          'mkdir -p ~/.billee-dash/tools && tar -C ~/.billee-dash/tools -xf -' \
          && ok "rover: copied the devcontainer CLI" || warn "rover: copying the CLI failed"
      else
        warn "rover: needs a copy of the CLI from this machine, which needs Node 20+ and npm here"
      fi
    fi
    if [[ "$r_node" -lt 20 ]]; then
      # Node 24 LTS from nodejs.org, checked against its published SHA-256, unpacked on the rover.
      case "$r_arch" in aarch64|arm64) na=arm64 ;; x86_64) na=x64 ;; *) na="" ;; esac
      base="https://nodejs.org/dist/latest-v24.x"
      tmp="$(mktemp -d)"
      if [[ -z "$na" ]]; then
        warn "rover: unknown architecture '$r_arch': install Node 20+ there yourself"
      elif sums="$(curl -fsSL "$base/SHASUMS256.txt")" &&
           line="$(grep -E " node-v[0-9.]+-linux-$na\.tar\.xz\$" <<<"$sums" | head -1)" && [[ -n "$line" ]] &&
           curl -fsSL -o "$tmp/node.tar.xz" "$base/${line##* }" &&
           [[ "$( (sha256sum "$tmp/node.tar.xz" 2>/dev/null || shasum -a 256 "$tmp/node.tar.xz") | cut -d' ' -f1)" == "${line%% *}" ]]; then
        ssh -o BatchMode=yes "$target" 'rm -rf ~/.billee-dash/tools/node && mkdir -p ~/.billee-dash/tools/node &&
          tar -C ~/.billee-dash/tools/node --strip-components=1 -xJf - && ~/.billee-dash/tools/node/bin/node --version' \
          < "$tmp/node.tar.xz" && ok "rover: installed ${line##* } into ~/.billee-dash/tools/node" \
          || warn "rover: unpacking Node failed"
      else
        warn "rover: could not download Node from $base (no internet here?)"
      fi
      rm -rf "$tmp"
    fi
  fi
fi

echo
b "Done. Start the dashboard with:  make start"
