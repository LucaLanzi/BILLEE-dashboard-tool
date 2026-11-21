"""python3 -m billee_dash [options]   (or ./billee-dash)"""

import argparse
import json
import sys

from . import commander


def main():
    ap = argparse.ArgumentParser(prog="billee-dash", description=__doc__)
    ap.add_argument("--config", help="extra JSON config merged over config/local.json")
    ap.add_argument("--port", type=int, help="dashboard port (default 7780)")
    ap.add_argument("--bind", help="address to listen on (default 127.0.0.1)")
    ap.add_argument("--no-browser", action="store_true", help="don't open any browser tab")
    ap.add_argument("--no-autostart", action="store_true",
                    help="don't start the dev containers at startup")
    ap.add_argument("--rover-local", action="store_true",
                    help="testing: run the rover agent in this process instead of over SSH")
    ap.add_argument("--print-config", action="store_true", help="print the merged config and exit")
    args = ap.parse_args()

    cfg = commander.load_config(args.config)
    if args.port:
        cfg["dashboard"]["port"] = args.port
    if args.bind:
        cfg["dashboard"]["bind"] = args.bind
    if args.rover_local:
        cfg["rover"]["host"] = "local"
    if args.print_config:
        print(json.dumps(cfg, indent=2))
        return 0
    commander.serve(cfg, open_browser=not args.no_browser, autostart=not args.no_autostart)
    return 0


if __name__ == "__main__":
    sys.exit(main())
