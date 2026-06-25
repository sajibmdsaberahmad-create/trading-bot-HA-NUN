Mac Storage Cleaner
===================

Standalone folder — not part of HANOON trading bot.
Copy this whole folder anywhere (Desktop, Applications, etc.).

DOUBLE-CLICK (Finder)
---------------------
  Start Mac Cleaner.command   → interactive menu (main launcher)
  Scan Storage.command        → show reclaimable space only
  Clean Safe.command          → clean caches, logs, trash, temp…
  Unload RAM.command          → unload Ollama models from memory
  Clean and Unload.command    → disk clean + RAM unload

First time: if macOS blocks the script, right-click → Open → Open.

TERMINAL
--------
  ./mac-clean.sh
  ./mac-clean.sh --unload
  ./mac-clean.sh --clean --yes

FILES
-----
  clean.py          engine
  mac-clean.sh      CLI wrapper
  menu.sh           interactive menu
  *.command         double-click launchers

SAFE BY DEFAULT
---------------
Never touches Documents, Desktop, Photos, Keychain, or iCloud.
Scan is always safe. Clean requires typing yes or y (or --yes in CLI).
