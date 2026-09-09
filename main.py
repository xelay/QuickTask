import os
import re
import sys
import json
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

import webview
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
    "handle_height": 36,
    "window_y": 120,
    "hotkey": "ctrl+alt+t",
    "pinned": False,
    "max_height": None,
    "theme": "dark",
    "tasks_dir": str(DEFAULT_TASKS_DIR)
}

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
        if (src_path and src_path.endswith(".md")) or (dest_path and dest_path.endswith(".md")):
            now = time.time()
            if now - self._last_event_time > 0.3:
                self._last_event_time = now
                self.api.notify_tasks_changed()


class QuickTaskAPI:
    def __init__(self):
        self.window = None
        self.screen_width = 1920
        self.screen_height = 1080
        self.is_expanded = False
        self.config = self.load_config()
        self.tasks_dir = Path(self.config.get("tasks_dir", str(DEFAULT_TASKS_DIR)))
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.observer: Optional[Observer] = None

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
        event_handler = TaskFileHandler(self)
        self.observer = Observer()
        self.observer.schedule(event_handler, str(self.tasks_dir), recursive=False)
        self.observer.daemon = True
        self.observer.start()

    def register_hotkey(self):
        hotkey_combo = self.config.get("hotkey", "ctrl+alt+t")
        if not keyboard:
            print("keyboard module not available", file=sys.stderr)
            return

        def on_hotkey_pressed():
            if not self.window:
                return
            if not self.is_expanded:
                self.expand()
            self.window.evaluate_js("window.onGlobalHotkeyTriggered && window.onGlobalHotkeyTriggered();")

        try:
            keyboard.add_hotkey(hotkey_combo, on_hotkey_pressed)
        except Exception as e:
            print(f"Failed to register global hotkey '{hotkey_combo}': {e}", file=sys.stderr)

    def notify_tasks_changed(self):
        if self.window:
            self.window.evaluate_js("window.refreshTasks && window.refreshTasks();")

    def expand(self):
        if not self.window:
            return
        width = int(self.config.get("sidebar_width", 380))
        max_h = self.config.get("max_height")
        
        if max_h and int(max_h) > 0 and int(max_h) < self.screen_height:
            target_height = int(max_h)
            y = max(0, (self.screen_height - target_height) // 2)
        else:
            target_height = self.screen_height
            y = 0
            
        x = self.screen_width - width
        
        self.window.resize(width, target_height)
        self.window.move(x, y)
        self.is_expanded = True
        self.window.evaluate_js("document.body.classList.remove('collapsed'); document.body.classList.add('expanded');")

    def collapse(self, force: bool = False):
        if not self.window:
            return
        if self.config.get("pinned", False) and not force:
            return
        
        h_width = int(self.config.get("handle_width", 36))
        h_height = int(self.config.get("handle_height", 36))
        x = self.screen_width - h_width
        y = int(self.config.get("window_y", 120))

        self.window.resize(h_width, h_height)
        self.window.move(x, y)
        self.is_expanded = False
        self.window.evaluate_js("document.body.classList.remove('expanded'); document.body.classList.add('collapsed');")

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

        self.save_config()
        if self.is_expanded:
            self.expand()
        return self.config

    def select_tasks_directory(self) -> Optional[str]:
        if not self.window:
            return None
        folder = self.window.create_file_dialog(webview.FOLDER_DIALOG)
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
        if self.window:
            self.window.destroy()
        sys.exit(0)

    def update_window_y(self, delta_y: int) -> int:
        current_y = int(self.config.get("window_y", 120))
        new_y = max(0, min(self.screen_height - int(self.config.get("handle_height", 36)), current_y + delta_y))
        self.config["window_y"] = new_y
        self.save_config()
        if not self.is_expanded and self.window:
            x = self.screen_width - int(self.config.get("handle_width", 36))
            self.window.move(x, new_y)
        return new_y

    def get_tasks(self) -> List[Dict[str, Any]]:
        tasks = []
        if not self.tasks_dir.exists():
            return tasks

        for file_path in self.tasks_dir.glob("*.md"):
            try:
                post = frontmatter.load(str(file_path))
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
                tasks.append({
                    "id": file_path.name,
                    "title": title,
                    "body": body,
                    "done": bool(metadata.get("done", False)),
                    "archived": bool(metadata.get("archived", False)),
                    "created_at": str(metadata.get("created_at", "")),
                    "updated_at": str(metadata.get("updated_at", "")),
                })
            except Exception as e:
                print(f"Error parsing task {file_path}: {e}", file=sys.stderr)

        tasks.sort(key=lambda t: t.get("updated_at") or t.get("created_at") or "", reverse=True)
        return tasks

    def create_task(self, title: str = "Новая задача", body: str = "") -> Optional[Dict[str, Any]]:
        now_str = datetime.now().isoformat(timespec="seconds")
        clean_title = title.strip() or "Новая задача"
        base_name = sanitize_filename(clean_title)
        file_path = get_unique_filename(self.tasks_dir, base_name)

        post = frontmatter.Post(
            content=f"# {clean_title}\n\n{body}".strip(),
            done=False,
            archived=False,
            created_at=now_str,
            updated_at=now_str
        )

        try:
            with open(file_path, "wb") as f:
                frontmatter.dump(post, f)
            return {
                "id": file_path.name,
                "title": clean_title,
                "body": body,
                "done": False,
                "archived": False,
                "created_at": now_str,
                "updated_at": now_str
            }
        except Exception as e:
            print(f"Error creating task: {e}", file=sys.stderr)
            return None

    def update_task_status(self, task_id: str, done: Optional[bool] = None, archived: Optional[bool] = None) -> bool:
        file_path = self.tasks_dir / task_id
        if not file_path.exists():
            return False
        try:
            post = frontmatter.load(str(file_path))
            if done is not None:
                post.metadata["done"] = bool(done)
            if archived is not None:
                post.metadata["archived"] = bool(archived)
            post.metadata["updated_at"] = datetime.now().isoformat(timespec="seconds")

            with open(file_path, "wb") as f:
                frontmatter.dump(post, f)
            return True
        except Exception as e:
            print(f"Error updating status for {task_id}: {e}", file=sys.stderr)
            return False

    def update_task_title(self, task_id: str, new_title: str) -> Optional[str]:
        file_path = self.tasks_dir / task_id
        if not file_path.exists():
            return None
        clean_title = new_title.strip() or "Без названия"
        try:
            post = frontmatter.load(str(file_path))
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
                with open(file_path, "wb") as f:
                    frontmatter.dump(post, f)
                file_path.rename(new_file_path)
            else:
                with open(file_path, "wb") as f:
                    frontmatter.dump(post, f)

            return new_file_path.name
        except Exception as e:
            print(f"Error updating title for {task_id}: {e}", file=sys.stderr)
            return None

    def update_task_body(self, task_id: str, new_body: str) -> bool:
        file_path = self.tasks_dir / task_id
        if not file_path.exists():
            return False
        try:
            post = frontmatter.load(str(file_path))
            post.metadata["updated_at"] = datetime.now().isoformat(timespec="seconds")

            title = file_path.stem
            for line in post.content.splitlines():
                if line.strip().startswith("# "):
                    title = line.strip()[2:].strip()
                    break

            post.content = f"# {title}\n\n{new_body.strip()}"
            with open(file_path, "wb") as f:
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
                return True
            except Exception as e:
                print(f"Error deleting task {task_id}: {e}", file=sys.stderr)
        return False


def main():
    api = QuickTaskAPI()
    html_path = Path(__file__).parent / "index.html"
    initial_y = api.config.get("window_y", 120)
    h_width = int(api.config.get("handle_width", 36))
    h_height = int(api.config.get("handle_height", 36))

    window = webview.create_window(
        title="QuickTask",
        url=str(html_path.resolve()),
        js_api=api,
        width=h_width,
        height=h_height,
        x=api.screen_width - h_width,
        y=initial_y,
        frameless=True,
        on_top=True,
        resizable=False,
        easy_drag=False
    )
    api.window = window

    def on_started():
        try:
            screens = webview.screens
            if screens:
                primary = screens[0]
                api.screen_width = primary.width
                api.screen_height = primary.height
        except Exception:
            pass
        api.start_watcher()
        api.register_hotkey()

    webview.start(on_started, debug=False)

if __name__ == "__main__":
    main()
