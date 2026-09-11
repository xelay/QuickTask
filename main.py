import json
import os
import sys
import time
import threading
import ctypes
import subprocess
from ctypes import wintypes
from pathlib import Path
from typing import Optional, Dict, Any, List

import webview
from webview.window import FixPoint
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from task_store import TaskStore

try:
    import keyboard
except ImportError:
    keyboard = None

CONFIG_DIR = Path.home() / ".quicktask"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_TASKS_DIR = Path.home() / "Documents" / "QuickTasks"

DEFAULT_CONFIG = {
    "sidebar_width": 380,
    "handle_width": 36,
    "handle_total_height": 100,
    "window_y": 120,
    "hotkey": "ctrl+alt+t",
    "pinned": False,
    "max_height": None,
    "theme": "dark",
    "language": "en",
    "tasks_dir": str(DEFAULT_TASKS_DIR),
    # Path to an external editor executable. Empty string means:
    # open the task's .md file with whatever application Windows has
    # associated with the .md extension (see open_external_editor()).
    "external_editor": ""
}

SUPPORTED_LANGUAGES = ("en", "ru", "zh")


def load_app_version() -> str:
    """Read the app version from the VERSION file shipped next to this script.

    Falls back to "0.0.0" if the file is missing or unreadable (e.g. a dev
    checkout without the file, or a packaging step that forgot to include
    it) so the UI always has something to show.
    """
    version_file = Path(__file__).resolve().parent / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip() or "0.0.0"
    except Exception:
        return "0.0.0"


APP_VERSION = load_app_version()

# Modifier tokens recognized by the `keyboard` library (see
# keyboard._canonical_names -- "win" is aliased to "windows" there).
HOTKEY_MODIFIER_NAMES = {"ctrl", "alt", "shift", "win"}


def validate_hotkey_combo(combo: str) -> Optional[str]:
    """Return an error code for an invalid hotkey combo, or None if OK.

    Requires at least one modifier (ctrl/alt/shift/win) plus exactly one
    non-modifier key, with no repeated parts. Error codes are mapped to
    localized messages on the JS side.
    """
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        return "empty"
    if len(set(parts)) != len(parts):
        return "duplicate_key"
    modifiers = [p for p in parts if p in HOTKEY_MODIFIER_NAMES]
    non_modifiers = [p for p in parts if p not in HOTKEY_MODIFIER_NAMES]
    if not modifiers:
        return "no_modifier"
    if len(non_modifiers) != 1:
        return "needs_one_key"
    return None

# Bounds for interactively resizing the expanded sidebar by dragging its
# left edge (QuickTaskAPI.resize_sidebar). Intentionally not persisted to
# config.json -- resets to sidebar_width on the next launch, but is kept
# for the rest of the current run (see QuickTaskAPI.session_sidebar_width).
MIN_SIDEBAR_WIDTH = 280
MAX_SIDEBAR_WIDTH = 900

CURRENT_WINDOW: Optional[webview.Window] = None
SCREEN_WIDTH: int = 1920
SCREEN_HEIGHT: int = 1080

# --- Single-instance guard (Windows) ---------------------------------------
# QuickTask is a small always-on-top sidebar that watches a tasks folder and
# registers a global hotkey. Running a second copy would start a second
# watcher on the same folder and fight over the same hotkey combo instead of
# doing anything useful, so we detect an existing instance via a named Win32
# mutex and, if found, just bring that instance forward instead of starting
# a duplicate.
_SINGLE_INSTANCE_MUTEX_NAME = "QuickTask_SingleInstance_9f3d2b7c-9e2a-4b7a-8e2b-6d1a7a2f5c31"
_single_instance_mutex = None  # kept alive for the process lifetime; GC'ing/closing it releases the lock


def _safe_stderr(message: str):
    """print() to stderr without ever raising.

    A --noconsole/--windowed PyInstaller build (see AGENTS.md's build
    command) has no console, and depending on the PyInstaller version
    sys.stderr can end up as None there -- a plain print(..., file=sys.stderr)
    would then raise AttributeError. That would matter a lot right here:
    unlike this file's other scattered error prints (rare edge cases), the
    single-instance message below runs on every single duplicate launch, so
    a crash here would surface as a visible "stopped working" dialog every
    time someone double-clicks the app while it's already open.
    """
    try:
        print(message, file=sys.stderr)
    except Exception:
        pass


