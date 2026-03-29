# kokoro-tts-macos

System-wide text-to-speech for macOS using [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) (ONNX). Select text anywhere, press a shortcut, and hear it read aloud with a natural-sounding neural voice.

A background daemon keeps the 82M-parameter model loaded in memory for near-instant response. A menu bar app provides status, voice/speed controls, and global keyboard shortcuts.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    macOS                            │
│                                                     │
│  Select text → Ctrl+Cmd+S ─┐                       │
│  Right-click → Read Aloud ─┤                       │
│  CLI: echo "..." | client ─┤                       │
│                             ▼                       │
│  ┌──────────────────────────────────────────────┐   │
│  │  menubar.py (menu bar app)                   │   │
│  │  - Global hotkeys (Ctrl+Cmd+S/X/R)          │   │
│  │  - Status icon (🔇/🔊)                       │   │
│  │  - Voice & speed controls                    │   │
│  └──────────┬───────────────────────────────────┘   │
│             │ Unix socket                           │
│  ┌──────────▼───────────────────────────────────┐   │
│  │  daemon.py (background service)              │   │
│  │  - Kokoro ONNX model (loaded once, ~150MB)   │   │
│  │  - Streaming synthesis + gapless playback    │   │
│  │  - Text preprocessing (markdown, unicode)    │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### Components

