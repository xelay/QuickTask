# Промпт для воссоздания QuickTask

Ты — senior Python desktop engineer, Windows UI engineer и frontend-разработчик. Создай **полностью рабочее** Windows-приложение `QuickTask` — лёгкий, всегда-поверх-окон правый сайдбар для быстрых задач, хранящихся в Markdown-файлах.

Не делай прототип, псевдокод, макет или набор несвязанных фрагментов. Создай завершённый проект, который можно запустить командой `python main.py` после установки зависимостей. Используй только указанный стек и не заменяй архитектуру на Electron, Qt, Tkinter, React или базу данных.

---

## 1. Цель и стек

### Цель

QuickTask — ненавязчивый task-виджет для Windows. В свёрнутом состоянии это компактный элемент справа на экране. По наведению, клику или глобальной горячей клавише он раскрывается в правый сайдбар. Пользователь быстро создаёт задачи, отмечает их выполненными, архивирует, удаляет, меняет название и редактирует подробное Markdown-описание.

Задачи — обычные `.md`-файлы, чтобы их можно было редактировать напрямую в Obsidian, VS Code или любом текстовом редакторе. Изменения внешних файлов должны подхватываться приложением автоматически.

### Обязательный стек

- Python 3.10+.
- `pywebview` для нативного окна Windows и JS↔Python bridge.
- Один файл `index.html` с HTML, CSS и Vanilla JavaScript — без React, Vue, сборщиков и npm.
- `python-frontmatter` для YAML frontmatter Markdown-файлов.
- `watchdog` для отслеживания внешних изменений в каталоге задач.
- `keyboard` для глобальной горячей клавиши.
- Стандартная библиотека Python: `pathlib`, `json`, `ctypes`, `threading`, `time`, `datetime`, `re`, `os`, `sys`.

Минимальная структура проекта:

```text
QuickTask/
├── main.py
├── index.html
├── README.md
└── requirements.txt
```

`main.py` — Python backend, управление окном, файловое хранилище, API для JS, hotkey, Watchdog и Win32-логика taskbar.

`index.html` — весь визуальный интерфейс, CSS и клиентская логика.

---

## 2. Хранилище и конфигурация

### Конфигурация приложения

Храни пользовательскую конфигурацию в:

```text
Path.home() / ".quicktask" / "config.json"
```

На Windows это обычно:

```text
C:\Users\<Username>\.quicktask\config.json
```

Если файл или папка отсутствуют, создавай их автоматически. Объединяй прочитанные настройки с дефолтными, чтобы новые поля не ломали старые конфиги.

Используй следующие дефолтные настройки:

```python
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
    "tasks_dir": str(DEFAULT_TASKS_DIR)
}
```

### Каталог задач

По умолчанию задачи хранятся в:

```text
~/Documents/QuickTasks/
```

Пользователь может сменить путь через настройки. При смене пути:

1. Нормализуй путь через `Path(...).expanduser().resolve()`.
2. Создай каталог, если его нет.
3. Перезапусти `watchdog.Observer` для нового пути.
4. Создай файл `index.json`, если его нет.

### Формат задачи

Каждая задача — UTF-8 Markdown-файл с YAML frontmatter. Пример:

```markdown
***
done: false
archived: false
urgent: false
created_at: 2026-09-09T18:00:00
updated_at: 2026-09-09T18:00:00
***
# Купить молоко

Проверить список продуктов перед магазином.
```

Правила:

- Заголовок задачи — первая строка Markdown вида `# <название>`.
- Всё после первой H1-строки — тело/описание задачи.
- В метаданных поддерживай: `done`, `archived`, `urgent`, `created_at`, `updated_at`.
- Не теряй неизвестные ключи frontmatter при сохранении.
- Создание, смена статуса, переименование, сохранение описания и удаление должны обновлять данные на диске.

### Безопасные имена файлов

Реализуй функцию `sanitize_filename`:

- Удаляй недопустимые для Windows символы: `\ / * ? : " < > |`.
- Пробелы и подчёркивания преобразуй в дефисы.
- Удали начальные и конечные дефисы и точки.
- Если название пустое, используй `untitled`.
- Ограничь базовое имя 50 символами.

Реализуй `get_unique_filename(directory, base_name, current_path=None)`:

- Если имя свободно — используй `<base>.md`.
- При конфликте создавай `<base>_1.md`, `<base>_2.md` и т.д.
- При переименовании не считай текущий файл конфликтом.

### Порядок задач

Храни пользовательский порядок в:

```text
<tasks_dir>/index.json
```

Это JSON-массив имён файлов:

```json
[
  "Купить-молоко.md",
  "Подготовить-отчёт.md"
]
```

При загрузке:

