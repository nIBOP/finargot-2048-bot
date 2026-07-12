# Восстановление окружения и обучение модели

Этот репозиторий содержит финальный Python-бот, патч для TDL2048 и скрипты
восстановления/обучения. В git намеренно нет `runs/`, `external/TDL2048/`,
`.exe` и больших `.w` моделей: там приватный Chrome-профиль, логи и файлы на
сотни мегабайт.

## Быстрый рабочий путь

1. Установить Python 3.10+ и Google Chrome.
2. Установить зависимости:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Восстановить TDL2048 и скачать модель:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\setup_tdl_windows.ps1
   ```

4. Запустить безопасный боевой режим:

   ```powershell
   .\START_BOT_SLOW.bat
   ```

Перед боевым запуском на чужой машине полезно выполнить preflight:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_ready_windows.ps1
```

## Что делает `setup_tdl_windows.ps1`

Скрипт:

1. Клонирует `https://github.com/moporgic/TDL2048` в `external/TDL2048`.
2. Применяет `patches/tdl2048-protocol.patch`.
3. Скачивает `8x6patt.w.xz` с `https://moporgic.info/2048/model/8x6patt.w.xz`.
4. Распаковывает модель в `external/TDL2048/8x6patt.w`.
5. Собирает `external/TDL2048/tdl2048.exe`.

Патч нужен обязательно: без `--protocol` Python-бот не сможет общаться с TDL2048
как с быстрым интерактивным решателем.

## Требования для сборки TDL2048 на Windows

Нужны Git, MSYS2, GCC, Make, curl/xz или Windows `tar`.

Рекомендуемый путь:

```powershell
winget install Git.Git
winget install MSYS2.MSYS2
```

Затем открыть `MSYS2 UCRT64` и выполнить:

```bash
pacman -S --needed mingw-w64-ucrt-x86_64-gcc make git curl xz
```

После этого вернуться в PowerShell в корень репозитория и запустить:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_tdl_windows.ps1
```

## Если модель уже есть

Положить файлы вручную:

```text
external/TDL2048/tdl2048.exe
external/TDL2048/8x6patt.w
```

После этого `START_BOT_SLOW.bat` должен работать без восстановления.

## Обучение модели

TDL2048 умеет обучать n-tuple сети. Это долгий CPU-heavy процесс: 8x6 модель
большая, обучение с нуля может занимать часы или дни. Для экспериментов лучше
начинать с `4x6patt`.

Боевой запуск не требует обучения с нуля: `START_BOT_SLOW.bat` использует
скачанную готовую модель `external/TDL2048/8x6patt.w`.

Smoke-test на маленьком числе эпизодов:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\train_tdl_windows.ps1 -Network 4x6patt -EpisodesK 10
```

Обучить `4x6patt` на 1000k эпизодов:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\train_tdl_windows.ps1 -Network 4x6patt -EpisodesK 1000 -Threads 8
```

Дообучить существующую `8x6patt.w` и сохранить новую модель отдельно:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\train_tdl_windows.ps1 `
  -Network 8x6patt `
  -EpisodesK 1000 `
  -Threads 8 `
  -InputModel external\TDL2048\8x6patt.w `
  -OutputModel external\TDL2048\8x6patt-custom.w
```

Чтобы использовать свою модель в боте, либо переименовать ее в
`external/TDL2048/8x6patt.w`, либо запускать напрямую:

```powershell
python -u bot_final.py --solver-backend tdl --tdl-network 8x6patt --tdl-search "5p limit=5p,5p,5p,5p,4p,4p,4p,4p,3p"
```

## Проверки после восстановления

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_tdl_windows.ps1 -SkipModel -SkipBuild
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_ready_windows.ps1
python -m py_compile main.py bot_final.py
```

Проверить, что TDL-файлы на месте:

```powershell
Test-Path external\TDL2048\tdl2048.exe
Test-Path external\TDL2048\8x6patt.w
```

## Боевые настройки

`START_BOT_SLOW.bat` включает:

```text
--rhythm-profile human
--tdl-search "5p limit=5p,5p,5p,5p,4p,4p,4p,4p,3p"
--force-loss-after-score 520000
--force-loss-after-moves 18500
```

`--rhythm-profile human` включает нерегулярные паузы без фиксированного периода.
`--tdl-search ... limit=...` включает более глубокий поиск на плотных досках:
5-ply при 0-3 пустых клетках, 4-ply при 4-7 пустых, 3-ply при 8+ пустых.
Последние два параметра нужны, чтобы не получить `BAD_MOVES`: сайт отклоняет
слишком длинные партии по лимиту ходов.
