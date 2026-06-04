# Funscript Matcher

A native PySide6 / Qt desktop app that pairs funscripts with their matching
videos across one or more source folders and renames (or moves) them in one
click. Hardware-accelerated rendering, fuzzy matching, optional archive
extraction.

## Requirements

- Windows
- Python 3.10+
- Python packages: `PySide6` (and optionally `rarfile` for RAR extraction) —
  the launcher installs these automatically on first run.

## Run

```bat
matcher.bat
```

or directly:

```bat
pythonw matcher.py
```

On first run the app creates its own `matcher_config.json`. Your local config
(which contains your folder paths) is **not** included in this repo and is
ignored by git.

## Support

If this tool saved you time, a tip is hugely appreciated 💜

- **Ko-fi:** https://ko-fi.com/sunblockbukkake
- **Monero (XMR):** `8AKehPGkA4UTw92xa4xXp8Qa99ZfrUUHsE21Hi9bVz4d8j5aEVgUEPSgR69j7XMXTYYNhArcsjCivAfVZyJmRaNX9wBzLLk`