1. Сначала покажи существующие задачи в порядке `index.json`.
2. Новые файлы, которых ещё нет в индексе, добавь в начало итогового списка.
3. Между собой новые файлы сортируй по `updated_at`, затем `created_at`, по убыванию.
4. После построения полного списка синхронизируй `index.json`.
5. Не отслеживай `index.json` в Watchdog как событие обновления задач — иначе возникнут лишние циклы refresh.

---

## 3. Нативное окно Windows

Создай одно окно `pywebview`:

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

Определи размеры основного экрана через `webview.screens` после запуска, с fallback:

```python
SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
```

### Свёрнутое состояние

- Размер окна: `handle_width × handle_total_height`, по умолчанию `36 × 100 px`.
- Положение: у правого края экрана.
- Координата X: `SCREEN_WIDTH - handle_width`.
- Координата Y: `config["window_y"]`.
- В HTML body используется класс `collapsed`.
- Основной сайдбар скрыт, виден только collapsed handle.

### Развёрнутое состояние

- Ширина: `sidebar_width`, по умолчанию `380 px`.
- Если `max_height` отсутствует, равен нулю или больше высоты экрана: высота = `SCREEN_HEIGHT`, `y = 0`.
- Если `max_height` — корректное положительное число меньше высоты экрана: высота равна `max_height`, окно центрируется по вертикали:

```python
y = max(0, (SCREEN_HEIGHT - target_height) // 2)
```

- Правый край всегда привязан к правому краю главного экрана:

```python
x = SCREEN_WIDTH - width
```

- В HTML body используется класс `expanded`.

### Autohide и pin

- Наведение мыши на collapsed handle раскрывает сайдбар после небольшой задержки, примерно 100 мс.
- Клик по collapsed handle раскрывает его сразу.
- При уходе мыши за пределы окна сайдбар сворачивается, если он не закреплён и не открыто модальное окно.
- Кнопка pin переключает `config["pinned"]` и сохраняет настройку.
- При закреплении окно не скрывается по `mouseleave`.
- `Esc` сворачивает сайдбар, если не открыта модалка и сайдбар не закреплён.

### Вертикальное перемещение

На левой стороне развёрнутого сайдбара находится вертикальный drag-handle шириной 16 px. Он нужен для изменения `window_y` свёрнутого ярлыка.

Поведение:

- При `mousedown` сохранить `screenY`.
- При `mousemove` вычислять вертикальную дельту и передавать её в Python API `update_window_y(delta_y)`.
- Клампить значение:

```python
new_y = max(
    0,
    min(SCREEN_HEIGHT - handle_total_height, current_y + delta_y)
)
```

- Сохранять `window_y` в `config.json`.
- Если окно в этот момент свёрнуто — сразу перемещать его в новую позицию.
- В развёрнутом состоянии перемещение меняет сохранённую позицию будущего свёрнутого ярлыка, не ломая развёрнутую геометрию.

Не реализуй изменение ширины окна перетаскиванием левого края. Нативное окно создаётся с `resizable=False`.

---

## 4. Критично: скрытие из Taskbar

QuickTask — фоновый виджет. Он обязан быть видимым на экране, но **не должен иметь кнопку в Windows taskbar и не должен появляться в Alt+Tab**.

Не полагайся на `window.native`, `Form.ShowInTaskbar` или `FindWindowW` по title: в используемом варианте pywebview `window.native` может быть `None`, а frameless-окно может не находиться по заголовку.

Реализуй проверенный подход именно так:

1. После `webview.start(on_started, debug=False)` внутри `on_started` запускай `remove_taskbar_icon()`.
2. В `remove_taskbar_icon` для Windows запускай daemon `threading.Thread`.
3. Внутри worker делай `time.sleep(0.3)`, чтобы нативное окно успело появиться.
4. Получи PID процесса через `os.getpid()`.
5. Используй `ctypes.WINFUNCTYPE`, `EnumWindows`, `IsWindowVisible`, `GetWindowThreadProcessId`.
6. Найди все видимые top-level окна текущего PID.
7. Для каждого найденного `HWND` выполни:

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

8. Лови исключения и печатай диагностическое сообщение в stderr, но не завершай приложение.
9. На не-Windows платформах функция должна сразу завершаться без действия.

Это требование не заменять и не «упрощать»: данный механизм был практически проверен для frameless pywebview-окна.

---

## 5. Python API для JavaScript

Экспортируй через `js_api=api` следующие публичные методы класса `QuickTaskAPI`:

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

### Поведение API

