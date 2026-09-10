import os
import re
import sys
import json
import time
import threading
import ctypes
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

import webview
from webview.window import FixPoint
import frontmatter
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

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
    "tasks_dir": str(DEFAULT_TASKS_DIR)
}

SUPPORTED_LANGUAGES = ("en", "ru", "zh")

# Bounds for interactively resizing the expanded sidebar by dragging its
# left edge (QuickTaskAPI.resize_sidebar). Intentionally not persisted to
# config.json -- resets to sidebar_width on the next launch, but is kept
# for the rest of the current run (see QuickTaskAPI.session_sidebar_width).
MIN_SIDEBAR_WIDTH = 280
MAX_SIDEBAR_WIDTH = 900

CURRENT_WINDOW: Optional[webview.Window] = None
SCREEN_WIDTH: int = 1920
SCREEN_HEIGHT: int = 1080


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

def sanitize_filename(title: str, max_length: int = 50) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", title).strip()
    cleaned = re.sub(r'[\s_]+', "-", cleaned)
    cleaned = cleaned.strip("-.")
    if not cleaned:
        cleaned = "untitled"
    return cleaned[:max_length].rstrip("-.")

def get_unique_filename(directory: Path, base_name: str, current_path: Optional[Path] = None) -> Path:
    target = directory / f"{base_name}.md"
    if current_path and target.resolve() == current_path.resolve():
        return target
    counter = 1
    while target.exists():
        target = directory / f"{base_name}_{counter}.md"
        counter += 1
    return target


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
    def __init__(self, api_ref):
        super().__init__()
        self.api = api_ref
        self._last_event_time = 0

    def on_any_event(self, event):
        if event.is_directory:
            return
        src_path = getattr(event, "src_path", "")
        dest_path = getattr(event, "dest_path", "")
        if "index.json" in src_path or "index.json" in dest_path:
            return
        if (src_path and src_path.endswith(".md")) or (dest_path and dest_path.endswith(".md")):
            now = time.time()
            if now - self._last_event_time > 0.3:
                self._last_event_time = now
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
        self.index_file = self.tasks_dir / "index.json"
        self._ensure_index_file()
        self.observer: Optional[Observer] = None

    def _ensure_index_file(self):
        try:
            if not self.index_file.exists():
                with open(self.index_file, "w", encoding="utf-8") as f:
                    json.dump([], f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error ensuring index.json: {e}", file=sys.stderr)

    def _load_index_order(self) -> List[str]:
        if self.index_file.exists():
            try:
                with open(self.index_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
            except Exception as e:
                print(f"Error loading index.json: {e}", file=sys.stderr)
        return []

    def _save_index_order(self, order: List[str]):
        try:
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump(order, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving index.json: {e}", file=sys.stderr)

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
        self.index_file = self.tasks_dir / "index.json"
        self._ensure_index_file()
        event_handler = TaskFileHandler(self)
        self.observer = Observer()
        self.observer.schedule(event_handler, str(self.tasks_dir), recursive=False)
        self.observer.daemon = True
        self.observer.start()

    def register_hotkey(self):
        hotkey_combo = self.config.get("hotkey", "ctrl+alt+t")
        if not keyboard:
            return

        def on_hotkey_pressed():
            if not CURRENT_WINDOW:
                return
            if not self.is_expanded:
                self.expand()
            CURRENT_WINDOW.evaluate_js("window.onGlobalHotkeyTriggered && window.onGlobalHotkeyTriggered();")

        try:
            keyboard.add_hotkey(hotkey_combo, on_hotkey_pressed)
        except Exception as e:
            print(f"Failed to register global hotkey '{hotkey_combo}': {e}", file=sys.stderr)

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
        return self.config

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

    def save_tasks_order(self, order: List[str]) -> bool:
        self._save_index_order(order)
        return True

    def get_tasks(self) -> List[Dict[str, Any]]:
        task_dict = {}
        if not self.tasks_dir.exists():
            return []

        for file_path in self.tasks_dir.glob("*.md"):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    post = frontmatter.load(f)
                metadata = post.metadata
                content = post.content

                title = file_path.stem
                body_lines = []
                h1_found = False
                for line in content.splitlines():
                    if not h1_found and line.strip().startswith("# "):
                        title = line.strip()[2:].strip()
                        h1_found = True
                    else:
                        body_lines.append(line)

                body = "\n".join(body_lines).strip()
                task_dict[file_path.name] = {
                    "id": file_path.name,
                    "title": title,
                    "body": body,
                    "done": bool(metadata.get("done", False)),
                    "archived": bool(metadata.get("archived", False)),
                    "urgent": bool(metadata.get("urgent", False)),
                    "created_at": str(metadata.get("created_at", "")),
                    "updated_at": str(metadata.get("updated_at", "")),
                    "metadata": {k: str(v) for k, v in metadata.items()}
                }
            except Exception as e:
                print(f"Error parsing task {file_path}: {e}", file=sys.stderr)

        order = self._load_index_order()
        sorted_tasks = []
        seen = set()

        for file_name in order:
            if file_name in task_dict:
                sorted_tasks.append(task_dict[file_name])
                seen.add(file_name)

        remaining = [t for fid, t in task_dict.items() if fid not in seen]
        remaining.sort(key=lambda t: t.get("updated_at") or t.get("created_at") or "", reverse=True)
        final_list = remaining + sorted_tasks

        new_order = [t["id"] for t in final_list]
        if new_order != order:
            self._save_index_order(new_order)

        return final_list

    def create_task(self, title: str = "New", body: str = "") -> Optional[Dict[str, Any]]:
        now_str = datetime.now().isoformat(timespec="seconds")
        clean_title = title.strip() or "New"
        base_name = sanitize_filename(clean_title)
        file_path = get_unique_filename(self.tasks_dir, base_name)

        post = frontmatter.Post(
            content=f"# {clean_title}\n\n{body}".strip(),
            done=False,
            archived=False,
            urgent=False,
            created_at=now_str,
            updated_at=now_str
        )

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                frontmatter.dump(post, f)
            
            order = self._load_index_order()
            order = [file_path.name] + [fid for fid in order if fid != file_path.name]
            self._save_index_order(order)

            return {
                "id": file_path.name,
                "title": clean_title,
                "body": body,
                "done": False,
                "archived": False,
                "urgent": False,
                "created_at": now_str,
                "updated_at": now_str,
                "metadata": {
                    "done": "False",
                    "archived": "False",
                    "urgent": "False",
                    "created_at": now_str,
                    "updated_at": now_str
                }
            }
        except Exception as e:
            print(f"Error creating task: {e}", file=sys.stderr)
            return None

    def update_task_status(self, task_id: str, done: Optional[bool] = None, archived: Optional[bool] = None, urgent: Optional[bool] = None) -> bool:
        file_path = self.tasks_dir / task_id
        if not file_path.exists():
            return False
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                post = frontmatter.load(f)
            if done is not None:
                post.metadata["done"] = bool(done)
            if archived is not None:
                post.metadata["archived"] = bool(archived)
            if urgent is not None:
                post.metadata["urgent"] = bool(urgent)
            post.metadata["updated_at"] = datetime.now().isoformat(timespec="seconds")

            with open(file_path, "w", encoding="utf-8") as f:
                frontmatter.dump(post, f)
            return True
        except Exception as e:
            print(f"Error updating status for {task_id}: {e}", file=sys.stderr)
            return False

    def update_task_title(self, task_id: str, new_title: str) -> Optional[str]:
        file_path = self.tasks_dir / task_id
        if not file_path.exists():
            return None
        clean_title = new_title.strip() or "Untitled"
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                post = frontmatter.load(f)
            post.metadata["updated_at"] = datetime.now().isoformat(timespec="seconds")

            body_lines = []
            h1_found = False
            for line in post.content.splitlines():
                if not h1_found and line.strip().startswith("# "):
                    h1_found = True
                else:
                    body_lines.append(line)

            post.content = f"# {clean_title}\n\n" + "\n".join(body_lines).strip()

            base_name = sanitize_filename(clean_title)
            new_file_path = get_unique_filename(self.tasks_dir, base_name, file_path)

            if new_file_path.resolve() != file_path.resolve():
                with open(file_path, "w", encoding="utf-8") as f:
                    frontmatter.dump(post, f)
                file_path.rename(new_file_path)

                order = self._load_index_order()
                order = [new_file_path.name if x == file_path.name else x for x in order]
                self._save_index_order(order)
            else:
                with open(file_path, "w", encoding="utf-8") as f:
                    frontmatter.dump(post, f)

            return new_file_path.name
        except Exception as e:
            print(f"Error updating title for {task_id}: {e}", file=sys.stderr)
            return None

    def update_task_full(self, task_id: str, new_title: str, new_body: str) -> Optional[Dict[str, Any]]:
        file_path = self.tasks_dir / task_id
        if not file_path.exists():
            return None
        clean_title = new_title.strip() or "Untitled"
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                post = frontmatter.load(f)
            now_str = datetime.now().isoformat(timespec="seconds")
            post.metadata["updated_at"] = now_str
            post.content = f"# {clean_title}\n\n{new_body.strip()}"

            base_name = sanitize_filename(clean_title)
            new_file_path = get_unique_filename(self.tasks_dir, base_name, file_path)

            if new_file_path.resolve() != file_path.resolve():
                with open(file_path, "w", encoding="utf-8") as f:
                    frontmatter.dump(post, f)
                file_path.rename(new_file_path)

                order = self._load_index_order()
                order = [new_file_path.name if x == file_path.name else x for x in order]
                self._save_index_order(order)
            else:
                with open(file_path, "w", encoding="utf-8") as f:
                    frontmatter.dump(post, f)

            return {
                "id": new_file_path.name,
                "title": clean_title,
                "body": new_body.strip(),
                "updated_at": now_str,
                "metadata": {k: str(v) for k, v in post.metadata.items()}
            }
        except Exception as e:
            print(f"Error updating task full for {task_id}: {e}", file=sys.stderr)
            return None

    def update_task_body(self, task_id: str, new_body: str) -> bool:
        file_path = self.tasks_dir / task_id
        if not file_path.exists():
            return False
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                post = frontmatter.load(f)
            post.metadata["updated_at"] = datetime.now().isoformat(timespec="seconds")

            title = file_path.stem
            for line in post.content.splitlines():
                if line.strip().startswith("# "):
                    title = line.strip()[2:].strip()
                    break

            post.content = f"# {title}\n\n{new_body.strip()}"
            with open(file_path, "w", encoding="utf-8") as f:
                frontmatter.dump(post, f)
            return True
        except Exception as e:
            print(f"Error updating body for {task_id}: {e}", file=sys.stderr)
            return False

    def delete_task(self, task_id: str) -> bool:
        file_path = self.tasks_dir / task_id
        if file_path.exists():
            try:
                file_path.unlink()
                order = self._load_index_order()
                if task_id in order:
                    order.remove(task_id)
                    self._save_index_order(order)
                return True
            except Exception as e:
                print(f"Error deleting task {task_id}: {e}", file=sys.stderr)
        return False


def main():
    global CURRENT_WINDOW, SCREEN_WIDTH, SCREEN_HEIGHT

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
