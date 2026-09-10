# Промпт для точного воссоздания текущего QuickTask

Ты — senior Python desktop engineer, Windows UI engineer и frontend-разработчик. Создай **полностью рабочее** Windows-приложение `QuickTask` — лёгкий правый task-сайдбар для быстрых задач, хранящихся в Markdown-файлах.

Не создавай прототип, псевдокод, макет или набор несвязанных фрагментов. Создай завершённый проект, который запускается командой `python main.py` после установки зависимостей. Используй именно указанную архитектуру: Python + pywebview + один HTML-файл с Vanilla JavaScript. Не заменяй стек на Electron, Qt, Tkinter, React, Vue, базу данных или облачный backend.

Это спецификация **актуального состояния проекта**. Особенно важно: используй составной свёрнутый handle 36×100 px с bookmark SVG в верхней части и burger SVG в нижней части. Не заменяй эти SVG на checkmark, другую иконку, emoji или внешнюю картинку.

---

## 1. Стек и структура

### Обязательные зависимости

- Python 3.10+.
- `pywebview` — нативное окно Windows и JS↔Python bridge.
- `python-frontmatter` — YAML frontmatter Markdown-задач.
- `watchdog` — отслеживание внешних изменений файлов задач.
- `keyboard` — глобальная горячая клавиша.
- Стандартная библиотека: `pathlib`, `json`, `ctypes`, `threading`, `time`, `datetime`, `re`, `os`, `sys`.

### Структура проекта

```text
QuickTask/
├── main.py
├── index.html
├── prompt.md
├── README.md
└── .gitignore
```

- `main.py`: Python backend, хранение задач, pywebview API, работа с окном, Watchdog, hotkey, Win32 taskbar integration.
- `index.html`: весь HTML, CSS и Vanilla JavaScript.
- Без npm, bundler, CDN, внешних иконок или web fonts.

---

## 2. Назначение приложения

QuickTask — ненавязчивый always-on-top виджет у правого края рабочего стола Windows.

В свёрнутом состоянии виден компактный вертикальный handle 36×100 px. Его верхняя часть раскрывает сайдбар при наведении или клике. Нижняя часть предназначена исключительно для перетаскивания окна по вертикали. В развёрнутом состоянии пользователь управляет списком Markdown-задач: создаёт, редактирует, выполняет, помечает срочными, архивирует, удаляет, сортирует drag-and-drop и ищет по заголовку/тексту.

Задачи хранятся как локальные `.md`-файлы, чтобы их можно было редактировать в Obsidian, VS Code и любом редакторе. Внешние изменения подхватываются автоматически.

---

## 3. Конфигурация

Храни пользовательскую конфигурацию в:

```python
CONFIG_DIR = Path.home() / ".quicktask"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_TASKS_DIR = Path.home() / "Documents" / "QuickTasks"
```

На Windows это обычно:

```text
C:\Users\<Username>\.quicktask\config.json
```

Используй дефолтную конфигурацию:

```python
DEFAULT_CONFIG = {
    "sidebar_width": 380,
    "handle_width": 36,
    "handle_total_height": 100,
    "window_y": 120,
    "hotkey": "ctrl+alt+t",
    "pinned": False,
    "max_height": None,
    "theme": "dark",
    "tasks_dir": str(DEFAULT_TASKS_DIR)
}
```

### Требования к config

- Создавай папку конфигурации автоматически.
- При чтении объединяй сохранённые данные с `DEFAULT_CONFIG`.
- При каждом изменении pin, темы, положения handle, каталога задач, высоты или hotkey сохраняй JSON UTF-8 с `ensure_ascii=False`.
- Не добавляй изменение ширины сайдбара пользователем: значение `sidebar_width` является конфигурационным, но нативное окно создаётся с `resizable=False`.

---

## 4. Markdown-хранилище задач

### Каталог задач

По умолчанию:

```text
~/Documents/QuickTasks/
```

Пользователь может сменить каталог через настройки. При смене:

1. Применяй `Path(path).expanduser().resolve()`.
2. Создавай каталог, если он отсутствует.
3. Перезапускай `watchdog.Observer` для нового пути.
4. Обновляй путь в config.
5. Создавай `<tasks_dir>/index.json`, если файла нет.

