"""Astronaut mode: report the astronaut stream's state in the dashboard's Rover output.

Runs on the Jetson host while Astronaut mode runs. It polls the astronaut-streamer dashboard
(billee-hardware-2027/astronaut-streamer, jetson/app.py: GET /api/status) and prints a line
whenever something changes: the server answering, the Pi's heartbeat (it joins the hotspot and
connects by itself after the hotspot and server are up), and the feed going LIVE or offline.

    python3 astro_watch.py [port]      (default 8080, astronaut-streamer's WEB_PORT)

Standard library only.
"""

import json
import sys
import time
import urllib.request


def summary(s):
    feed = s.get("feed") or {}
    pi = s.get("pi")
    talk = s.get("talkback") or {}
    if feed.get("live"):
        tracks = " + ".join(feed.get("tracks") or []) or "?"
        line = f"feed LIVE ({tracks}, {feed.get('bitrate_kbps', '?')} kbit/s, " \
               f"{feed.get('viewers', 0)} viewer(s))"
    else:
        line = f"feed offline ({feed.get('message') or 'no stream yet'})"
    if pi:
        wifi = pi.get("wifi") or {}
        line += f" | Pi {pi.get('state', '?')} at {pi.get('ip', '?')}, " \
                f"signal {wifi.get('signal_dbm', '?')} dBm, {pi.get('temp_c', '?')} C"
        if pi.get("last_error"):
            line += f", error: {pi['last_error']}"
    else:
        line += " | waiting for the astronaut Pi to join the hotspot"
    if talk.get("live"):
        line += " | talk-back live" + ("" if talk.get("listeners") else " (Pi not playing it)")
    return line


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    url = f"http://127.0.0.1:{port}/api/status"
    last, last_print = None, 0.0
    print(f"watching the astronaut stream ({url})", flush=True)
    while True:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                line = summary(json.load(r))
        except (OSError, ValueError) as e:
            line = f"astronaut server not answering on :{port} ({getattr(e, 'reason', e)})"
        now = time.monotonic()
        # Print changes at once, and the current state once a minute anyway.
        if line != last or now - last_print > 60:
            print(line, flush=True)
            last, last_print = line, now
        time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
