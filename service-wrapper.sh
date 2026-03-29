#!/bin/bash
# Wrapper for macOS Service — captures stdin and passes to client with logging
LOGFILE="$HOME/.local/share/kokoro-tts/service.log"
VENV="$HOME/.local/share/kokoro-tts/venv/bin/python3"
CLIENT="$HOME/.local/share/kokoro-tts/client.py"

TEXT=$(cat)
echo "=== $(date) ===" >> "$LOGFILE"
echo "TEXT_LENGTH: ${#TEXT}" >> "$LOGFILE"
echo "TEXT_FIRST_100: ${TEXT:0:100}" >> "$LOGFILE"

echo "$TEXT" | "$VENV" "$CLIENT" 2>> "$LOGFILE"
EXIT=$?
echo "EXIT: $EXIT" >> "$LOGFILE"
echo "=== END ===" >> "$LOGFILE"
