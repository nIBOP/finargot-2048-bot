# External Solvers

Large third-party solver folders are intentionally ignored by git.

For the strongest backend, place TDL2048 files at:

```text
external/TDL2048/tdl2048.exe
external/TDL2048/8x6patt.w
```

The public contest build uses TDL2048 for the strongest moves. The Python
solver inside `bot_final.py` is kept only as a local fallback/smoke-check path.

To recreate this folder from scratch on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_tdl_windows.ps1
```

That script clones upstream TDL2048, applies `patches/tdl2048-protocol.patch`,
downloads the `8x6patt` model and builds `tdl2048.exe`.