- `get_tasks`: читает все `*.md`, возвращает объекты с `id` (имя файла), `title`, `body`, `done`, `archived`, `urgent`, `created_at`, `updated_at`, `metadata`.
- `create_task`: создаёт новую Markdown-задачу, добавляет имя файла в начало `index.json`, возвращает созданную задачу.
- `update_task_status`: обновляет только переданные флаги и `updated_at`.
- `update_task_title`: изменяет H1, `updated_at`, безопасно переименовывает `.md`-файл и обновляет `index.json`.
- `update_task_full`: сохраняет title и body в одном действии, безопасно переименовывает файл при необходимости, возвращает новый `id` и `body`.
- `delete_task`: удаляет файл и удаляет его `id` из `index.json`.
- `save_tasks_order`: сохраняет массив ID в `index.json`.

Реализуй уведомление UI при внешних изменениях:

```python
CURRENT_WINDOW.evaluate_js(
    "window.refreshTasks && window.refreshTasks();"
)
```

### Watchdog

- Создай `TaskFileHandler(FileSystemEventHandler)`.
- Игнорируй директории и `index.json`.
- Реагируй на `.md` в `src_path` и `dest_path`, включая rename/move.
- Используй debounce примерно 0.3 секунды.
- Запускай observer как daemon.

---

## 6. Global hotkey

Настройка по умолчанию:

```text
ctrl+alt+t
```

При срабатывании хоткея:

1. Если сайдбар свёрнут — раскрыть его.
2. Вызвать JavaScript:

```javascript
window.onGlobalHotkeyTriggered &&
window.onGlobalHotkeyTriggered();
```

В JavaScript `onGlobalHotkeyTriggered` должен запускать создание новой задачи и фокусировать поле её названия.

Если библиотека `keyboard` недоступна, приложение должно продолжать работать без глобального хоткея. Ошибки регистрации должны попадать в stderr и не быть фатальными.

---

## 7. Дизайн и DOM

### Общие требования

- Язык интерфейса: русский.
- Тёмная тема по умолчанию и светлая тема.
- Стиль: аккуратный минималистичный интерфейс, близкий к VS Code / GitHub Dark.
- Никаких внешних CSS/JS-зависимостей, веб-шрифтов или иконок из CDN.
- Используй встроенные inline SVG.
- Body начинается с:

```html
<body class="collapsed theme-dark">
```

### CSS-переменные

Тёмная тема:

```css
body.theme-dark {
  --bg-primary: #18181b;
  --bg-secondary: #27272a;
  --bg-card: #202023;
  --bg-card-hover: #2e2e33;
  --text-main: #f4f4f5;
  --text-muted: #a1a1aa;
  --accent: #3b82f6;
  --accent-hover: #2563eb;
  --accent-active: #1d4ed8;
  --border-color: #3f3f46;
  --done-color: #71717a;
  --danger: #ef4444;
  --danger-hover: #dc2626;
  --modal-bg: rgba(0, 0, 0, 0.75);
}
```

Светлая тема:

```css
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
  --modal-bg: rgba(15, 23, 42, 0.45);
}
```

### Свёрнутый handle

Сохрани и используй следующий SVG **как есть**, без замены на emoji, bookmark, hamburger, другую иконку или внешнее изображение:

```html
<div id="collapsed-handle" title="QuickTask (Кликните или наведите)">
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="2.2"
    stroke-linecap="round"
    stroke-linejoin="round"
  >
    <path d="M9 11l3 3L22 4"></path>
    <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path>
  </svg>
</div>
```

В CSS:

- Handle — 36×36 px.
- Фон: `--bg-secondary`.
- Левая скруглённая грань: `border-radius: 6px 0 0 6px`.
- Правая граница отсутствует, так как элемент расположен у правого края экрана.
- Цвет иконки: `--accent`.
- При hover фон становится `--accent`, иконка белая, элемент сдвигается влево на `2 px`.

### Развёрнутый сайдбар

DOM:

```text
#sidebar
  #sidebar-drag-handle
  #sidebar-content
    header
      .header-top
        #btn-new
        .header-actions
          #btn-theme
          #btn-settings
          #btn-pin
          #btn-close-app
      .filter-tabs
    #task-list
```

`#sidebar-drag-handle` — вертикальная полоса 16 px слева, с иконками вверх, точками и вниз; курсор `ns-resize`.

Шапка:

- Основная кнопка: `+ Новая задача`.
- Кнопка theme с SVG солнца/луны.
- Кнопка settings с шестерёнкой.
- Кнопка pin.
- Кнопка закрытия с крестиком; при hover становится красной.

Фильтры:

```text
Активные | Все | Выполненные | Архив
```

Логика фильтров:

- Активные: `!done && !archived`.
- Все: все задачи.
- Выполненные: `done && !archived`.
- Архив: `archived`.

### Карточка задачи

Каждая карточка содержит:

