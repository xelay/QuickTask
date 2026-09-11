"""
task_store.py -- persistent, incrementally-updated storage layer for QuickTask.

Why this exists (see the perf investigation that preceded it):

  * The old QuickTaskAPI.get_tasks() opened and YAML-parsed every .md file in
    the tasks folder on every single call -- including calls triggered by the
    app's own writes (via the Watchdog observer). With N tasks, every click
    cost O(N) disk I/O and O(N) YAML parses, no matter how small the actual
    change was.

  * Task order lived in one big index.json array, rewritten in full on every
    reorder -- another O(N) write per drag-and-drop, however many tasks exist.

This module fixes both:

  * TaskCache is a small SQLite database (kept outside the tasks folder, in
    the app's config dir -- a task folder full of .md notes should stay
    exactly that) that remembers, per file, the mtime QuickTask last saw and
    the metadata/body it parsed at that mtime. Listing tasks means: stat every
    file (cheap -- a single os.scandir, no per-file open), and only re-open
    and re-parse the ones whose mtime actually moved. Cost is O(files
    changed since last look), not O(total files).

  * Order is a property of each task: an `order_key` field written straight
    into that task's own frontmatter. Keys are short strings from a
    lexicographic "fractional indexing" scheme (the technique behind Jira's
    LexoRank): moving one task to a new position only ever assigns ONE new
    key to that ONE file. Nothing else is read or rewritten.
"""
import json
import os
import re
import sqlite3
import threading
from datetime import datetime
from hashlib import sha1
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

CONFIG_DIR = Path.home() / ".quicktask"
CACHE_DIR = CONFIG_DIR / "cache"


# ---------------------------------------------------------------------------
# Minimal frontmatter (YAML front matter) parsing.
#
# Deliberately not the python-frontmatter package: this is the ~10 lines we
# actually used from it. Keeping it in-house drops an external dependency
# and means the on-disk format is fully specified here. It reads files
# written by python-frontmatter without any change (same "---"-delimited
# block), so existing task folders keep working unmodified.
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?\r?\n)---[ \t]*\r?\n?(.*)\Z", re.DOTALL)


def read_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    yaml_block, content = match.groups()
    try:
        metadata = yaml.safe_load(yaml_block)
    except yaml.YAMLError:
        metadata = None
    if not isinstance(metadata, dict):
        metadata = {}
    return metadata, content.lstrip("\n")


