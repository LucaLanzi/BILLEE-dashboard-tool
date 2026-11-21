#!/usr/bin/env bash
# setup.sh — get the ground station ready to run the BILLEE dashboard (`make setup-dash`).
#
# Runs on the ground station HOST (not in a container): the x86 Linux ground station, or a Mac.
# On a Mac nothing runs locally but the dashboard itself: no Docker, no ground container; the
# gamepad is read by the browser and sent to the rover. Idempotent: run it again any time.
#
#   1. host tools: python3, ssh, curl, xdg-utils (Linux: apt, asks for sudo) + checks Docker
#   2. config/local.json: the rover's address, SSH user and repo path (asks; Enter keeps the default)
#   3. an SSH key, installed on the rover (asks for the rover password once)
#   4. checks the rover: python3, docker, the repo, the CAN tools
#   5. optional: a sudo rule on the rover so the dashboard can run can-up and restart the GDS
#      services without a password prompt
#   6. optional: the devcontainer CLI (only needed to CREATE a container that was never built;
#      existing containers are started with plain docker)
#
# Options (environment):
#   ROVER_HOST=10.110.0.230 ROVER_USER=billee ROVER_REPO=~/billee-software-2027
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

# ---------------------------------------------------------------------------- 1. host tools
b "1/6 Ground station tools"
os="$(uname -s)"
arch="$(uname -m)"
if [[ "$os" == Darwin ]]; then
  ok "macOS ($arch): dashboard only; the rover runs everything, the gamepad goes through the browser"
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
ok "ssh $(ssh -V 2>&1 | cut -d, -f1)"
if [[ "$os" == Darwin ]]; then
  ok "no Docker needed on a Mac (ground.mode auto -> none)"
elif ! command -v docker >/dev/null; then
  warn "docker is missing: install Docker + the NVIDIA toolkit first (billee-software-2027 README §1)"
elif ! docker info >/dev/null 2>&1; then
  warn "docker needs sudo for $USER. Fix: sudo usermod -aG docker $USER, then log out and in"
else
  ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null)"
fi
mkdir -p "$state"

# ---------------------------------------------------------------------------- 2. config
b "2/6 Rover address"
read_cfg() { python3 -c "import json,sys; print(json.load(open(sys.argv[1]))$1)" "$local_cfg" 2>/dev/null || true; }
cur_host="$(read_cfg "['rover']['host']")"; cur_user="$(read_cfg "['rover']['user']")"; cur_repo="$(read_cfg "['rover']['repo']")"
host="${ROVER_HOST:-$(ask "Rover (Jetson) address" "${cur_host:-10.110.0.230}")}"
user="${ROVER_USER:-$(ask "SSH user on the rover" "${cur_user:-$USER}")}"
repo="${ROVER_REPO:-$(ask "billee-software-2027 path on the rover" "${cur_repo:-~/billee-software-2027}")}"
python3 - "$local_cfg" "$host" "$user" "$repo" <<'EOF'
import json, os, sys
path, host, user, repo = sys.argv[1:]
try:
    cfg = json.load(open(path))
except (OSError, ValueError):
    cfg = {}
cfg.setdefault("rover", {}).update(host=host, user=user, repo=repo)
links = cfg.setdefault("links", {})
for key, port, scheme in (("rcm_gds", 5000, "http"), ("scm_gds", 5001, "http"), ("foxglove_ws", 8765, "ws")):
    links.setdefault(key, f"{scheme}://{host}:{port}")
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
EOF
ok "saved $local_cfg (edit it for links, ports, services)"
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
# shellcheck disable=SC2016
ssh -o BatchMode=yes "$target" "REPO=$repo bash -s" <<'EOF' || true
r="${REPO/#\~/$HOME}"
p() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
w() { printf '  \033[33m!\033[0m %s\n' "$*"; }
command -v python3 >/dev/null && p "python3 on the rover" || w "no python3 on the rover host: sudo apt install python3"
if docker info >/dev/null 2>&1; then p "docker usable by $USER"; else w "docker needs sudo for $USER on the rover: sudo usermod -aG docker $USER"; fi
[ -d "$r/ros2_ws" ] && p "repo at $r" || w "no billee-software-2027 at $r (clone it there or set ROVER_REPO)"
[ -x "$r/tooling/can-up" ] && p "tooling/can-up" || w "no $r/tooling/can-up"
if docker ps -a --filter "label=devcontainer.local_folder=$(realpath "$r" 2>/dev/null || echo "$r")" --format '{{.Names}} ({{.State}})' 2>/dev/null | grep -q .; then
  p "l4t dev container: $(docker ps -a --filter "label=devcontainer.local_folder=$(realpath "$r")" --format '{{.Names}} ({{.State}})' | head -1)"
