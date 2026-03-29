#!/usr/bin/env python3
"""Lightweight client for the Kokoro TTS daemon. No heavy deps."""

import json
import os
import socket
import subprocess
import sys
import time

SOCKET_PATH = "/tmp/kokoro-tts.sock"
PLIST_LABEL = "com.filip.kokoro-tts"


def send_command(cmd_dict, timeout=3.0):
    """Send a JSON command to the daemon, return the response dict."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(SOCKET_PATH)
        sock.sendall(json.dumps(cmd_dict).encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)
        resp = sock.recv(4096)
        return json.loads(resp.decode("utf-8")) if resp else {"status": "no response"}
    finally:
        sock.close()


def try_start_daemon():
    """Try to start the daemon via launchctl."""
    uid = os.getuid()
    try:
        subprocess.run(
            ["launchctl", "kickstart", f"gui/{uid}/{PLIST_LABEL}"],
            capture_output=True, timeout=5
        )
    except Exception:
        pass


def fallback_say(text):
    """Fall back to macOS say command if daemon is unavailable."""
    try:
        subprocess.Popen(["say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def main():
    # Parse args
    if "--stop" in sys.argv:
        cmd = {"cmd": "stop"}
        text = None
    elif "--status" in sys.argv:
        cmd = {"cmd": "status"}
        text = None
    else:
        # Read text from stdin or remaining args
        if not sys.stdin.isatty():
            text = sys.stdin.read().strip()
        elif len(sys.argv) > 1:
            text = " ".join(a for a in sys.argv[1:] if not a.startswith("--"))
        else:
            text = None

        if text:
            cmd = {"cmd": "speak", "text": text}
        else:
            # No text provided — toggle: stop if playing
            cmd = {"cmd": "stop"}

    # Try to send command
    try:
        resp = send_command(cmd)
        if "--status" in sys.argv:
            print(json.dumps(resp, indent=2))
        sys.exit(0)
    except (FileNotFoundError, ConnectionRefusedError):
        pass

    # Daemon not running — try to start it
    try_start_daemon()
    time.sleep(2)

    try:
        resp = send_command(cmd)
        if "--status" in sys.argv:
            print(json.dumps(resp, indent=2))
        sys.exit(0)
    except (FileNotFoundError, ConnectionRefusedError):
        # Last resort: fall back to macOS say
        if text:
            fallback_say(text)
            sys.exit(0)
        else:
            print("Error: daemon not running and could not start it", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