1. Захват перетаскивания порядка — SVG из шести точек.
2. Кастомный checkbox.
3. Инлайн `<input>` названия.
4. Кнопки действий: редактировать описание (карандаш), архивировать, удалить.

Требования:

- Выполненная задача: зачёркнутый title с `--done-color`.
- Кастомный checkbox: 16×16 px, синяя заливка при выборе, белая галочка.
- Title сохраняется с debounce около 600 мс, при `blur` и `Enter`.
- Пустое название нельзя сохранять: верни прежнее значение.
- Двойной клик по карточке открывает редактор описания.
- Удаление подтверждается нативным `confirm`.
- При выполнении или архивации сразу обновляй нужный фильтр.

### Изменение порядка задач

- Делай HTML5 drag-and-drop только при захвате за grip.
- В конце drag вычисляй порядок визуальных карточек.
- Обновляй `currentTasks`.
- Вызывай:

```javascript
save_tasks_order(currentTasks.map(t => t.id))
```

- Показывай точку вставки через accent border сверху или снизу карточки.

---

## 8. Модальные окна

Используй overlay `.app-modal` внутри body:

```css
.app-modal {
  display: none;
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  backdrop-filter: blur(2px);
  z-index: 100;
}
```

### Модалка описания задачи

- ID: `#editor-modal`.
- Header: название задачи и кнопка закрытия.
- Большой textarea `#task-body-input` для Markdown-описания.
- Кнопки `Отмена` и `Сохранить`.
- `Esc` сохраняет изменения и закрывает эту модалку.
- Кнопка «Отмена» и крестик просто закрывают модалку без сохранения.

### Модалка настроек

- ID: `#settings-modal`.
- Поле только для чтения с путём каталога задач.
- Кнопка `Обзор...`, открывающая `CURRENT_WINDOW.create_file_dialog(webview.FOLDER_DIALOG)` через Python API.
- Поле number для `max_height`.
- Подсказка: «Оставьте пустым для полной высоты экрана».
- Кнопки `Отмена` и `Применить`.
- При применении передавай `tasks_dir`, `max_height`, `theme` в `save_settings`.
- При открытии загружай текущие значения через `get_config()`.
- `Esc` закрывает настройки без сохранения.

### Переключение темы

- `btn-theme` переключает `dark` ↔ `light` сразу в DOM и вызывает Python `set_theme`.
- Тема должна переживать перезапуск через `config.json`.
- На `pywebviewready` загрузи config, выстави корректный класс body и правильную иконку солнца/луны.

### Полное закрытие

Кнопка закрытия должна спрашивать:

```javascript
confirm('Вы уверены, что хотите полностью закрыть QuickTask?')
```

При подтверждении вызвать:

```javascript
window.pywebview.api.close_app()
```

`close_app` должен останавливать observer, уничтожать окно и завершать процесс.

---

## 9. Точки интеграции JavaScript

После готовности pywebview:

```javascript
window.addEventListener('pywebviewready', async () => {
  const cfg = await window.pywebview.api.get_config();

  isPinned = !!cfg.pinned;
  btnPin.classList.toggle('active', isPinned);

  applyTheme(cfg.theme || 'dark');

  await window.refreshTasks();
});
```

Нужны глобальные функции:

```javascript
window.refreshTasks = async function() {
  if (window.pywebview) {
    currentTasks = await window.pywebview.api.get_tasks();
    renderTasks();
  }
};

window.onGlobalHotkeyTriggered = function() {
  createNewTaskAction();
};
```

Проверяй `window.pywebview` перед каждым вызовом API.

---

## 10. Критерии готовности

Считай работу завершённой, только если выполнены все пункты:

1. `python main.py` запускает QuickTask на Windows.
2. Окно frameless, always-on-top и не видно в taskbar/Alt+Tab благодаря PID+EnumWindows+WS_EX_TOOLWINDOW механизму.
3. Свёрнутый 36×36 handle расположен у правого края и использует ровно заданный выше SVG.
4. Handle раскрывает сайдбар по наведению, клику и hotkey.
5. Задачи создаются, сохраняются как Markdown + YAML, редактируются, выполняются, архивируются и удаляются.
6. Редактирование внешнего `.md` файла автоматически обновляет список через Watchdog.
7. Порядок задач сохраняется в `index.json`.
8. Настройки каталога задач, темы, фиксированной высоты, pin, позиции ярлыка и hotkey сохраняются в `~/.quicktask/config.json`.
9. Работают тёмная и светлая темы.
10. Работают модалки описания и настроек.
11. Нет консольных трассировок при штатных действиях.
12. Код разделён логично, использует UTF-8 и имеет обработку ожидаемых ошибок ввода-вывода.

В финальном ответе после реализации кратко перечисли созданные файлы, зависимости, команду запуска и результаты ручной проверки ключевых сценариев.