### Формат задачи

Каждая задача — Markdown UTF-8 с YAML frontmatter:

```markdown
***
done: false
archived: false
urgent: false
created_at: 2026-09-10T08:00:00
updated_at: 2026-09-10T08:00:00
***
# Купить молоко

Проверить список продуктов перед магазином.
```

Правила:

- Первая строка контента формата `# <заголовок>` — title.
- Текст после первой H1 — body.
- Используй и сохраняй `done`, `archived`, `urgent`, `created_at`, `updated_at`.
- Не теряй неизвестные metadata-поля frontmatter.
- Обновляй `updated_at` при редактировании статуса, title или полного содержимого.

### Безопасное имя файла

Реализуй:

```python
sanitize_filename(title: str, max_length: int = 50) -> str
get_unique_filename(
    directory: Path,
    base_name: str,
    current_path: Optional[Path] = None
) -> Path
```

Санитизация:

- Удаляй `\ / * ? : " < > |`.
- Пробелы и `_` превращай в `-`.
- Удаляй крайние `-` и `.`.
- Если пусто — `untitled`.
- Ограничивай базовое имя 50 символами.

При коллизии создавай `name_1.md`, `name_2.md` и далее. При переименовании текущий файл не считается конфликтом.

### Порядок задач

Храни порядок в:

```text
<tasks_dir>/index.json
```

Пример:

```json
[
  "Купить-молоко.md",
  "Подготовить-отчёт.md"
]
```

Алгоритм загрузки:

1. Прочитай все `*.md`.
2. Сначала включи файлы, которые присутствуют в `index.json`, в указанном порядке.
3. Остальные файлы сортируй по `updated_at` или `created_at` по убыванию.
4. Добавь эти новые или внешние файлы **в начало** итогового списка.
5. Синхронизируй `index.json` с итоговым порядком.
6. При удалении задачи удаляй её id из index.

---

## 5. Watchdog и внешние изменения

Создай `TaskFileHandler(FileSystemEventHandler)`.

Требования:

- Игнорируй директории.
- Игнорируй `index.json` по `src_path` и `dest_path`.
- Реагируй на `.md` в `src_path` или `dest_path`, включая rename/move.
- Используй debounce примерно 0.3 секунды.
- Watchdog должен вызывать:

```python
CURRENT_WINDOW.evaluate_js(
    "window.refreshTasks && window.refreshTasks();"
)
```

- Observer должен работать в daemon mode.
- Перед созданием нового observer корректно останавливай и `join()` предыдущий.

---

## 6. Нативное окно pywebview

Создай одно frameless окно:

```python
CURRENT_WINDOW = webview.create_window(
    title="QuickTask",
    url=str(html_path.resolve()),
    js_api=api,
    width=36,
    height=100,
    x=SCREEN_WIDTH - 36,
    y=initial_y,
    frameless=True,
    on_top=True,
    resizable=False,
    easy_drag=False,
)
```

Изначальные fallback размеры:

```python
SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
```

После запуска окна попробуй получить реальные размеры primary screen через `webview.screens`.

### Свёрнутое состояние

- Размер: 36×100 px по умолчанию (`handle_width`, `handle_total_height`).
- X: `SCREEN_WIDTH - handle_width`.
- Y: `window_y` из config.
- `body` имеет класс `collapsed`.
- Сайдбар скрыт.
- Видна только составная вертикальная панель handle.

### Развёрнутое состояние

- Ширина: `sidebar_width`, по умолчанию 380 px.
- X: `SCREEN_WIDTH - width`.
- Если `max_height` отсутствует, нулевой или не меньше высоты экрана: высота `SCREEN_HEIGHT`, Y=0.
- Для валидного `max_height < SCREEN_HEIGHT` центрируй по вертикали:

```python
target_height = int(max_height)
y = max(0, (SCREEN_HEIGHT - target_height) // 2)
```

- `body` имеет класс `expanded`.

### Поведение окна

