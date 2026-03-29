# kokoro-tts-macos

System-wide text-to-speech for macOS using [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) (ONNX). Select text in any app, right-click, and have it read aloud.

A background daemon keeps the model loaded in memory for instant response (~500ms to first audio).

## How it works

```
[Any macOS app] → select text → right-click → "Read Aloud"
       ↓
[macOS Service (.workflow)] → pipes selected text to client
       ↓
[client.py] → sends JSON over Unix socket → [daemon.py] → speakers
```

- **daemon.py** — Asyncio socket server that loads the Kokoro ONNX model once and stays resident. Accepts `speak`, `stop`, and `status` commands over a Unix socket (`/tmp/kokoro-tts.sock`). Uses `sounddevice` for gapless audio playback via a callback-based `OutputStream`.
- **client.py** — Lightweight client (no heavy deps) that sends text to the daemon. Falls back to macOS `say` if the daemon is unreachable.
- **macOS Services** — Automator Quick Actions that appear in the right-click context menu for selected text across all apps.
- **LaunchAgent** — Auto-starts the daemon at login via `launchd`. Restarts on crash.

## Requirements

- macOS (tested on Apple Silicon)
- Python 3.10+ (Homebrew: `brew install python`)
- ~400MB disk (model + venv)
- ~150MB RAM idle

## Install

```bash
# 1. Clone
git clone https://github.com/FilDro/kokoro-tts-macos.git ~/.local/share/kokoro-tts
cd ~/.local/share/kokoro-tts

# 2. Create venv and install deps
python3 -m venv venv
venv/bin/pip install kokoro-onnx sounddevice numpy

# 3. Download model files (~340MB total)
mkdir -p models
curl -L https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx -o models/kokoro-v1.0.onnx
curl -L https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin -o models/voices-v1.0.bin

# 4. Test it
venv/bin/python3 daemon.py &
echo "Hello world" | venv/bin/python3 client.py
# You should hear audio. Kill the test daemon:
kill %1

# 5. Install macOS Services (right-click menu)
# Update the paths in the .workflow files to match your install location,
# then copy them:
cp -r services/Read\ Aloud.workflow ~/Library/Services/
cp -r services/Stop\ Reading.workflow ~/Library/Services/
/System/Library/CoreServices/pbs -update

# 6. Install LaunchAgent (auto-start daemon at login)
# Update paths in the plist to match your install location, then:
cp launchd/com.filip.kokoro-tts.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.filip.kokoro-tts.plist

# 7. Set a keyboard shortcut (optional)
# System Settings → Keyboard → Keyboard Shortcuts → Services → Text
# Find "Read Aloud" and assign a shortcut (e.g. Ctrl+Cmd+S)
```

## Usage

**Right-click menu:** Select text in any app → right-click → Services → "Read Aloud"

**Command line:**
```bash
# Speak text
echo "Some text to read" | venv/bin/python3 client.py

# Stop playback
venv/bin/python3 client.py --stop

# Check daemon status
venv/bin/python3 client.py --status
```

**New text interrupts current playback** — no need to stop first.

## Configuration

Edit `config.json` to change voice, speed, or language:

```json
{
  "voice": "af_heart",
  "speed": 1.0,
  "model": "kokoro-v1.0.onnx",
  "lang": "en-us"
}
```

Restart the daemon after changing config:
```bash
launchctl kickstart -k gui/$(id -u)/com.filip.kokoro-tts
```

### Available voices

54 voices across 8 languages. Some highlights:

| Voice | Language | Grade |
|-------|----------|-------|
| `af_heart` | American English (F) | A |
| `af_bella` | American English (F) | A- |
| `am_adam` | American English (M) | — |
| `bf_emma` | British English (F) | B- |
| `bm_george` | British English (M) | — |

Full list: [Kokoro-82M voices](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md)

## File layout

```
~/.local/share/kokoro-tts/
├── daemon.py           # TTS daemon
├── client.py           # Socket client
├── config.json         # Voice/speed/model config
├── models/             # ONNX model + voice embeddings (not in git)
│   ├── kokoro-v1.0.onnx
│   └── voices-v1.0.bin
├── services/           # macOS Service workflow bundles
│   ├── Read Aloud.workflow/
│   └── Stop Reading.workflow/
├── launchd/            # LaunchAgent plist
│   └── com.filip.kokoro-tts.plist
└── venv/               # Python virtual environment (not in git)
```

## Troubleshooting

**Service not appearing in right-click menu:**
- Run `/System/Library/CoreServices/pbs -update`
- Log out and back in
- Check that the workflow files are in `~/Library/Services/`

**No audio:**
- Check daemon is running: `venv/bin/python3 client.py --status`
- Check logs: `tail -20 kokoro-tts.log`
- Verify model files exist in `models/`

**Daemon won't start:**
- Check `launchd-stderr.log` for errors
- Try running manually: `venv/bin/python3 daemon.py`

## Credits

- [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) — TTS model by hexgrad
- [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) — ONNX runtime wrapper by thewh1teagle

## License

MIT
