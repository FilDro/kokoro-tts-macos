#!/usr/bin/env python3
"""Kokoro TTS menu bar app — status, controls, global hotkeys."""

import json
import logging
import os
import socket
import subprocess
import threading

import rumps

# Global hotkey support via pyobjc (bundled with rumps)
from AppKit import NSEvent, NSKeyDownMask
from Cocoa import NSControlKeyMask, NSCommandKeyMask

SOCKET_PATH = "/tmp/kokoro-tts.sock"
PLIST_LABEL = "com.filip.kokoro-tts"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "menubar.log")

ICON_IDLE = "🔇"
ICON_SPEAKING = "🔊"
ICON_OFFLINE = "⚠️"

log = logging.getLogger("kokoro-menubar")


def setup_logging():
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=500_000, backupCount=1
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


import logging.handlers  # noqa: E402


def send_command(cmd_dict, timeout=2.0):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(SOCKET_PATH)
        sock.sendall(json.dumps(cmd_dict).encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)
        resp = sock.recv(4096)
        return json.loads(resp.decode("utf-8")) if resp else None
    except Exception:
        return None
    finally:
        sock.close()


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {"voice": "af_heart", "speed": 1.0}


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


class KokoroMenuBar(rumps.App):
    def __init__(self):
        super().__init__("Kokoro TTS", title=ICON_IDLE)
        self.cfg = load_config()

        self.status_item = rumps.MenuItem("Status: checking...")
        self.pause_item = rumps.MenuItem("Pause  (⌃⌘P)", callback=self.on_pause)
        self.stop_item = rumps.MenuItem("Stop Reading  (⌃⌘X)", callback=self.on_stop)
        self.read_clipboard_item = rumps.MenuItem(
            "Read Clipboard  (⌃⌘R)", callback=self.on_read_clipboard
        )

        # Voice submenu
        voices = [
            ("af_heart", "Heart (F, US)"),
            ("af_bella", "Bella (F, US)"),
            ("af_nicole", "Nicole (F, US)"),
            ("af_sarah", "Sarah (F, US)"),
            ("am_adam", "Adam (M, US)"),
            ("am_michael", "Michael (M, US)"),
            ("bf_emma", "Emma (F, UK)"),
            ("bm_george", "George (M, UK)"),
        ]
        self.voice_menu = rumps.MenuItem("Voice")
        self.voice_items = {}
        for voice_id, voice_label in voices:
            item = rumps.MenuItem(
                f"{voice_label} [{voice_id}]",
                callback=self.make_voice_callback(voice_id),
            )
            if voice_id == self.cfg.get("voice"):
                item.state = 1
            self.voice_items[voice_id] = item
            self.voice_menu.add(item)

        # Speed submenu
        speeds = [0.8, 0.9, 1.0, 1.1, 1.2, 1.5]
        self.speed_menu = rumps.MenuItem("Speed")
        self.speed_items = {}
        for spd in speeds:
            label = f"{spd}x"
            item = rumps.MenuItem(label, callback=self.make_speed_callback(spd))
            if abs(spd - self.cfg.get("speed", 1.0)) < 0.01:
                item.state = 1
            self.speed_items[spd] = item
            self.speed_menu.add(item)

        self.restart_item = rumps.MenuItem("Restart Daemon", callback=self.on_restart)

        self.menu = [
            self.status_item,
            None,
            self.pause_item,
            self.stop_item,
            self.read_clipboard_item,
            None,
            self.voice_menu,
            self.speed_menu,
            None,
            self.restart_item,
        ]

        # Register global hotkeys
        self._register_hotkeys()

        # Poll status every 2 seconds
        self._poll_timer = rumps.Timer(self.poll_status, 2)
        self._poll_timer.start()

        log.info("Menu bar app started")

    def _register_hotkeys(self):
        """Register global keyboard shortcuts via NSEvent monitor."""
        mask = NSKeyDownMask

        def handler(event):
            flags = event.modifierFlags()
            has_ctrl = flags & NSControlKeyMask
            has_cmd = flags & NSCommandKeyMask
            keycode = event.keyCode()

            if has_ctrl and has_cmd:
                # Ctrl+Cmd+X (keycode 7) → Stop
                if keycode == 7:
                    log.info("Global hotkey: Ctrl+Cmd+X → Stop")
                    threading.Thread(target=self._do_stop, daemon=True).start()
                # Ctrl+Cmd+P (keycode 35) → Pause/Resume
                elif keycode == 35:
                    log.info("Global hotkey: Ctrl+Cmd+P → Pause/Resume")
                    threading.Thread(target=self._do_pause, daemon=True).start()
                # Ctrl+Cmd+S (keycode 1) → Read Selection (copy + speak)
                elif keycode == 1:
                    log.info("Global hotkey: Ctrl+Cmd+S → Read Selection")
                    threading.Thread(target=self._do_read_selection, daemon=True).start()
                # Ctrl+Cmd+R (keycode 15) → Read Clipboard
                elif keycode == 15:
                    log.info("Global hotkey: Ctrl+Cmd+R → Read Clipboard")
                    threading.Thread(target=self._do_read_clipboard, daemon=True).start()

        NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, handler)
        log.info("Global hotkeys registered: Ctrl+Cmd+S (read), Ctrl+Cmd+P (pause), Ctrl+Cmd+X (stop), Ctrl+Cmd+R (clipboard)")

    def _do_stop(self):
        send_command({"cmd": "stop"})

    def _do_pause(self):
        send_command({"cmd": "pause"})

    def _do_read_selection(self):
        """Copy current selection (simulate Cmd+C), then speak it."""
        import time as _time
        # Save current clipboard
        old = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
        # Simulate Cmd+C
        subprocess.run([
            "osascript", "-e",
            'tell application "System Events" to keystroke "c" using command down'
        ], capture_output=True)
        _time.sleep(0.15)  # wait for clipboard to update
        result = subprocess.run(["pbpaste"], capture_output=True, text=True)
        text = result.stdout.strip()
        # Restore clipboard if we got text
        if text and text != old.strip():
            send_command({"cmd": "speak", "text": text})
        elif old.strip():
            # No new selection — fall back to whatever was on clipboard
            send_command({"cmd": "speak", "text": old.strip()})

    def _do_read_clipboard(self):
        try:
            result = subprocess.run(["pbpaste"], capture_output=True, text=True)
            text = result.stdout.strip()
            if text:
                send_command({"cmd": "speak", "text": text})
        except Exception as e:
            log.error("Read clipboard error: %s", e)

    def poll_status(self, _=None):
        resp = send_command({"cmd": "status"})
        if resp and resp.get("status") == "ok":
            speaking = resp.get("speaking", False)
            paused = resp.get("paused", False)
            voice = resp.get("voice", "?")
            if paused:
                self.title = "⏸"
                state = "Paused"
            elif speaking:
                self.title = ICON_SPEAKING
                state = "Speaking"
            else:
                self.title = ICON_IDLE
                state = "Idle"
            self.status_item.title = f"Status: {state} | Voice: {voice}"
            # Update pause item label
            self.pause_item.title = "Resume  (⌃⌘P)" if paused else "Pause  (⌃⌘P)"
            active = speaking or paused
            self.pause_item.set_callback(self.on_pause if active else None)
            self.stop_item.set_callback(self.on_stop if active else None)
        else:
            self.title = ICON_OFFLINE
            self.status_item.title = "Status: Daemon offline"
            self.pause_item.set_callback(None)
            self.stop_item.set_callback(None)

    def on_pause(self, _):
        threading.Thread(target=self._do_pause, daemon=True).start()

    def on_stop(self, _):
        threading.Thread(target=self._do_stop, daemon=True).start()
        self.poll_status()

    def on_read_clipboard(self, _):
        try:
            result = subprocess.run(["pbpaste"], capture_output=True, text=True)
            text = result.stdout.strip()
            if text:
                threading.Thread(
                    target=send_command,
                    args=({"cmd": "speak", "text": text},),
                    daemon=True,
                ).start()
                rumps.notification(
                    "Kokoro TTS", "", f"Reading {len(text)} chars...", sound=False
                )
            else:
                rumps.notification("Kokoro TTS", "", "Clipboard is empty", sound=False)
        except Exception as e:
            rumps.notification("Kokoro TTS", "", f"Error: {e}", sound=False)

    def make_voice_callback(self, voice_id):
        def callback(_):
            self.cfg["voice"] = voice_id
            save_config(self.cfg)
            for vid, item in self.voice_items.items():
                item.state = 1 if vid == voice_id else 0
            self.on_restart(None)
        return callback

    def make_speed_callback(self, speed):
        def callback(_):
            self.cfg["speed"] = speed
            save_config(self.cfg)
            for spd, item in self.speed_items.items():
                item.state = 1 if abs(spd - speed) < 0.01 else 0
            self.on_restart(None)
        return callback

    def on_restart(self, _):
        uid = os.getuid()
        subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/{PLIST_LABEL}"],
            capture_output=True,
        )
        rumps.notification("Kokoro TTS", "", "Daemon restarting...", sound=False)


if __name__ == "__main__":
    setup_logging()
    try:
        KokoroMenuBar().run()
    except Exception as e:
        log.error("Menu bar app crashed: %s", e, exc_info=True)
        raise