- Верхняя часть collapsed handle открывает окно после hover delay 100 мс.
- Клик по верхней части тоже раскрывает окно.
- Нижняя часть collapsed handle **не должна раскрывать окно**: она служит только drag-handle для перемещения по вертикали.
- При уходе мыши с боковой панели сворачивай её, только если `pinned == false`, нет открытой модалки, и пользователь не занят drag-перемещением.
- Учитывай focus/blur: если приложение теряет фокус и не закреплено, а модалки не открыты, сайдбар сворачивается.
- Кнопка pin отключает autohide.
- `Esc`: закрывает open confirm modal, иначе сохраняет и закрывает editor modal, иначе закрывает settings modal, иначе сворачивает sidebar при `pinned == false`.

### Вертикальное перемещение

Поддерживай два независимых drag-сценария:

1. В развёрнутом состоянии используй `#sidebar-drag-handle` слева у sidebar.
2. В свёрнутом состоянии используй нижнюю часть `#collapsed-drag-tab`.

Логика:

- `mousedown`: запоминай `screenY`, включай `isDragging`.
- `mousemove`: вычисляй `deltaY` и передавай в Python `update_window_y(deltaY)`.
- `mouseup`: выключай drag с небольшой безопасной задержкой, чтобы click не раскрывал handle после drag.
- Клампинг в Python:

```python
new_y = max(
    0,
    min(SCREEN_HEIGHT - handle_total_height, current_y + delta_y)
)
```

- Сохраняй `window_y` в config.
- Не реализуй изменение ширины сайдбара мышью.

---

## 7. Критично: убрать окно из Taskbar и Alt+Tab

QuickTask должен быть видимым поверх рабочего стола, но не иметь taskbar button и не участвовать в Alt+Tab.

Используй проверенный Win32 подход для frameless pywebview окна. Не полагайся только на `window.native`, `ShowInTaskbar` или `FindWindowW` по заголовку, потому что native может быть `None`, а title поиск может не найти frameless окно.

После запуска pywebview из `on_started` вызови `remove_taskbar_icon()`.

Алгоритм `remove_taskbar_icon()`:

1. На не-Windows немедленно завершай функцию.
2. Запускай daemon `threading.Thread`.
3. В worker используй `time.sleep(0.3)`.
4. Получи текущий PID через `os.getpid()`.
5. Через `EnumWindows`, `IsWindowVisible` и `GetWindowThreadProcessId` найди видимые top-level окна данного PID.
6. Для каждого найденного HWND примени:

```python
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020

style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
new_style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW

ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)

ctypes.windll.user32.SetWindowPos(
    hwnd,
    0,
    0,
    0,
    0,
    0,
    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
)
```

7. Ошибки перехватывай и пиши в stderr, но не падай.

---

## 8. Python API для JavaScript

Экспортируй через `js_api=api` минимум следующие методы:

```text
get_tasks() -> list[dict]
create_task(title="New", body="") -> dict | None
update_task_status(task_id, done=None, archived=None, urgent=None) -> bool
update_task_title(task_id, new_title) -> str | None
update_task_full(task_id, new_title, new_body) -> dict | None
delete_task(task_id) -> bool
save_tasks_order(order) -> bool
expand()
collapse(force=False)
toggle_pin() -> bool
get_config() -> dict
set_theme(theme_name) -> str
save_settings(settings) -> dict
select_tasks_directory() -> str | None
update_window_y(delta_y) -> int
close_app()
```

### API semantics

- `get_tasks`: читает `.md`; возвращает `id`, `title`, `body`, `done`, `archived`, `urgent`, `created_at`, `updated_at`, `metadata`.
- `create_task`: создаёт файл и добавляет его id в начало index.
- `update_task_status`: меняет только переданные статусы, включая `urgent`.
- `update_task_title`: меняет H1, `updated_at`, безопасно переименовывает файл и обновляет order.
- `update_task_full`: сохраняет title+body, обновляет frontmatter, безопасно переименовывает файл, возвращает данные с новым id.
- `delete_task`: удаляет `.md` и id из index.
- `save_tasks_order`: сохраняет пользовательский порядок.
- `set_theme`: допускает только `dark` и `light`.
- `save_settings`: обновляет task folder, maximum height и theme.
- `close_app`: остановка watcher, destroy window, выход процесса.

---

## 9. Global hotkey

Стандартная комбинация:

```text
ctrl+alt+t
```

