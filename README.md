**English** · [Русский](README.ru.md) · [中文](README.zh-CN.md)

# QuickTask

A notes-and-tasks side panel that lives as an unobtrusive tab at the edge of the screen and expands on click or hotkey.

![QuickTask](screenshots/quick-task.gif)

## Key Features

- A 36×36 px tab at the screen edge that expands into a 380 px sidebar on hover, click, or the `Ctrl+Alt+T` hotkey; collapses automatically when it loses focus.
- Tasks are stored as plain `.md` files with YAML frontmatter (`done`, `archived`, dates) — open them in Obsidian, VS Code, or any text editor.
- External file-change tracking (Watchdog) — edits made outside the app are picked up on the fly.
- A modal task-description editor with Markdown support (CodeMirror) and autosave.
- Dark and light themes, toggled with a single button.
- Stays out of the taskbar and `Alt+Tab` (a ToolWindow-style window).
- Choice of tasks storage folder, adjustable sidebar height, and dragging/pinning (**Pin**) the tab.

## System Requirements

Windows 10 or 11 (64-bit). Uses the system WebView2 component — nothing else needs to be installed.

## Installation

Download `QuickTaskPortable.zip` from the [Releases](https://github.com/xelay/QuickTask/releases) page and run it. Python and other dependencies are not required.

Want to run it from source or build the `.exe` yourself? See [CONTRIBUTING.md](CONTRIBUTING.md).

## Controls

- `Ctrl+Alt+T` — expand the tab into the sidebar and immediately create a new task. The combination can be changed or disabled in Settings.
- Hover over the top part of the tab (the bookmark icon) or click it — the sidebar expands; move the cursor away and it collapses again, unless it's pinned with the **Pin** button.
- Hold the left mouse button on the bottom part of the tab (the "hamburger" icon) and drag up or down — the tab moves along the screen edge.

## Where Data Is Stored

- Tasks — `~/Documents/QuickTasks/` by default (the folder can be changed to any other in Settings).
- App settings — `~/.quicktask/config.json`.
- Internal metadata cache — `~/.quicktask/cache/<hash of the folder path>.sqlite3` (see "Metadata Cache" below).

### What Gets Scanned in the Tasks Folder

QuickTask only looks at `*.md` files sitting directly in the root of the selected folder — it does not recurse into subfolders. Everything else is left untouched and unnoticed:

- subfolders — feel free to keep attachments, images, a by-year archive, an `.obsidian` folder, etc. right alongside your tasks;
- files with any other extension;
- hidden and system files.

### Task File Format

Each task is a plain `.md` file with YAML frontmatter:

```markdown
---
done: false
archived: false
urgent: false
created_at: 2026-09-10T12:00:00
updated_at: 2026-09-10T12:05:00
order_key: a0
---
# Task title

Description text in Markdown.
```

- `done`, `archived`, `urgent` — status flags.
- `created_at` / `updated_at` — timestamps.
- `order_key` — a short string sort key for manual ordering (see below); appears only on tasks that have been manually reordered at least once.
- The task title is the first `# ...` line in the file body; if there isn't one, the filename is used.
- Any other frontmatter fields you add yourself (tags, links, etc.) are left untouched by QuickTask and saved as-is.

### Working Through Obsidian or Any Other Editor

Since tasks are plain `.md` files, the folder can be opened as a vault in Obsidian or in any Markdown-capable editor:

- edits made outside the app (in Obsidian, VS Code, Notepad) are picked up on the fly — the app watches the folder (Watchdog) and only re-reads the files that changed;
- the `done` / `archived` / `urgent` / `created_at` / `updated_at` / `order_key` fields show up in Obsidian as regular Properties; `done`/`archived`/`urgent` are safe to toggle from outside too, while `order_key` and the timestamps are best left untouched by hand;
- renaming the file from outside doesn't break the task — all its information, including its order, lives in the file itself;
- your own extra frontmatter fields and Obsidian tags are left alone by QuickTask and saved as-is.

### Keeping It in a Git Repository

You can safely turn the tasks folder into a git repository — running `git init` in it won't break anything:

- normal actions (creating a task, marking it done, editing it, dragging it in the list) change exactly one `.md` file at a time — there's no shared index file that gets rewritten wholesale anymore;
- you don't need to add anything to `.gitignore`: the cache (`~/.quicktask/cache/`) and settings (`~/.quicktask/config.json`) physically live outside the tasks folder and will never end up in it.

### Metadata Cache (SQLite)

To speed up working with the task list, QuickTask keeps an internal cache — one file, `~/.quicktask/cache/<hash of the folder path>.sqlite3`, per configured tasks folder. This is **not a source of data, just a disposable cache kept for speed**:

- it duplicates data already parsed out of the `.md` files so the whole folder doesn't need to be re-read on every action;
- the `.md` files themselves always remain the source of truth — the cache can be deleted at any time; the app will simply rebuild it the next time it starts;
- there's no need to read or edit this file directly via SQL: changes made that way won't make it back into the `.md` files and will be overwritten the next time the corresponding task changes.

## Contributing

Instructions for running from source, building the `.exe`, and the project structure are in [CONTRIBUTING.md](CONTRIBUTING.md).