def write_frontmatter(metadata: Dict[str, Any], content: str) -> str:
    yaml_block = yaml.safe_dump(metadata, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return f"---\n{yaml_block}---\n\n{content}"


# ---------------------------------------------------------------------------
# Order keys ("fractional indexing" / LexoRank-lite).
#
# Keys are plain strings over a 62-character alphabet chosen so that Python's
# (and JavaScript's) ordinary string comparison ("<") matches the intended
# task order. key_between(lo, hi) returns a new key that sorts strictly
# after `lo` and strictly before `hi`; lo=None means "before everything",
# hi=None means "after everything". Moving a task = compute one new key for
# it from its two new neighbours' keys; nothing else changes.
# ---------------------------------------------------------------------------

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_BASE = len(_ALPHABET)
_DIGIT_VALUE = {ch: i for i, ch in enumerate(_ALPHABET)}


def _digit_at(key: str, i: int) -> int:
    return _DIGIT_VALUE[key[i]] if i < len(key) else 0


_MAX_KEY_DEPTH = 200  # generous safety net; real keys stay a handful of chars


def key_between(lo: Optional[str], hi: Optional[str]) -> str:
    """A key that sorts strictly after `lo` and strictly before `hi`.

    lo=None means "before everything"; hi=None means "after everything".
    Requires lo < hi (as strings) when both are given.

    Note on why this terminates for every key our own scheme produces: the
    digit this function appends just before it returns is always strictly
    greater than lo's digit at that position (never equal), so a generated
    key never ends in the alphabet's zero digit ('0'). That in turn means a
    later call can never find hi to be an exhausted prefix of a longer lo
    while still "matching" -- the one situation that would otherwise leave
    no room. Hand-written/foreign keys are not guaranteed this property,
    hence the depth guard below rather than a silent infinite loop.
    """
    if lo is not None and hi is not None and lo >= hi:
        raise ValueError(f"key_between requires lo < hi, got lo={lo!r} hi={hi!r}")

    lo = lo or ""
    digits: List[str] = []
    i = 0
    while True:
        if i > _MAX_KEY_DEPTH:
            raise RuntimeError(
                f"no room found between {lo!r} and {hi!r} within {_MAX_KEY_DEPTH} digits "
                "(likely a hand-edited order_key touching the alphabet's boundary)"
            )
        d_lo = _digit_at(lo, i)
        d_hi = _digit_at(hi, i) if hi is not None and i < len(hi) else _BASE

        if d_lo == d_hi:
            digits.append(_ALPHABET[d_lo])
            i += 1
            continue

        if d_hi - d_lo > 1:
            digits.append(_ALPHABET[d_lo + (d_hi - d_lo) // 2])
            return "".join(digits)

        # Adjacent digits (d_hi - d_lo == 1): no room at this position. Keep
        # lo's digit -- the result's prefix so far still equals lo's, and is
        # now provably < hi from here on, since it diverges from hi one
        # digit below hi at the first position where they differ -- and
        # look for room one level deeper against lo alone.
        digits.append(_ALPHABET[d_lo])
        i += 1


def initial_key() -> str:
    return key_between(None, None)


def key_before(hi: Optional[str]) -> str:
    return key_between(None, hi)


def key_after(lo: Optional[str]) -> str:
    return key_between(lo, None)


def _encode_fixed_width(value: int, width: int) -> str:
    digits = []
    for _ in range(width):
        digits.append(_ALPHABET[value % _BASE])
        value //= _BASE
    return "".join(reversed(digits))


def bulk_keys_between(lo: Optional[str], hi: Optional[str], n: int) -> List[str]:
    """n strictly increasing keys, all strictly between lo and hi, evenly
    spread across the available room.

    Use this instead of calling key_between()/key_after() n times in a row
    to seed a whole KNOWN list at once (a migration, or a folder full of
    pre-existing files that have never had an order_key). Repeating
    key_after(prev) n times is the textbook worst case for this kind of
    scheme -- each call only ever halves the room left in front of it, so
    the key length grows by roughly one character every ~6 items (measured:
    10,000 sequential key_after() calls produced 200+ character keys).
    Knowing the count n upfront lets us divide the available space into n
    evenly sized slots instead, which stays at a handful of characters even
    for tens of thousands of items.

    Only supports lo=None here (the two call sites -- migration and
    "assign keys to files that don't have one yet" -- never need anything
    else); a non-None lo would need its digits beyond `width` accounted
    for, which isn't worth the complexity this module has no user for.
    """
    if n <= 0:
        return []
    if lo is not None:
        raise NotImplementedError("bulk_keys_between: lo must be None (see docstring)")

    width = 1
    while True:
        span = _BASE ** width
        hi_val = span if hi is None else sum(
            _digit_at(hi, i) * (_BASE ** (width - 1 - i)) for i in range(width)
        )
        if hi_val >= n + 2:
            break
        width += 1
        if width > 40:
            raise RuntimeError("bulk_keys_between: could not find enough room")

    step = hi_val / (n + 1)
    values = []
    prev = 0
    for i in range(1, n + 1):
        v = max(int(round(i * step)), prev + 1)
        v = min(v, hi_val - 1)
        values.append(v)
        prev = v
    return [_encode_fixed_width(v, width) for v in values]


# ---------------------------------------------------------------------------
# Filename helpers (unchanged behaviour from the original main.py).
# ---------------------------------------------------------------------------

def sanitize_filename(title: str, max_length: int = 50) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", title).strip()
    cleaned = re.sub(r"[\s_]+", "-", cleaned)
    cleaned = cleaned.strip("-.")
    if not cleaned:
        cleaned = "untitled"
    return cleaned[:max_length].rstrip("-.")


def unique_filename(directory: Path, base_name: str, current_path: Optional[Path] = None) -> Path:
    target = directory / f"{base_name}.md"
    if current_path and target.resolve() == current_path.resolve():
        return target
    counter = 1
    while target.exists():
        target = directory / f"{base_name}_{counter}.md"
        counter += 1
    return target


def cache_path_for(tasks_dir: Path) -> Path:
    """Where this tasks_dir's cache lives -- keyed by its resolved path so
    switching the tasks folder in Settings and switching back doesn't throw
    away the cache either direction was using."""
    digest = sha1(str(tasks_dir.resolve()).encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{digest}.sqlite3"


# ---------------------------------------------------------------------------
# TaskCache: the persisted half of the picture. One row per .md file, keyed
# by filename, holding the mtime QuickTask last parsed it at plus everything
# parsed out of it. list_tasks() below trusts a row whose mtime still
# matches the file on disk instead of re-opening and re-parsing that file.
# ---------------------------------------------------------------------------

class TaskCache:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    filename      TEXT PRIMARY KEY,
                    mtime         REAL NOT NULL,
                    order_key     TEXT NOT NULL,
                    title         TEXT NOT NULL,
                    body          TEXT NOT NULL,
                    done          INTEGER NOT NULL,
                    archived      INTEGER NOT NULL,
                    urgent        INTEGER NOT NULL,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            self._conn.commit()

    def all_mtimes(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._conn.execute("SELECT filename, mtime FROM tasks").fetchall())

    def all_records(self) -> Dict[str, dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT filename, order_key, title, body, done, archived, urgent, "
                "created_at, updated_at, metadata_json FROM tasks"
            ).fetchall()
        out = {}
        for (filename, order_key, title, body, done, archived, urgent,
             created_at, updated_at, metadata_json) in rows:
            out[filename] = {
                "id": filename,
                "order_key": order_key,
                "title": title,
                "body": body,
                "done": bool(done),
                "archived": bool(archived),
                "urgent": bool(urgent),
                "created_at": created_at,
                "updated_at": updated_at,
                "metadata": json.loads(metadata_json),
            }
        return out

    def upsert(self, filename: str, mtime: float, record: dict) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tasks (filename, mtime, order_key, title, body, done, archived,
                                    urgent, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(filename) DO UPDATE SET
                    mtime=excluded.mtime, order_key=excluded.order_key, title=excluded.title,
                    body=excluded.body, done=excluded.done, archived=excluded.archived,
                    urgent=excluded.urgent, created_at=excluded.created_at,
                    updated_at=excluded.updated_at, metadata_json=excluded.metadata_json
                """,
                (
                    filename, mtime, record["order_key"], record["title"], record["body"],
                    int(record["done"]), int(record["archived"]), int(record["urgent"]),
                    record["created_at"], record["updated_at"], json.dumps(record["metadata"]),
                ),
            )
            self._conn.commit()

    def delete(self, filename: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM tasks WHERE filename=?", (filename,))
            self._conn.commit()

    def delete_missing(self, keep_filenames) -> None:
        keep = set(keep_filenames)
        with self._lock:
            existing = [row[0] for row in self._conn.execute("SELECT filename FROM tasks")]
            stale = [f for f in existing if f not in keep]
            if stale:
                self._conn.executemany("DELETE FROM tasks WHERE filename=?", [(f,) for f in stale])
                self._conn.commit()

    def get_order_keys(self, filenames) -> Dict[str, str]:
        """Order keys for a handful of specific files -- O(the handful),
        not O(everything), unlike all_records()."""
        names = [n for n in filenames if n]
        if not names:
            return {}
        placeholders = ",".join("?" for _ in names)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT filename, order_key FROM tasks WHERE filename IN ({placeholders})", names
            ).fetchall()
        return dict(rows)

    def min_order_key(self) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT MIN(order_key) FROM tasks").fetchone()
        return row[0] if row else None

    def max_order_key(self) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT MAX(order_key) FROM tasks").fetchone()
        return row[0] if row else None


# ---------------------------------------------------------------------------
# TaskStore: what QuickTaskAPI talks to. Owns the .md files, the cache, and
# order-key bookkeeping.
# ---------------------------------------------------------------------------

class TaskStore:
    def __init__(self, tasks_dir: Path):
        self.tasks_dir = tasks_dir
        self.cache = TaskCache(cache_path_for(tasks_dir))
        self._migrate_legacy_order_if_needed()

    # -- one-time upgrade path from the old index.json-array ordering ------

    def _migrate_legacy_order_if_needed(self) -> None:
        legacy_index = self.tasks_dir / "index.json"
        if not legacy_index.exists():
            return
        try:
            md_files = sorted(p.name for p in self.tasks_dir.glob("*.md"))
        except OSError:
            return
        if not md_files:
            return

        # Already on the new scheme? (Or a folder that never used index.json
        # for ordering in the first place.) Nothing to migrate -- index.json
        # is dead weight from here on, but harmless, so it's left alone.
        for name in md_files[:5]:
            try:
                metadata, _ = read_frontmatter((self.tasks_dir / name).read_text(encoding="utf-8"))
            except OSError:
                continue
            if metadata.get("order_key"):
                return

        try:
            old_order = json.loads(legacy_index.read_text(encoding="utf-8"))
            if not isinstance(old_order, list):
                old_order = []
        except (OSError, json.JSONDecodeError):
            old_order = []

        md_set = set(md_files)
        remaining = [n for n in md_files if n not in old_order]  # matches old "new stuff first"
        ordered_names = [n for n in old_order if n in md_set]
        full_sequence = remaining + ordered_names

        keys = bulk_keys_between(None, None, len(full_sequence))
        for name, key in zip(full_sequence, keys):
            path = self.tasks_dir / name
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            metadata, content = read_frontmatter(text)
            metadata["order_key"] = key
            try:
                path.write_text(write_frontmatter(metadata, content), encoding="utf-8")
            except OSError:
                continue

        try:
            legacy_index.rename(self.tasks_dir / "index.json.migrated")
        except OSError:
            pass

    # -- parsing --------------------------------------------------------

    @staticmethod
    def _parse_file(path: Path) -> Optional[dict]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        metadata, content = read_frontmatter(text)

        title = path.stem
        body_lines = []
        h1_found = False
        for line in content.splitlines():
            if not h1_found and line.strip().startswith("# "):
                title = line.strip()[2:].strip()
                h1_found = True
            else:
                body_lines.append(line)
        body = "\n".join(body_lines).strip()

        return {
            "id": path.name,
            "order_key": metadata.get("order_key") or None,
            "title": title,
            "body": body,
            "done": bool(metadata.get("done", False)),
            "archived": bool(metadata.get("archived", False)),
            "urgent": bool(metadata.get("urgent", False)),
            "created_at": str(metadata.get("created_at", "")),
            "updated_at": str(metadata.get("updated_at", "")),
            "metadata": {k: str(v) for k, v in metadata.items()},
        }

    @staticmethod
    def _write_order_key(file_path: Path, key: str) -> None:
        metadata, content = read_frontmatter(file_path.read_text(encoding="utf-8"))
        metadata["order_key"] = key
        file_path.write_text(write_frontmatter(metadata, content), encoding="utf-8")

    def _recache(self, file_path: Path) -> Optional[dict]:
        record = self._parse_file(file_path)
        if record is None:
            self.cache.delete(file_path.name)
            return None
        if not record["order_key"]:
            new_key = key_after(self.cache.max_order_key())
            record["order_key"] = new_key
            self._write_order_key(file_path, new_key)
        mtime = file_path.stat().st_mtime
        self.cache.upsert(file_path.name, mtime, record)
        return record

    @staticmethod
    def _public_view(record: dict) -> dict:
        return {k: v for k, v in record.items() if k != "order_key"}

    # -- the operation that replaces the old full-rescan get_tasks() -------

    def list_tasks(self) -> List[dict]:
        if not self.tasks_dir.exists():
            return []
        try:
            entries = {
                e.name: e for e in os.scandir(self.tasks_dir)
                if e.name.endswith(".md") and e.is_file()
            }
        except OSError:
            return []

        cached_mtimes = self.cache.all_mtimes()
        dirty = [name for name, e in entries.items() if cached_mtimes.get(name) != e.stat().st_mtime]

        fresh: Dict[str, Tuple[float, dict]] = {}
        for name in dirty:
            entry = entries[name]
            mtime = entry.stat().st_mtime
            record = self._parse_file(Path(entry.path))
            if record is not None:
                fresh[name] = (mtime, record)

        records: Dict[str, dict] = {name: rec for name, (_, rec) in fresh.items()}
        if len(records) < len(entries):
            for name, cached in self.cache.all_records().items():
                if name in entries and name not in records:
                    records[name] = cached

        needs_order_key = [n for n in records if not records[n].get("order_key")]
        if needs_order_key:
            existing = sorted(records[n]["order_key"] for n in records if records[n].get("order_key"))
            floor = existing[0] if existing else None
            needs_order_key.sort(
                key=lambda n: records[n].get("updated_at") or records[n].get("created_at") or "",
                reverse=True,
            )
            # bulk_keys_between, not len(needs_order_key) sequential
            # key_before() calls -- the latter is the same "keys grow ~1
            # char per 6 items" trap as key_after() (see its docstring),
            # just walking toward the low end instead of the high end.
            new_keys = bulk_keys_between(None, floor, len(needs_order_key))
            for name, key in zip(needs_order_key, new_keys):
                records[name]["order_key"] = key
                path = Path(entries[name].path)
                self._write_order_key(path, key)
                # A fresh stat, not entries[name].stat() -- DirEntry caches
                # its first stat() result, which would still be the
                # pre-write mtime and make the next call re-parse for
                # nothing.
                fresh[name] = (os.stat(path).st_mtime, records[name])

        if self._rebalance_if_needed(records, entries):
            # Rebalancing rewrote every file's order_key, so every mtime
            # moved -- refresh the cache for all of them, not just the ones
            # that were already "fresh" from this call's own edits.
            for name, record in records.items():
                fresh[name] = (os.stat(entries[name].path).st_mtime, record)

        for name, (mtime, record) in fresh.items():
            self.cache.upsert(name, mtime, record)
        self.cache.delete_missing(entries.keys())

        ordered = sorted(records.values(), key=lambda r: r["order_key"])
        return [self._public_view(r) for r in ordered]

    # Only reached by long runs of always-insert-at-the-same-end usage (see
    # bulk_keys_between's docstring) -- e.g. thousands of new tasks created
    # one at a time, with no reordering in between, over the app's
    # lifetime. Keeps order_key from growing without bound; otherwise
    # harmless and normally never triggers.
    _REBALANCE_KEY_LENGTH_THRESHOLD = 24

    def _rebalance_if_needed(self, records: Dict[str, dict], entries) -> bool:
        if not records:
            return False
        if max(len(r["order_key"]) for r in records.values()) < self._REBALANCE_KEY_LENGTH_THRESHOLD:
            return False
        ordered_names = [name for name, _ in sorted(records.items(), key=lambda kv: kv[1]["order_key"])]
        keys = bulk_keys_between(None, None, len(ordered_names))
        for name, key in zip(ordered_names, keys):
            records[name]["order_key"] = key
            self._write_order_key(Path(entries[name].path), key)
        return True

    # -- CRUD, each one O(1) in the number of OTHER tasks -------------------

    def create_task(self, title: str = "New", body: str = "") -> dict:
        now_str = datetime.now().isoformat(timespec="seconds")
        clean_title = title.strip() or "New"
        base_name = sanitize_filename(clean_title)
        file_path = unique_filename(self.tasks_dir, base_name)

        order_key = key_before(self.cache.min_order_key())
        metadata = {
            "done": False, "archived": False, "urgent": False,
            "created_at": now_str, "updated_at": now_str, "order_key": order_key,
        }
        content = f"# {clean_title}\n\n{body}".strip()
        file_path.write_text(write_frontmatter(metadata, content), encoding="utf-8")

        record = {
            "id": file_path.name, "order_key": order_key, "title": clean_title, "body": body,
            "done": False, "archived": False, "urgent": False,
            "created_at": now_str, "updated_at": now_str,
            "metadata": {k: str(v) for k, v in metadata.items()},
        }
        self.cache.upsert(file_path.name, file_path.stat().st_mtime, record)
        return self._public_view(record)

    def update_task_status(self, task_id: str, done: Optional[bool] = None,
                            archived: Optional[bool] = None, urgent: Optional[bool] = None) -> bool:
        file_path = self.tasks_dir / task_id
        if not file_path.exists():
            return False
        metadata, content = read_frontmatter(file_path.read_text(encoding="utf-8"))
        if done is not None:
            metadata["done"] = bool(done)
        if archived is not None:
            metadata["archived"] = bool(archived)
        if urgent is not None:
            metadata["urgent"] = bool(urgent)
        metadata["updated_at"] = datetime.now().isoformat(timespec="seconds")
        file_path.write_text(write_frontmatter(metadata, content), encoding="utf-8")
        self._recache(file_path)
        return True

    def update_task_title(self, task_id: str, new_title: str) -> Optional[str]:
        file_path = self.tasks_dir / task_id
        if not file_path.exists():
            return None
        clean_title = new_title.strip() or "Untitled"
        metadata, content = read_frontmatter(file_path.read_text(encoding="utf-8"))
        metadata["updated_at"] = datetime.now().isoformat(timespec="seconds")

        body_lines = []
        h1_found = False
        for line in content.splitlines():
            if not h1_found and line.strip().startswith("# "):
                h1_found = True
            else:
                body_lines.append(line)
        new_content = f"# {clean_title}\n\n" + "\n".join(body_lines).strip()

        base_name = sanitize_filename(clean_title)
        new_file_path = unique_filename(self.tasks_dir, base_name, file_path)

        file_path.write_text(write_frontmatter(metadata, new_content), encoding="utf-8")
        if new_file_path.resolve() != file_path.resolve():
            file_path.rename(new_file_path)
            self.cache.delete(file_path.name)

        self._recache(new_file_path)
        return new_file_path.name

    def update_task_full(self, task_id: str, new_title: str, new_body: str) -> Optional[dict]:
        file_path = self.tasks_dir / task_id
        if not file_path.exists():
            return None
        clean_title = new_title.strip() or "Untitled"
        metadata, _ = read_frontmatter(file_path.read_text(encoding="utf-8"))
        now_str = datetime.now().isoformat(timespec="seconds")
        metadata["updated_at"] = now_str
        new_content = f"# {clean_title}\n\n{new_body.strip()}"

        base_name = sanitize_filename(clean_title)
        new_file_path = unique_filename(self.tasks_dir, base_name, file_path)

        file_path.write_text(write_frontmatter(metadata, new_content), encoding="utf-8")
        if new_file_path.resolve() != file_path.resolve():
            file_path.rename(new_file_path)
            self.cache.delete(file_path.name)

        record = self._recache(new_file_path)
        if record is None:
            return None
        return self._public_view(record)

    def update_task_body(self, task_id: str, new_body: str) -> bool:
        file_path = self.tasks_dir / task_id
        if not file_path.exists():
            return False
        metadata, content = read_frontmatter(file_path.read_text(encoding="utf-8"))
        metadata["updated_at"] = datetime.now().isoformat(timespec="seconds")

        title = file_path.stem
        for line in content.splitlines():
            if line.strip().startswith("# "):
                title = line.strip()[2:].strip()
                break

        new_content = f"# {title}\n\n{new_body.strip()}"
        file_path.write_text(write_frontmatter(metadata, new_content), encoding="utf-8")
        self._recache(file_path)
        return True

    def delete_task(self, task_id: str) -> bool:
        file_path = self.tasks_dir / task_id
        if not file_path.exists():
            return False
        try:
            file_path.unlink()
        except OSError:
            return False
        self.cache.delete(task_id)
        return True

    def reorder_task(self, task_id: str, before_id: Optional[str], after_id: Optional[str]) -> bool:
        """Move task_id to sit right after before_id and right before
        after_id (either may be None for "start"/"end" of the list).
        Writes exactly one file, regardless of how many tasks exist."""
        file_path = self.tasks_dir / task_id
        if not file_path.exists():
            return False

        neighbour_keys = self.cache.get_order_keys([before_id, after_id])
        lo = neighbour_keys.get(before_id) if before_id else None
        hi = neighbour_keys.get(after_id) if after_id else None
        try:
            new_key = key_between(lo, hi)
        except (ValueError, RuntimeError):
            # Neighbours reported out of order or with no room left between
            # them (shouldn't happen with our own keys; cheap to be safe
            # anyway) -- fall back to "end of the list" rather than failing
            # the user's drag-and-drop outright.
            new_key = key_after(self.cache.max_order_key())

        metadata, content = read_frontmatter(file_path.read_text(encoding="utf-8"))
        metadata["order_key"] = new_key
        file_path.write_text(write_frontmatter(metadata, content), encoding="utf-8")
        self._recache(file_path)
        return True

    # -- incremental hooks for the filesystem watcher -----------------------

    def refresh_file(self, filename: str) -> Optional[dict]:
        """Re-parse exactly one file after an external change. O(1) in the
        number of other tasks -- this is what lets a Watchdog event stay
        cheap regardless of how many files are in the folder."""
        file_path = self.tasks_dir / filename
        if not file_path.exists():
            self.cache.delete(filename)
            return None
        record = self._recache(file_path)
        return self._public_view(record) if record else None

    def forget_file(self, filename: str) -> None:
        self.cache.delete(filename)