При срабатывании:

1. Если sidebar не раскрыт — раскрыть.
2. Выполнить:

```python
CURRENT_WINDOW.evaluate_js(
    "window.onGlobalHotkeyTriggered && window.onGlobalHotkeyTriggered();"
)
```

На стороне JS:

```javascript
window.onGlobalHotkeyTriggered = function() {
  createNewTaskAction();
};
```

Это создаёт задачу и фокусирует первый title input.

Если `keyboard` не установлен или регистрация не удалась, не заверши приложение: сообщи об ошибке в stderr.

---

## 10. Визуальный язык

- Интерфейс на русском языке.
- Тёмная тема по умолчанию, переключаемая светлая тема.
- Стиль — компактный VS Code / GitHub Dark.
- Никаких внешних ассетов: все значки inline SVG.
- Стартовый body:

```html
<body class="collapsed theme-dark">
```

### Цветовые переменные

```css
body.theme-dark {
  --bg-primary: #18181b;
  --bg-secondary: #27272a;
  --bg-card: #202023;
  --bg-card-hover: #2d2d31;
  --text-main: #f4f4f5;
  --text-muted: #a1a1aa;
  --accent: #3b82f6;
  --accent-hover: #2563eb;
  --accent-active: #1d4ed8;
  --border-color: #3f3f46;
  --done-color: #71717a;
  --danger: #ef4444;
  --danger-hover: #dc2626;
  --urgent-color: #ef4444;
  --modal-bg: rgba(0, 0, 0, 0.75);
  --frontmatter-bg: #121214;
}

body.theme-light {
  --bg-primary: #f8fafc;
  --bg-secondary: #ffffff;
  --bg-card: #ffffff;
  --bg-card-hover: #f1f5f9;
  --text-main: #0f172a;
  --text-muted: #64748b;
  --accent: #2563eb;
  --accent-hover: #1d4ed8;
  --accent-active: #1e40af;
  --border-color: #e2e8f0;
  --done-color: #94a3b8;
  --danger: #ef4444;
  --danger-hover: #dc2626;
  --urgent-color: #e11d48;
  --modal-bg: rgba(15, 23, 42, 0.45);
  --frontmatter-bg: #f1f5f9;
}
```

---

## 11. Точный collapsed handle и SVG

Это самый важный визуальный элемент. Он составной, вертикальный, 36×100 px и расположен у правого края экрана. Используй именно следующую структуру и SVG.

```html
<!-- Monolithic 100px collapsed handle at x=0 of the collapsed window -->
<div id="collapsed-wrapper">
  <!-- Верхняя интерактивная часть: bookmark; hover/click раскрывает sidebar -->
  <div id="collapsed-tab" title="QuickTask (Кликните или наведите)">
    <svg viewBox="0 0 24 24" width="24" height="24">
      <path d="M20.137,24a2.8,2.8,0,0,1-1.987-.835L12,17.051,5.85,23.169a2.8,2.8,0,0,1-3.095.609A2.8,2.8,0,0,1,1,21.154V5A5,5,0,0,1,6,0H18a5,5,0,0,1,5,5V21.154a2.8,2.8,0,0,1-1.751,2.624A2.867,2.867,0,0,1,20.137,24ZM6,2A3,3,0,0,0,3,5V21.154a.843.843,0,0,0,1.437.6h0L11.3,14.933a1,1,0,0,1,1.41,0l6.855,6.819a.843.843,0,0,0,1.437-.6V5a3,3,0,0,0-3-3Z"/>
    </svg>
  </div>

  <div class="handle-divider"></div>

  <!-- Нижняя часть: burger; только vertical drag, не раскрывает sidebar -->
  <div id="collapsed-drag-tab" title="Зажмите ЛКМ и потяните для перемещения по вертикали">
    <svg viewBox="0 0 512 512" width="512" height="512">
      <path d="M480,224H32c-17.673,0-32,14.327-32,32s14.327,32,32,32h448c17.673,0,32-14.327,32-32S497.673,224,480,224z"/>
      <path d="M32,138.667h448c17.673,0,32-14.327,32-32s-14.327-32-32-32H32c-17.673,0-32,14.327-32,32S14.327,138.667,32,138.667z"/>
      <path d="M480,373.333H32c-17.673,0-32,14.327-32,32s14.327,32,32,32h448c17.673,0,32-14.327,32-32S497.673,373.333,480,373.333z"/>
    </svg>
  </div>
</div>
```

