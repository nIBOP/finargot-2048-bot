# FINARGOT 2048 Bot

Automation bot for the BattlePass 2048 mini-game. The current main entrypoint is
`bot_final.py`; `main.py` is kept in sync with it.

## What Is Included

- Python Selenium bot with friendly Russian console prompts.
- Built-in Python fallback solver for smoke checks.
- `START_BOT_SLOW.bat` for a safer launch with non-periodic human-paced rhythm.
- `patches/tdl2048-protocol.patch`, required to make upstream TDL2048 work as
  an interactive solver for this bot.
- `scripts/setup_tdl_windows.ps1`, which restores the external TDL2048 folder,
  downloads the model and builds `tdl2048.exe`.
- `scripts/check_ready_windows.ps1`, which verifies Python, Chrome, TDL files and
  the TDL protocol before a real run.
- `scripts/train_tdl_windows.ps1`, a small wrapper for TDL2048 model training.

## What Is Not In Git

The repository intentionally does not include runtime/private/heavy files:

- `runs/` with Chrome profile, cookies, logs, screenshots and diagnostics.
- `external/TDL2048/` and model files such as `8x6patt.w`.
- `.exe` and other generated binaries.

GitHub rejects files above 100 MB, and the TDL model is much larger than that.
Keep those files local or share them as a separate archive/release asset.

## Quick Start On Windows

1. Install Python 3.10+ and Google Chrome.
2. Install Python dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Restore the strongest TDL backend:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\setup_tdl_windows.ps1
   ```

   This clones `moporgic/TDL2048`, applies this repo's protocol patch, downloads
   `8x6patt.w.xz`, extracts it and builds `tdl2048.exe`.

4. Or, if you already have the files, put them here manually:

   ```text
   external/TDL2048/tdl2048.exe
   external/TDL2048/8x6patt.w
   ```

5. Run the safe launcher:

   ```powershell
   .\START_BOT_SLOW.bat
   ```

   For a one-shot, accuracy-first run, use `START_BOT_MAX_SCORE.bat` instead.
   It expands 7-ply search through positions with six empty cells. This can make
   the game substantially longer, especially after reaching 8192, but it avoids
   reducing the search budget in the most constrained positions.

The console explains what to do in the opened Chrome window.

The battle launcher uses a deep limited TDL search with a 512 MB transposition
table and deep-cache reuse:

```text
7p limit=7p,7p,6p,6p,6p,6p,6p,6p,6p,6p,6p,6p,6p,6p,6p,6p
```

This keeps at least 6-ply search throughout the game and uses 7-ply search
on the densest endgame boards where low-score losses usually happen. The cache
keeps that deeper search practical on the battle machine.

The interactive protocol also preserves 80-bit board states through
`131072` and enables TDL's built-in tile-downgrading from `32768`. This is
needed to keep a `65536` tile from being misread as an empty cell.

The battle launcher leaves forced-loss limits disabled so a rare high-scoring
game can finish naturally instead of being cut off by a local move or score cap.

To verify the machine before a real try:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_ready_windows.ps1
```

## MSYS2/GCC Requirement

`setup_tdl_windows.ps1` needs Git and a working `make`/`g++` toolchain. The
recommended Windows route is:

```powershell
winget install Git.Git
winget install MSYS2.MSYS2
```

Then open `MSYS2 UCRT64` and run:

```bash
pacman -S --needed mingw-w64-ucrt-x86_64-gcc make git curl xz
```

After that, run `scripts/setup_tdl_windows.ps1` from PowerShell in the repo
root.

## Training A Model

The battle launcher uses the downloaded `8x6patt.w` model and does not require
training from scratch. Training is only for experiments and can take a long
time, especially for 8x6 networks. Start with a small 4x6 smoke test:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\train_tdl_windows.ps1 -Network 4x6patt -EpisodesK 10
```

Longer 4x6 training:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\train_tdl_windows.ps1 -Network 4x6patt -EpisodesK 1000 -Threads 8
```

Fine-tune an existing 8x6 model:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\train_tdl_windows.ps1 `
  -Network 8x6patt `
  -EpisodesK 1000 `
  -Threads 8 `
  -InputModel external\TDL2048\8x6patt.w `
  -OutputModel external\TDL2048\8x6patt-custom.w
```

More details in [docs/RESTORE_AND_TRAIN_RU.md](docs/RESTORE_AND_TRAIN_RU.md).

## Development Checks

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_tdl_windows.ps1 -SkipModel -SkipBuild
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_ready_windows.ps1
python -m py_compile main.py bot_final.py
```