def acquire_single_instance_lock() -> bool:
    """Return True if this is the only running instance (and hold the lock).

    Uses a named kernel mutex: CreateMutexW returns a handle whether or not
    the name already existed, but GetLastError() tells us which case we're
    in. The handle is stashed in a module global (not just left as a local)
    so the lock is held for as long as this process is alive -- closing it
    or letting it get garbage-collected would release the name and defeat
    the whole point.

    No-op (always returns True) on non-Windows platforms: multi-instance
    isn't something we currently guard against there.
    """
    global _single_instance_mutex

    if sys.platform != "win32":
        return True

    ERROR_ALREADY_EXISTS = 183
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
    if not handle:
        # Creating the mutex itself failed (very unlikely) -- fail open
        # rather than blocking the user from starting the app at all.
        return True

    _single_instance_mutex = handle
    already_running = ctypes.GetLastError() == ERROR_ALREADY_EXISTS
    return not already_running


def focus_existing_instance():
    """Best-effort: bring the already-running instance's window to the front.

    QuickTask's window is created with a fixed title ("QuickTask" -- see
    webview.create_window() in main()), so FindWindowW can locate it even
    though the window is frameless and has no visible caption.
    """
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, "QuickTask")
        if hwnd:
            SW_SHOWNA = 8  # show without activating layout/z-order changes, then explicitly focus below
            ctypes.windll.user32.ShowWindow(hwnd, SW_SHOWNA)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception as e:
        _safe_stderr(f"Error focusing existing instance: {e}")


def _read_config_language() -> str:
    """Best-effort read of the configured UI language, straight from disk.

    Needed only for show_already_running_message(): that fires before
    QuickTaskAPI (which normally owns config loading) is ever constructed
    for this process, since we're about to exit without doing anything
    else. Falls back to "en" -- same default as DEFAULT_CONFIG -- on any
    problem (missing/corrupt config, unsupported value, etc.).
    """
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        language = data.get("language")
        if language in SUPPORTED_LANGUAGES:
            return language
    except Exception:
        pass
    return "en"


def show_already_running_message():
    """Native "already running" popup shown on a duplicate launch.

    Uses MessageBoxW directly rather than anything webview-based: at this
    point in main() no window/webview runtime has been created for this
    (second, about-to-exit) process, so there's nothing to show it in.
    MB_SETFOREGROUND + MB_TOPMOST keep it from getting lost behind the
    existing instance's own always-on-top window, which
    focus_existing_instance() just raised.
    """
    if sys.platform != "win32":
        return

    messages = {
        "en": "QuickTask is already running.",
        "ru": "QuickTask \u0443\u0436\u0435 \u0437\u0430\u043f\u0443\u0449\u0435\u043d.",
        "zh": "QuickTask \u5df2\u7ecf\u5728\u8fd0\u884c\u3002",
    }
    message = messages.get(_read_config_language(), messages["en"])

    MB_OK = 0x00000000
    MB_ICONINFORMATION = 0x00000040
    MB_TOPMOST = 0x00040000
    MB_SETFOREGROUND = 0x00010000
    try:
        ctypes.windll.user32.MessageBoxW(
            None, message, "QuickTask",
            MB_OK | MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND
        )
    except Exception as e:
        _safe_stderr(f"Error showing already-running message box: {e}")


def get_logical_screen_size() -> "tuple[int, int]":
    """
    Best-effort *logical*-pixel size of the primary monitor, in the same
    coordinate space pywebview itself expects.

    pywebview (winforms/WebView2 backend) treats every x/y/width/height you
    pass to create_window()/move()/resize(), and every value it reports via
    webview.screens, as LOGICAL pixels (96 DPI baseline) -- it multiplies
    them internally by the window's real DPI scale (GetDpiForWindow()/96)
    to get physical pixels for the actual Win32 window. See
    BrowserForm.__init__ / BrowserForm.move() in
    webview/platforms/winforms.py.

    Before this process (or pywebview) declares any DPI awareness,
    GetSystemMetrics(SM_CXSCREEN/Y) returns Windows' DPI-virtualized
    ("logical") screen size -- exactly the unit pywebview wants here. This
    is why we must NOT call SetProcessDpiAwareness*/Context anywhere in
    this file: doing so would make this call return *physical* pixels
    instead, which pywebview would then scale a second time, pushing the
    window off the right edge of the screen on any display scaled above
    100% (this was the actual cause of the positioning bug).

    Falls back to the current SCREEN_WIDTH/SCREEN_HEIGHT globals if the
    WinAPI call is unavailable.
    """
    if sys.platform == "win32":
        try:
            width = ctypes.windll.user32.GetSystemMetrics(0)   # SM_CXSCREEN
            height = ctypes.windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN
            if width > 0 and height > 0:
                return width, height
        except Exception:
            pass
    return SCREEN_WIDTH, SCREEN_HEIGHT


