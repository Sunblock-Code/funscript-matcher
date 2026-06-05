# Funscript Matcher

A native PySide6 / Qt desktop app that pairs funscripts with their matching
videos across one or more source folders and renames them in one click.
Hardware-accelerated rendering, fuzzy matching, optional archive extraction.

The **Operation** selector chooses how matched files land in the output folder:

- **Move** — relocate the files (removes them from the source).
- **Copy** — duplicate the files (uses extra disk space).
- **Symlink** — create correctly-named links that point back to the originals.
  No extra disk space and the originals stay put; works with most playback
  software. On Windows this needs Developer Mode enabled (Settings → Privacy &
  security → For developers) or running the app as administrator.
- **Hardlink** — a second name for the same file: no extra disk space, no admin
  needed, and deleting the source won't break the linked copy (the data lives
  until the last name is removed). Works for files on the **same drive** only;
  if a hardlink isn't possible it falls back to a symlink, then to a copy.

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
