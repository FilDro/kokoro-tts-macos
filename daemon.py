#!/usr/bin/env python3
"""Kokoro TTS daemon — keeps model loaded, accepts text over Unix socket."""

import asyncio
import json
import logging
import logging.handlers
import numpy as np
import os
import queue
import signal
import sys
import threading
import time

import sounddevice as sd
from kokoro_onnx import Kokoro

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOCKET_PATH = "/tmp/kokoro-tts.sock"
LOG_PATH = os.path.join(BASE_DIR, "kokoro-tts.log")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SAMPLE_RATE = 24000

log = logging.getLogger("kokoro-tts")


def setup_logging():
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=2
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.addHandler(logging.StreamHandler(sys.stderr))
    log.setLevel(logging.INFO)


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        log.warning("Config load failed (%s), using defaults", e)
        return {"voice": "af_heart", "speed": 1.0, "model": "kokoro-v1.0.onnx", "lang": "en-us"}


class TTSPlayer:
    """Manages audio playback with interruption support."""

    def __init__(self):
        self._audio_queue = queue.Queue()
        self._stream = None
        self._playing = False
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._done.set()

    def _callback(self, outdata, frames, time_info, status):
        try:
            data = self._audio_queue.get_nowait()
        except queue.Empty:
            if self._cancel.is_set() or self._audio_queue.empty():
                outdata.fill(0)
                if not self._playing:
                    raise sd.CallbackAbort
                return
            outdata.fill(0)
            return

        if len(data) < frames:
            outdata[:len(data), 0] = data
            outdata[len(data):, 0] = 0
        elif len(data) > frames:
            outdata[:, 0] = data[:frames]
            self._audio_queue.put(data[frames:])
        else:
            outdata[:, 0] = data

    def stop(self):
        self._cancel.set()
        self._playing = False
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._done.set()

    def play_chunks(self, chunks):
        """Play a list of audio chunks (numpy arrays) with gapless output."""
        self.stop()
        self._cancel.clear()
        self._done.clear()
        self._playing = True

        # Break audio into small blocks for the callback
        block_size = 2048
        for chunk in chunks:
            if self._cancel.is_set():
                break
            for i in range(0, len(chunk), block_size):
                if self._cancel.is_set():
                    break
                self._audio_queue.put(chunk[i:i + block_size].astype(np.float32))

        if self._cancel.is_set():
            self._playing = False
            self._done.set()
            return

        try:
            self._stream = sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                callback=self._callback,
                blocksize=block_size,
                finished_callback=lambda: self._done.set(),
            )
            self._stream.start()
        except Exception as e:
            log.error("Audio stream error: %s", e)
            self._playing = False
            self._done.set()

    def wait(self):
        self._done.wait()
        self._playing = False

    @property
    def is_playing(self):
        return self._playing and not self._done.is_set()


class KokoroDaemon:
    def __init__(self):
        setup_logging()
        self.config = load_config()
        self.player = TTSPlayer()
        self._speak_task = None

        model_path = os.path.join(BASE_DIR, "models", self.config["model"])
        voices_path = os.path.join(BASE_DIR, "models", "voices-v1.0.bin")

        log.info("Loading model: %s", model_path)
        t0 = time.time()
        self.kokoro = Kokoro(model_path, voices_path)
        log.info("Model loaded in %.1fs", time.time() - t0)
        log.info("Voice: %s, speed: %.1f, lang: %s",
                 self.config["voice"], self.config["speed"], self.config["lang"])

    async def handle_speak(self, text):
        """Synthesize and play text. Interrupts any current playback."""
        if self._speak_task and not self._speak_task.done():
            self._speak_task.cancel()
            self.player.stop()
            try:
                await self._speak_task
            except asyncio.CancelledError:
                pass

        self._speak_task = asyncio.current_task()
        voice = self.config["voice"]
        speed = self.config["speed"]
        lang = self.config["lang"]

        log.info("Speaking %d chars, voice=%s", len(text), voice)
        t0 = time.time()

        chunks = []
        try:
            async for samples, sr in self.kokoro.create_stream(
                text, voice=voice, speed=speed, lang=lang
            ):
                chunks.append(samples)
        except asyncio.CancelledError:
            log.info("Synthesis cancelled")
            return
        except Exception as e:
            log.error("Synthesis error: %s", e)
            return

        if not chunks:
            log.warning("No audio generated")
            return

        log.info("Synthesized in %.2fs, %d chunks", time.time() - t0, len(chunks))
        self.player.play_chunks(chunks)

    def handle_stop(self):
        if self._speak_task and not self._speak_task.done():
            self._speak_task.cancel()
        self.player.stop()
        log.info("Playback stopped")

    async def handle_client(self, reader, writer):
        try:
            data = await asyncio.wait_for(reader.read(1_000_000), timeout=5.0)
            if not data:
                writer.close()
                return

            msg = json.loads(data.decode("utf-8"))
            cmd = msg.get("cmd", "")

            if cmd == "speak":
                text = msg.get("text", "").strip()
                if text:
                    resp = {"status": "ok", "length": len(text)}
                    writer.write(json.dumps(resp).encode() + b"\n")
                    await writer.drain()
                    writer.close()
                    await self.handle_speak(text)
                else:
                    resp = {"status": "error", "message": "empty text"}
                    writer.write(json.dumps(resp).encode() + b"\n")
                    await writer.drain()
                    writer.close()

            elif cmd == "stop":
                self.handle_stop()
                resp = {"status": "ok"}
                writer.write(json.dumps(resp).encode() + b"\n")
                await writer.drain()
                writer.close()

            elif cmd == "status":
                resp = {
                    "status": "ok",
                    "speaking": self.player.is_playing,
                    "voice": self.config["voice"],
                    "speed": self.config["speed"],
                }
                writer.write(json.dumps(resp).encode() + b"\n")
                await writer.drain()
                writer.close()

            else:
                resp = {"status": "error", "message": f"unknown command: {cmd}"}
                writer.write(json.dumps(resp).encode() + b"\n")
                await writer.drain()
                writer.close()

        except asyncio.TimeoutError:
            log.warning("Client read timeout")
            writer.close()
        except Exception as e:
            log.error("Client handler error: %s", e)
            try:
                writer.close()
            except Exception:
                pass

    async def run(self):
        # Clean stale socket
        if os.path.exists(SOCKET_PATH):
            try:
                r, w = await asyncio.open_unix_connection(SOCKET_PATH)
                w.close()
                await w.wait_closed()
                log.error("Another daemon is running on %s", SOCKET_PATH)
                sys.exit(1)
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                os.unlink(SOCKET_PATH)

        server = await asyncio.start_unix_server(self.handle_client, SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o600)
        log.info("Listening on %s", SOCKET_PATH)

        loop = asyncio.get_event_loop()
        stop_event = asyncio.Event()

        def _signal_handler():
            log.info("Received shutdown signal")
            stop_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _signal_handler)

        await stop_event.wait()

        log.info("Shutting down")
        self.player.stop()
        server.close()
        await server.wait_closed()
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)


def main():
    daemon = KokoroDaemon()
    asyncio.run(daemon.run())


if __name__ == "__main__":
    main()