| File | Role |
|------|------|
| `daemon.py` | Asyncio socket server. Loads the Kokoro ONNX model once, accepts `speak`/`stop`/`status` commands over `/tmp/kokoro-tts.sock`. Streams audio via `sounddevice` OutputStream callback for gapless playback. |
| `client.py` | Lightweight socket client (no heavy deps). Reads text from stdin or args, sends to daemon. Auto-starts daemon via launchctl if not running. |
| `menubar.py` | macOS menu bar app ([rumps](https://github.com/jaredks/rumps)). Shows speaking status, registers global keyboard shortcuts via NSEvent, provides voice/speed controls. |
| `preprocess.py` | Text cleaning for TTS input. Strips markdown, ANSI codes, box-drawing chars, normalizes unicode, collapses whitespace. |
| `service-wrapper.sh` | Shell wrapper invoked by macOS Automator Services. Pipes selected text to client with logging. |
| `config.json` | Voice, speed, model, and language preferences. |

## Requirements

- macOS 12+ (tested on Apple Silicon)
- Python 3.10+ (`brew install python`)
- ~400MB disk (model files + Python venv)
- ~150MB RAM when idle

## Install

```bash
# Clone
git clone https://github.com/FilDro/kokoro-tts-macos.git ~/.local/share/kokoro-tts
cd ~/.local/share/kokoro-tts

# Create venv and install dependencies
python3 -m venv venv
venv/bin/pip install kokoro-onnx sounddevice numpy rumps

# Download model files (~340MB)
mkdir -p models
curl -L https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx \
     -o models/kokoro-v1.0.onnx
curl -L https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin \
     -o models/voices-v1.0.bin

# Quick test
venv/bin/python3 daemon.py &
echo "Hello, this is Kokoro." | venv/bin/python3 client.py
kill %1
```

### Set up macOS integration

```bash
# Install right-click Services
cp -r services/Read\ Aloud.workflow ~/Library/Services/
cp -r services/Stop\ Reading.workflow ~/Library/Services/
/System/Library/CoreServices/pbs -update

# Install LaunchAgents (auto-start at login)
cp launchd/com.filip.kokoro-tts.plist ~/Library/LaunchAgents/
cp launchd/com.filip.kokoro-tts-menubar.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.filip.kokoro-tts.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.filip.kokoro-tts-menubar.plist
```

### Grant Accessibility permission (required for global shortcuts)

The menu bar app needs Accessibility access to listen for keyboard shortcuts globally.

1. Open **System Settings → Privacy & Security → Accessibility**
2. Click **+** and add `Python.app` from:
   ```
   /opt/homebrew/Cellar/python@3.13/*/Frameworks/Python.framework/Versions/*/Resources/Python.app
   ```
3. Toggle it **on**

> **Note:** If using a different Python version, find the correct path with:
> `ps aux | grep menubar.py | grep -v grep | awk '{print $11}'`

## Usage

### Keyboard shortcuts (global — work in any app)

| Shortcut | Action |
|----------|--------|
| `Ctrl+Cmd+S` | Read selected text (copies selection, then speaks) |
| `Ctrl+Cmd+P` | Pause / Resume (toggle — freezes both audio and synthesis) |
| `Ctrl+Cmd+X` | Stop reading (immediately halts playback and frees CPU) |
| `Ctrl+Cmd+R` | Read clipboard contents |

### Right-click menu

Select text in any app → right-click → **Services** → **Read Aloud**

### Menu bar

Click the 🔇 / 🔊 / ⏸ icon in the menu bar to:
- See current status (idle / speaking / paused)
- Pause or resume playback
- Stop reading
- Read clipboard
- Change voice (8 presets)
- Change speed (0.8x – 1.5x)
- Restart the daemon

### Command line

```bash
# Speak text from stdin
echo "Text to read aloud" | venv/bin/python3 client.py

# Speak text from argument
venv/bin/python3 client.py "Read this sentence"

# Pause / resume
venv/bin/python3 client.py --pause

# Stop playback
venv/bin/python3 client.py --stop

# Check daemon status
venv/bin/python3 client.py --status
```

Sending new text automatically interrupts current playback.

## Configuration

Edit `config.json`:

```json
{
  "voice": "af_heart",
  "speed": 1.0,
  "model": "kokoro-v1.0.onnx",
  "lang": "en-us"
}
```

Changes take effect after restarting the daemon:

```bash
launchctl kickstart -k gui/$(id -u)/com.filip.kokoro-tts
```

Or use the menu bar: click the icon → **Restart Daemon**. Changing voice or speed via the menu bar restarts the daemon automatically.

### Voices

54 voices across 8 languages. Highlights:

| ID | Description | Quality |
|----|-------------|---------|
| `af_heart` | American English, female | A |
| `af_bella` | American English, female | A- |
| `af_nicole` | American English, female | — |
| `am_adam` | American English, male | — |
| `am_michael` | American English, male | — |
| `bf_emma` | British English, female | B- |
| `bm_george` | British English, male | — |

Full voice list: [Kokoro-82M VOICES.md](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md)

Supported languages: English (US/UK), Spanish, French, Japanese, Mandarin, Hindi, Italian, Brazilian Portuguese.

## Text preprocessing

The `preprocess.py` module automatically cleans input text before synthesis:

- Strips markdown formatting (`**bold**`, `# headings`, `` `code` ``, code blocks)
- Removes ANSI escape codes (terminal color output)
- Replaces unicode symbols (em dashes, arrows, bullets) with speech-friendly equivalents
- Removes box-drawing characters
- Collapses excessive whitespace
- Strips file paths and horizontal rules

This means you can select text from terminals, markdown documents, chat apps, and code editors without worrying about formatting artifacts.

## File layout

```
~/.local/share/kokoro-tts/
├── daemon.py              # TTS daemon (asyncio socket server)
├── client.py              # Lightweight socket client
├── menubar.py             # macOS menu bar app
├── preprocess.py          # Text cleaning for TTS
├── service-wrapper.sh     # Automator service wrapper
├── config.json            # User configuration
├── models/                # Model files (not in git)
│   ├── kokoro-v1.0.onnx   #   310 MB
│   └── voices-v1.0.bin    #    27 MB
├── services/              # macOS Automator workflows
│   ├── Read Aloud.workflow/
│   └── Stop Reading.workflow/
├── launchd/               # LaunchAgent plists
│   ├── com.filip.kokoro-tts.plist
│   └── com.filip.kokoro-tts-menubar.plist
└── venv/                  # Python virtual environment (not in git)
```

## Troubleshooting

### Service not appearing in right-click menu

```bash
/System/Library/CoreServices/pbs -update
```

If still missing, log out and back in. Verify workflows are in `~/Library/Services/`.

### No audio

```bash
# Check daemon is running
venv/bin/python3 client.py --status

# Check logs
tail -20 kokoro-tts.log

# Verify model files
ls -lh models/
```

### Global shortcuts not working

The menu bar app needs Accessibility permission. Check **System Settings → Privacy & Security → Accessibility** and ensure `Python.app` is listed and enabled. Restart the menu bar app after granting access:

```bash
launchctl kickstart -k gui/$(id -u)/com.filip.kokoro-tts-menubar
```

### Daemon won't start

```bash
# Check error logs
cat launchd-stderr.log

# Try running manually
venv/bin/python3 daemon.py
```

### Slow synthesis

Ensure the LaunchAgent plist has `ProcessType` set to `Standard` (not `Background`). The `Background` process type causes macOS to throttle CPU significantly.

## Uninstall

```bash
# Stop services
launchctl bootout gui/$(id -u)/com.filip.kokoro-tts
launchctl bootout gui/$(id -u)/com.filip.kokoro-tts-menubar

# Remove files
rm -rf ~/.local/share/kokoro-tts
rm ~/Library/LaunchAgents/com.filip.kokoro-tts.plist
rm ~/Library/LaunchAgents/com.filip.kokoro-tts-menubar.plist
rm -rf ~/Library/Services/Read\ Aloud.workflow
rm -rf ~/Library/Services/Stop\ Reading.workflow
```

## Credits

- [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) by hexgrad — the TTS model
- [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) by thewh1teagle — ONNX runtime wrapper
- [rumps](https://github.com/jaredks/rumps) — macOS menu bar framework

## License

MIT
