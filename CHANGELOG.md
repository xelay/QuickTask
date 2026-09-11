# Changelog

All notable changes to QuickTask are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions correspond to git tags where available; changes not yet tagged are
listed under [Unreleased].

## [Unreleased]

## [1.2.0] - 2026-09-11

### Added

- Show the app version in Settings, read from a `VERSION` file shipped with
  the app.
- Interactive hotkey recording in Settings: click "Record..." and press a
  key combination, with validation and a "Disable" option to turn the
  global hotkey off entirely.
- Single-instance guard: launching QuickTask while it's already running no
  longer starts a second copy (which would double up the file watcher and
  fight over the global hotkey) -- it now brings the existing window
  forward and shows a native "QuickTask is already running" popup
  (localized to the configured UI language) before exiting.

### Changed

- Replaced full-folder rescans with an incremental SQLite metadata cache
  keyed by filename + modification time, so listing tasks scales with the
  number of *changed* files instead of the total number of files.
- Replaced the shared `index.json` ordering file with a fractional sort key
  stored in each task's own frontmatter, so reordering one task writes only
  that task's file.
- Task list rendering now patches existing DOM cards in place instead of
  rebuilding the whole list on every update.
- Virtualized the task list so only the cards scrolled into view are
  mounted, keeping DOM size independent of the total number of tasks.

## [1.0.0] - 2026-09-10

### Added

- Global hotkey to open the app from anywhere, configurable in Settings.
- Sidebar resizing by dragging its edge.
- Interface language support (English, Russian, Chinese).
- Markdown editor (CodeMirror) for task descriptions.
- "Open in external editor" action for tasks.

### Fixed

- Quick search filtering across task titles and bodies.
- Window/sidebar scaling issues.

## [0.0.1] - 2026-09-09

### Added

- Initial desktop app: a collapsible sidebar backed by plain Markdown task
  files with YAML frontmatter, synced live via a filesystem watcher.
- Close button, light/dark theme switcher, settings modal, and a centered
  sidebar layout.
- Manual task ordering, an "urgent" flag, and title/frontmatter editing.
- Live search/filter across task titles and bodies.
- Smart auto-collapse: the sidebar collapses on hover-out while unfocused,
  and on window blur once focused.
- Packaging instructions for building a standalone `.exe` with PyInstaller.

### Changed

- Reworked the collapsed handle into a single 36x36 px tab pinned to the
  screen edge, with corrected icon rendering and positioning.
- Hid the app icon from the Windows taskbar and Alt+Tab.

### Fixed

- Crashes caused by pywebview's COM reflection of the JS API bridge object.