def remove_taskbar_icon():
    """
    Guaranteed removal of frameless window from Windows taskbar:
    Enumerate visible windows belonging to current process (PID)
    and set WS_EX_TOOLWINDOW with SWP_FRAMECHANGED.
    """
    if sys.platform != "win32":
        return

    def _worker():
        time.sleep(0.3)
        try:
            current_pid = os.getpid()
            found_hwnds = []
            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def enum_cb(hwnd, lparam):
                if ctypes.windll.user32.IsWindowVisible(hwnd):
                    pid = wintypes.DWORD()
                    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value == current_pid:
                        found_hwnds.append(hwnd)
                return True

            ctypes.windll.user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW = 0x00040000
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020

            for hwnd in found_hwnds:
                style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                new_style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
                ctypes.windll.user32.SetWindowPos(
                    hwnd, 0, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED
                )
        except Exception as e:
            print(f"Error hiding taskbar icon: {e}", file=sys.stderr)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


class TaskFileHandler(FileSystemEventHandler):
    """
    Forwards individual filesystem changes to the TaskStore so exactly the
    file that changed gets re-parsed (see task_store.TaskStore.refresh_file)
    -- not the whole folder. The 0.3s debounce below only throttles how
    often the JS side is told "something changed, ask for the list again";
    it never delays updating the underlying cache itself, so the app's own
    next get_tasks() call is always correct even if a notify was skipped.
    """

    def __init__(self, api_ref):
        super().__init__()
        self.api = api_ref
        self._last_notify_time = 0

    def _handle_path(self, path: str) -> bool:
        if not path or not path.endswith(".md"):
            return False
        name = os.path.basename(path)
        if os.path.exists(path):
            self.api.store.refresh_file(name)
        else:
            self.api.store.forget_file(name)
        return True

    def on_any_event(self, event):
        if event.is_directory:
            return
        src_path = getattr(event, "src_path", "") or ""
        dest_path = getattr(event, "dest_path", "") or ""
        if "index.json" in src_path or "index.json" in dest_path:
            return

        touched = False
        for path in (src_path, dest_path):
            if self._handle_path(path):
                touched = True
        if not touched:
            return

        now = time.time()
        if now - self._last_notify_time > 0.3:
            self._last_notify_time = now
            self.api.notify_tasks_changed()


