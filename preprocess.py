"""Text preprocessing for TTS — strip formatting, normalize unicode, clean for speech."""

import re
import unicodedata


def preprocess_for_tts(text: str) -> str:
    """Clean text for TTS synthesis. Strips formatting, normalizes unicode,
    removes non-speech content."""

    # Normalize unicode (NFKD decomposes, then we keep compatible chars)
    text = unicodedata.normalize("NFKC", text)

    # Replace common unicode with speech-friendly equivalents
    replacements = {
        "\u2014": " — ",   # em dash
        "\u2013": " - ",   # en dash
        "\u2192": " to ",  # →
        "\u2190": " from ",  # ←
        "\u2026": "...",   # ellipsis
        "\u201c": '"',     # left double quote
        "\u201d": '"',     # right double quote
        "\u2018": "'",     # left single quote
        "\u2019": "'",     # right single quote
        "\u2022": ". ",    # bullet
        "\u25cf": ". ",    # black circle bullet
        "\u25cb": ". ",    # white circle bullet
        "\u2023": ". ",    # triangular bullet
        "\u00b7": ". ",    # middle dot
        "\u2502": "",      # box drawing vertical
        "\u2500": "",      # box drawing horizontal
        "\u250c": "",      # box drawing corner
        "\u2510": "",      # box drawing corner
        "\u2514": "",      # box drawing corner
        "\u2518": "",      # box drawing corner
        "\u251c": "",      # box drawing tee
        "\u2524": "",      # box drawing tee
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    # Strip markdown-style formatting
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # **bold**
    text = re.sub(r'__(.+?)__', r'\1', text)       # __bold__
    text = re.sub(r'\*(.+?)\*', r'\1', text)       # *italic*
    text = re.sub(r'_(.+?)_', r'\1', text)         # _italic_
    text = re.sub(r'`(.+?)`', r'\1', text)         # `code`
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)  # # headings
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)   # - bullet lists
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE) # 1. numbered lists
    text = re.sub(r'^\s*>\s+', '', text, flags=re.MULTILINE)     # > blockquotes
    text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)      # --- dividers

    # Strip code blocks
    text = re.sub(r'```[\s\S]*?```', '', text)

    # Clean file paths and technical tokens that read badly
    # Keep class/function names but strip paths
    text = re.sub(r'[/\\][\w./\\]+\.\w+', '', text)  # file paths like /foo/bar.dart

    # Collapse whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'(\n\s*){3,}', '\n\n', text)

    # Remove lines that are just whitespace or punctuation
    lines = text.split('\n')
    lines = [l.strip() for l in lines if l.strip() and not re.match(r'^[\s\-_=*#>|]+$', l.strip())]
    text = '\n'.join(lines)

    # Final cleanup
    text = re.sub(r'\s+([.,;:!?])', r'\1', text)  # fix space before punctuation
    text = re.sub(r'\s{2,}', ' ', text)
    text = text.strip()

    return text


if __name__ == "__main__":
    import sys
    text = sys.stdin.read()
    print(f"Before: {len(text)} chars")
    cleaned = preprocess_for_tts(text)
    print(f"After:  {len(cleaned)} chars")
    print("---")
    print(cleaned[:500])
