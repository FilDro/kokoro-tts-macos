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
import time

import sounddevice as sd
from kokoro_onnx import Kokoro

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOCKET_PATH = "/tmp/kokoro-tts.sock"
LOG_PATH = os.path.join(BASE_DIR, "kokoro-tts.log")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SAMPLE_RATE = 24000
BLOCK_SIZE = 2048

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


class StreamingPlayer:
    """Plays audio chunks as they arrive, with interruption support."""

    def __init__(self):
        self._queue = queue.Queue()
        self._stream = None
        self._finished = False  # True when all chunks have been queued
        self._cancel = False

    def _callback(self, outdata, frames, time_info, status):
        try:
            data = self._queue.get_nowait()
        except queue.Empty:
            outdata.fill(0)
            if self._finished or self._cancel:
                raise sd.CallbackAbort
            return  # Synthesis still producing — output silence, keep going

        if len(data) < frames:
            outdata[:len(data), 0] = data
            outdata[len(data):, 0] = 0
        elif len(data) > frames:
            outdata[:, 0] = data[:frames]
            self._queue.put(data[frames:])
        else:
            outdata[:, 0] = data

    def start(self):
        """Start the audio output stream. Call feed_chunk() to push audio."""
        self._queue = queue.Queue()
        self._finished = False
        self._cancel = False
        try:
            self._stream = sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                callback=self._callback,
                blocksize=BLOCK_SIZE,
            )
            self._stream.start()
        except Exception as e:
            log.error("Audio stream start error: %s", e)
            self._stream = None

    def feed_chunk(self, samples):
        """Feed a synthesized audio chunk into the playback queue."""
        if self._cancel:
            return
        data = samples.astype(np.float32)
        for i in range(0, len(data), BLOCK_SIZE):
            if self._cancel:
                return
            self._queue.put(data[i:i + BLOCK_SIZE])

    def finish(self):
        """Signal that no more chunks will arrive."""
        self._finished = True

    def stop(self):
        """Stop playback immediately."""
        self._cancel = True
        self._finished = True
        # Drain queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    @property
    def is_playing(self):
        return self._stream is not None and self._stream.active


class KokoroDaemon:
    def __init__(self):
        setup_logging()
        self.config = load_config()
        self.player = StreamingPlayer()
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
        """Synthesize and play text. Streams audio — plays as chunks arrive."""
        # Cancel previous speak task
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

        # Clean text for TTS
        from preprocess import preprocess_for_tts
        text = preprocess_for_tts(text)

        log.info("Speaking %d chars, voice=%s", len(text), voice)
        t0 = time.time()

        # Start audio output immediately — chunks will feed in as synthesized
        self.player.start()
        chunk_count = 0

        try:
            async for samples, sr in self.kokoro.create_stream(
                text, voice=voice, speed=speed, lang=lang
            ):
                chunk_count += 1
                self.player.feed_chunk(samples)
                if chunk_count == 1:
                    log.info("First chunk ready in %.2fs", time.time() - t0)
        except asyncio.CancelledError:
            log.info("Synthesis cancelled after %d chunks", chunk_count)
            self.player.stop()
            return
        except Exception as e:
            log.error("Synthesis error: %s", e)
            self.player.stop()
            return

        self.player.finish()
        log.info("Synthesized %d chunks in %.2fs", chunk_count, time.time() - t0)

    def handle_stop(self):
        if self._speak_task and not self._speak_task.done():
            self._speak_task.cancel()
        self.player.stop()
        log.info("Playback stopped")

    async def handle_client(self, reader, writer):
        try:
            data = await asyncio.wait_for(reader.read(-1), timeout=5.0)
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
