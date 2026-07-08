# FINARGOT 2048 Bot

Automation bot for the BattlePass 2048 mini-game. The current main entrypoint is
`bot_final.py`; `main.py` is kept in sync with it.

## What Is Included

- Python Selenium bot with friendly Russian console prompts.
- Rust fallback solver in `src/main.rs`.
- Java fallback solver source in `Solver2048.java`.
- Local simulator and tests.
- `START_BOT_SLOW.bat` for a safer slow launch that reduces `TOO_FAST` risk.

## What Is Not In Git

The repository intentionally does not include runtime/private/heavy files:

- `runs/` with Chrome profile, cookies, logs, screenshots and diagnostics.
- `dist/` release package.
- `target/` build outputs.
- `external/TDL2048/` and model files such as `8x6patt.w`.
- `.exe`, `.class` and other generated binaries.

GitHub rejects files above 100 MB, and the TDL model is much larger than that.
Keep those files local or share them as a separate archive/release asset.

## Quick Start

1. Install Python 3.10+ and Google Chrome.
2. Install Python dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Put the TDL2048 executable and model here if you want the strongest backend:

   ```text
   external/TDL2048/tdl2048.exe
   external/TDL2048/8x6patt.w
   ```

4. Run the safe launcher:

   ```powershell
   .\START_BOT_SLOW.bat
   ```

The console explains what to do in the opened Chrome window.

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

