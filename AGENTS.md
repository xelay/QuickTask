# AGENTS.md

Guidance for AI coding agents working in this repository.

## Project Overview

QuickTask is a Windows desktop widget: a small tab docked to the edge of the screen that expands into a 380px sidebar for managing tasks stored as plain Markdown files. It is built with Python + [pywebview](https://pywebview.flowrl.com/) (a native window hosting a single HTML/CSS/JS page) — no Electron, no web framework, no database.

## Tech Stack

- **Python 3.10+** — application logic, native window, filesystem/task store.
- **pywebview** (`edgechromium` backend via `pythonnet`/`clr_loader`) — native window + JS↔Python bridge. Relies on the WebView2 runtime bundled with Windows 10/11.
- **watchdog** — filesystem watcher, picks up external edits to task files.
- **keyboard** — global hotkey registration.
- **PyYAML** — parsing/writing task frontmatter (own minimal parser wraps it; not the `python-frontmatter` package).
- **sqlite3** (stdlib) — local metadata cache, not a data store of record.
- **CodeMirror** (vendored under `vendor/codemirror/`) — Markdown editor for task descriptions.
- Frontend is vanilla JS in a single HTML file — no bundler, no npm, no build step for the UI.

## Repository Structure

- `main.py` — entry point: the pywebview window, `QuickTaskAPI` (the JS↔Python bridge class), watchdog wiring, global hotkey registration/validation, Windows taskbar-icon hiding (ctypes/WinAPI).
- `task_store.py` — the task store: frontmatter read/write, the SQLite metadata cache, and the fractional sort-key (`order_key`) scheme used for manual ordering. Has an extensive module docstring explaining the performance rationale — read it before touching this file.
- `index.html` — the entire UI: markup, CSS, and JS in one file. Includes a hand-rolled i18n system (`I18N` object with `en`/`ru`/`zh` keys, a `t(key, vars)` helper, `data-i18n*` attributes on elements).
- `vendor/codemirror/` — third-party CodeMirror build, loaded by `index.html` via a relative path. Do not edit; if it's missing, the description editor silently fails to load.
- `VERSION` — plain text file holding the current app version (e.g. `1.1.0`). Read once at startup by `load_app_version()` in `main.py`; the value is surfaced read-only in Settings. Bump it before building a release.
- `requirements.txt` — pinned dependency versions.
- `CHANGELOG.md` — version history, Keep a Changelog format, English.
- `README.md` / `README.ru.md` / `README.zh-CN.md` — user-facing docs in English (default), Russian, and Simplified Chinese, cross-linked with a language switcher on the first line of each.
- `CONTRIBUTING.md` — build/dev instructions, English only.
- `PROMPT.md` — a large "reconstruct this app from scratch" prompt used to regenerate the project via an LLM. Note it can drift out of sync with the real code (e.g. it currently still lists `python-frontmatter` as a dependency, which is no longer used) — don't treat it as a source of truth about current behavior.
- `screenshots/` — assets referenced by the READMEs.

## Setup Commands

Windows only (`cmd.exe` syntax, matches `CONTRIBUTING.md`):

```cmd
git clone https://github.com/xelay/QuickTask.git
cd QuickTask
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
venv\Scripts\python.exe main.py
```

## Build Commands

PyInstaller, `--onedir` (recommended) or `--onefile`:

```cmd
venv\Scripts\pip.exe install pyinstaller
venv\Scripts\pyinstaller.exe --noconsole --name "QuickTask" --add-data "index.html;." --add-data "vendor;vendor" --add-data "VERSION;." main.py
```

All three `--add-data` flags are required — dropping `vendor` breaks the description editor, dropping `VERSION` makes Settings show `0.0.0`. See `CONTRIBUTING.md` for full details, including the `--onefile` variant and autostart setup.

## Platform Constraint — Read Before Attempting to Run or Test

This app **only runs on Windows 10/11 (64-bit)**. `main.py` imports `ctypes.wintypes` and does Win32-specific work (taskbar icon removal via `EnumWindows`/`WS_EX_TOOLWINDOW`, window styling), and `pywebview`'s `edgechromium` backend requires the Windows WebView2 runtime. None of this works on Linux or macOS.

Practical consequence for an agent working from a non-Windows sandbox:

- You **cannot** run `main.py`, launch the app, or visually verify UI/interaction changes yourself.
- `import main` / `import task_store` will fail outside Windows (missing `ctypes.wintypes` members, `webview`, `keyboard` may not install cleanly either). At most, verify Python syntax with `python3 -m py_compile main.py task_store.py` or `ast.parse(...)` — this catches syntax errors only, not runtime or logic errors.
- `index.html` is plain HTML/CSS/JS and can be read and reasoned about statically, but there's no headless harness in this repo to execute it against.
- Treat any non-trivial change as **unverified** until it's actually run on a Windows machine with the app installed. Say so explicitly rather than implying a change has been tested.

## Testing

There is no automated test suite in this repository (no `pytest` files, no JS test harness, no CI workflow under `.github/`). Verification is manual, on Windows, by the app's maintainer.

## Runtime Data (outside the repository)

Not part of the repo, but relevant when reasoning about behavior:

- Tasks: `~/Documents/QuickTasks/` by default (configurable) — one `.md` file per task, YAML frontmatter (`done`, `archived`, `urgent`, `created_at`, `updated_at`, `order_key`), scanned non-recursively (top-level `*.md` only).
- App config: `~/.quicktask/config.json`.
- Metadata cache: `~/.quicktask/cache/<sha1 of the resolved tasks folder path>.sqlite3` — purely derived/disposable, safe to delete; never a source of truth.

## Notable Implementation Details

- **Ordering**: tasks are ordered by a string `order_key` (fractional indexing / LexoRank-lite, see `task_store.py`) stored in each task's own frontmatter. A normal reorder rewrites exactly one file. When keys grow too long, `_rebalance_if_needed` rewrites the `order_key` of *every* task once — this is intentional, not a bug, but means that operation is O(N) by design.
- **Incremental cache**: `TaskStore.list_tasks()` only re-parses `.md` files whose mtime changed since the last look (tracked in the SQLite cache); don't reintroduce a full-folder re-parse on every call.
- **i18n**: `index.html` supports `en`, `ru`, `zh`. Any new user-facing string needs a key in all three blocks of the `I18N` object (`en: {...}`, `ru: {...}`, `zh: {...}`) and either a `data-i18n`/`data-i18n-title`/`data-i18n-placeholder` attribute or a `t('key')` call — don't add a string only to the `en` block.
- **Config vs. version**: `QuickTaskAPI.get_config()` merges `APP_VERSION` into the dict it returns for the UI, but never writes it into `self.config` — `app_version` must never end up persisted into `config.json`.

## Git Notes

- Tags exist for `v0.0.1` and `v1.0.0`; nothing past `v1.0.0` is tagged yet (see `CHANGELOG.md`'s `[Unreleased]` section for what that covers).
- If a git command warns about `.git/index.lock` and being unable to remove it, that's usually a stale lock left behind by the sandboxing/bridge tooling rather than an actual concurrent git operation — confirm no other git process is running before removing it manually.
