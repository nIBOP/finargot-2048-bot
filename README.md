# FINARGOT 2048 Bot

Automation bot for the BattlePass 2048 mini-game. The current main entrypoint is
`bot_final.py`; `main.py` is kept in sync with it.

## What Is Included

- Python Selenium bot with friendly Russian console prompts.
- Rust fallback solver in `src/main.rs`.
- Java fallback solver source in `Solver2048.java`.
- Local simulator and tests.
- `START_BOT_SLOW.bat` for a safer slow launch that reduces `TOO_FAST` risk.
- `patches/tdl2048-protocol.patch`, required to make upstream TDL2048 work as
  an interactive solver for this bot.
- `scripts/setup_tdl_windows.ps1`, which restores the external TDL2048 folder,
  downloads the model and builds `tdl2048.exe`.
- `scripts/train_tdl_windows.ps1`, a small wrapper for TDL2048 model training.

## What Is Not In Git

The repository intentionally does not include runtime/private/heavy files:

- `runs/` with Chrome profile, cookies, logs, screenshots and diagnostics.
- `dist/` release package.
- `target/` build outputs.
- `external/TDL2048/` and model files such as `8x6patt.w`.
- `.exe`, `.class` and other generated binaries.

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

The console explains what to do in the opened Chrome window.

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

Training is handled by TDL2048 and can take a long time. Start with a small
4x6 smoke test:

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
python -m py_compile main.py bot_final.py simulate_local.py
python -m unittest discover -s tests -v
cargo build --release
```

## Package Build

If the external TDL files are present locally, build the portable folder and zip:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The result is written to `dist/finargot-bot` and `dist/finargot-bot.zip`.