Стили:

```css
:root {
  --handle-width: 36px;
  --handle-total-height: 100px;
}

body.collapsed {
  background: var(--bg-secondary);
  display: block;
  padding: 0;
  margin: 0;
}

body.collapsed #sidebar {
  display: none;
}

body.collapsed #collapsed-wrapper {
  display: flex;
}

body.expanded #collapsed-wrapper {
  display: none;
}

body.expanded #sidebar {
  display: flex;
}

#collapsed-wrapper {
  display: flex;
  flex-direction: column;
  width: var(--handle-width) !important;
  min-width: var(--handle-width) !important;
  max-width: var(--handle-width) !important;
  height: var(--handle-total-height) !important;
  position: absolute;
  top: 0;
  left: 0;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-right: none;
  border-radius: 8px 0 0 8px;
  box-shadow: -3px 0 12px rgba(0, 0, 0, 0.25);
  overflow: hidden;
  align-items: center;
  justify-content: space-between;
}

#collapsed-tab {
  width: 100%;
  height: 58px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: var(--accent);
  transition: background 0.15s, color 0.15s;
}

#collapsed-tab:hover {
  background: var(--accent);
  color: #ffffff;
}

#collapsed-tab svg {
  width: 18px;
  height: 18px;
  min-width: 18px;
  min-height: 18px;
  display: block;
  fill: currentColor;
}

.handle-divider {
  height: 1px;
  width: 75%;
  background: var(--border-color);
  flex-shrink: 0;
}

#collapsed-drag-tab {
  width: 100%;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: ns-resize;
  color: var(--text-muted);
  transition: background 0.15s, color 0.15s;
}

#collapsed-drag-tab:hover {
  background: var(--bg-card-hover);
  color: var(--accent);
}

#collapsed-drag-tab svg {
  width: 16px;
  height: 16px;
  min-width: 16px;
  min-height: 16px;
  display: block;
  fill: currentColor;
}
```

---

## 12. Развёрнутый sidebar

Структура:

```text
#sidebar
  header
    .header-top
      #btn-new
      .header-actions
        #btn-theme
        #btn-settings
        #btn-pin
        #btn-close-app
    .filter-tabs
    .search-box
  #task-list
```

### Header

- `#btn-new`: кнопка `New` с SVG plus.
- `#btn-theme`: светлая/тёмная тема, SVG солнца/луны.
- `#btn-settings`: шестерёнка.
- `#btn-pin`: кнопка pin.
- `#btn-close-app`: крестик, при hover danger red.

### Фильтры

Порядок вкладок:

```text
Активные | Выполненные | Архив | Все
```

Логика:

- Активные: `!done && !archived`.
- Выполненные: `done && !archived`.
- Архив: `archived`.
- Все: всё.

### Поиск

Добавь `.search-box` в header:

- SVG лупы.
- `#search-input` с placeholder `Поиск по названию и телу...`.
- `#search-clear-btn` с SVG X.
- Фильтрация substring без учёта регистра по title и body.
- Очистка показывается только при непустом запросе.
- `Esc` внутри search input очищает поиск и не закрывает sidebar.

---

## 13. Карточка задачи

Каждая карточка:

1. `.task-drag-grip` — шесть точек, для drag reorder.
2. `.task-checkbox` — custom checkbox.
3. `.btn-flag` — urgent flag.
4. `.task-title-input` — инлайн-заголовок.
5. `.task-actions`: карандаш/редактирование, архив, удаление.

### Статусы

- `done`: зачёркивает title, `--done-color`.
- `urgent`: `.btn-flag.active`, `--urgent-color`, SVG flag с fill currentColor.
- `archived`: не показывай в active/done, показывай в archive/all.

### Инлайн title

- Сохраняй с debounce 600 мс, на blur и Enter.
- Если пустой — возвращай прежнее название.
- При успешном rename обновляй `task.id` и `card.dataset.id`.

### Reorder