class QuickTaskAPI:
    def __init__(self):
        self.is_expanded = False
        # Session-only sidebar width set by dragging the left edge
        # (resize_sidebar). None means "use config['sidebar_width']".
        # Deliberately never written to config.json: resets on next launch.
        self.session_sidebar_width: Optional[int] = None
        self.config = self.load_config()
        self.tasks_dir = Path(self.config.get("tasks_dir", str(DEFAULT_TASKS_DIR)))
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.store = TaskStore(self.tasks_dir)
        self.observer: Optional[Observer] = None
        # Combo string currently registered with the `keyboard` library, if
        # any. Tracked separately from config["hotkey"] so register_hotkey()
        # can cleanly unhook the previous combo before hooking a new one.
        self.registered_hotkey: Optional[str] = None

    def load_config(self) -> Dict[str, Any]:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    cfg = DEFAULT_CONFIG.copy()
                    cfg.update(data)
                    return cfg
            except Exception as e:
                print(f"Error loading config: {e}", file=sys.stderr)
        return DEFAULT_CONFIG.copy()

    def save_config(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving config: {e}", file=sys.stderr)

    def start_watcher(self):
        if self.observer:
            try:
                self.observer.stop()
                self.observer.join()
            except Exception:
                pass
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.store = TaskStore(self.tasks_dir)
        event_handler = TaskFileHandler(self)
        self.observer = Observer()
        self.observer.schedule(event_handler, str(self.tasks_dir), recursive=False)
        self.observer.daemon = True
        self.observer.start()

    def register_hotkey(self) -> bool:
        """(Re)register the global hotkey from the current config.

        Safe to call repeatedly: any previously registered combo is
        unhooked first. An empty/missing "hotkey" in config means the
        global hotkey is intentionally disabled -- that is treated as
        success. Returns False only when a non-empty combo failed to
        register with the OS (e.g. reserved by another application).
        """
        if not keyboard:
            return False

        if self.registered_hotkey:
            try:
                keyboard.remove_hotkey(self.registered_hotkey)
            except (KeyError, ValueError):
                pass
            self.registered_hotkey = None

        hotkey_combo = str(self.config.get("hotkey", "") or "").strip()
        if not hotkey_combo:
            return True

        def on_hotkey_pressed():
            if not CURRENT_WINDOW:
                return
            if not self.is_expanded:
                self.expand()
            CURRENT_WINDOW.evaluate_js("window.onGlobalHotkeyTriggered && window.onGlobalHotkeyTriggered();")

        try:
            keyboard.add_hotkey(hotkey_combo, on_hotkey_pressed)
            self.registered_hotkey = hotkey_combo
            return True
        except Exception as e:
            print(f"Failed to register global hotkey '{hotkey_combo}': {e}", file=sys.stderr)
            return False

    def set_hotkey(self, combo: str) -> Dict[str, Any]:
        """Validate, apply and persist a new global hotkey from the UI.

        Applies immediately (no restart needed): on success the new combo
        is hooked right away and saved to config. On failure nothing is
        saved and the previously working hotkey (if any) is re-hooked, so
        the app is never left without the hotkey the user had before.
        Pass an empty string to disable the global hotkey entirely.
        """
        combo = str(combo or "").strip().lower()

        if not combo:
            self.config["hotkey"] = ""
            self.register_hotkey()
            self.save_config()
            return {"success": True, "hotkey": ""}

        if not keyboard:
            return {"success": False, "error": "keyboard_unavailable", "hotkey": self.config.get("hotkey", "")}

        error = validate_hotkey_combo(combo)
        if error:
            return {"success": False, "error": error, "hotkey": self.config.get("hotkey", "")}

        previous = self.config.get("hotkey", "")
        self.config["hotkey"] = combo
        if not self.register_hotkey():
            self.config["hotkey"] = previous
            self.register_hotkey()
            return {"success": False, "error": "register_failed", "hotkey": previous}

        self.save_config()
        return {"success": True, "hotkey": combo}

    def notify_tasks_changed(self):
        if CURRENT_WINDOW:
            CURRENT_WINDOW.evaluate_js("window.refreshTasks && window.refreshTasks();")

    def expand(self):
        if not CURRENT_WINDOW:
            return
        width = int(self.session_sidebar_width or self.config.get("sidebar_width", 380))
        max_h = self.config.get("max_height")

        if max_h and int(max_h) > 0 and int(max_h) < SCREEN_HEIGHT:
            target_height = int(max_h)
            y = max(0, (SCREEN_HEIGHT - target_height) // 2)
        else:
            target_height = SCREEN_HEIGHT
            y = 0

        x = SCREEN_WIDTH - width

        CURRENT_WINDOW.resize(width, target_height)
        CURRENT_WINDOW.move(x, y)
        self.is_expanded = True
        CURRENT_WINDOW.evaluate_js("document.body.classList.remove('collapsed'); document.body.classList.add('expanded');")

    def collapse(self, force: bool = False):
        if not CURRENT_WINDOW:
            return
        if self.config.get("pinned", False) and not force:
            return

        h_width = int(self.config.get("handle_width", 36))
        total_h = int(self.config.get("handle_total_height", 100))
        x = SCREEN_WIDTH - h_width
        y = int(self.config.get("window_y", 120))

        CURRENT_WINDOW.resize(h_width, total_h)
        CURRENT_WINDOW.move(x, y)
        self.is_expanded = False
        CURRENT_WINDOW.evaluate_js("document.body.classList.remove('expanded'); document.body.classList.add('collapsed');")

    def toggle_pin(self) -> bool:
        new_state = not self.config.get("pinned", False)
        self.config["pinned"] = new_state
        self.save_config()
        return new_state

    def get_config(self) -> Dict[str, Any]:
        # app_version is intentionally not merged into self.config: it is
        # not a user setting and must never be written to config.json by
        # save_config().
        return {**self.config, "app_version": APP_VERSION}

    def set_theme(self, theme_name: str) -> str:
        self.config["theme"] = "light" if theme_name == "light" else "dark"
        self.save_config()
        return self.config["theme"]

    def set_language(self, lang_code: str) -> str:
        self.config["language"] = lang_code if lang_code in SUPPORTED_LANGUAGES else "en"
        self.save_config()
        return self.config["language"]

    def save_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        if "max_height" in settings:
            val = settings["max_height"]
            self.config["max_height"] = int(val) if val and str(val).isdigit() and int(val) > 0 else None

        if "tasks_dir" in settings and settings["tasks_dir"]:
            new_path = Path(settings["tasks_dir"]).expanduser().resolve()
            if new_path != self.tasks_dir:
                self.tasks_dir = new_path
                self.config["tasks_dir"] = str(new_path)
                self.start_watcher()

        if "theme" in settings:
            self.config["theme"] = settings["theme"]

        if "language" in settings and settings["language"] in SUPPORTED_LANGUAGES:
            self.config["language"] = settings["language"]

        if "external_editor" in settings:
            self.config["external_editor"] = str(settings["external_editor"] or "").strip()

        self.save_config()
        if self.is_expanded:
            self.expand()
        return self.config

    def select_tasks_directory(self) -> Optional[str]:
        if not CURRENT_WINDOW:
            return None
        folder = CURRENT_WINDOW.create_file_dialog(webview.FOLDER_DIALOG)
        if folder and len(folder) > 0:
            chosen = folder[0]
            return str(chosen)
        return None

    def select_external_editor_path(self) -> Optional[str]:
        if not CURRENT_WINDOW:
            return None
        result = CURRENT_WINDOW.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("Executable files (*.exe)", "All files (*.*)"),
        )
        if result and len(result) > 0:
            return str(result[0])
        return None

    def close_app(self):
        if self.observer:
            try:
                self.observer.stop()
            except Exception:
                pass
        if CURRENT_WINDOW:
            CURRENT_WINDOW.destroy()
        sys.exit(0)

    def update_window_y(self, delta_y: int) -> int:
        current_y = int(self.config.get("window_y", 120))
        total_h = int(self.config.get("handle_total_height", 100))
        new_y = max(0, min(SCREEN_HEIGHT - total_h, current_y + delta_y))
        self.config["window_y"] = new_y
        self.save_config()
        if not self.is_expanded and CURRENT_WINDOW:
            x = SCREEN_WIDTH - int(self.config.get("handle_width", 36))
            CURRENT_WINDOW.move(x, new_y)
        return new_y

    def resize_sidebar(self, delta_x: int) -> int:
        """
        Resize the expanded sidebar by dragging its left edge.

        delta_x is the incremental mouse movement along X since the last
        call (positive = pointer moved right, negative = moved left).
        Moving the handle left should grow the sidebar, so width changes
        by -delta_x. The window's right edge stays put via
        FixPoint.EAST (pywebview keeps that edge fixed and grows/shrinks
        toward the left) -- see BrowserForm.resize() in
        webview/platforms/winforms.py.

        The resulting width is kept only in memory (session_sidebar_width)
        and is NOT written to config.json: it resets to the configured
        default on the next launch, but persists across collapse/expand
        within the current run.
        """
        current_width = int(self.session_sidebar_width or self.config.get("sidebar_width", 380))
        new_width = current_width - int(delta_x)
        new_width = max(MIN_SIDEBAR_WIDTH, min(MAX_SIDEBAR_WIDTH, new_width, SCREEN_WIDTH))

        if not CURRENT_WINDOW or not self.is_expanded:
            self.session_sidebar_width = new_width
            return new_width

        max_h = self.config.get("max_height")
        if max_h and int(max_h) > 0 and int(max_h) < SCREEN_HEIGHT:
            target_height = int(max_h)
        else:
            target_height = SCREEN_HEIGHT

        CURRENT_WINDOW.resize(new_width, target_height, FixPoint.EAST)
        self.session_sidebar_width = new_width
        return new_width

    def get_tasks(self) -> List[Dict[str, Any]]:
        return self.store.list_tasks()

    def create_task(self, title: str = "New", body: str = "") -> Optional[Dict[str, Any]]:
        try:
            return self.store.create_task(title, body)
        except Exception as e:
            print(f"Error creating task: {e}", file=sys.stderr)
            return None

    def update_task_status(self, task_id: str, done: Optional[bool] = None,
                            archived: Optional[bool] = None, urgent: Optional[bool] = None) -> bool:
        try:
            return self.store.update_task_status(task_id, done, archived, urgent)
        except Exception as e:
            print(f"Error updating status for {task_id}: {e}", file=sys.stderr)
            return False

    def update_task_title(self, task_id: str, new_title: str) -> Optional[str]:
        try:
            return self.store.update_task_title(task_id, new_title)
        except Exception as e:
            print(f"Error updating title for {task_id}: {e}", file=sys.stderr)
            return None

    def update_task_full(self, task_id: str, new_title: str, new_body: str) -> Optional[Dict[str, Any]]:
        try:
            return self.store.update_task_full(task_id, new_title, new_body)
        except Exception as e:
            print(f"Error updating task full for {task_id}: {e}", file=sys.stderr)
            return None

    def update_task_body(self, task_id: str, new_body: str) -> bool:
        try:
            return self.store.update_task_body(task_id, new_body)
        except Exception as e:
            print(f"Error updating body for {task_id}: {e}", file=sys.stderr)
            return False

    def reorder_task(self, task_id: str, before_id: Optional[str] = None, after_id: Optional[str] = None) -> bool:
        """Move task_id to sit right after before_id and right before
        after_id (either may be None/omitted for "start"/"end" of the
        list). Called by the sidebar's drag-and-drop after a card is
        dropped, with its new immediate neighbours' ids -- writes exactly
        one file no matter how many tasks exist (see task_store.py)."""
        try:
            return self.store.reorder_task(task_id, before_id, after_id)
        except Exception as e:
            print(f"Error reordering {task_id}: {e}", file=sys.stderr)
            return False

    def open_external_editor(self, task_id: str) -> Dict[str, Any]:
        """
        Open the task's underlying .md file in an external editor.

        Uses config["external_editor"] (a path to an editor executable,
        set in Settings) when configured; otherwise falls back to
        os.startfile(), which opens the file with whatever application
        Windows has associated with the .md extension -- the same as
        double-clicking the file in Explorer.
        """
        file_path = self.tasks_dir / task_id
        if not file_path.exists():
            return {"success": False, "error": "file_not_found"}

        editor_path = str(self.config.get("external_editor") or "").strip()

        if editor_path:
            try:
                subprocess.Popen([editor_path, str(file_path)])
                return {"success": True}
            except Exception as e:
                print(f"Failed to launch configured external editor '{editor_path}': {e}", file=sys.stderr)
                # Fall through to the system default below.

        try:
            if sys.platform == "win32":
                os.startfile(str(file_path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(file_path)])
            else:
                subprocess.Popen(["xdg-open", str(file_path)])
            return {"success": True}
        except Exception as e:
            print(f"Failed to open external editor for {file_path}: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}

    def delete_task(self, task_id: str) -> bool:
        try:
            return self.store.delete_task(task_id)
        except Exception as e:
            print(f"Error deleting task {task_id}: {e}", file=sys.stderr)
            return False


def main():
    global CURRENT_WINDOW, SCREEN_WIDTH, SCREEN_HEIGHT

    if not acquire_single_instance_lock():
        _safe_stderr("QuickTask is already running -- focusing the existing window and showing a popup instead of starting a second copy.")
        focus_existing_instance()
        show_already_running_message()
        return

    SCREEN_WIDTH, SCREEN_HEIGHT = get_logical_screen_size()

    api = QuickTaskAPI()
    html_path = Path(__file__).parent / "index.html"
    initial_y = api.config.get("window_y", 120)
    h_width = 36
    total_h = int(api.config.get("handle_total_height", 100))

    CURRENT_WINDOW = webview.create_window(
        title="QuickTask",
        url=str(html_path.resolve()),
        js_api=api,
        width=h_width,
        height=total_h,
        x=SCREEN_WIDTH - h_width,
        y=initial_y,
        frameless=True,
        on_top=True,
        resizable=False,
        easy_drag=False
    )

    def on_started():
        global SCREEN_WIDTH, SCREEN_HEIGHT
        try:
            screens = webview.screens
            if screens:
                primary = screens[0]
                if primary.width and primary.height:
                    SCREEN_WIDTH = primary.width
                    SCREEN_HEIGHT = primary.height
        except Exception:
            pass

        # The pre-start guess above (get_logical_screen_size) can be off
        # (process launched on a different monitor/DPI than where the
        # window ends up, work-area edge cases, etc.). Re-apply the now
        # -authoritative screen size immediately so the window is
        # guaranteed to land in the right place from the start, instead
        # of waiting for the user to trigger expand()/collapse() manually.
        try:
            api.collapse(force=True)
        except Exception as e:
            print(f"Error re-positioning window on startup: {e}", file=sys.stderr)

        remove_taskbar_icon()
        api.start_watcher()
        api.register_hotkey()

    webview.start(on_started, debug=False)

if __name__ == "__main__":
    main()
