# Developing and Building QuickTask

This is a technical guide for anyone who wants to run QuickTask from source, tweak the code, or build their own `.exe`. For a general overview of the project and its features, see [README.md](README.md).

---

## Project Structure

- `main.py` — entry point: the window (pywebview), the QuickTaskAPI wiring, watchdog sync, the global hotkey, and hiding the icon from the taskbar.
- `task_store.py` — the task store: frontmatter parsing/writing, a SQLite metadata cache (so the task list isn't rebuilt from scratch out of every `.md` file on every little thing), and fractional sort keys (`order_key` stored in the task's own file — no shared ordering file).
- `index.html` — the entire UI (HTML/CSS/JS in a single file).
- `vendor/codemirror/` — the CodeMirror editor used for the task description. Loaded from `index.html` via a relative path.
- `VERSION` — a text file with the current app version (e.g. `1.1.0`). `main.py` reads it on startup; the value is shown in Settings. Update it before building a release.
- `requirements.txt` — pinned dependency versions.
- `CHANGELOG.md` — the version history (Keep a Changelog format).

---

## Quick Start (Development)

### 1. Clone the repository
```cmd
git clone https://github.com/xelay/QuickTask.git
cd QuickTask
```

### 2. Create a virtual environment
```cmd
python -m venv venv
venv\Scripts\activate
```

### 3. Install dependencies
```cmd
pip install -r requirements.txt
```
*(or manually: `pip install pywebview watchdog keyboard PyYAML`)*

### 4. Run the app
```cmd
venv\Scripts\python.exe main.py
```
*(To run silently without a console window, use `venv\Scripts\pythonw.exe main.py`)*

---

## Building a Standalone EXE

The app can be built into a portable `.exe` that runs on its own without Python installed.

### 1. Install PyInstaller
With the virtual environment active, run:
```cmd
venv\Scripts\pip.exe install pyinstaller
```

### 2. Build the app (recommended `--onedir` mode)
Folder mode gives an instant cold start with no unpacking to a temp directory:
```cmd
venv\Scripts\pyinstaller.exe --noconsole --name "QuickTask" --add-data "index.html;." --add-data "vendor;vendor" --add-data "VERSION;." main.py
```

> The `--add-data "vendor;vendor"` flag is required: `index.html` loads the CodeMirror editor via a relative path (`vendor/codemirror/...`). Without this flag, the task description editor won't load in the built `.exe`.
>
> The `--add-data "VERSION;."` flag is also required: without a `VERSION` file next to the executable, the app will show version `0.0.0` in Settings. Remember to update the contents of the `VERSION` file before building a new release.

- The finished executable and its dependencies will be created in the `dist\QuickTask\` folder.
- Run it: `dist\QuickTask\QuickTask.exe`.

### 3. Build a single file (`--onefile`)
If you need to distribute the app as a single file:
```cmd
venv\Scripts\pyinstaller.exe --noconsole --onefile --name "QuickTask" --add-data "index.html;." --add-data "vendor;vendor" --add-data "VERSION;." main.py
```
The file will be created at `dist\QuickTask.exe`.

### 4. Start with Windows
To have QuickTask launch at system startup:
1. Press `Win + R`, type `shell:startup`, and press `Enter`.
2. Create a shortcut to `QuickTask.exe` in the startup folder that opens.

---

## Technical Notes

- Under the hood, `pywebview` uses the `edgechromium` backend via `pythonnet` — this is the system WebView2 component included with Windows 10/11. No extra .NET or browser engine needs to be installed, but `pythonnet`/`clr_loader` will show up as dependencies automatically.
- `pywebview` ships with its own PyInstaller hook (`webview/__pyinstaller/hook-webview.py`), so no extra `--hidden-import` is needed for it.
- The global hotkey is implemented via the `keyboard` library (`main.py`, `register_hotkey`).

---

## How to Propose Changes

1. Create a separate branch off `main`.
2. Make sure the app runs from source (see "Quick Start" above) and, if possible, that the `--onedir` build succeeds without errors.
3. Add a short entry describing your change to the `[Unreleased]` section of `CHANGELOG.md`.
4. Open a Pull Request with a brief description of what changed and why.