- Включай HTML5 draggable только при `mousedown` на grip.
- В `dragover` перетаскивай карточку перед/после target исходя из координаты мыши.
- Отображай `drag-over` border-top accent.
- После dragend собирай id всех `.task-card` и сохраняй порядок через `save_tasks_order`.

---

## 14. Модальный редактор задачи

`#editor-modal` должен содержать:

- Header `Редактор задачи`.
- Кнопку закрытия вверху.
- Input `#modal-task-title-input`.
- Textarea `#task-body-input`.
- Read-only `<pre id="modal-frontmatter-view" class="frontmatter-viewer"></pre>`.
- Footer: `Отмена`, `Сохранить`.

При открытии:

- Загрузи текущий title и body.
- Построй видимый YAML viewer из metadata:

```text
***
done: ...
archived: ...
urgent: ...
created_at: ...
updated_at: ...
***
```

При save вызывай `update_task_full(task.id, newTitle, newBody)` и затем обновляй список задач.

При `Esc` в editor сохраняй и закрывай.

---

## 15. Settings modal

`#settings-modal`:

- Выбор папки `.md` задач: read-only path + `Обзор...`.
- Высота сайдбара в пикселях.
- Подсказка о пустом поле для full height и центрировании fixed height.
- `Отмена`, `Применить`.
- При открытии загрузить config через `get_config()`.
- При save передать `tasks_dir`, `max_height`, `theme` в API.

### Theme

- Dark и light переключаются моментально.
- Сохраняй theme в config.
- `body.theme-dark` / `body.theme-light`.

---

## 16. Custom confirm dialog

Не используй нативный browser `confirm` для удаления и закрытия приложения. Реализуй стилизованное окно:

```text
#confirm-modal
  #confirm-title
  #confirm-message
  #confirm-btn-cancel
  #confirm-btn-ok
```

Нужна функция:

```javascript
showCustomConfirm(message, title, confirmText, confirmClass)
```

Она возвращает Promise boolean.

Использование:

- При удалении: `Удалить задачу "<title>" навсегда?`, title `Удаление задачи`, action `Удалить`.
- При закрытии app: `Вы уверены, что хотите полностью закрыть QuickTask?`, title `Закрыть приложение`, action `Закрыть`.

`Esc`, отмена и X закрывают confirm с false.

---

## 17. JavaScript startup и globals

Обязательно реализуй:

```javascript
window.refreshTasks = async function(force = false) {
  const activeEl = document.activeElement;

  if (
    !force &&
    activeEl &&
    (activeEl.classList.contains('task-title-input') || activeEl.id === 'search-input')
  ) {
    return;
  }

  if (window.pywebview) {
    currentTasks = await window.pywebview.api.get_tasks();
    renderTasks();
  }
};

window.onGlobalHotkeyTriggered = function() {
  createNewTaskAction();
};

window.addEventListener('pywebviewready', async () => {
  const cfg = await window.pywebview.api.get_config();
  isPinned = !!cfg.pinned;
  btnPin.classList.toggle('active', isPinned);
  applyTheme(cfg.theme || 'dark');
  await window.refreshTasks(true);
});
```

Проверяй существование `window.pywebview` перед вызовами API.

---

## 18. Критерии готовности

Результат считается готовым только если:

1. `python main.py` запускает приложение на Windows.
2. Окно frameless, on_top, скрыто из taskbar и Alt+Tab.
3. Свёрнутый handle имеет размер 36×100 px.
4. Сверху используется bookmark SVG, снизу burger SVG строго по разделу 11.
5. Верхняя часть раскрывает sidebar; нижняя двигает handle и не раскрывает sidebar.
6. Задачи реально хранятся в Markdown + YAML.
7. Работают create, inline rename, full editor, status, urgent, archive, delete, search и drag ordering.
8. Watchdog видит внешние изменения `.md`.
9. Работают dark/light themes, settings, pin, fixed height и centered sidebar.
10. Работает global hotkey `ctrl+alt+t`.
11. Работает стилизованный confirm dialog.
12. Нет React/Electron/Qt/database/CDN.

В финальном ответе перечисли файлы, зависимости, команду запуска и результаты ручной проверки основных сценариев.