else
  w "no l4t dev container yet: build it once (VS Code: Reopen in Container -> rover-roshumble_l4t-aarch64)"
fi
for s in billee-rcm-lan-gds billee-scm-lan-gds; do
  st=$(systemctl is-active "$s" 2>/dev/null); [ "$st" = active ] && p "$s active" || w "$s: ${st:-unknown}"
done
if sudo -n true 2>/dev/null; then p "passwordless sudo"; else
  sudo -n -l /usr/sbin/ip link >/dev/null 2>&1 && p "sudo rule for ip (can-up)" || w "can-up needs sudo without a password (step 5)"
fi
EOF

# ---------------------------------------------------------------------------- 5. sudo rule
b "5/6 Rover sudo rule (can-up, GDS restarts)"
if ssh -o BatchMode=yes "$target" 'sudo -n -l /usr/sbin/ip link >/dev/null 2>&1 || sudo -n true 2>/dev/null'; then
  ok "already allowed"
elif yesno "Install /etc/sudoers.d/billee-dash on the rover (ip, modprobe, systemctl for the GDS units)?" y; then
  rule="$user ALL=(root) NOPASSWD: /usr/sbin/ip, /sbin/ip, /usr/sbin/modprobe vcan, /sbin/modprobe vcan, /usr/bin/systemctl restart billee-rcm-lan-gds, /usr/bin/systemctl restart billee-scm-lan-gds, /usr/bin/systemctl start billee-rcm-lan-gds, /usr/bin/systemctl start billee-scm-lan-gds, /usr/bin/systemctl stop billee-rcm-lan-gds, /usr/bin/systemctl stop billee-scm-lan-gds"
  echo "  enter $user's sudo password on the rover when asked"
  # visudo -c checks the file before it goes live, so a typo can't lock sudo.
  if ssh -t "$target" "echo '$rule' > /tmp/billee-dash.sudoers && sudo visudo -cf /tmp/billee-dash.sudoers && sudo install -m 0440 -o root -g root /tmp/billee-dash.sudoers /etc/sudoers.d/billee-dash; rm -f /tmp/billee-dash.sudoers"; then
    ok "sudo rule installed"
  else
    warn "sudo rule not installed: can-up from the dashboard will fail with 'a password is required'"
  fi
else
  warn "skipped: can-up and GDS restarts from the dashboard need it"
fi
fi  # SKIP_ROVER

# ---------------------------------------------------------------------------- 6. devcontainer CLI
b "6/6 devcontainer CLI (optional)"
cli="$state/tools/node_modules/.bin/devcontainer"
if [[ "$os" == Darwin ]]; then
  ok "not needed on a Mac"
elif command -v devcontainer >/dev/null || [[ -x "$cli" ]]; then
  ok "devcontainer CLI present"
elif command -v npm >/dev/null && [[ "$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)" -ge 18 ]]; then
  if yesno "Install the devcontainer CLI (npm, into $state/tools) to create containers that were never built?" n; then
    npm install --silent --prefix "$state/tools" @devcontainers/cli && ok "installed $cli"
  else
    ok "skipped (existing containers start with docker; build new ones once in VS Code)"
  fi
else
  ok "skipped: no Node 18+ (existing containers start with docker; build new ones once in VS Code)"
fi

echo
b "Done. Start the dashboard with:  make start"
